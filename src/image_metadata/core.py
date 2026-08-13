"""Core image metadata inspection and removal behavior."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageCms, ImageOps, JpegImagePlugin
from PIL.ExifTags import TAGS

try:
    import piexif
except ImportError:  # pragma: no cover - piexif is an installed dependency
    piexif = None

SUPPORTED_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png"})
MessageSink = Callable[[str], None]
Metadata = dict[str | int, Any]


@dataclass(frozen=True, slots=True)
class ReportSummary:
    """Counts collected while creating a metadata report."""

    files_scanned: int = 0
    files_with_metadata: int = 0
    files_modified: int = 0
    files_skipped: int = 0
    errors: int = 0


class SanitizationSkipped(Exception):
    """An image cannot be safely handled by the sanitization preset."""


def get_metadata(file_path: str | os.PathLike[str]) -> Metadata:
    """Return the metadata Pillow exposes for an image.

    Read failures are represented by an ``error`` entry so callers can include
    the problem in a report without aborting an entire directory scan.
    """

    try:
        with Image.open(file_path) as image:
            metadata: Metadata = {}
            exif_data = image.getexif()
            if exif_data:
                for tag, value in exif_data.items():
                    metadata[TAGS.get(tag, tag)] = value
            metadata.update(image.info)
            return metadata
    except Exception as exc:
        return {"error": str(exc)}


def has_removable_metadata(metadata: Mapping[object, object] | None) -> bool:
    """Return whether metadata contains a removable EXIF/XMP/ICC/IPTC value."""

    if not metadata:
        return False

    for key, value in metadata.items():
        if isinstance(key, str):
            normalized_key = key.lower()
            if (
                any(
                    marker in normalized_key
                    for marker in ("exif", "xmp", "xml", "icc", "iptc")
                )
                and value
            ):
                return True
        if isinstance(value, (bytes, bytearray)) and value:
            return True
    return False


def _run_lossless_tool(arguments: list[str]) -> None:
    subprocess.run(
        arguments,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def nuke_exif(
    file_path: str | os.PathLike[str],
    *,
    log: MessageSink | None = None,
) -> bool:
    """Attempt lossless metadata removal without re-encoding image pixels.

    ``piexif`` handles EXIF in JPEG files. When installed on the host,
    ``exiftool`` can remove remaining JPEG metadata and metadata in other
    formats, while ``pngcrush`` handles PNG ancillary chunks.
    """

    path = Path(file_path)
    emit = log if log is not None else lambda _message: None

    try:
        if path.stat().st_size == 0:
            emit(f"Skipped empty file: {path}")
            return False
    except OSError as exc:
        emit(f"Could not access {path}: {exc}")
        return False

    extension = path.suffix.lower()
    has_exiftool = shutil.which("exiftool") is not None
    has_pngcrush = shutil.which("pngcrush") is not None

    if extension in {".jpg", ".jpeg"}:
        removed_any = False
        if piexif is not None:
            try:
                piexif.remove(str(path))
                emit("Removed EXIF with piexif.")
                removed_any = True
            except Exception as exc:
                emit(f"piexif could not remove EXIF: {exc}")

        if removed_any and not has_removable_metadata(get_metadata(path)):
            return True

        if has_exiftool:
            try:
                _run_lossless_tool(
                    ["exiftool", "-overwrite_original", "-all=", str(path)]
                )
                emit("Removed remaining metadata with exiftool.")
                return True
            except (OSError, subprocess.CalledProcessError) as exc:
                emit(f"exiftool could not remove metadata: {exc}")

        if removed_any:
            emit("Some metadata may remain because exiftool is unavailable.")
            return True

        emit("No lossless JPEG metadata remover is available; file unchanged.")
        return False

    if extension == ".png":
        removed_any = False
        if has_pngcrush:
            try:
                _run_lossless_tool(
                    ["pngcrush", "-rem", "alla", "-q", "-ow", str(path)]
                )
                emit("Removed PNG metadata with pngcrush.")
                removed_any = True
            except (OSError, subprocess.CalledProcessError) as exc:
                emit(f"pngcrush could not remove metadata: {exc}")

        if removed_any and not has_removable_metadata(get_metadata(path)):
            return True

        if has_exiftool:
            try:
                _run_lossless_tool(
                    ["exiftool", "-overwrite_original", "-all=", str(path)]
                )
                emit("Removed remaining metadata with exiftool.")
                return True
            except (OSError, subprocess.CalledProcessError) as exc:
                emit(f"exiftool could not remove metadata: {exc}")

        if removed_any:
            emit("Some metadata may remain because exiftool is unavailable.")
            return True

        emit("No lossless PNG metadata remover is available; file unchanged.")
        return False

    if has_exiftool:
        try:
            _run_lossless_tool(["exiftool", "-overwrite_original", "-all=", str(path)])
            emit("Removed metadata with exiftool.")
            return True
        except (OSError, subprocess.CalledProcessError) as exc:
            emit(f"exiftool could not remove metadata: {exc}")

    emit(f"No lossless metadata remover supports {extension or 'this format'}.")
    return False


def _iter_images(root_dir: Path) -> Iterator[Path]:
    def raise_walk_error(error: OSError) -> None:
        raise error

    for dirpath, dirnames, filenames in os.walk(root_dir, onerror=raise_walk_error):
        dirnames.sort()
        for filename in sorted(filenames):
            path = Path(dirpath, filename)
            if path.suffix.lower() in SUPPORTED_EXTENSIONS:
                yield path


def _is_protected_original(path: Path) -> bool:
    """Return whether path resolves inside a test_images/originals tree."""

    parts = path.resolve(strict=False).parts
    return any(
        parts[index : index + 2] == ("test_images", "originals")
        for index in range(len(parts) - 1)
    )


def _png_bit_depth(path: Path) -> int:
    """Read the PNG IHDR bit depth without trusting Pillow's converted mode."""

    with path.open("rb") as stream:
        header = stream.read(25)
    if (
        len(header) < 25
        or header[:8] != b"\x89PNG\r\n\x1a\n"
        or header[12:16] != b"IHDR"
    ):
        raise SanitizationSkipped("file does not contain a valid PNG header")
    return header[24]


def _convert_to_srgb(image: Image.Image, profile_data: bytes) -> Image.Image:
    """Apply an embedded ICC profile, returning unprofiled sRGB samples."""

    try:
        source_profile = ImageCms.ImageCmsProfile(BytesIO(profile_data))
        target_profile = ImageCms.createProfile("sRGB")
        alpha = image.getchannel("A") if "A" in image.getbands() else None
        if image.mode == "RGBA":
            color = image.convert("RGB")
        elif image.mode == "LA":
            color = image.getchannel("L")
        else:
            color = image
        converted = ImageCms.profileToProfile(
            color,
            source_profile,
            target_profile,
            outputMode="RGB",
        )
        if converted is None:  # Defensive: inPlace=False always returns an image.
            raise ValueError("ICC conversion returned no image")
        if alpha is not None:
            converted.putalpha(alpha)
        return converted
    except (ImageCms.PyCMSError, OSError, TypeError, ValueError) as exc:
        raise SanitizationSkipped(f"embedded ICC profile is invalid: {exc}") from exc


def _randomize_low_bits(image: Image.Image) -> Image.Image:
    """Replace every non-alpha 8-bit sample's low bit with a random bit."""

    bands = len(image.getbands())
    alpha_index = image.getbands().index("A") if "A" in image.getbands() else None
    sample_count = image.width * image.height * (
        bands - (1 if alpha_index is not None else 0)
    )
    random_bytes = os.urandom(sample_count)
    samples = bytearray(image.tobytes())
    random_index = 0
    for sample_index in range(0, len(samples), bands):
        for band_index in range(bands):
            if band_index == alpha_index:
                continue
            samples[sample_index + band_index] = (
                samples[sample_index + band_index] & 0xFE
            ) | (random_bytes[random_index] & 1)
            random_index += 1
    return Image.frombytes(image.mode, image.size, bytes(samples))


def _validate_reencoded(path: Path, expected_format: str, expected: Image.Image) -> None:
    with Image.open(path) as candidate:
        if candidate.format != expected_format:
            raise OSError(
                f"temporary output format is {candidate.format}, not {expected_format}"
            )
        candidate.load()
        if candidate.size != expected.size or candidate.mode != expected.mode:
            raise OSError("temporary output dimensions or mode changed unexpectedly")


def sanitize_image(file_path: str | os.PathLike[str]) -> None:
    """Atomically apply the experimental light sanitization preset in place.

    Unsupported or unsafe inputs raise :class:`SanitizationSkipped`. Other
    exceptions indicate processing failures. The original is not replaced
    until a fully decoded temporary output has been validated.
    """

    path = Path(file_path)
    if path.is_symlink():
        raise SanitizationSkipped("symbolic links are not sanitized")
    if _is_protected_original(path):
        raise SanitizationSkipped("test_images/originals is immutable")

    original_stat = path.stat(follow_symlinks=False)
    expected_format = "PNG" if path.suffix.lower() == ".png" else "JPEG"

    with Image.open(path) as source:
        if source.format != expected_format:
            raise SanitizationSkipped(
                f"extension indicates {expected_format}, but content is {source.format}"
            )
        if getattr(source, "is_animated", False) or getattr(source, "n_frames", 1) != 1:
            raise SanitizationSkipped("animated images are not supported")

        original_mode = source.mode
        if expected_format == "PNG" and original_mode == "P":
            raise SanitizationSkipped("unsupported PNG mode: P")
        if expected_format == "PNG" and _png_bit_depth(path) != 8:
            raise SanitizationSkipped("only 8-bit PNG samples are supported")
        supported_modes = (
            {"L", "LA", "RGB", "RGBA"}
            if expected_format == "PNG"
            else {"L", "RGB", "CMYK"}
        )
        if original_mode not in supported_modes:
            raise SanitizationSkipped(
                f"unsupported {expected_format} mode: {original_mode}"
            )

        profile_data = source.info.get("icc_profile")
        if original_mode == "CMYK" and not profile_data:
            raise SanitizationSkipped("CMYK JPEG requires a valid embedded ICC profile")
        if profile_data is not None and not isinstance(profile_data, bytes):
            raise SanitizationSkipped("embedded ICC profile is malformed")

        progressive = bool(
            source.info.get("progressive") or source.info.get("progression")
        )
        sampling = (
            JpegImagePlugin.get_sampling(source) if expected_format == "JPEG" else -1
        )
        source.load()
        decoded = ImageOps.exif_transpose(source)
        if profile_data:
            decoded = _convert_to_srgb(decoded, profile_data)
        else:
            decoded = decoded.copy()

    randomized = _randomize_low_bits(decoded)
    save_options: dict[str, object]
    if expected_format == "PNG":
        save_options = {"format": "PNG", "optimize": True}
    else:
        save_options = {
            "format": "JPEG",
            "quality": 90,
            "optimize": True,
            "progressive": progressive,
        }
        if sampling in {0, 1, 2}:
            save_options["subsampling"] = sampling

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=path.suffix,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
        randomized.save(temporary_path, **save_options)
        _validate_reencoded(temporary_path, expected_format, randomized)
        os.chmod(temporary_path, stat.S_IMODE(original_stat.st_mode))
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _display_value(value: object) -> str:
    if isinstance(value, (bytes, bytearray)):
        return f"<binary data: {len(value)} bytes>"
    return str(value)


def _write_metadata(report: Any, metadata: Mapping[object, object], indent: str) -> None:
    if not metadata:
        report.write(f"{indent}No metadata found.\n")
        return

    for key in sorted(metadata, key=lambda item: str(item).lower()):
        value_lines = _display_value(metadata[key]).splitlines() or [""]
        report.write(f"{indent}{key}: {value_lines[0]}\n")
        for line in value_lines[1:]:
            report.write(f"{indent}  {line}\n")


def main(
    root_dir: str | os.PathLike[str],
    report_file: str | os.PathLike[str],
    nuke_exif_flag: bool = False,
    *,
    sanitize: bool = False,
) -> ReportSummary:
    """Scan ``root_dir`` and atomically write a metadata report."""

    root = Path(root_dir)
    output = Path(report_file)
    if sanitize and _is_protected_original(output):
        raise PermissionError("sanitization cannot write into test_images/originals")
    files_scanned = 0
    files_with_metadata = 0
    files_modified = 0
    files_skipped = 0
    errors = 0
    temporary_path: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=output.parent,
            prefix=f".{output.name}.",
            suffix=".tmp",
            delete=False,
        ) as report:
            temporary_path = Path(report.name)

            for image_path in _iter_images(root):
                files_scanned += 1
                report.write(f"File: {image_path}\n")

                if sanitize:
                    if _is_protected_original(image_path):
                        files_skipped += 1
                        report.write(
                            "  Skipped: test_images/originals is immutable\n"
                        )
                    else:
                        metadata_before = get_metadata(image_path)
                        report.write("  Before:\n")
                        _write_metadata(report, metadata_before, "    ")
                        if metadata_before and "error" not in metadata_before:
                            files_with_metadata += 1
                        try:
                            sanitize_image(image_path)
                        except SanitizationSkipped as exc:
                            files_skipped += 1
                            report.write(f"  Skipped: {exc}\n")
                        except Exception as exc:
                            errors += 1
                            report.write(f"  Error: sanitization failed: {exc}\n")
                        else:
                            files_modified += 1
                            metadata_after = get_metadata(image_path)
                            report.write("  After:\n")
                            _write_metadata(report, metadata_after, "    ")
                            if "error" in metadata_after:
                                errors += 1
                elif nuke_exif_flag:
                    metadata_before = get_metadata(image_path)
                    report.write("  Before:\n")
                    _write_metadata(report, metadata_before, "    ")

                    if "error" in metadata_before:
                        errors += 1
                    elif has_removable_metadata(metadata_before):
                        messages: list[str] = []
                        removed = nuke_exif(image_path, log=messages.append)
                        for message in messages:
                            report.write(f"  Action: {message}\n")
                        if removed:
                            files_modified += 1
                        else:
                            errors += 1

                        metadata_after = get_metadata(image_path)
                        report.write("  After:\n")
                        _write_metadata(report, metadata_after, "    ")
                        if "error" in metadata_after:
                            errors += 1
                    else:
                        report.write("  No removable metadata found; skipped.\n")
                else:
                    metadata = get_metadata(image_path)
                    _write_metadata(report, metadata, "  ")
                    if "error" in metadata:
                        errors += 1

                if not sanitize:
                    metadata_for_count = (
                        metadata_before if nuke_exif_flag else metadata
                    )
                    if metadata_for_count and "error" not in metadata_for_count:
                        files_with_metadata += 1
                report.write("\n")

        os.replace(temporary_path, output)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)

    return ReportSummary(
        files_scanned=files_scanned,
        files_with_metadata=files_with_metadata,
        files_modified=files_modified,
        files_skipped=files_skipped,
        errors=errors,
    )

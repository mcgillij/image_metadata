"""Core image metadata inspection and removal behavior."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image
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
    errors: int = 0


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
) -> ReportSummary:
    """Scan ``root_dir`` and atomically write a metadata report."""

    root = Path(root_dir)
    output = Path(report_file)
    files_scanned = 0
    files_with_metadata = 0
    files_modified = 0
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

                if nuke_exif_flag:
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
        errors=errors,
    )

import os
import stat
from pathlib import Path

import pytest
from PIL import Image, ImageCms, JpegImagePlugin, PngImagePlugin

from image_metadata.core import (
    SanitizationSkipped,
    get_metadata,
    has_removable_metadata,
    main,
    nuke_exif,
    sanitize_image,
)


def make_jpeg(path: Path, *, with_exif: bool = False) -> None:
    image = Image.new("RGB", (2, 2), color="red")
    if with_exif:
        exif = Image.Exif()
        exif[0x010F] = "Test Camera"
        image.save(path, exif=exif)
    else:
        image.save(path)


def test_get_metadata_reads_exif(tmp_path: Path) -> None:
    image_path = tmp_path / "photo.jpg"
    make_jpeg(image_path, with_exif=True)

    metadata = get_metadata(image_path)

    assert metadata["Make"] == "Test Camera"
    assert has_removable_metadata(metadata)


def test_main_writes_sorted_report_and_summary(tmp_path: Path) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    make_jpeg(image_dir / "b.jpg")
    make_jpeg(image_dir / "a.jpg", with_exif=True)
    (image_dir / "ignored.txt").write_text("not an image", encoding="utf-8")
    report = tmp_path / "report.txt"

    summary = main(image_dir, report)

    contents = report.read_text(encoding="utf-8")
    assert contents.index("a.jpg") < contents.index("b.jpg")
    assert "Make: Test Camera" in contents
    assert "<binary data:" in contents
    assert summary.files_scanned == 2
    assert summary.files_with_metadata == 2
    assert summary.files_modified == 0
    assert summary.errors == 0


def test_main_records_unreadable_images_without_aborting(tmp_path: Path) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    (image_dir / "broken.png").write_bytes(b"not a png")
    report = tmp_path / "report.txt"

    summary = main(image_dir, report)

    assert "error:" in report.read_text(encoding="utf-8")
    assert summary.files_scanned == 1
    assert summary.errors == 1


def test_nuke_exif_removes_jpeg_exif_without_changing_pixels(
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "photo.jpg"
    make_jpeg(image_path, with_exif=True)
    with Image.open(image_path) as image:
        pixels_before = image.tobytes()
    messages: list[str] = []

    removed = nuke_exif(image_path, log=messages.append)

    with Image.open(image_path) as image:
        pixels_after = image.tobytes()
    assert removed
    assert pixels_after == pixels_before
    assert not has_removable_metadata(get_metadata(image_path))
    assert messages == ["Removed EXIF with piexif."]


def test_sanitize_png_randomizes_only_color_low_bits_and_strips_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image_path = tmp_path / "photo.png"
    original = Image.new("RGBA", (2, 2))
    original.putdata(
        [(10, 20, 30, 40), (10, 20, 30, 41), (10, 20, 30, 42), (10, 20, 30, 43)]
    )
    png_info = PngImagePlugin.PngInfo()
    png_info.add_text("prompt", "recognizable secret prompt")
    original.save(image_path, pnginfo=png_info)
    original_bytes = image_path.read_bytes()
    monkeypatch.setattr(
        "image_metadata.core.os.urandom",
        lambda count: bytes(index % 2 for index in range(count)),
    )

    sanitize_image(image_path)

    assert image_path.read_bytes() != original_bytes
    assert b"recognizable secret prompt" not in image_path.read_bytes()
    with Image.open(image_path) as sanitized:
        sanitized.load()
        assert sanitized.mode == "RGBA"
        assert sanitized.info == {}
        before_bytes = original.tobytes()
        after_bytes = sanitized.tobytes()
        before = [tuple(before_bytes[index : index + 4]) for index in range(0, 16, 4)]
        after = [tuple(after_bytes[index : index + 4]) for index in range(0, 16, 4)]
    assert [pixel[3] for pixel in after] == [pixel[3] for pixel in before]
    differences = [
        abs(new[channel] - old[channel])
        for old, new in zip(before, after)
        for channel in range(3)
    ]
    assert max(differences) == 1
    assert differences.count(1) == 6


@pytest.mark.parametrize("mode", ["L", "LA", "RGB", "RGBA"])
def test_sanitize_supports_static_8_bit_png_modes(tmp_path: Path, mode: str) -> None:
    image_path = tmp_path / f"{mode}.png"
    Image.new(mode, (3, 2), 127).save(image_path)

    sanitize_image(image_path)

    with Image.open(image_path) as image:
        image.load()
        assert image.mode == mode
        assert image.size == (3, 2)


@pytest.mark.parametrize("mode", ["L", "RGB"])
def test_sanitize_supports_jpeg_and_preserves_encoder_shape(
    tmp_path: Path, mode: str
) -> None:
    image_path = tmp_path / f"{mode}.jpg"
    Image.new(mode, (16, 16), 127).save(
        image_path, progressive=True, subsampling=1
    )

    sanitize_image(image_path)

    with Image.open(image_path) as image:
        image.load()
        assert image.mode == mode
        assert image.info["progressive"] == 1
        if mode == "RGB":
            assert JpegImagePlugin.get_sampling(image) == 1
        assert "exif" not in image.info
        assert "icc_profile" not in image.info


def test_sanitize_applies_exif_orientation(tmp_path: Path) -> None:
    image_path = tmp_path / "oriented.jpg"
    exif = Image.Exif()
    exif[0x0112] = 6
    Image.new("RGB", (4, 2), "red").save(image_path, exif=exif)

    sanitize_image(image_path)

    with Image.open(image_path) as image:
        assert image.size == (2, 4)
        assert not image.getexif()


def test_sanitize_converts_valid_profile_and_drops_it(tmp_path: Path) -> None:
    image_path = tmp_path / "profiled.png"
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    Image.new("RGB", (3, 3), "blue").save(image_path, icc_profile=profile)

    sanitize_image(image_path)

    with Image.open(image_path) as image:
        image.load()
        assert image.mode == "RGB"
        assert "icc_profile" not in image.info


def test_sanitize_removes_appended_payload(tmp_path: Path) -> None:
    image_path = tmp_path / "payload.png"
    Image.new("RGB", (2, 2), "green").save(image_path)
    marker = b"appended-hidden-payload-marker"
    with image_path.open("ab") as stream:
        stream.write(marker)

    sanitize_image(image_path)

    assert marker not in image_path.read_bytes()
    with Image.open(image_path) as image:
        image.load()
        assert image.format == "PNG"


@pytest.mark.parametrize(
    ("name", "make_image", "message"),
    [
        ("palette.png", lambda: Image.new("P", (2, 2)), "unsupported PNG mode"),
        ("one_bit.png", lambda: Image.new("1", (2, 2)), "only 8-bit PNG"),
        ("cmyk.jpg", lambda: Image.new("CMYK", (2, 2)), "requires a valid"),
    ],
)
def test_sanitize_skips_unsupported_inputs(
    tmp_path: Path, name: str, make_image: object, message: str
) -> None:
    image_path = tmp_path / name
    image = make_image()  # type: ignore[operator]
    image.save(image_path)
    before = image_path.read_bytes()

    with pytest.raises(SanitizationSkipped, match=message):
        sanitize_image(image_path)

    assert image_path.read_bytes() == before


def test_sanitize_skips_16_bit_and_animated_png(tmp_path: Path) -> None:
    sixteen_bit = tmp_path / "sixteen.png"
    Image.new("I;16", (2, 2), 1000).save(sixteen_bit)
    with pytest.raises(SanitizationSkipped, match="only 8-bit"):
        sanitize_image(sixteen_bit)

    animated = tmp_path / "animated.png"
    frames = [Image.new("RGBA", (2, 2), color) for color in ("red", "blue")]
    frames[0].save(animated, save_all=True, append_images=frames[1:], duration=10)
    with pytest.raises(SanitizationSkipped, match="animated"):
        sanitize_image(animated)


def test_sanitize_skips_mismatch_malformed_profile_and_symlink(tmp_path: Path) -> None:
    mismatch = tmp_path / "mismatch.png"
    Image.new("RGB", (2, 2)).save(mismatch, format="JPEG")
    with pytest.raises(SanitizationSkipped, match="extension indicates PNG"):
        sanitize_image(mismatch)

    malformed = tmp_path / "malformed.png"
    Image.new("RGB", (2, 2)).save(malformed, icc_profile=b"not an ICC profile")
    with pytest.raises(SanitizationSkipped, match="ICC profile is invalid"):
        sanitize_image(malformed)

    target = tmp_path / "target.png"
    Image.new("RGB", (2, 2)).save(target)
    link = tmp_path / "link.png"
    link.symlink_to(target)
    with pytest.raises(SanitizationSkipped, match="symbolic"):
        sanitize_image(link)


def test_sanitize_is_atomic_and_preserves_permissions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image_path = tmp_path / "photo.png"
    Image.new("RGB", (2, 2), "red").save(image_path)
    image_path.chmod(0o640)
    original = image_path.read_bytes()
    monkeypatch.setattr(
        "image_metadata.core._validate_reencoded",
        lambda *_args: (_ for _ in ()).throw(OSError("validation failed")),
    )

    with pytest.raises(OSError, match="validation failed"):
        sanitize_image(image_path)

    assert image_path.read_bytes() == original
    assert stat.S_IMODE(image_path.stat().st_mode) == 0o640
    assert list(tmp_path.glob(".photo.png.*")) == []

    monkeypatch.undo()
    sanitize_image(image_path)
    assert stat.S_IMODE(image_path.stat().st_mode) == 0o640


def test_sanitize_main_continues_and_reports_skips_and_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    Image.new("RGB", (2, 2)).save(image_dir / "good.png")
    Image.new("P", (2, 2)).save(image_dir / "skip.png")
    Image.new("RGB", (2, 2)).save(image_dir / "error.png")
    real_replace = os.replace

    def selective_replace(source: object, target: object) -> None:
        if os.fspath(target).endswith("error.png"):
            raise OSError("injected replace failure")
        real_replace(source, target)

    monkeypatch.setattr("image_metadata.core.os.replace", selective_replace)
    report = tmp_path / "report.txt"

    summary = main(image_dir, report, sanitize=True)

    assert summary.files_scanned == 3
    assert summary.files_modified == 1
    assert summary.files_skipped == 1
    assert summary.errors == 1
    contents = report.read_text(encoding="utf-8")
    assert "Skipped: unsupported PNG mode" in contents
    assert "Error: sanitization failed: injected replace failure" in contents


def test_sanitize_never_reads_or_writes_test_originals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    originals = tmp_path / "test_images" / "originals"
    originals.mkdir(parents=True)
    image_path = originals / "fixture.png"
    Image.new("RGB", (2, 2)).save(image_path)
    before = image_path.read_bytes()

    with pytest.raises(SanitizationSkipped, match="immutable"):
        sanitize_image(image_path)
    with pytest.raises(PermissionError, match="cannot write"):
        main(tmp_path, originals / "report.txt", sanitize=True)

    real_get_metadata = get_metadata

    def reject_original_read(path: object) -> dict[object, object]:
        if Path(path).resolve() == image_path.resolve():  # type: ignore[arg-type]
            raise AssertionError("protected original was read")
        return real_get_metadata(path)  # type: ignore[arg-type]

    monkeypatch.setattr("image_metadata.core.get_metadata", reject_original_read)
    report = tmp_path / "report.txt"
    summary = main(tmp_path, report, sanitize=True)

    assert image_path.read_bytes() == before
    assert summary.files_scanned == 1
    assert summary.files_skipped == 1
    assert summary.errors == 0

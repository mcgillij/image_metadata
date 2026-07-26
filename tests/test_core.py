from pathlib import Path

from PIL import Image

from image_metadata.core import get_metadata, has_removable_metadata, main, nuke_exif


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

from pathlib import Path

from click.testing import CliRunner
from PIL import Image

from image_metadata.cli import cli


def test_help_describes_safe_default_and_removal() -> None:
    result = CliRunner().invoke(cli, ["--help"])

    assert result.exit_code == 0
    assert "Inspect metadata in JPEG and PNG images" in result.output
    assert "--remove-metadata" in result.output
    assert "--nuke-exif" in result.output
    assert "--output" in result.output
    assert "--sanitize" in result.output
    assert "Images are read-only unless" in result.output


def test_version_is_available() -> None:
    result = CliRunner().invoke(cli, ["--version"])

    assert result.exit_code == 0
    assert result.output.startswith("image-metadata, version ")


def test_cli_creates_report_at_requested_path(tmp_path: Path) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    Image.new("RGB", (2, 2), color="blue").save(image_dir / "photo.png")
    report = tmp_path / "reports" / "metadata.txt"
    report.parent.mkdir()

    result = CliRunner().invoke(
        cli,
        [str(image_dir), "--output", str(report)],
        color=False,
    )

    assert result.exit_code == 0
    assert report.is_file()
    assert "photo.png" in report.read_text(encoding="utf-8")
    assert "Report written to" in result.output
    assert "Images scanned" in result.output


def test_cli_rejects_missing_output_directory(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        cli,
        [str(tmp_path), "--output", str(tmp_path / "missing" / "report.txt")],
    )

    assert result.exit_code == 2
    assert "parent directory does not exist" in result.output


def test_cli_rejects_both_destructive_modes(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        cli, [str(tmp_path), "--remove-metadata", "--sanitize"]
    )

    assert result.exit_code == 2
    assert "mutually exclusive" in result.output


def test_sanitize_cli_exits_one_after_writing_report_for_skip(tmp_path: Path) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    Image.new("P", (2, 2)).save(image_dir / "unsupported.png")
    report = tmp_path / "report.txt"

    result = CliRunner().invoke(
        cli, [str(image_dir), "--sanitize", "--output", str(report)]
    )

    assert result.exit_code == 1
    assert report.is_file()
    assert "Skipped" in result.output
    assert "unsupported PNG mode" in report.read_text(encoding="utf-8")

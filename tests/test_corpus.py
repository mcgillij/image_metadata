"""Opt-in end-to-end checks against immutable real-image fixtures."""

from __future__ import annotations

import hashlib
import math
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pytest
from PIL import Image, ImageChops


pytestmark = pytest.mark.skipif(
    os.environ.get("IMAGE_METADATA_RUN_CORPUS") != "1",
    reason="set IMAGE_METADATA_RUN_CORPUS=1 to run immutable corpus checks",
)


@dataclass(frozen=True)
class OriginalState:
    resolved_path: Path
    digest: str
    inode: int
    size: int
    permissions: int
    mtime_ns: int


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _snapshot(path: Path) -> OriginalState:
    details = path.stat()
    return OriginalState(
        resolved_path=path.resolve(strict=True),
        digest=_digest(path),
        inode=details.st_ino,
        size=details.st_size,
        permissions=stat.S_IMODE(details.st_mode),
        mtime_ns=details.st_mtime_ns,
    )


def _publish_atomic(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=destination.suffix,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
        shutil.copyfile(source, temporary_path)
        os.replace(temporary_path, destination)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def test_sanitize_real_png_corpus_without_touching_originals(tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[1]
    originals_dir = repository / "test_images" / "originals"
    processed_dir = repository / "test_images" / "processed"
    originals = sorted(originals_dir.glob("*.png"))
    assert len(originals) == 2
    states = {path: _snapshot(path) for path in originals}
    working_dir = tmp_path / "working"
    working_dir.mkdir()
    working_files = []

    try:
        for original in originals:
            copy = working_dir / original.name
            shutil.copy2(original, copy)
            working_files.append(copy)

        report = tmp_path / "corpus-report.txt"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "image_metadata",
                os.fspath(working_dir),
                "--sanitize",
                "--output",
                os.fspath(report),
            ],
            cwd=repository,
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "Images scanned  2" in result.stdout
        assert "Modified        2" in result.stdout
        assert "Skipped" not in result.stdout
        assert "Errors" not in result.stdout
        report_text = report.read_text(encoding="utf-8")
        assert report_text.count("File: ") == 2
        assert report_text.count("  After:\n") == 2
        assert "  Skipped:" not in report_text
        assert "  Error:" not in report_text

        for original, processed in zip(originals, working_files):
            output_bytes = processed.read_bytes()
            assert b"prompt" not in output_bytes.lower()
            assert b"comfyui" not in output_bytes.lower()
            with Image.open(original) as before_image, Image.open(processed) as after_image:
                before_image.load()
                after_image.load()
                assert before_image.mode == after_image.mode == "RGB"
                assert before_image.size == after_image.size
                assert "prompt" in before_image.info
                assert "prompt" not in after_image.info
                difference = ImageChops.difference(before_image, after_image)
                channel_differences = list(difference.tobytes())
            assert max(channel_differences) == 1
            changed_ratio = sum(value != 0 for value in channel_differences) / len(
                channel_differences
            )
            assert 0.45 <= changed_ratio <= 0.55
            mse = sum(value * value for value in channel_differences) / len(
                channel_differences
            )
            psnr = 10 * math.log10((255 * 255) / mse)
            assert 50 <= psnr <= 52

        for processed in working_files:
            _publish_atomic(processed, processed_dir / processed.name)
    finally:
        for original, expected in states.items():
            assert _snapshot(original) == expected

"""Inspect and remove metadata embedded in image files."""

from importlib.metadata import PackageNotFoundError, version

from image_metadata.core import (
    ReportSummary,
    get_metadata,
    has_removable_metadata,
    main,
    nuke_exif,
)

try:
    __version__ = version("image-metadata")
except PackageNotFoundError:
    __version__ = "0.1.0"

__all__ = [
    "ReportSummary",
    "__version__",
    "get_metadata",
    "has_removable_metadata",
    "main",
    "nuke_exif",
]

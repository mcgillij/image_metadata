"""Command-line interface for image-metadata."""

from __future__ import annotations

import os
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table
from rich.text import Text

from image_metadata import __version__
from image_metadata.core import ReportSummary, main

CONTEXT_SETTINGS = {
    "help_option_names": ["-h", "--help"],
    "max_content_width": 100,
}


def _print_summary(
    console: Console,
    output: Path,
    summary: ReportSummary,
    removing_metadata: bool,
    sanitizing: bool,
) -> None:
    console.print(
        Text.assemble(
            ("Report written to ", "bold green"),
            (os.fspath(output), "bold cyan"),
        )
    )

    table = Table.grid(padding=(0, 2))
    table.add_column(style="dim")
    table.add_column(justify="right")
    table.add_row("Images scanned", str(summary.files_scanned))
    table.add_row("With metadata", str(summary.files_with_metadata))
    if removing_metadata or sanitizing:
        table.add_row("Modified", str(summary.files_modified))
    if summary.files_skipped:
        table.add_row("Skipped", str(summary.files_skipped), style="yellow")
    if summary.errors:
        table.add_row("Errors", str(summary.errors), style="red")
    console.print(table)

    if summary.files_scanned == 0:
        console.print(
            "[yellow]No supported images were found "
            "(expected .jpg, .jpeg, or .png).[/yellow]"
        )


@click.command(
    context_settings=CONTEXT_SETTINGS,
    options_metavar="[OPTIONS]",
    epilog="""\b
Examples:
  image-metadata ~/Pictures
  image-metadata ./photos --output metadata.txt
  image-metadata ./photos --remove-metadata
  image-metadata ./photos --sanitize

Metadata removal never re-encodes image pixels. JPEG EXIF is handled by piexif;
exiftool and pngcrush are used when available for additional formats/metadata.""",
)
@click.argument(
    "directory",
    type=click.Path(
        exists=True,
        file_okay=False,
        dir_okay=True,
        readable=True,
        path_type=Path,
    ),
    default=Path("."),
    metavar="[DIRECTORY]",
)
@click.option(
    "-o",
    "--output",
    type=click.Path(file_okay=True, dir_okay=False, path_type=Path),
    default=Path("image_metadata_report.txt"),
    show_default=True,
    metavar="FILE",
    help="Write the report to FILE.",
)
@click.option(
    "--sanitize",
    is_flag=True,
    help=(
        "EXPERIMENTAL: fully re-encode images, strip metadata, and randomize "
        "non-alpha pixel low bits."
    ),
)
@click.option(
    "--remove-metadata",
    "--nuke-exif",
    "remove_metadata",
    is_flag=True,
    help=(
        "Remove detected metadata in place using lossless tools. "
        "--nuke-exif is retained as a compatibility alias."
    ),
)
@click.option(
    "-q",
    "--quiet",
    is_flag=True,
    help="Do not print the completion summary.",
)
@click.version_option(version=__version__, prog_name="image-metadata")
def cli(
    directory: Path,
    output: Path,
    remove_metadata: bool,
    sanitize: bool,
    quiet: bool,
) -> None:
    """Inspect metadata in JPEG and PNG images below DIRECTORY.

    DIRECTORY defaults to the current working directory. The scan is recursive
    and produces a UTF-8 text report. Images are read-only unless
    --remove-metadata or --sanitize is explicitly supplied.
    """

    if remove_metadata and sanitize:
        raise click.UsageError(
            "--sanitize and --remove-metadata are mutually exclusive"
        )

    if not output.parent.is_dir():
        raise click.BadParameter(
            f"parent directory does not exist: {output.parent}",
            param_hint="'--output'",
        )

    try:
        summary = main(directory, output, remove_metadata, sanitize=sanitize)
    except OSError as exc:
        raise click.ClickException(f"could not create report: {exc}") from exc

    if not quiet:
        _print_summary(
            Console(highlight=False), output, summary, remove_metadata, sanitize
        )

    if sanitize and (summary.files_skipped or summary.errors):
        raise click.exceptions.Exit(1)

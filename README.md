# image-metadata

`image-metadata` recursively inspects JPEG and PNG files, then writes the
metadata Pillow can read to a UTF-8 text report. It can also remove detected
metadata in place using lossless tools, without re-encoding image pixels.

## Installation

Install the project with Poetry:

```console
poetry install
```

Or build a wheel and install it with `pip`:

```console
poetry build
python -m pip install dist/image_metadata-*.whl
```

Python 3.10 or newer is required.

## Usage

Scan the current directory:

```console
image-metadata
```

Scan another directory and choose the report path:

```console
image-metadata ~/Pictures --output picture-metadata.txt
```

Remove detected metadata while recording before-and-after values:

```console
image-metadata ~/Pictures --remove-metadata
```

Removal changes the source files in place. JPEG EXIF is handled by the bundled
`piexif` dependency. If installed on the host, `exiftool` can remove remaining
metadata and `pngcrush` can process PNG ancillary chunks. When no suitable
lossless tool is available, the image is left unchanged and the report explains
why.

Run `image-metadata --help` for every option. The legacy `--nuke-exif` flag is
still accepted as an alias for `--remove-metadata`.

The package can also be run without its console script:

```console
python -m image_metadata --help
```

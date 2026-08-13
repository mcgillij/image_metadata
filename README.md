# image-metadata

`image-metadata` recursively inspects JPEG and PNG files, then writes the
metadata Pillow can read to a UTF-8 text report. It can also remove detected
metadata in place using lossless tools, without re-encoding image pixels.
An experimental sanitization preset is available for a more destructive,
privacy-oriented rewrite.

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

Fully decode and re-encode supported images, strip optional metadata, and
randomize the least-significant bit of every non-alpha 8-bit sample:

```console
image-metadata ~/Pictures --sanitize
```

`--sanitize` is mutually exclusive with `--remove-metadata`. It supports static
8-bit `L`, `LA`, `RGB`, and `RGBA` PNGs and `L` and `RGB` JPEGs. CMYK JPEGs are
accepted only with a valid embedded color profile. Files are replaced atomically
without backups after their temporary outputs validate, and their permission
bits are preserved. Unsupported, animated, mismatched, malformed-profile, and
symlink inputs are skipped; the command finishes the report and exits with
status 1 if any input was skipped or failed.

The preset is intentionally light: it targets metadata, appended payloads,
ordinary pixel-LSB steganography, and fragile JPEG transform payloads. It does
not guarantee removal of robust steganography or watermarks, some of which can
[survive transcoding](https://www.usenix.org/system/files/conference/foci14/foci14-connolly.pdf).

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

## Corpus test

The checked-in originals are immutable inputs. The opt-in corpus test copies
them to a temporary directory, invokes the CLI only on those copies, validates
the results, and then atomically publishes disposable outputs under
`test_images/processed/`:

```console
IMAGE_METADATA_RUN_CORPUS=1 poetry run pytest tests/test_corpus.py
```

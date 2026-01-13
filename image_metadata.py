import os
import tempfile
import subprocess
import shutil
from PIL import Image, UnidentifiedImageError
from PIL.ExifTags import TAGS

# Optional lossless tools
try:
    import piexif
    HAS_PIEXIF = True
except Exception:
    piexif = None
    HAS_PIEXIF = False

HAS_PNGCRUSH = shutil.which("pngcrush") is not None
HAS_EXIFTOOL = shutil.which("exiftool") is not None

def get_metadata(file_path):
    try:
        with Image.open(file_path) as img:
            info = img.info
            exif_data = img._getexif() if hasattr(img, '_getexif') else None
            metadata = {}
            if exif_data:
                for tag, value in exif_data.items():
                    tag_name = TAGS.get(tag, tag)
                    metadata[tag_name] = value
            # Merge info and exif
            metadata.update(info)
            return metadata
    except Exception as e:
        return {"error": str(e)}

def has_removable_metadata(metadata):
    """Return True if the passed metadata dict contains removable metadata like EXIF/XMP/ICC/IPTC."""
    if not metadata or not isinstance(metadata, dict):
        return False
    # Keys that usually indicate embedded metadata
    for k, v in metadata.items():
        if not isinstance(k, str):
            continue
        lk = k.lower()
        if any(x in lk for x in ("exif", "xmp", "xml", "icc", "iptc")):
            # non-empty value
            if v:
                return True
        # raw byte blobs are usually metadata
        if isinstance(v, (bytes, bytearray)) and len(v) > 0:
            return True
    return False


def nuke_exif(file_path):
    """Attempt lossless metadata removal only. Return True if removal attempted/succeeded."""
    try:
        # Skip empty files early
        try:
            size = os.path.getsize(file_path)
        except OSError:
            print(f"Failed to access {file_path}")
            return False
        if size == 0:
            print(f"Skipping empty file {file_path}")
            return False

        ext = os.path.splitext(file_path)[1].lower()

        # JPEG/JFIF: prefer piexif, then exiftool; no lossy fallback
        if ext in (".jpg", ".jpeg"):
            removed_any = False
            if HAS_PIEXIF:
                try:
                    piexif.remove(file_path)
                    print(f"Losslessly removed EXIF from {file_path} (piexif)")
                    removed_any = True
                except Exception as e:
                    print(f"piexif failed for {file_path}: {e} — falling back")
            # If piexif removed EXIF, check if other removable metadata remains (e.g., ICC/XMP)
            if removed_any and not has_removable_metadata(get_metadata(file_path)):
                return True
            if HAS_EXIFTOOL:
                try:
                    subprocess.run(["exiftool", "-overwrite_original", "-all=", file_path], check=False, stdout=subprocess.DEVNULL)
                    print(f"Losslessly removed remaining metadata from {file_path} (exiftool)")
                    return True
                except Exception as e:
                    print(f"exiftool failed for {file_path}: {e}")
            if removed_any:
                print(f"Some metadata may remain in {file_path}, but no exiftool available to remove it")
                return True
            print(f"No lossless tool available for JPEG {file_path}; skipping to avoid lossy re-encode.")
            return False

        # PNG: prefer pngcrush, then exiftool; no lossy Pillow re-save fallback
        if ext == ".png":
            removed_any = False
            if HAS_PNGCRUSH:
                try:
                    subprocess.run(["pngcrush", "-rem", "alla", "-q", "-ow", file_path], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    print(f"Losslessly removed metadata from {file_path} (pngcrush)")
                    removed_any = True
                except Exception as e:
                    print(f"pngcrush failed for {file_path}: {e} — falling back")
            # If pngcrush ran, check if more metadata remains (e.g., XMP)
            if removed_any and not has_removable_metadata(get_metadata(file_path)):
                return True
            if HAS_EXIFTOOL:
                try:
                    subprocess.run(["exiftool", "-overwrite_original", "-all=", file_path], check=False, stdout=subprocess.DEVNULL)
                    print(f"Removed remaining metadata from {file_path} (exiftool)")
                    return True
                except Exception as e:
                    print(f"exiftool failed for {file_path}: {e}")
            if removed_any:
                print(f"Some metadata may remain in {file_path}, but no exiftool available to remove it")
                return True
            print(f"No lossless tool available for PNG {file_path}; skipping to avoid unintended changes.")
            return False

        # Generic fallback for other formats: try exiftool only
        if HAS_EXIFTOOL:
            try:
                subprocess.run(["exiftool", "-overwrite_original", "-all=", file_path], check=False, stdout=subprocess.DEVNULL)
                print(f"Removed metadata from {file_path} (exiftool)")
                return True
            except Exception as e:
                print(f"exiftool failed for {file_path}: {e}")
        print(f"No lossless tool available for {file_path} (format {ext}); skipping.")
        return False

    except UnidentifiedImageError:
        print(f"Failed to nuke EXIF for {file_path}: cannot identify image (possibly corrupt or zero-length)")
        return False
    except Exception as e:
        print(f"Failed to nuke EXIF for {file_path}: {e}")
        return False
def main(root_dir, report_file, nuke_exif_flag=False):
    with open(report_file, 'w', encoding='utf-8') as report:
        for dirpath, _, filenames in os.walk(root_dir):
            for fname in filenames:
                if fname.lower().endswith(('.jpg', '.jpeg', '.png')):
                    fpath = os.path.join(dirpath, fname)
                    report.write(f"File: {fpath}\n")
                    if nuke_exif_flag:
                        meta_before = get_metadata(fpath)
                        report.write("  Before:\n")
                        if meta_before:
                            for k, v in meta_before.items():
                                report.write(f"    {k}: {v}\n")
                        else:
                            report.write("    No metadata found.\n")

                        # Only attempt removal if there is removable metadata
                        if has_removable_metadata(meta_before):
                            nuke_exif(fpath)
                            meta_after = get_metadata(fpath)
                            report.write("  After:\n")
                            if meta_after:
                                for k, v in meta_after.items():
                                    report.write(f"    {k}: {v}\n")
                            else:
                                report.write("    No metadata found.\n")
                        else:
                            report.write("  No removable metadata found; skipped.\n")
                    else:
                        metadata = get_metadata(fpath)
                        if metadata:
                            for k, v in metadata.items():
                                report.write(f"  {k}: {v}\n")
                        else:
                            report.write("  No metadata found.\n")
                    report.write("\n")

if __name__ == "__main__":
    import sys
    # Use command-line argument if provided, else default to current directory
    root_directory = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith('--') else "."
    output_report = "image_metadata_report.txt"
    nuke_exif_flag = "--nuke-exif" in sys.argv
    main(root_directory, output_report, nuke_exif_flag)
    print(f"Report written to {output_report}")

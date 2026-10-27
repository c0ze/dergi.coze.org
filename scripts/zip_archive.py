"""One ZIP per title from data/archive/, for a copy in Google Drive.

PDFs and disk images are stored as they are (already compressed); text files are deflated.
Names are NFC-normalised and flagged UTF-8, so "Amiga Dünyası" reads right on Windows too;
ZIP64 handles the 5 GB Commodore folder. Each zip carries the archive README. Every zip is
read back and CRC-checked before it is copied to the destination.

Usage: zip_archive.py <destination folder> [--work data/zips]
"""

import argparse
import shutil
import unicodedata
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ARCHIVE = ROOT / "data" / "archive"
DEFLATE = {".txt", ".json", ".nfo", ".md"}


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def build(title_dir: Path, out: Path) -> int:
    files = sorted(p for p in title_dir.rglob("*") if p.is_file() and not p.name.startswith("."))
    tmp = out.with_suffix(".zip.part")
    with zipfile.ZipFile(tmp, "w", allowZip64=True) as z:
        z.write(ARCHIVE / "README.txt", nfc(f"{title_dir.name}/README.txt"), zipfile.ZIP_DEFLATED)
        for f in files:
            how = zipfile.ZIP_DEFLATED if f.suffix.lower() in DEFLATE else zipfile.ZIP_STORED
            z.write(f, nfc(f"{title_dir.name}/{f.relative_to(title_dir)}"), how)
    with zipfile.ZipFile(tmp) as z:
        bad = z.testzip()
        if bad:
            raise SystemExit(f"{tmp}: CRC error in {bad}")
    tmp.replace(out)
    return len(files)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dest", type=Path)
    ap.add_argument("--work", type=Path, default=ROOT / "data" / "zips")
    args = ap.parse_args()
    args.work.mkdir(parents=True, exist_ok=True)
    args.dest.mkdir(parents=True, exist_ok=True)
    for title_dir in sorted(p for p in ARCHIVE.iterdir() if p.is_dir()):
        name = nfc(title_dir.name) + ".zip"
        local, remote = args.work / name, args.dest / name
        if remote.exists() and local.exists() and remote.stat().st_size == local.stat().st_size:
            print(f"{name}: already in place")
            continue
        n = build(title_dir, local)
        shutil.copyfile(local, remote)
        if remote.stat().st_size != local.stat().st_size:
            raise SystemExit(f"{remote}: size differs after copy")
        print(f"{name}: {n} files, {local.stat().st_size / 1e9:.2f} GB", flush=True)
    for extra in ("README.txt", "manifest.json"):
        shutil.copyfile(ARCHIVE / extra, args.dest / extra)
    print("done")


if __name__ == "__main__":
    main()

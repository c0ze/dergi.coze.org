"""The personal archive: one PDF per issue from the best scan, cleaned, with a text layer.

For every title in catalog/sources.yaml (English titles included), sources.py picks the best
scan of each issue. Then:
- Born-digital PDFs and re-typeset remakes keep their content; only metadata is set.
- Scans are rebuilt page by page: the page image is cleaned (cleanup.py: tilt straightened,
  no cropping; untouched pages keep their original bytes), each page is sized to A4
  height, and OCRmyPDF adds an invisible text layer (Turkish or English) without touching
  the images.
- Supplements (inserts) are appended to their issue; disk images and remakes that lost to a
  real scan go to <Title>/extras/.
- manifest.json lists every file with its checksum, source, alternatives and cleanup stats.

Output: data/archive/<Title>/<Title> - NNN (YYYY-MM).pdf, extras/, manifest.json, README.txt
Usage:  archive.py [slug ...] [--no-ocr] [--jobs 4]      (default: all titles; resumable)
"""

import argparse
import hashlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pymupdf
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
import cleanup  # noqa: E402
import sources  # noqa: E402
from fsutil import read_json, write_json  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "archive"
PAGE_HEIGHT = 842  # points: every scanned page is A4-tall, whatever the scan's resolution


def file_name(cfg: dict, key, date: str | None) -> str:
    name = cfg["name"]
    if cfg.get("book"):
        return f"{name}.pdf"
    if cfg.get("key") == "date":
        return f"{name} - {date}.pdf"
    if cfg.get("key") == "volume":
        vol, no = key.split("-")
        return f"{name} - Vol {vol} No {no}.pdf"
    return f"{name} - {key:03d}" + (f" ({date})" if date else "") + ".pdf"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def pdf_page_images(path: Path):
    """Page images of a scanned PDF as a reader sees them (see sources.visible_scan)."""
    for page in pymupdf.open(path):
        yield sources.visible_scan(page)[0]


def djvu_page_images(path: Path):
    with tempfile.TemporaryDirectory() as t:
        for n in range(1, sources.djvu_pages(path) + 1):
            ppm = Path(t) / f"{n}.ppm"
            subprocess.run(["ddjvu", "-format=ppm", f"-page={n}", str(path), str(ppm)], check=True)
            buf = io.BytesIO()
            Image.open(ppm).convert("RGB").save(buf, "PNG")  # lossless hand-off; cleanup encodes
            yield buf.getvalue()
            ppm.unlink()


def page_images(c: dict):
    if c["kind"] == "pdf":
        yield from pdf_page_images(c["path"])
    elif c["kind"] == "djvu":
        yield from djvu_page_images(c["path"])
    else:
        for f in sources.page_images(c):
            yield f.read_bytes()


def build_scan(parts: list[dict], out: Path, stats: dict) -> None:
    doc = pymupdf.open()
    for c in parts:
        for data in page_images(c):
            cleaned, info = cleanup.clean(data)
            stats["pages"] += 1
            stats["rotated"] += bool(info["angle"])
            stats["cropped"] += any(info["crop"])
            w, h = Image.open(io.BytesIO(cleaned)).size
            page = doc.new_page(width=PAGE_HEIGHT * w / h, height=PAGE_HEIGHT)
            page.insert_image(page.rect, stream=cleaned)
    doc.save(out, garbage=3, deflate=True)


def set_metadata(path: Path, meta: dict) -> None:
    doc = pymupdf.open(path)
    doc.set_metadata({**doc.metadata, **meta})
    tmp = path.with_suffix(".meta.pdf")
    doc.save(tmp, garbage=3, deflate=True)
    tmp.replace(path)


def ocr(src: Path, dst: Path, lang: str, meta: dict, jobs: int) -> None:
    subprocess.run([
        "ocrmypdf", "-l", lang, "--output-type", "pdf", "--optimize", "0", "--jobs", str(jobs),
        "--title", meta["title"], "--subject", meta["subject"], "--keywords", meta["keywords"],
        "--quiet", str(src), str(dst)], check=True,
        env={**os.environ, "TESSDATA_PREFIX": str(ROOT / "data" / "tessdata")})


def build_title(slug: str, cfg: dict, args, manifest: dict) -> None:
    plan = sources.choose(slug, cfg)
    sources.write_report(slug, plan)
    folder = OUT / cfg["name"]
    folder.mkdir(parents=True, exist_ok=True)
    lang = cfg.get("lang", "tur")
    for key, p in plan.items():
        for e in p["extras"]:
            prefix = "" if key == "_title" or e["path"].name.startswith(cfg["name"]) else f"{key} - "
            dst = folder / "extras" / (("remake - " if e.get("remake") else "") + prefix + e["path"].name)
            if not dst.exists():
                dst.parent.mkdir(exist_ok=True)
                shutil.copy2(e["path"], dst)
            manifest["extras"].append({"title": cfg["name"], "issue": key, "file": str(dst.relative_to(OUT)),
                                       "source": str(e["path"].relative_to(ROOT)), "sha256": sha256(dst)})
        if not p["chosen"]:
            continue
        out = folder / file_name(cfg, key, p["date"])
        entry = {"title": cfg["name"], "issue": key, "date": p["date"], "file": str(out.relative_to(OUT)),
                 "source": sources.report_row(p["chosen"]),
                 "supplements": [sources.report_row(c) for c in p["supplements"]],
                 "alternatives": [sources.report_row(c) for c in p["alternatives"]]}
        meta = {"title": out.stem, "subject": f"{cfg['name']}" + (f", {p['date']}" if p["date"] else ""),
                "keywords": f"{cfg['name']}; retro; Commodore; Amiga; dergi.coze.org"}
        # What this file is built from and how; a mismatch (new source, new cleanup, OCR
        # turned on) rebuilds it instead of trusting a file that merely exists.
        parts = [p["chosen"], *p["supplements"]]
        recipe = {"cleanup": cleanup.VERSION, "ocr": not args.no_ocr,
                  "sources": [[str(c["path"].relative_to(ROOT)), c["path"].stat().st_size]
                              for c in parts if c["path"].is_file()]}
        side = folder / f".{out.stem}.json"
        if out.exists() and (read_json(side) or {}).get("recipe") != recipe:
            out.unlink()
        entry["recipe"] = recipe
        if not out.exists():
            t0 = time.time()
            stats = {"pages": 0, "rotated": 0, "cropped": 0}
            if p["chosen"].get("digital") or p["chosen"].get("remake"):
                # Already text: keep the content, append supplements, set metadata.
                doc = pymupdf.open(p["chosen"]["path"])
                tmp = out.with_suffix(".tmp.pdf")
                if p["supplements"]:
                    build_scan(p["supplements"], tmp, stats)
                    doc.insert_pdf(pymupdf.open(tmp))
                doc.set_metadata({**doc.metadata, **meta})
                doc.save(out, garbage=3, deflate=True)
                tmp.unlink(missing_ok=True)
                entry["kind"] = "remake" if p["chosen"].get("remake") else "born-digital"
            else:
                raw = out.with_suffix(".raw.pdf")
                build_scan(parts, raw, stats)
                if args.no_ocr:
                    raw.rename(out)
                    set_metadata(out, meta)
                else:
                    ocr(raw, out, lang, meta, args.jobs)
                    raw.unlink()
                entry["kind"] = "scan"
            entry["cleanup"] = stats
            write_json(side, entry)
            print(f"{out.relative_to(OUT)}: {stats['pages']} pages, {stats['rotated']} straightened, "
                  f"{stats['cropped']} cropped, {out.stat().st_size / 1e6:.1f} MB, {time.time() - t0:.0f}s", flush=True)
        if side.exists():
            entry = read_json(side) or entry
        entry.update({"bytes": out.stat().st_size, "sha256": sha256(out), "pages": pymupdf.open(out).page_count})
        manifest["issues"].append(entry)


README = """Kişisel dergi arşivi — personal magazine archive
=================================================

One PDF per issue, built from the best available scan (most complete, then highest
resolution) of every copy found on the NAS and in the downloaded collections.

- Scans: tilted pages straightened where clearly needed (nothing is cropped); pages that
  needed nothing keep their original image bytes. Every scanned PDF has an invisible OCR
  text layer (Turkish or English), so it is searchable and copyable in any PDF viewer.
- Born-digital issues (C64 Türkiye, Total Amiga, CommodoreFree, APoV, ...) are kept as made.
- Amiga Dünyası: most issues exist only as ABBYY FineReader re-typesettings (retyped text,
  cut-out pictures), not page scans. Where a real scan exists it is the main file and the
  re-typesetting is in extras/.
- Supplements (Commodore's Program Döküm Ekleri, 64'ler's Sega Magazine and Professional)
  are appended to the end of their issue.
- extras/: disk images (D64, ADF) and other companion files, unchanged.
- manifest.json: every file with SHA-256, page count, the source scan it was built from, the
  alternatives that lost, and what cleanup changed.

Built by pipeline/archive.py in ~/projects/dergi.coze.org.
"""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("slugs", nargs="*")
    ap.add_argument("--no-ocr", action="store_true")
    ap.add_argument("--jobs", type=int, default=4)
    args = ap.parse_args()
    cfgs = sources.config()
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = {"issues": [], "extras": []}
    for slug in args.slugs or cfgs:
        build_title(slug, cfgs[slug], args, manifest)
    # A run over some titles updates their entries and keeps the others.
    built = {e["title"] for e in manifest["issues"] + manifest["extras"]}
    old = read_json(OUT / "manifest.json") or {"issues": [], "extras": []}
    for part in ("issues", "extras"):
        manifest[part] = [e for e in old[part] if e["title"] not in built] + manifest[part]
    write_json(OUT / "manifest.json", manifest, indent=1, default=str)
    (OUT / "README.txt").write_text(README)
    total = sum(e["bytes"] for e in manifest["issues"])
    print(f"{len(manifest['issues'])} issues, {len(manifest['extras'])} extras, {total / 1e9:.2f} GB")


if __name__ == "__main__":
    main()

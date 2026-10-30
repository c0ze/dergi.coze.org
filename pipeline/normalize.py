"""Phase 0/1: turn one source issue (PDF) into page images and, for born-digital PDFs, text blocks.

Output, under data/issues/<slug>/<NN>/:
  issue.json             issue metadata (source, hash, page count, born_digital)
  pages/NNN.webp         reader image, REVIEW_HEIGHT px tall
  thumbs/NNN.webp        thumbnail, THUMB_HEIGHT px tall
  blocks/NNN.json        text blocks (only for pages that carry a text layer)

Scanned pages get no blocks here; the OCR step writes blocks/NNN.json in the same schema.

Usage: normalize.py <slug> <source.pdf> [<source.pdf> ...]
Source file names follow "<Title> - NN (MM-YYYY).pdf".
"""

import json
import os
import re
import shutil
import statistics
import subprocess
import sys
from pathlib import Path

import pymupdf
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
import sources  # noqa: E402
from fsutil import read_json, sha256, write_json  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
READER_HEIGHT = 1600
THUMB_HEIGHT = 320
# A page with fewer extractable characters than this is treated as a scan.
MIN_TEXT_CHARS = 200

NAME_RE = re.compile(r" - (\d+)(?: \((?:(\d{2})-)?(\d{4})\))?\.pdf$", re.I)  # "(MM-YYYY)" or "(YYYY)"

# Turkish text set in fonts that use the Windows-1252 layout instead of Windows-1254
# comes out with Icelandic letters in their place. None of these occur in Turkish.
MOJIBAKE = str.maketrans({"ý": "ı", "Ý": "İ", "þ": "ş", "Þ": "Ş", "ð": "ğ", "Ð": "Ğ"})


def parse_name(path: Path) -> tuple[int, str | None]:
    m = NAME_RE.search(path.name)
    if not m:
        raise SystemExit(f"cannot parse issue number from {path.name!r}")
    number, month, year = m.groups()
    return int(number), (f"{year}-{month}" if month else year)



def save_webp(pix: pymupdf.Pixmap, height: int, out: Path) -> None:
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    if img.height != height:
        img = img.resize((round(img.width * height / img.height), height), Image.LANCZOS)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "WEBP", quality=80, method=6)


def is_shadow(a: dict, b: dict) -> bool:
    return a["text"] == b["text"] and abs(a["bbox"][0] - b["bbox"][0]) <= 4 and abs(a["bbox"][1] - b["bbox"][1]) <= 4


def deshadowed_lines(spans: list[dict]) -> list[str] | None:
    """Rebuild a shadowed title's lines from span positions, or None if it has no shadow.

    QuarkXPress titles in issue 04 are drawn twice (a grey shadow 2 pt off the black text)
    with the first letter as its own larger span, so the PDF's own line structure reads
    "CHECKSUMMER\nHECKSUMMER". Drop the shadow copies, then join spans that share a line.
    """
    kept: list[dict] = []
    for s in sorted(spans, key=lambda s: sum(s["color"].to_bytes(3, "big"))):  # darkest first
        if not any(is_shadow(k, s) for k in kept):
            kept.append(s)
    if len(kept) == len(spans):
        return None

    def overlap(a: dict, b: dict) -> float:
        top, bottom = max(a["bbox"][1], b["bbox"][1]), min(a["bbox"][3], b["bbox"][3])
        return (bottom - top) / min(a["bbox"][3] - a["bbox"][1], b["bbox"][3] - b["bbox"][1])

    lines: list[list[dict]] = []
    for s in sorted(kept, key=lambda s: s["bbox"][1]):
        line = next((line for line in lines if any(overlap(s, o) >= 0.5 for o in line)), None)
        if line is None:
            lines.append([s])
        else:
            line.append(s)
    out = []
    for line in lines:
        line.sort(key=lambda s: s["bbox"][0])
        text = line[0]["text"]
        for prev, s in zip(line, line[1:]):
            if s["bbox"][0] < prev["bbox"][2] - 2:
                # Overlapping pieces of the same word ("CREA" + "REATOR"): skip the repeated part.
                k = max((k for k in range(1, len(s["text"]) + 1) if text.endswith(s["text"][:k])), default=0)
                text += s["text"][k:]
            else:
                # A gap wider than a quarter of the font size is a word space.
                text += (" " if s["bbox"][0] - prev["bbox"][2] > 0.25 * s["size"] else "") + s["text"]
        out.append(text)
    return out


def merged_image_boxes(page: pymupdf.Page) -> list[tuple[float, float, float, float]]:
    """Image bboxes, with images stored as stacked strips (Word's WordArt) joined into one."""
    boxes = sorted((tuple(i["bbox"]) for i in page.get_image_info()), key=lambda b: (b[0], b[1]))
    merged: list[list[float]] = []
    for x0, y0, x1, y1 in boxes:
        m = next((m for m in merged if abs(m[0] - x0) <= 3 and abs(m[2] - x1) <= 3 and -3 <= y0 - m[3] <= 3), None)
        if m:
            m[3] = max(m[3], y1)
        else:
            merged.append([x0, y0, x1, y1])
    return [tuple(m) for m in merged]


def ocr_line(page: pymupdf.Page, clip: pymupdf.Rect) -> str:
    """Text in one region of the page (a WordArt title, say).

    Apple Vision reads stylised display type far better than Tesseract (3 of 3 WordArt
    titles in C64 Türkiye 03 vs 0 of 3), so it is used when available (macOS, ocr group).
    """
    pix = page.get_pixmap(dpi=200, clip=clip, alpha=False)
    try:
        from ocrmac import ocrmac
    except ImportError:
        out = subprocess.run(["tesseract", "-", "-", "-l", "tur", "--psm", "7"], input=pix.tobytes("png"),
                             capture_output=True, check=True,
                             env=dict(os.environ, TESSDATA_PREFIX=str(ROOT / "data" / "tessdata"))).stdout
        return " ".join(out.decode().split())
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    lines = ocrmac.OCR(img, recognition_level="accurate", language_preference=["tr-TR"]).recognize()
    return " ".join(text for text, _conf, _box in lines)


def extract_blocks(page: pymupdf.Page) -> list[dict]:
    """Text and image blocks with page-relative bboxes, in content-stream order."""
    w, h = page.rect.width, page.rect.height
    raw = page.get_text("dict", flags=pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES)["blocks"]
    sizes = [s["size"] for b in raw if b["type"] == 0 for l in b["lines"] for s in l["spans"] if s["text"].strip()]
    body = statistics.median(sizes) if sizes else 10.0

    blocks = []
    for b in raw:
        x0, y0, x1, y1 = b["bbox"]
        box = [round(x0 / w, 4), round(y0 / h, 4), round(x1 / w, 4), round(y1 / h, 4)]
        lines, sizes, spans = [], [], []
        for line in b["lines"]:
            visible = [s for s in line["spans"] if s["text"].strip()]
            if visible:
                lines.append("".join(s["text"] for s in line["spans"]).translate(MOJIBAKE))
                sizes.append(max(s["size"] for s in visible))
                spans.extend(visible)
        if not spans:
            continue
        size = max(sizes)
        if (rebuilt := deshadowed_lines(spans)) is not None:
            lines = [line.translate(MOJIBAKE) for line in rebuilt]
            sizes = [size] * len(lines)
        blocks.append({
            "kind": "title" if size >= 1.4 * body else "text",
            "bbox": box,
            "text": "\n".join(lines),
            # Per-line size relative to body text, so a heading line can be told from the
            # paragraph that follows it in the same block.
            "line_rel_sizes": [round(s / body, 2) for s in sizes],
            "font_size": round(size, 1),
            "rel_size": round(size / body, 2),
            "bold": any(s["flags"] & 16 for s in spans),
            "mono": any(s["flags"] & 8 or "courier" in s["font"].lower() for s in spans),
        })
    for x0, y0, x1, y1 in merged_image_boxes(page):
        # Skip slivers and rules; keep anything a reader would call a picture.
        if (x1 - x0) * (y1 - y0) >= 0.005 * w * h:
            fig = {"kind": "figure",
                   "bbox": [round(x0 / w, 4), round(y0 / h, 4), round(x1 / w, 4), round(y1 / h, 4)]}
            # Wide, short pictures are often WordArt titles: keep their text.
            if (x1 - x0) >= 3 * (y1 - y0) and (y1 - y0) <= 0.15 * h:
                fig["ocr_text"] = ocr_line(page, pymupdf.Rect(x0, y0, x1, y1))
            blocks.append(fig)
    for i, blk in enumerate(blocks):
        blk["id"] = f"b{i:03d}"
        blk["order"] = i
    return blocks


# Everything derived from an issue's source; stale once the source changes.
DERIVED = ("blocks", "labels", "labels-text", "figures", "prg", "articles.json", "pages.json")


def normalize(slug: str, src: Path) -> Path:
    number, date = parse_name(src)
    out = ROOT / "data" / "issues" / slug / f"{number:02d}"
    digest = sha256(src)
    old = read_json(out / "issue.json")
    if old and old.get("sha256") != digest:
        for name in DERIVED:
            target = out / name
            shutil.rmtree(target) if target.is_dir() else target.unlink(missing_ok=True)
    doc = pymupdf.open(src)
    text_pages = 0
    for i, page in enumerate(doc, start=1):
        # Render once at reader size; the thumbnail is downscaled from it.
        zoom = READER_HEIGHT / page.rect.height
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        save_webp(pix, READER_HEIGHT, out / "pages" / f"{i:03d}.webp")
        save_webp(pix, THUMB_HEIGHT, out / "thumbs" / f"{i:03d}.webp")

        # A page covered by a scanned image is a scan even if it carries someone's OCR
        # layer (Acrobat Paper Capture in 64'ler): those go through our OCR instead.
        if len(page.get_text().strip()) >= MIN_TEXT_CHARS and not sources.is_scan(page):
            text_pages += 1
            record = {"issue": f"{slug}/{number:02d}", "page": i, "source": "pdf-text",
                      "blocks": extract_blocks(page)}
            write_json(out / "blocks" / f"{i:03d}.json", record, indent=1)

    meta = {
        "slug": slug,
        "number": number,
        "date": date,
        "source": str(src),
        "sha256": digest,
        "pages": doc.page_count,
        "text_pages": text_pages,
        "born_digital": text_pages >= 0.8 * doc.page_count,
    }
    write_json(out / "issue.json", meta, indent=1)
    return out


if __name__ == "__main__":
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    for arg in sys.argv[2:]:
        out = normalize(sys.argv[1], Path(arg))
        meta = json.loads((out / "issue.json").read_text())
        print(f"{out.relative_to(ROOT)}: {meta['pages']} pages, {meta['text_pages']} with text")

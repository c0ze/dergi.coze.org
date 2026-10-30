"""Read the display type (titles) of scanned pages again with Apple Vision.

Tesseract reads body text well but stylised display type badly ("THE PATRICIAN" comes out as
"T FE CIAN", "M.I.D.I." not at all), and a title it cannot read cannot be matched to the
contents page. Vision reads each page once; its tall lines (DISPLAY × the page's median line
height) become the title blocks: a short Tesseract block under such a line takes Vision's text
and size, and a title Tesseract missed is added as a new block. Body lines Tesseract dropped at the end
of a column are appended to their block (tail_lines); other body text is not touched.

Pages whose blocks change are re-labelled by classify.py (labels record the blocks' hash).
Only for scanned pages read by Tesseract; born-digital text and pages ocr.py read with Vision
are left alone. Each step is done once per page (the blocks file records `display` and `tails`),
so re-running is free. --tails-only skips the titles: the blocks keep their ids and nothing is
removed, so an article segmentation made on them (segments.json) stays valid.

Usage: display.py <slug>/<NN> [...] [--tails-only]
"""

import json
import statistics
import sys
from pathlib import Path

import pymupdf
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
from fsutil import read_json, write_json  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
VERSION = 1
DISPLAY = 1.8   # a line this much taller than the page's median line is display type
DPI = 110
MIN_CONF = 0.3


def vision_lines(page: pymupdf.Page) -> list[tuple[str, float, list[float]]]:
    """(text, confidence, [x0, y0, x1, y1] top-left, page = 1) for each line Vision reads."""
    from ocrmac import ocrmac
    pix = page.get_pixmap(dpi=DPI, alpha=False)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    out = []
    for text, conf, (x, y, w, h) in ocrmac.OCR(img, recognition_level="accurate",
                                                language_preference=["tr-TR"]).recognize():
        out.append((text.strip(), conf, [x, 1 - y - h, x + w, 1 - y]))  # Vision's origin is bottom-left
    return out


def inside(box: list[float], block: list[float], share: float = 0.5) -> bool:
    """At least `share` of `box` lies within `block`."""
    w = min(box[2], block[2]) - max(box[0], block[0])
    h = min(box[3], block[3]) - max(box[1], block[1])
    area = (box[2] - box[0]) * (box[3] - box[1])
    return w > 0 and h > 0 and w * h >= share * area


def tail_lines(blocks: list[dict], lines: list[tuple[str, float, list[float]]], median: float) -> bool:
    """Tesseract can stop a few lines short of a column's end, most often in narrow columns
    beside a picture (64'ler 29 p8: "ra at-" and then nothing, where the page goes on
    "laya- / rak / karşı-"). A body-size line Vision reads that no block covers, right under
    a text block's last line in the same column, is appended to that block."""
    texts = [b for b in blocks if b["kind"] == "text"]
    changed = False
    for text, conf, box in sorted(lines, key=lambda l: l[2][1]):
        x0, y0, x1, y1 = box
        h = y1 - y0
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        if (conf < MIN_CONF or not 0.6 <= h / median <= 1.6 or not any(ch.isalnum() for ch in text)
                or any(b["bbox"][0] - .005 <= cx <= b["bbox"][2] + .005
                       and b["bbox"][1] - .005 <= cy <= b["bbox"][3] + .005 for b in blocks)):
            continue
        best = None
        for b in texts:
            bx0, by0, bx1, by1 = b["bbox"]
            line = (by1 - by0) / (b["text"].count("\n") + 1)
            gap = y0 - by1
            if (min(x1, bx1) - max(x0, bx0) > 0.6 * (x1 - x0) and -0.3 * h <= gap <= 1.2 * h
                    and 0.6 <= h / line <= 1.6 and (best is None or gap < best[0])):
                best = (gap, b)
        if best:
            b = best[1]
            b["text"] += "\n" + text
            b["line_rel_sizes"] = b.get("line_rel_sizes", []) + [round(h / median, 2)]
            b["bbox"] = [min(b["bbox"][0], x0), b["bbox"][1], max(b["bbox"][2], x1), y1]
            b["tail_lines"] = b.get("tail_lines", 0) + 1
            changed = True
    return changed


def upgrade(issue: str, page_no: int, page: pymupdf.Page, tails_only: bool = False) -> bool:
    path = ROOT / "data" / "issues" / issue / "blocks" / f"{page_no:03d}.json"
    data = read_json(path)
    if not data or data.get("source") != "ocr:tesseract":
        return False  # born-digital, or read whole by Vision already (ocr.py), titles included
    titles = data.get("display") != VERSION and not tails_only
    tails = data.get("tails") != VERSION
    if not titles and not tails:
        return False
    lines = [l for l in vision_lines(page) if l[0]]
    heights = [l[2][3] - l[2][1] for l in lines]
    changed = False
    if heights:
        median = statistics.median(heights)
        display = [(t, c, b, (b[3] - b[1]) / median) for t, c, b in lines
                   if (b[3] - b[1]) >= DISPLAY * median and c >= MIN_CONF
                   and sum(ch.isalpha() for ch in t) >= 3
                   and all(ord(ch) < 0x370 for ch in t)]  # Latin only: no Cyrillic or Greek misreads
        blocks = data["blocks"]
        for text, _conf, box, rel in display if titles else []:
            hits = [b for b in blocks if b["kind"] != "figure" and inside(box, b["bbox"], 0.3)]
            short = [b for b in hits if b["text"].count("\n") <= 2 and len(b["text"]) <= 80]
            if short:
                # Tesseract's reading of the title: Vision's text, at display size, in its place.
                keep = min(short, key=lambda b: b["order"])
                for b in short:
                    if b is not keep:
                        blocks.remove(b)
                keep.update(kind="title", text=text, rel_size=round(rel, 2), line_rel_sizes=[round(rel, 2)],
                            bbox=[min(keep["bbox"][0], box[0]), min(keep["bbox"][1], box[1]),
                                  max(keep["bbox"][2], box[2]), max(keep["bbox"][3], box[3])],
                            display="vision")
                changed = True
            elif not hits:
                # A title Tesseract did not see at all: a new block, before what lies below it.
                below = [b for b in blocks if b["bbox"][1] >= box[1]]
                order = min((b["order"] for b in below), default=len(blocks)) - 0.5
                blocks.append({"kind": "title", "bbox": [round(v, 4) for v in box], "text": text,
                               "rel_size": round(rel, 2), "line_rel_sizes": [round(rel, 2)],
                               "id": f"v{len(blocks):03d}", "order": order, "display": "vision"})
                changed = True
        if tails:
            changed |= tail_lines(blocks, lines, median)
        if changed:
            blocks.sort(key=lambda b: b["order"])
            for i, b in enumerate(blocks):
                b["order"] = i
    if titles:
        data["display"] = VERSION
    if tails:
        data["tails"] = VERSION
    write_json(path, data, indent=1)
    return changed


def main() -> None:
    tails_only = "--tails-only" in sys.argv
    issues = [a for a in sys.argv[1:] if a != "--tails-only"]
    if not issues:
        raise SystemExit(__doc__)
    try:
        import ocrmac  # noqa: F401
    except ImportError:
        # Apple Vision is macOS only: elsewhere the pages keep Tesseract's titles (the Haiku
        # split reads titles from the page images anyway); run this on a Mac later if wanted.
        print("display.py: no Apple Vision (ocrmac) here, skipped", flush=True)
        return
    for issue in issues:
        meta = json.loads((ROOT / "data" / "issues" / issue / "issue.json").read_text())
        doc = pymupdf.open(ROOT / meta["source"])
        n = sum(upgrade(issue, p, doc[p - 1], tails_only) for p in range(1, meta["pages"] + 1))
        print(f"{issue}: {n} pages with new title text or tail lines", flush=True)


if __name__ == "__main__":
    main()

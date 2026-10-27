"""Read the display type (titles) of scanned pages again with Apple Vision.

Tesseract reads body text well but stylised display type badly ("THE PATRICIAN" comes out as
"T FE CIAN", "M.I.D.I." not at all), and a title it cannot read cannot be matched to the
contents page. Vision reads each page once; its tall lines (DISPLAY × the page's median line
height) become the title blocks: a short Tesseract block under such a line takes Vision's text
and size, and a title Tesseract missed is added as a new block. Body text is not touched.

Pages whose blocks change are re-labelled by classify.py (labels record the blocks' hash).
Only for scanned pages (blocks from Tesseract); born-digital text is left alone. A page is
done once (the blocks file records `display`), so re-running is free.

Usage: display.py <slug>/<NN> [...]
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


def upgrade(issue: str, page_no: int, page: pymupdf.Page) -> bool:
    path = ROOT / "data" / "issues" / issue / "blocks" / f"{page_no:03d}.json"
    data = read_json(path)
    if not data or data.get("display") == VERSION or not str(data.get("source", "")).startswith("ocr:"):
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
        for text, _conf, box, rel in display:
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
        if changed:
            blocks.sort(key=lambda b: b["order"])
            for i, b in enumerate(blocks):
                b["order"] = i
    data["display"] = VERSION
    write_json(path, data, indent=1)
    return changed


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    for issue in sys.argv[1:]:
        meta = json.loads((ROOT / "data" / "issues" / issue / "issue.json").read_text())
        doc = pymupdf.open(ROOT / meta["source"])
        n = sum(upgrade(issue, p, doc[p - 1]) for p in range(1, meta["pages"] + 1))
        print(f"{issue}: {n} pages with new title text", flush=True)


if __name__ == "__main__":
    main()

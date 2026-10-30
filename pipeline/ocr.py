"""Phase 1 for scanned issues: OCR each page with Tesseract and write text blocks.

Tesseract's own page segmentation gives the blocks (one per layout region it finds), each
with its lines in reading order. The output uses the same schema as normalize.py's
born-digital blocks, so classify.py and later stages don't care where text came from.

Collages (hatched backgrounds, boxes drawn by hand) can defeat the layout analysis: it then
finds no text on a page full of it (Laneth 28 p12: a page of typed album reviews, 0 words).
When it reads fewer than SPARSE_MIN_WORDS words, Apple Vision reads the scan line by line and
its lines are joined into column paragraphs; the better reading is kept. (Tesseract's sparse
mode reads such pages too, but as dozens of junk fragments from the hatching.) --redo-empty
reads only such pages again.

Output: data/issues/<slug>/<NN>/blocks/NNN.json  (source "ocr:tesseract", "ocr:vision")
Usage:  ocr.py <slug>/<NN> [...] [--jobs 4] [--force | --redo-empty]
"""

import argparse
import csv
import io
import json
import os
import statistics
import subprocess
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).parent))
import sources  # noqa: E402
from fsutil import read_json, write_json  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TESSDATA = ROOT / "data" / "tessdata"
MIN_CONF = 30  # words below this Tesseract confidence are dropped as noise
SPARSE_MIN_WORDS = 20  # fewer words than this from the layout analysis: read with Vision
VISION = threading.Lock()  # Vision deadlocks in TextRecognition when called from two threads at once


def page_scan(doc: pymupdf.Document, index: int) -> tuple[bytes, str]:
    """The page's scan as the reader sees it (see sources.visible_scan)."""
    return sources.visible_scan(doc[index])


def tesseract_tsv(image: bytes, ext: str, psm: str = "3") -> list[dict]:
    with tempfile.NamedTemporaryFile(suffix=f".{ext}") as f:
        f.write(image)
        f.flush()
        out = subprocess.run(
            ["tesseract", f.name, "-", "-l", "tur", "--psm", psm, "-c", "tessedit_create_tsv=1"],
            capture_output=True, text=True, check=True,
            env=dict(os.environ, TESSDATA_PREFIX=str(TESSDATA), OMP_THREAD_LIMIT="1"),
        ).stdout
    return list(csv.DictReader(io.StringIO(out), delimiter="\t", quoting=csv.QUOTE_NONE))


def blocks_from_tsv(rows: list[dict]) -> list[dict]:
    page = next(r for r in rows if r["level"] == "1")
    pw, ph = int(page["width"]), int(page["height"])
    words = [r for r in rows if r["level"] == "5" and r["text"].strip() and float(r["conf"]) >= MIN_CONF]

    grouped: dict[str, dict[tuple, list[dict]]] = {}
    for w in words:
        grouped.setdefault(w["block_num"], {}).setdefault((w["par_num"], w["line_num"]), []).append(w)

    line_heights = [statistics.median(int(w["height"]) for w in ws) for lines in grouped.values() for ws in lines.values()]
    body = statistics.median(line_heights) if line_heights else 1

    blocks = []
    for block_num, lines in grouped.items():
        ws = [w for line in lines.values() for w in line]
        x0 = min(int(w["left"]) for w in ws)
        y0 = min(int(w["top"]) for w in ws)
        x1 = max(int(w["left"]) + int(w["width"]) for w in ws)
        y1 = max(int(w["top"]) + int(w["height"]) for w in ws)
        text = "\n".join(" ".join(w["text"] for w in line) for line in lines.values())
        line_sizes = [statistics.median(int(w["height"]) for w in line) for line in lines.values()]
        size = max(line_sizes)
        blocks.append({
            "kind": "title" if size >= 1.6 * body else "text",
            "bbox": [round(x0 / pw, 4), round(y0 / ph, 4), round(x1 / pw, 4), round(y1 / ph, 4)],
            "text": text,
            "rel_size": round(size / body, 2),
            # Per line, so a heading line can be told from the paragraph after it.
            "line_rel_sizes": [round(x / body, 2) for x in line_sizes],
            "ocr_conf": round(statistics.mean(float(w["conf"]) for w in ws), 1),
        })
    for i, blk in enumerate(blocks):
        blk["id"] = f"b{i:03d}"
        blk["order"] = i
    return blocks


def word_count(blocks: list[dict]) -> int:
    return sum(len(b["text"].split()) for b in blocks)


def vision_fragments(image: bytes) -> list[dict]:
    """One fragment per line Apple Vision reads on the scan (at its own resolution).
    None off macOS (no ocrmac): the page keeps Tesseract's reading."""
    try:
        from ocrmac import ocrmac
    except ImportError:
        return []
    from PIL import Image
    with VISION:
        lines = ocrmac.OCR(Image.open(io.BytesIO(image)).convert("RGB"), recognition_level="accurate",
                           language_preference=["tr-TR"]).recognize()
    lines = [(t.strip(), c, [x, 1 - y - h, x + w, 1 - y]) for t, c, (x, y, w, h) in lines if t.strip()]
    if not lines:
        return []
    body = statistics.median(bb[3] - bb[1] for _, _, bb in lines)
    return [{"bbox": [round(v, 4) for v in bb], "text": t, "rel_size": round((bb[3] - bb[1]) / body, 2),
             "line_rel_sizes": [round((bb[3] - bb[1]) / body, 2)], "ocr_conf": round(100 * c, 1)}
            for t, c, bb in lines]


def join_fragments(frags: list[dict]) -> list[dict]:
    """Vision reads line by line: join a line to the paragraph above it in the same column
    (overlapping horizontally, under 1.5 lines below it, about the same size)."""
    groups: list[list[dict]] = []
    for f in sorted(frags, key=lambda b: (b["bbox"][1], b["bbox"][0])):
        x0, y0, x1, y1 = f["bbox"]
        for g in groups:
            gx0, _, gx1, gy1 = g[-1]["bbox"]
            line = g[-1]["bbox"][3] - g[-1]["bbox"][1]
            overlap = min(x1, gx1) - max(x0, gx0)
            if (overlap > 0.5 * min(x1 - x0, gx1 - gx0) and -0.3 * line <= y0 - gy1 <= 1.5 * line
                    and 0.6 <= f["rel_size"] / g[-1]["rel_size"] <= 1.6):
                g.append(f)
                break
        else:
            groups.append([f])
    groups.sort(key=lambda g: (round(g[0]["bbox"][0], 1), g[0]["bbox"][1]))  # columns left to right
    blocks = []
    for i, g in enumerate(groups):
        sizes = [s for f in g for s in f["line_rel_sizes"]]
        blocks.append({
            "kind": "title" if max(sizes) >= 1.6 else "text",
            "bbox": [min(f["bbox"][0] for f in g), g[0]["bbox"][1], max(f["bbox"][2] for f in g), g[-1]["bbox"][3]],
            "text": "\n".join(f["text"] for f in g),
            "rel_size": max(f["rel_size"] for f in g),
            "line_rel_sizes": sizes,
            "ocr_conf": round(statistics.mean(f["ocr_conf"] for f in g), 1),
            "id": f"b{i:03d}", "order": i,
        })
    return blocks


def read_page(image: bytes, ext: str) -> tuple[list[dict], str]:
    blocks = blocks_from_tsv(tesseract_tsv(image, ext))
    if word_count(blocks) < SPARSE_MIN_WORDS:
        vision = join_fragments(vision_fragments(image))
        if word_count(vision) > max(word_count(blocks), SPARSE_MIN_WORDS):
            return vision, "ocr:vision"
    return blocks, "ocr:tesseract"


def ocr_issue(issue: str, jobs: int, force: bool, redo_empty: bool = False) -> None:
    issue_dir = ROOT / "data" / "issues" / issue
    meta = json.loads((issue_dir / "issue.json").read_text())
    doc = pymupdf.open(meta["source"])
    out_dir = issue_dir / "blocks"
    out_dir.mkdir(exist_ok=True)

    def wanted(index: int) -> bool:
        old = read_json(out_dir / f"{index + 1:03d}.json")
        if old is None or force:
            return True
        # Only pages the layout analysis read (almost) nothing on, and not read sparse yet.
        return redo_empty and old["source"] == "ocr:tesseract" and word_count(old["blocks"]) < SPARSE_MIN_WORDS

    todo = [i for i in range(doc.page_count) if wanted(i)]
    # Extracting images from one Document isn't thread-safe: do that serially, OCR in parallel.
    scans = {i: page_scan(doc, i) for i in todo}

    def one(index: int) -> None:
        blocks, source = read_page(*scans[index])
        if redo_empty and source == "ocr:tesseract" and not force:
            return  # nothing more to read on this page: leave its file (and later stages) alone
        write_json(out_dir / f"{index + 1:03d}.json",
                   {"issue": issue, "page": index + 1, "source": source, "blocks": blocks}, indent=1)
        print(f"{issue} p{index + 1:03d}: {len(blocks)} blocks ({source})", flush=True)

    with ThreadPoolExecutor(jobs) as pool:
        list(pool.map(one, todo))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("issues", nargs="+")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--redo-empty", action="store_true", help="read pages with almost no text again")
    args = ap.parse_args()
    for issue in args.issues:
        ocr_issue(issue, args.jobs, args.force, args.redo_empty)


if __name__ == "__main__":
    main()

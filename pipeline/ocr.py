"""Phase 1 for scanned issues: OCR each page with Tesseract and write text blocks.

Tesseract's own page segmentation gives the blocks (one per layout region it finds), each
with its lines in reading order. The output uses the same schema as normalize.py's
born-digital blocks, so classify.py and later stages don't care where text came from.

Output: data/issues/<slug>/<NN>/blocks/NNN.json  (source "ocr:tesseract")
Usage:  ocr.py <slug>/<NN> [...] [--jobs 4] [--force]
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
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).parent))
import sources  # noqa: E402
from fsutil import read_json, write_json  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TESSDATA = ROOT / "data" / "tessdata"
MIN_CONF = 30  # words below this Tesseract confidence are dropped as noise


def page_scan(doc: pymupdf.Document, index: int) -> tuple[bytes, str]:
    """The page's scan as the reader sees it (see sources.visible_scan)."""
    return sources.visible_scan(doc[index])


def tesseract_tsv(image: bytes, ext: str) -> list[dict]:
    with tempfile.NamedTemporaryFile(suffix=f".{ext}") as f:
        f.write(image)
        f.flush()
        out = subprocess.run(
            ["tesseract", f.name, "-", "-l", "tur", "--psm", "3", "-c", "tessedit_create_tsv=1"],
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


def ocr_issue(issue: str, jobs: int, force: bool) -> None:
    issue_dir = ROOT / "data" / "issues" / issue
    meta = json.loads((issue_dir / "issue.json").read_text())
    doc = pymupdf.open(meta["source"])
    out_dir = issue_dir / "blocks"
    out_dir.mkdir(exist_ok=True)

    todo = [i for i in range(doc.page_count) if force or read_json(out_dir / f"{i + 1:03d}.json") is None]
    # Extracting images from one Document isn't thread-safe: do that serially, OCR in parallel.
    scans = {i: page_scan(doc, i) for i in todo}

    def one(index: int) -> None:
        blocks = blocks_from_tsv(tesseract_tsv(*scans[index]))
        write_json(out_dir / f"{index + 1:03d}.json",
                   {"issue": issue, "page": index + 1, "source": "ocr:tesseract", "blocks": blocks}, indent=1)
        print(f"{issue} p{index + 1:03d}: {len(blocks)} blocks", flush=True)

    with ThreadPoolExecutor(jobs) as pool:
        list(pool.map(one, todo))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("issues", nargs="+")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    for issue in args.issues:
        ocr_issue(issue, args.jobs, args.force)


if __name__ == "__main__":
    main()

"""Run the whole pipeline over many issues: normalize → OCR → JEV → assemble → correct.

Each stage skips work that is already on disk, so a stopped batch picks up where it left
off. Heavy: run it through ~/.local/bin/heavy-slot with nice.

Usage: batch.py <slug> <source.pdf> [<source.pdf> ...] [--jobs 4] [--stages normalize,ocr,classify,assemble,correct]
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
sys.path.insert(0, str(Path(__file__).parent))
from fsutil import read_json, sha256  # noqa: E402
from normalize import parse_name  # noqa: E402


def run(*args: str) -> None:
    subprocess.run([PY, *args], cwd=ROOT, check=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("slug")
    ap.add_argument("sources", nargs="+", type=Path)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--stages", default="normalize,ocr,classify,assemble,correct",
                    help="comma-separated subset, e.g. normalize,ocr while JEV is unavailable")
    args = ap.parse_args()
    stages = set(args.stages.split(","))

    for src in sorted(args.sources):
        # Steps run from the repo root: pass sources relative to it, wherever batch.py ran.
        src = src.resolve().relative_to(ROOT)
        number, _ = parse_name(src)
        issue = f"{args.slug}/{number:02d}"
        d = ROOT / "data" / "issues" / issue
        t0 = time.time()
        meta = read_json(d / "issue.json")
        # A missing issue, or a different source file than last time (a better scan was
        # chosen): normalize again, which also drops everything derived from the old one.
        if "normalize" in stages and (meta is None or meta.get("sha256") != sha256(ROOT / src)):
            run("pipeline/normalize.py", args.slug, str(src))
            meta = read_json(d / "issue.json")
        if "ocr" in stages:
            # Scans, and any page of a born-digital issue that had no text of its own.
            run("pipeline/ocr.py", issue, "--jobs", str(args.jobs))
        if "classify" in stages:
            run("pipeline/classify.py", issue, "--jobs", str(args.jobs))
        if "assemble" in stages:
            run("pipeline/assemble.py", issue)
        if "correct" in stages:
            run("pipeline/correct.py", issue)  # cached by text: re-running costs nothing
        print(f"== {issue}: {meta['pages']} pages in {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()

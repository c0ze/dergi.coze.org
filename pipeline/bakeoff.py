"""OCR bake-off: run each engine on the same scanned pages and compare.

There is no hand-corrected ground truth yet, so the score is a proxy: the share of words
found in a large Turkish word-form list, plus how often the Turkish letters come out.
The HTML report puts the page image next to each engine's text for reading by eye.

Usage:
  bakeoff.py extract            pull the test pages out of the source PDFs
  bakeoff.py run <engine>...    tesseract | vision | surya
  bakeoff.py report             write data/bakeoff/report.html
"""

import html
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BAKE = ROOT / "data" / "bakeoff"
PAGES = BAKE / "pages"
SOURCES = ROOT / "data" / "sources" / "commodore" / "Commodore"
# (issue, page): low-res scans of 42 and high-res scans of 75, mixing editorials,
# articles, listings, ad pages and game reviews.
TEST_PAGES = [(42, 5), (42, 24), (42, 42), (75, 5), (75, 6), (75, 15), (75, 20), (75, 44)]
WORDLIST_URL = "https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018/tr/tr_full.txt"
WORD_RE = re.compile(r"[A-Za-zÇĞİÖŞÜçğıöşüÂâÎîÛû]{3,}")


def tr_lower(s: str) -> str:
    return s.replace("I", "ı").replace("İ", "i").lower()


def extract() -> None:
    import pymupdf
    PAGES.mkdir(parents=True, exist_ok=True)
    for issue, page in TEST_PAGES:
        src = next(SOURCES.glob(f"Commodore - {issue:02d} *.pdf"))
        doc = pymupdf.open(src)
        # Each page is one full-page scan; save it as stored, without re-encoding.
        xref = doc[page - 1].get_images()[0][0]
        img = doc.extract_image(xref)
        out = PAGES / f"c{issue}-p{page:03d}.{img['ext']}"
        out.write_bytes(img["image"])
        print(out.name, img["width"], "x", img["height"])


def page_files() -> list[Path]:
    return sorted(p for p in PAGES.iterdir() if p.suffix in (".jpg", ".jpeg", ".png"))


def run_tesseract(img: Path) -> str:
    env = dict(os.environ, TESSDATA_PREFIX=str(ROOT / "data" / "tessdata"))
    return subprocess.run(["tesseract", str(img), "-", "-l", "tur", "--psm", "3"],
                          capture_output=True, text=True, env=env, check=True).stdout


def run_vision(img: Path) -> str:
    from ocrmac import ocrmac
    lines = ocrmac.OCR(str(img), recognition_level="accurate",
                       language_preference=["tr-TR"]).recognize()
    # Vision returns lines with bottom-left origin boxes; sort top-to-bottom, left-to-right.
    lines.sort(key=lambda r: (round(-r[2][1] - r[2][3], 2), r[2][0]))
    return "\n".join(text for text, _conf, _box in lines)


def run_surya(images: list[Path]) -> dict[str, tuple[str, float]]:
    """Surya runs as one batch through its CLI; per-page time is the batch time split evenly."""
    out_dir = BAKE / "surya_raw"
    t0 = time.time()
    subprocess.run([str(ROOT / ".venv" / "bin" / "surya_ocr"), str(PAGES), "--output_dir", str(out_dir)],
                   check=True)
    per_page = (time.time() - t0) / len(images)
    results = json.loads(next(out_dir.rglob("results.json")).read_text())
    return {name: (surya_text(pages[0]), per_page) for name, pages in results.items()}


def surya_text(page: dict) -> str:
    """Surya 2 returns layout blocks with HTML; flatten them in reading order."""
    blocks = sorted((b for b in page["blocks"] if not b.get("skipped")), key=lambda b: b["reading_order"])
    return "\n\n".join(html.unescape(re.sub(r"<[^>]+>", "", b["html"])).strip() for b in blocks if b["html"])


def run(engines: list[str]) -> None:
    images = page_files()
    for engine in engines:
        out_dir = BAKE / engine
        out_dir.mkdir(parents=True, exist_ok=True)
        if engine == "surya":
            for stem, (text, secs) in run_surya(images).items():
                (out_dir / f"{stem}.txt").write_text(text)
                (out_dir / f"{stem}.time").write_text(f"{secs:.2f}")
            continue
        fn = {"tesseract": run_tesseract, "vision": run_vision}[engine]
        for img in images:
            t0 = time.time()
            text = fn(img)
            (out_dir / f"{img.stem}.txt").write_text(text)
            (out_dir / f"{img.stem}.time").write_text(f"{time.time() - t0:.2f}")
            print(engine, img.stem, f"{time.time() - t0:.1f}s")


def load_wordlist() -> set[str]:
    path = BAKE / "tr_full.txt"
    if not path.exists():
        urllib.request.urlretrieve(WORDLIST_URL, path)
    return {tr_lower(line.split(" ", 1)[0]) for line in path.read_text().splitlines() if line}


def score(text: str, words: set[str]) -> dict:
    tokens = WORD_RE.findall(text)
    known = sum(tr_lower(t) in words for t in tokens)
    return {
        "words": len(tokens),
        "known": round(known / len(tokens), 3) if tokens else 0.0,
        "tr_letters": sum(text.count(c) for c in "ıİşŞğĞ"),
    }


def report() -> None:
    words = load_wordlist()
    engines = sorted(d.name for d in BAKE.iterdir()
                     if d.is_dir() and d.name not in ("pages", "surya_raw"))
    rows, totals = [], {e: {"words": 0, "known": 0.0, "secs": 0.0} for e in engines}
    for img in page_files():
        cells = []
        for e in engines:
            txt = BAKE / e / f"{img.stem}.txt"
            if not txt.exists():
                cells.append("<td>—</td>")
                continue
            text = txt.read_text()
            s = score(text, words)
            secs = float((BAKE / e / f"{img.stem}.time").read_text())
            t = totals[e]
            t["words"] += s["words"]
            t["known"] += s["known"] * s["words"]
            t["secs"] += secs
            cells.append(f"<td><b>{s['known']:.0%}</b> known of {s['words']} words · "
                         f"{s['tr_letters']} ı/ş/ğ · {secs:.1f}s<pre>{html.escape(text)}</pre></td>")
        rel = os.path.relpath(img, BAKE)
        rows.append(f"<tr><td><a href='{rel}'><img src='{rel}' width=360></a><br>{img.stem}</td>{''.join(cells)}</tr>")
    head = "".join(f"<th>{e}</th>" for e in engines)
    summary = "".join(
        f"<li><b>{e}</b>: {t['known'] / max(t['words'], 1):.1%} known words, {t['words']} words, "
        f"{t['secs'] / max(len(page_files()), 1):.1f}s per page</li>" for e, t in totals.items())
    (BAKE / "report.html").write_text(f"""<!doctype html><meta charset=utf-8><title>OCR bake-off</title>
<style>body{{font:14px system-ui;margin:16px}} td{{vertical-align:top;border-top:1px solid #ccc;padding:6px}}
pre{{white-space:pre-wrap;max-height:600px;overflow:auto;font-size:12px;background:#f6f6f6;padding:6px}}</style>
<h1>OCR bake-off</h1><ul>{summary}</ul>
<table><tr><th>page</th>{head}</tr>{''.join(rows)}</table>""")
    for e, t in totals.items():
        print(f"{e:10s} known {t['known'] / max(t['words'], 1):.1%}  words {t['words']}  "
              f"{t['secs'] / max(len(page_files()), 1):.1f}s/page")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "extract":
        extract()
    elif cmd == "run":
        run(sys.argv[2:])
    elif cmd == "report":
        report()
    else:
        raise SystemExit(__doc__)

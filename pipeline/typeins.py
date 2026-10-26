"""Type-in programs → runnable .prg files.

Supported listing formats:
- CHECKSUMMER (C64 Türkiye, from issue 4): "0801: 0B 08 D3 07 9E 32 30 36 - 4391" — address,
  8 bytes, and a 16-bit checksum: the sum of byte[i] × (17, 19, 23, 29, 31, 37, 41, 59)
  (from CHECKSUMMER's own source in issue 4, label `multiple`). Every line is verified;
  a program is published only when all of its lines pass.

A program is a run of lines with consecutive addresses; its name comes from the nearest
"PROGRAM ADI :" line before it. The .prg is the 2-byte load address plus the bytes.

Output: data/issues/<slug>/<NN>/prg/<name>.prg and prg.json (name, load address, size,
article, pages, verified lines, failed lines)
Usage:  typeins.py <slug>/<NN> [...]
"""

import json
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from fsutil import write_json  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
MULTIPLES = (17, 19, 23, 29, 31, 37, 41, 59)
HEX_LINE = re.compile(r"^\s*([0-9A-Fa-f]{4})\s*:\s*((?:[0-9A-Fa-f]{2}\s+){1,8}?)\s*[-–—]\s*([0-9A-Fa-f]{4})\s*$")
NAME_LINE = re.compile(r"PROGRAM\s+ADI\s*:\s*(.+)", re.I)


def checksum(data: bytes) -> int:
    return sum(b * m for b, m in zip(data, MULTIPLES)) & 0xFFFF


def safe_name(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.translate(str.maketrans("ıİşŞğĞ", "iIsSgG")))
    text = text.encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()[:40] or "program"


def scan_issue(articles: list[dict]) -> list[dict]:
    """CHECKSUMMER programs in an issue, in order. A listing that runs on past an article
    boundary (consecutive addresses) stays one program. An address gap inside one named
    listing (no new "PROGRAM ADI" in between) means a missing or unreadable line: both
    pieces are marked failed instead of being published as two "verified" fragments."""
    programs: list[dict] = []
    name = None
    named = False  # a "PROGRAM ADI" line came after the previous hex line
    current = None
    for article, item in ((a, x) for a in articles for x in a["body"]):
        for line in item.get("text", "").split("\n"):
            if m := NAME_LINE.search(line):
                # "KARSET5   0801 0860": the address range is printed with the listing anyway.
                name = re.sub(r"[\s(]*\$?[0-9A-Fa-f]{4}[\s-]+\$?[0-9A-Fa-f]{4}\)?\s*$", "", m.group(1)).strip()
                named = True
                continue
            m = HEX_LINE.match(line)
            if not m:
                continue
            addr = int(m.group(1), 16)
            data = bytes(int(b, 16) for b in m.group(2).split())
            ok = checksum(data) == int(m.group(3), 16)
            if current is None or addr != current["next"]:
                previous = current
                current = {"name": name or article["title"], "load": addr, "next": addr, "data": bytearray(),
                           "lines": 0, "failed": [], "pages": set(), "article": article}
                if previous is not None and not named:
                    gap = f"gap {previous['next']:04X}-{addr:04X}"
                    previous["failed"].append(gap)
                    current["failed"].append(gap)
                programs.append(current)
            named = False
            current["data"] += data
            current["next"] = addr + len(data)
            current["lines"] += 1
            current["pages"].add(item.get("page"))
            if not ok:
                current["failed"].append(f"{addr:04X}")
    return programs


def extract(issue: str) -> list[dict]:
    d = ROOT / "data" / "issues" / issue
    articles = json.loads((d / "articles.json").read_text())
    out_dir = d / "prg"
    found = []
    used = set()
    for p in scan_issue(articles):
        a = p["article"]
        if p["lines"] < 2:
            continue
        base = safe_name(p["name"])
        fname, n = f"{base}.prg", 2
        while fname in used:
            fname, n = f"{base}-{n}.prg", n + 1
        used.add(fname)
        entry = {"name": p["name"], "file": fname, "article": a["slug"], "article_title": a["title"],
                 "pages": sorted(x for x in p["pages"] if x), "load": f"${p['load']:04X}",
                 "end": f"${p['next']:04X}", "bytes": len(p["data"]), "lines": p["lines"],
                 "failed": p["failed"], "verified": not p["failed"], "format": "checksummer"}
        if entry["verified"]:
            out_dir.mkdir(exist_ok=True)
            (out_dir / fname).write_bytes(p["load"].to_bytes(2, "little") + bytes(p["data"]))
        found.append(entry)
    if found:
        out_dir.mkdir(exist_ok=True)
        write_json(out_dir / "prg.json", found, indent=1)
    return found


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    for issue in sys.argv[1:]:
        for e in extract(issue):
            status = "ok" if e["verified"] else f"{len(e['failed'])} bad lines: {' '.join(e['failed'][:6])}"
            print(f"{issue}  {e['file']:32s} {e['load']}-{e['end']} {e['bytes']:5d} B  {e['lines']:3d} lines  {status}")

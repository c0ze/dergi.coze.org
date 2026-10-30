"""Check a segments.json against its blocks.txt: seg_check.py <issue folder>"""
import json, re, sys
from pathlib import Path
d = Path(sys.argv[1])
ids, page = set(), None
for line in (d / "blocks.txt").read_text().splitlines():
    if m := re.match(r"== page (\d+)", line): page = int(m.group(1))
    elif m := re.match(r"([bv]\d+) \[", line): ids.add((page, m.group(1)))
pages_with = {p for p, _ in ids}
ids |= {(p, "page") for p in range(1, (page or 0) + 1) if p not in pages_with}  # pages without text
try:
    seg = json.loads((d / "segments.json").read_text())
except Exception as e:
    sys.exit(f"segments.json is not valid JSON: {e}")
errors, seen = [], {}
arts = seg.get("articles")
if not isinstance(arts, list) or not arts: sys.exit("need a non-empty \"articles\" list")
last = (0, -1)
for n, a in enumerate(arts):
    for k in ("title", "start"):
        if k not in a: errors.append(f"article {n}: missing {k}")
    marks = [("start", a.get("start", {}))] + [("resume", r) for r in a.get("resumes", [])]
    for what, m in marks:
        key = (m.get("page"), m.get("block"))
        if key not in ids: errors.append(f"article {n} {a.get('title')!r}: {what} {key} is not a text block on that page ('page' only for a page without text blocks)")
        if key in seen: errors.append(f"article {n}: {what} {key} already used by article {seen[key]}")
        seen[key] = n
    s = a.get("start", {})
    pos = (s.get("page", 0), int(str(s.get("block", "b-1"))[1:] or -1) if s.get("block") != "page" else -1)
print("\n".join(errors) if errors else f"OK: {len(arts)} articles, {sum(len(a.get('resumes', [])) for a in arts)} resumes")
sys.exit(1 if errors else 0)

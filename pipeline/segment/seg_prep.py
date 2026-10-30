"""Inputs for the segmentation experiment: page images and a block listing per issue."""
import json, sys
from pathlib import Path
import pymupdf
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pipeline"))
from assemble import Labels
OUT = Path(sys.argv[1])
for issue in sys.argv[2:]:
    d = ROOT / "data/issues" / issue
    meta = json.loads((d / "issue.json").read_text())
    out = OUT / issue.replace("/", "-")
    (out / "pages").mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open(ROOT / meta["source"])
    lines = [f"# {issue}: {meta['pages']} pages. Blocks in reading order per page.",
             "# id [kind, size × body text, JEV heading guess, JEV role] text (first 160 chars, / = line break)"]
    for p in range(1, meta["pages"] + 1):
        pg = doc[p - 1]
        if not (out / "pages" / f"p{p:03d}.jpg").exists():
            pg.get_pixmap(matrix=pymupdf.Matrix(1000 / pg.rect.width, 1000 / pg.rect.width)).save(out / "pages" / f"p{p:03d}.jpg", jpg_quality=55)
        L = Labels(d, p)
        lines.append(f"\n== page {p} (JEV page kind: {L.page_kind()})  image: pages/p{p:03d}.jpg")
        bf = d / "blocks" / f"{p:03d}.json"
        for b in json.loads(bf.read_text())["blocks"] if bf.exists() else []:
            if b["kind"] == "figure":
                lines.append(f"{b['id']} [figure]"); continue
            h = L.heading(b["id"]); r = L.role(b["id"])
            tags = [b["kind"], f"{b.get('rel_size') or 1:.1f}x"]
            if h and h[0] != "not_heading": tags.append(f"{h[0]} {h[1]:.2f}")
            tags.append(r[0] + ("?" if r[1] else ""))
            text = " / ".join(b["text"].split("\n"))
            lines.append(f"{b['id']} [{', '.join(tags)}] {text[:160]}")
    (out / "blocks.txt").write_text("\n".join(lines) + "\n")
    print(out, meta["pages"], "pages")

"""Assemble an issue with the Haiku segmentation into its experiment folder and compare."""
import contextlib, io, json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pipeline"))
import assemble
EXP = Path(sys.argv[1])

def stats(arts):
    first = lambda a: next((x for x in a["body"] if x["type"] == "p"), {"text": ""})
    return (len(arts), sum(len(a["pages"]) >= 12 for a in arts),
            sum(first(a).get("raw", first(a)["text"])[:1].islower() for a in arts),
            sum(len(x.get("raw", x.get("text", ""))) for a in arts for x in a["body"] if x["type"] in ("p", "pre")))

for issue in sys.argv[2:]:
    d = EXP / issue.replace("/", "-")
    out = d / "out"; out.mkdir(exist_ok=True)
    with contextlib.redirect_stdout(io.StringIO()):
        assemble.assemble(issue, segments=d / "segments.json", out_dir=out)
    old = json.loads((ROOT / "data/issues" / issue / "articles.json").read_text())
    new = json.loads((out / "articles.json").read_text())
    o, n = stats(old), stats(new)
    print(f"== {issue}: articles {o[0]} → {n[0]}, spans of 12+ pages {o[1]} → {n[1]}, "
          f"starting mid-sentence {o[2]} → {n[2]}, body chars {o[3]} → {n[3]}")
    rows = max(len(old), len(new))
    for i in range(rows):
        l = f"{old[i]['pages'][0]:>3}-{old[i]['pages'][-1]:<3} {old[i]['title'][:34]}" if i < len(old) else ""
        r = (f"{new[i]['pages'][0]:>3}-{new[i]['pages'][-1]:<3} {new[i]['title'][:40]}"
             f"{'  · ' + new[i]['author'] if new[i]['author'] else ''}") if i < len(new) else ""
        print(f"   {l:42s} | {r}")

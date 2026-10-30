"""Old body 6-word runs not in the new articles: which are really lost (no 3-word piece of them anywhere in the new text, compacted)."""
import json, re, sys
from pathlib import Path
EXP = Path(sys.argv[1]); ROOT = Path(__file__).resolve().parents[2]
norm = lambda s: re.sub(r"\s+", " ", s)
comp = lambda s: re.sub(r"[\W_]+", "", s.casefold())
for issue in sys.argv[2:]:
    old = json.loads((EXP / issue.replace("/", "-") / "old-articles.json").read_text())
    new = json.loads((ROOT / "data/issues" / issue / "articles.json").read_text())
    allnew = norm(" ".join(x.get("text", "") + " " + x.get("raw", "") for a in new for x in a["body"]))  # raw: before correct.py
    cnew = comp(allnew + " " + " ".join(a["title"] for a in new))
    lost = []
    for a in old:
        for x in a["body"]:
            if x["type"] not in ("p", "pre"): continue
            for w in re.findall(r"\S+(?:\s+\S+){5}", norm(x.get("raw", x["text"]))):
                if w in allnew: continue
                ws = w.split()
                if not any(comp(" ".join(ws[i:i+3])) in cnew for i in range(4)):
                    lost.append((a["title"][:30], w))
    print(f"{issue}: {len(lost)} really lost")
    for t, w in lost[:6]: print("   ", t, "|", w)

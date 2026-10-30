"""Install Haiku segmentations: back up articles.json, copy segments.json into the issue, assemble,
and check that every 6-word run of the old body text is still somewhere in the issue."""
import contextlib, io, json, re, shutil, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pipeline"))
import assemble
EXP = Path(sys.argv[1])
norm = lambda s: re.sub(r"\s+", " ", s)
for issue in sys.argv[2:]:
    d, live = EXP / issue.replace("/", "-"), ROOT / "data/issues" / issue
    backup = d / "old-articles.json"
    if not backup.exists():
        if not (live / "articles.json").exists():
            # A new issue: the JEV-only assembly is the baseline for the lost-text check.
            (live / "segments.json").unlink(missing_ok=True)
            with contextlib.redirect_stdout(io.StringIO()):
                assemble.assemble(issue)
        shutil.copy2(live / "articles.json", backup)
    shutil.copy2(d / "segments.json", live / "segments.json")
    with contextlib.redirect_stdout(io.StringIO()):
        assemble.assemble(issue)
    old, new = json.loads(backup.read_text()), json.loads((live / "articles.json").read_text())
    allnew = norm(" ".join(x.get("text", "") for a in new for x in a["body"]))
    oldtitles = {norm(a["title"]) for a in old}
    miss = [w for a in old for x in a["body"] if x["type"] in ("p", "pre")
            for w in re.findall(r"\S+(?:\s+\S+){5}", norm(x.get("raw", x["text"]))) if w not in allnew]
    big = sum(len(a["pages"]) >= 12 for a in new)
    print(f"{issue}: {len(old)} → {len(new)} articles, 12+ page spans {sum(len(a['pages']) >= 12 for a in old)} → {big}, "
          f"missing 6-word runs {len(miss)}" + (f"  e.g. {miss[0][:60]!r}" if miss else ""))

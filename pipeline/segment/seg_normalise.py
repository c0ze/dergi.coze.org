"""House rules applied to every segments.json after the Haiku run."""
import json, re, sys
from pathlib import Path
EXP = Path(sys.argv[1])
fold = lambda s: (s or "").casefold().replace("i̇", "i")
for f in sorted(EXP.glob("*/segments.json")):
    s = json.loads(f.read_text()); changed = []
    keep = []
    for a in s["articles"]:
        t = fold(a["title"])
        if t.startswith("değerlendiren"):
            changed.append(f"title {a['title']!r} → ÇÖZÜMLER"); a["title"] = "ÇÖZÜMLER"; a["author"] = None
        if f.parent.name.startswith("matematik-dunyasi") and keep and (
                t.startswith("yazarlara") or t.startswith("sayın okurlarımız")):
            changed.append(f"folded notice {a['title']!r}")
            continue  # its blocks fall to the article before it
        au = a.get("author")
        if au and "matematik dünyası" in fold(au):
            new = au.split("adına", 1)[1].strip() if "adına" in au else None
            changed.append(f"author {au!r} → {new!r}"); a["author"] = new
        if au and fold(au).strip(" .") in ("amiga dünyası", "amiga dergisi", "megamiga", "commodore", "commodore amiga", "64'ler", "c64 türkiye"):
            changed.append(f"author {au!r} → None (magazine name)"); a["author"] = None
        au = a.get("author")
        if au and fold(au).startswith("değerlendiren"):
            changed.append(f"author {au!r} → None"); a["author"] = None
        au = a.get("author")
        if au and re.search(r"çevir(en|i)|\bçev\b", fold(au)):
            # "Herbert Pahli'den çeviren: Güven Savaş KIZILTAN": the translator signs the Turkish text
            new = re.split(r"(?i)çevir(?:en|i)\s*:?|\bçev\.?\s*:?", au)[-1].strip(" ,.:;") or None
            changed.append(f"author {au!r} → {new!r}"); a["author"] = new
        a["title"] = a["title"].replace("Şarkı sögleri", "Şarkı sözleri")
        keep.append(a)
    if changed:
        s["articles"] = keep
        f.write_text(json.dumps(s, ensure_ascii=False, indent=1))
        print(f.parent.name, "; ".join(changed))

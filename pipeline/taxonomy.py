"""Authors, games and topics across all magazines, from the JEV tags (tags.py).

- Authors: in a segmented issue (segments.json) the byline read from the page image; otherwise
  JEV's byline choice (confident), else the "Hazırlayan:" byline from assembly.
  Spellings that differ only in case, Turkish letters, punctuation or a quoted nickname
  ("İsmail "Hades" Şahin" / "İSMAİL ŞAHİN") are one author. Other close matches are written
  to catalog/authors.yaml under `suggested`; entries under `merge` (edited by hand) join
  spellings into one profile and win over everything else.
- Games: headings JEV calls a game name, matched against the known C64 and Amiga titles
  (catalog/games/*.txt) so OCR variants collapse ("TURRIİCAN" → "Turrican"); grouped by the
  article's platform.
- Bands (metal corpus): headings JEV calls a band or musician, grouped by folded name.
- Topics: catalog/topics.yaml (per corpus), where JEV says yes with confidence.

`collect()` returns everything the site needs; run directly to refresh the author
suggestions and print a summary.
"""

import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import yaml
from rapidfuzz import fuzz, process

sys.path.insert(0, str(Path(__file__).parent))
from fsutil import read_json  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
AUTHOR_CONF = 0.6
GAME_P = 0.7
BAND_P = 0.7
TOPIC_P = 0.6
GAME_MATCH = 88
PLATFORM_NAMES = {"c64": "C64", "amiga": "Amiga", "pc": "PC", "other_computer": "Diğer", "console": "Konsol"}
FOLD = str.maketrans("çğıöşüâîûÇĞİÖŞÜÂÎÛ", "cgiosuaiuCGIOSUAIU")


def fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.translate(FOLD)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def slugify(text: str) -> str:
    return fold(text).replace(" ", "-")[:60] or "x"


ROLE_PREFIX = re.compile(r"^\s*(?:çev\.?|çeviren|hazırlayan|yazan|derleyen|editör)\s*:?\s*", re.I)


def tr_title(name: str) -> str:
    """Each all-caps word in Turkish title case: 'Samet KUMAŞ' → 'Samet Kumaş',
    'İSMAİL ŞAHİN' → 'İsmail Şahin'; initials ('A.') and mixed-case words stay as written."""
    def word(w: str) -> str:
        if len(w.rstrip(".")) <= 1 or w != w.upper():
            return w
        return w[:1] + w[1:].replace("I", "ı").replace("İ", "i").lower()
    return " ".join(word(w) for w in ROLE_PREFIX.sub("", name).split())


def author_key(name: str) -> str:
    """Same person: same letters once case, Turkish letters, punctuation and a quoted
    nickname are set aside."""
    name = re.sub(r"[\"“”'‘’].*?[\"“”'‘’]", " ", ROLE_PREFIX.sub("", name))
    # Spaces too: OCR splits names ("CEM GENÇ ER", "ERDEM TAY LAN").
    return fold(name).replace(" ", "")


def game_key(name: str) -> str:
    name = fold(name)
    name = re.sub(r"\b(demo|disk \d+|side \d+|version [\d.]+|v\d[\d.]*|oyun degerlendirmesi)\b", " ", name)
    roman = {" ii": " 2", " iii": " 3", " iv": " 4"}
    for r, n in roman.items():
        name = re.sub(rf"{r}\b", n, name)
    return " ".join(name.split())


def load_games() -> dict[str, dict[str, str]]:
    """platform → {game_key: display name} from catalog/games/*.txt (shortest name per key)."""
    out: dict[str, dict[str, str]] = {}
    for f in (ROOT / "catalog" / "games").glob("*.txt"):
        names: dict[str, str] = {}
        for line in f.read_text().splitlines():
            k = game_key(line)
            if k and (k not in names or len(line) < len(names[k])):
                names[k] = line.strip()
        out[f.stem] = names
    return out


def canonical_game(name: str, platform: str, games: dict, sure: bool = False) -> tuple[str, str] | None:
    """(display name, platform): the known title it matches (whole name close, or all of its
    words in a known title: "VENOM STRIKE" → "Venom Strikes Back"); else, when JEV is very
    sure it is a game, the name itself, tidied. None otherwise."""
    k = game_key(name)
    if len(k) < 3:
        return None
    order = [platform] + [p for p in games if p != platform] if platform in games else list(games)
    plat = lambda p: platform if platform in PLATFORM_NAMES else p  # noqa: E731
    for scorer, cutoff in ((fuzz.ratio, GAME_MATCH), (fuzz.token_set_ratio, 85)):
        for p in order:
            hit = process.extractOne(k, list(games[p]), scorer=scorer, score_cutoff=cutoff)
            # The word-set match must go the right way: every word of the name starts a word
            # of the title ("venom strike" ⊂ "venom strikes back"), not the reverse ("strike").
            if hit and (scorer is fuzz.ratio or (len(k) >= 0.6 * len(hit[0]) and all(
                    any(t.startswith(w) for t in hit[0].split()) for w in k.split()))):
                return games[p][hit[0]], plat(p)
    if sure and platform in PLATFORM_NAMES and len(k.split()) <= 5:
        # Game names are mostly English: plain capitalisation, not Turkish (I → ı).
        return " ".join(w[:1].upper() + w[1:].lower().replace("ı", "i") for w in name.replace("İ", "I").split()), platform
    return None


def collect() -> dict:
    titles = {t["slug"]: t for t in yaml.safe_load((ROOT / "catalog" / "titles.yaml").read_text())}
    topics = yaml.safe_load((ROOT / "catalog" / "topics.yaml").read_text())
    games = load_games()
    merges_file = read_yaml(ROOT / "catalog" / "authors.yaml")
    alias_to: dict[str, str] = {}
    for canonical, aliases in (merges_file.get("merge") or {}).items():
        for a in [canonical, *aliases]:
            alias_to[author_key(a)] = canonical

    articles = []  # one entry per article with its resolved tags
    for tags_file in sorted((ROOT / "data" / "issues").glob("*/*/tags.json")):
        issue_dir = tags_file.parent
        slug, nn = issue_dir.parent.name, issue_dir.name
        meta = read_json(issue_dir / "issue.json")
        tags = read_json(tags_file) or {}
        for a in read_json(issue_dir / "articles.json") or []:
            t = tags.get(a["slug"])
            if not t:
                continue
            author = None
            if a.get("author") and (issue_dir / "segments.json").exists():
                author = a["author"]  # read from the page image with the segmentation
            elif t.get("author") and t["author"]["confidence"] >= AUTHOR_CONF:
                author = t["author"]["name"]
            elif a.get("author"):
                author = a["author"]
            corpus = titles[slug].get("corpus", "retro")
            platform = (t.get("platform") or {}).get("choice")
            found = []
            game_article = max(t["topics"].get("oyun-inceleme", 0), t["topics"].get("oyun-cozum", 0)) >= TOPIC_P
            for i, g in enumerate(t["games"]):
                # The title of a game review or tips article that matches a known game counts
                # even when JEV was less sure it is a game name ("SHANGHAI").
                sure = g["p"] >= GAME_P or (i == 0 and game_article and g["p"] >= 0.3)
                if sure and (c := canonical_game(g["name"], platform, games, sure=g["p"] >= 0.9)):
                    found.append(c)
            bands = list(dict.fromkeys(band_name(b["name"]) for b in t.get("bands", []) if b["p"] >= BAND_P))
            articles.append({
                "url": f"/{slug}/{nn}/{a['slug']}/", "title": a["title"], "magazine": titles[slug]["name"],
                "corpus": corpus, "bands": [b for b in bands if b],
                "slug": slug, "number": meta["number"], "date": meta.get("date"), "pages": a["pages"],
                "author": author, "platform": platform, "games": list(dict.fromkeys(found)),
                "topics": [tp["slug"] for tp in topics if t["topics"].get(tp["slug"], 0) >= TOPIC_P],
            })

    # Authors: group spellings, pick a display name, apply hand merges.
    spellings: dict[str, Counter] = defaultdict(Counter)
    for art in articles:
        if art["author"]:
            k = author_key(art["author"])
            k = author_key(alias_to[k]) if k in alias_to else k
            spellings[k][art["author"]] += 1
            art["author_key"] = k
    authors = {}
    for k, names in spellings.items():
        merged = [n for n in (merges_file.get("merge") or {}) if author_key(n) == k]
        display = merged[0] if merged else tr_title(max(names, key=lambda n: (names[n], n != n.upper())))
        authors[k] = {"name": display, "slug": slugify(display), "spellings": sorted(names),
                      "articles": [a for a in articles if a.get("author_key") == k]}

    game_index: dict[tuple, list] = defaultdict(list)
    for art in articles:
        for name, platform in art["games"]:
            game_index[(name, platform)].append(art)
    band_index: dict[str, dict] = {}
    for art in articles:
        for name in art["bands"]:
            entry = band_index.setdefault(fold(name), {"names": Counter(), "articles": []})
            entry["names"][name] += 1
            entry["articles"].append(art)
    bands = {}
    for k, v in band_index.items():
        name = v["names"].most_common(1)[0][0]
        bands[slugify(name)] = {"name": name, "articles": v["articles"]}
    topic_index = {tp["slug"]: {**tp, "articles": [a for a in articles if tp["slug"] in a["topics"]]} for tp in topics}
    return {"articles": articles, "authors": authors, "games": game_index, "bands": bands, "topics": topic_index}


def band_name(name: str) -> str | None:
    """A heading tidied into a band name: "MORBID ANGEL" → "Morbid Angel" (all-caps words get
    plain capitalisation, mixed case stays); too long or empty is not a band."""
    words = name.replace("İ", "I").split()
    if not words or len(words) > 5:
        return None
    return " ".join(w[:1].upper() + w[1:].lower().replace("ı", "i") if w.isupper() and len(w) > 1 else w for w in words)


def read_yaml(path: Path) -> dict:
    return (yaml.safe_load(path.read_text()) or {}) if path.exists() else {}


def suggest(authors: dict) -> list[list[str]]:
    """Pairs of distinct authors whose names are close enough to be one person."""
    keys = list(authors)
    names = {k: fold(ROLE_PREFIX.sub("", authors[k]["name"])) for k in keys}
    pairs = []
    for i, k0 in enumerate(keys):
        k = names[k0]
        for j in range(i + 1, len(keys)):
            other = names[keys[j]]
            same_surname = k.split()[-1:] == other.split()[-1:] and k[:1] == other[:1]
            if fuzz.ratio(k, other) >= 85 or (same_surname and fuzz.token_set_ratio(k, other) >= 80):
                pairs.append(sorted([authors[k0]["name"], authors[keys[j]]["name"]]))
    return pairs


def write_suggestions(authors: dict) -> None:
    path = ROOT / "catalog" / "authors.yaml"
    data = read_yaml(path)
    merged = {author_key(n) for c, al in (data.get("merge") or {}).items() for n in [c, *al]}
    separate = {frozenset(map(author_key, p)) for p in data.get("separate") or []}
    data["suggested"] = [p for p in suggest(authors) if not all(author_key(n) in merged for n in p)
                         and frozenset(map(author_key, p)) not in separate]
    header = ("# Authors: spellings that are one person.\n"
              "# `merge`: canonical name → other spellings; edit by hand, it wins over everything.\n"
              "# `separate`: pairs that look alike but are different people; never suggested again.\n"
              "# `suggested`: close matches found automatically (regenerated by taxonomy.py);\n"
              "# to merge a pair, add it under `merge`.\n")
    path.write_text(header + yaml.safe_dump({"merge": data.get("merge") or {}, "separate": data.get("separate") or [],
                                             "suggested": data["suggested"]},
                                            allow_unicode=True, sort_keys=False))


if __name__ == "__main__":
    t = collect()
    write_suggestions(t["authors"])
    print(f"{len(t['articles'])} tagged articles, {len(t['authors'])} authors, {len(t['games'])} games")
    top = sorted(t["authors"].values(), key=lambda a: -len(a["articles"]))[:10]
    print("top authors:", [(a["name"], len(a["articles"])) for a in top])
    print("top games:", sorted(((n, PLATFORM_NAMES.get(p, p), len(v)) for (n, p), v in t["games"].items()), key=lambda x: -x[2])[:12])
    print("topics:", {k: len(v["articles"]) for k, v in t["topics"].items()})

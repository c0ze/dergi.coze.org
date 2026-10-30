"""Tag each article with JEV: author, topics, and per corpus games and platform (retro) or bands (metal).

One request per article. The state is the article's title, magazine and date, its headings,
the start of its text, and candidate lines picked out in code:
- name candidates: short capitalised lines near the start or end of the article (bylines
  such as "Can Yalçın", sign-offs such as "Abdurrahman PALA") and any "Hazırlayan:" line;
- game candidates: the article title and its section headings.
Questions:
  author           Choice among the name candidates, or none
  game_<i>         retro: Noul per heading candidate: it is the name of a computer game discussed here
  band_<i>         metal: Noul per heading candidate: it is the name of a band or musician covered
  platform         retro: Choice: which computer the article is about
  topic_<slug>     Noul per topic of the title's corpus in catalog/topics.yaml

Output: data/issues/<slug>/<NN>/tags.json  {article slug: {author, games, platform, topics}}
with raw probabilities; thresholds are applied when the site is built. Existing results are
kept unless --force (cache key: the article's text).
Usage: tags.py <slug>/<NN> [...] [--force] [--jobs 4]
"""

import argparse
import hashlib
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml
from typesafe_sdk import Choice, Noul, TypeSafeClient

sys.path.insert(0, str(Path(__file__).parent))
from classify import MODEL, load_env  # noqa: E402
from fsutil import read_json, write_json  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TEXT = 2500          # characters of article text in the state
MAX_GAMES = 12
PLATFORMS = {
    "c64": "Commodore 64 or Commodore 128.",
    "amiga": "Commodore Amiga (including CD32).",
    "pc": "IBM PC or compatible (DOS, Windows).",
    "other_computer": "Another home computer: Atari ST/XL, ZX Spectrum, Amstrad CPC, MSX, VIC-20, C16/Plus4.",
    "console": "A games console (Sega, Nintendo, Atari 2600...).",
    "general": "No single computer: general topic, news or letters about several machines.",
}
NAME_WORD = re.compile(r"^[A-ZÇĞİÖŞÜ][A-Za-zÇĞİÖŞÜçğıöşü'’\".-]*$")
BYLINE = re.compile(r"(?i)\b(?:hazırlayan|yazan|yazar|hazirlayan)\s*:\s*(.+)$")


def name_candidates(article: dict) -> list[str]:
    paras = [x["text"] for x in article["body"] if x["type"] in ("p", "caption") and x.get("text")]
    out = []
    if article.get("author"):
        out.append(article["author"])
    for text in paras[:8] + paras[-3:]:
        if m := BYLINE.search(text):
            out.append(m.group(1).split(" İLETİŞİM")[0].strip()[:60])
        words = text.replace("“", '"').replace("”", '"').split()
        # Two to four capitalised words and nothing else: looks like a person's name.
        if 2 <= len(words) <= 4 and all(NAME_WORD.match(w) for w in words) and len(text) <= 40:
            out.append(text.strip())
    seen, unique = set(), []
    for n in out:
        if n.lower() not in seen and n.lower() != article["title"].lower():
            seen.add(n.lower())
            unique.append(n)
    return unique[:8]


def game_candidates(article: dict) -> list[str]:
    heads = [article["title"]] + [x["text"] for x in article["body"] if x["type"] == "h2" and x.get("text")]
    out, seen = [], set()
    for h in heads:
        h = " ".join(h.split())[:60]
        if 2 <= len(h) and h.lower() not in seen:
            seen.add(h.lower())
            out.append(h)
    return out[:MAX_GAMES]


# What each corpus asks about the heading candidates ("game_candidates" in the state).
ENTITIES = {
    "retro": ("games", "game", "is the name of a computer game that this article reviews, explains or gives tips for."),
    "metal": ("bands", "band", "is the name of a band or musician that this article is about "
                               "(interview, review, history, news or lyrics)."),
}


def tag_article(client: TypeSafeClient, magazine: str, date: str | None, article: dict, topics: list[dict],
                corpus: str = "retro") -> dict:
    text = "\n".join(x["text"] for x in article["body"] if x["type"] in ("p", "h2", "caption") and x.get("text"))
    names, games = name_candidates(article), game_candidates(article)
    state = {"magazine": magazine, "date": date, "title": article["title"],
             "headings": [x["text"] for x in article["body"] if x["type"] == "h2"][:20],
             "text_start": text[:TEXT], "text_end": text[-400:] if len(text) > TEXT else "",
             "name_candidates": names, "game_candidates": games}
    qs = {}
    if corpus == "retro":
        qs["platform"] = Choice(instructions="Which computer is this magazine article mainly about?", criteria=PLATFORMS)
    if names:
        qs["author"] = Choice(
            instructions="Which of `name_candidates` is the name of the article's author (byline or sign-off)?",
            criteria={f"n{i}": f"`name_candidates[{i}]`: {n}" for i, n in enumerate(names)}
                     | {"none": "None of them is the author (or the author isn't named)."})
    key, prefix, what = ENTITIES.get(corpus, (None, None, None))
    for i, g in enumerate(games if key else []):
        qs[f"{prefix}_{i}"] = Noul(instructions=f"`game_candidates[{i}]` (\"{g}\") {what}")
    for t in topics:
        qs[f"topic_{t['slug']}"] = Noul(instructions=f"The article is about this topic: {t['about']}")
    resp = client.system_one(state=state, questions=qs)
    a = resp.answers
    author = None
    if "author" in a and a["author"].choice != "none":
        author = {"name": names[int(a["author"].choice[1:])], "confidence": a["author"].confidence}
    out = {
        "text_sha": hashlib.sha1(text.encode()).hexdigest(),
        "platform": {"choice": a["platform"].choice, "confidence": a["platform"].confidence} if "platform" in a else None,
        "author": author,
        "games": [],
        "topics": {t["slug"]: round(a[f"topic_{t['slug']}"].noul, 3) for t in topics},
        "tokens": resp.usage.input_tokens,
    }
    if key:
        out[key] = [{"name": g, "p": round(a[f"{prefix}_{i}"].noul, 3)} for i, g in enumerate(games)]
    return out


def tag_issue(client: TypeSafeClient, issue: str, topics: list[dict], titles: dict, args) -> None:
    title = titles[issue.split("/")[0]]
    corpus = title.get("corpus", "retro")
    topics = [t for t in topics if t.get("corpus", "retro") == corpus]
    d = ROOT / "data" / "issues" / issue
    meta = read_json(d / "issue.json")
    articles = read_json(d / "articles.json") or []
    old = {} if args.force else (read_json(d / "tags.json") or {})

    def todo(a: dict) -> bool:
        text = "\n".join(x["text"] for x in a["body"] if x["type"] in ("p", "h2", "caption") and x.get("text"))
        return a["slug"] not in old or old[a["slug"]].get("text_sha") != hashlib.sha1(text.encode()).hexdigest()

    work = [a for a in articles if todo(a)]
    with ThreadPoolExecutor(args.jobs) as pool:
        results = list(pool.map(lambda a: tag_article(client, title["name"], meta.get("date"), a, topics, corpus), work))
    tags = {a["slug"]: old[a["slug"]] for a in articles if a["slug"] in old and a not in work}
    tags |= {a["slug"]: r for a, r in zip(work, results)}
    write_json(d / "tags.json", tags, indent=1)
    print(f"{issue}: {len(work)} tagged, {len(articles) - len(work)} kept, "
          f"{sum(r['tokens'] or 0 for r in results)} tokens", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("issues", nargs="+")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--jobs", type=int, default=4)
    args = ap.parse_args()
    load_env()
    if not os.environ.get("TYPESAFE_API_KEY"):
        raise SystemExit("TYPESAFE_API_KEY is not set (environment or .env)")
    topics = yaml.safe_load((ROOT / "catalog" / "topics.yaml").read_text())
    titles = {t["slug"]: t for t in yaml.safe_load((ROOT / "catalog" / "titles.yaml").read_text())}
    with TypeSafeClient(model=MODEL) as client:
        for issue in args.issues:
            tag_issue(client, issue, topics, titles, args)


if __name__ == "__main__":
    main()

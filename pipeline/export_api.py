"""Everything the public API and MCP server answer from, in one file.

Magazines, issues (contents, reader-page links, type-in programs), every article's
proofread text as Markdown-like plain text, and the author, game and topic indexes from
taxonomy.py. Written to data/search/catalog.json.gz; api/deploy.sh ships it in the image.

Usage: export_api.py
"""

import gzip
import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))
import taxonomy  # noqa: E402
from fsutil import read_json  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SITE = "https://dergi.coze.org"
OUT = ROOT / "data" / "search" / "catalog.json.gz"


def article_text(article: dict, byline: str | None) -> str:
    """Headings as "## ", listings fenced, captions in italics; figures left out. A byline
    paragraph near the top ("SAMET KUMAŞ") is dropped: the author is given separately."""
    parts, seen = [], 0
    key = taxonomy.author_key(byline) if byline else None
    for x in article["body"]:
        text = (x.get("text") or "").strip()
        if not text:
            continue
        if x["type"] == "p" and seen < 4:
            seen += 1
            if key and taxonomy.author_key(text) == key:
                continue
        if x["type"] == "h2":
            parts.append(f"## {text}")
        elif x["type"] == "pre":
            parts.append(f"```\n{text}\n```")
        elif x["type"] == "caption":
            parts.append(f"_{text}_")
        else:
            parts.append(text)
    return "\n\n".join(parts)


def main() -> None:
    titles = {t["slug"]: t for t in yaml.safe_load((ROOT / "catalog" / "titles.yaml").read_text())}
    tax = taxonomy.collect()
    by_url = {a["url"]: a for a in tax["articles"]}
    topic_names = {k: v["ad"] for k, v in tax["topics"].items()}

    magazines, issues, articles = {}, {}, {}
    for issue_dir in sorted((ROOT / "data" / "issues").glob("*/*/")):
        meta, arts = read_json(issue_dir / "issue.json"), read_json(issue_dir / "articles.json")
        if not meta or arts is None:
            continue
        slug, nn = issue_dir.parent.name, issue_dir.name
        t = titles[slug]
        mag = magazines.setdefault(slug, {
            "slug": slug, "name": t["name"], "years": str(t.get("years", "")), "publisher": t.get("publisher"),
            "description": " ".join(str(t.get("aciklama", "")).split()) or None,
            "url": f"{SITE}/{slug}/", "issues": []})
        mag["issues"].append(meta["number"])
        base = f"{slug}/{nn}"
        progs = [p for p in read_json(issue_dir / "prg" / "prg.json") or [] if p.get("verified")]
        issues[base] = {
            "magazine": slug, "magazine_name": t["name"], "number": meta["number"], "date": meta.get("date"),
            "pages": meta["pages"], "url": f"{SITE}/{base}/",
            # Images are served only to the site's own pages (img.dergi.coze.org checks the
            # Referer), so agents get the reader pages, not image URLs.
            "page_url": f"{SITE}/{base}/sayfa/{{page}}/",
            "articles": [f"{SITE}/{base}/{a['slug']}/" for a in arts],
            "programs": [{"name": p["name"], "file": p["file"], "load": p["load"],
                          "bytes": p["bytes"], "start": p.get("start"), "article": f"{SITE}/{base}/{p['article']}/"}
                         for p in progs]}
        for a in arts:
            path = f"/{base}/{a['slug']}/"
            tags = by_url.get(path) or {}
            author = tax["authors"].get(tags.get("author_key")) if tags.get("author_key") else None
            articles[SITE + path] = {
                "title": a["title"], "magazine": slug, "magazine_name": t["name"], "issue": meta["number"],
                "date": meta.get("date"), "pages": a["pages"],
                "author": author["name"] if author else a.get("author"),
                "author_url": f"{SITE}/yazar/{author['slug']}/" if author else None,
                "games": [f"{n} ({taxonomy.PLATFORM_NAMES.get(p, p)})" for n, p in tags.get("games", [])],
                "topics": [topic_names[s] for s in tags.get("topics", [])],
                "text": article_text(a, tags.get("author"))}

    def urls(arts: list[dict]) -> list[str]:
        return [SITE + a["url"] for a in arts if SITE + a["url"] in articles]

    authors = {a["slug"]: {"name": a["name"], "spellings": a["spellings"], "url": f"{SITE}/yazar/{a['slug']}/",
                           "articles": urls(a["articles"])} for a in tax["authors"].values()}
    games: dict[str, dict] = {}
    for (name, platform), arts in tax["games"].items():
        g = games.setdefault(taxonomy.slugify(name), {"name": name, "url": f"{SITE}/oyun/{taxonomy.slugify(name)}/",
                                                      "platforms": {}})
        g["platforms"][taxonomy.PLATFORM_NAMES.get(platform, platform)] = urls(arts)
    topics = {k: {"name": v["ad"], "about": v["about"], "url": f"{SITE}/konu/{k}/", "articles": urls(v["articles"])}
              for k, v in tax["topics"].items()}

    catalog = {"magazines": magazines, "issues": issues, "articles": articles,
               "authors": authors, "games": games, "topics": topics}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    tmp.write_bytes(gzip.compress(json.dumps(catalog, ensure_ascii=False).encode(), 9))
    tmp.replace(OUT)
    print(f"{len(magazines)} magazines, {len(issues)} issues, {len(articles)} articles, {len(authors)} authors, "
          f"{len(games)} games, {len(topics)} topics → {OUT} ({OUT.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()

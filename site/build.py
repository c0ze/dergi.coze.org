"""Phase 4: build the static site from the catalogue and the assembled issues.

Output goes to data/site/ (git-ignored). Page images and figures are copied
under /i/; in production they move to the CDN host and ASSET_BASE points there.

Usage: build.py [--asset-base URL] [--only slug,slug]
Then:  npx -y pagefind@1.3.0 --site data/site   (search index)
"""

import argparse
import json
import shutil
from pathlib import Path

import math
import sys

import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
import taxonomy  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SITE = Path(__file__).resolve().parent
OUT = ROOT / "data" / "site"
ISSUES = ROOT / "data" / "issues"

MONTHS = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
          "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]


FOLD = str.maketrans("çğıöşüÇĞİÖŞÜâîûÂÎÛ", "cgiosuCGIOSUaiuAIU")


def fold(article: dict) -> str:
    """The article's words written without Turkish letters, only where they differ, so that
    a search for "kursor" finds "kursör" (Pagefind doesn't fold diacritics itself)."""
    text = " ".join([article["title"]] + [x.get("text", "") for x in article["body"]])
    return " ".join(dict.fromkeys(w.translate(FOLD) for w in text.split() if w.translate(FOLD) != w))


def human_date(date: str | None) -> str:
    if not date:
        return ""
    year, month = date.split("-")
    return f"{MONTHS[int(month) - 1]} {year}"


def known_issues(title: dict) -> list[int]:
    run = title.get("issues")
    if isinstance(run, dict):
        return list(range(run["from"], run["to"] + 1))
    return run if isinstance(run, list) else []


def load_issue(slug: str, number: str) -> dict:
    d = ISSUES / slug / number
    meta = json.loads((d / "issue.json").read_text())
    meta["dir"] = d
    meta["nn"] = number
    meta["human_date"] = human_date(meta.get("date"))
    meta["articles"] = json.loads((d / "articles.json").read_text()) if (d / "articles.json").exists() else []
    pages = json.loads((d / "pages.json").read_text()) if (d / "pages.json").exists() else {}
    meta["page_info"] = {int(k): v for k, v in pages.items()}
    by_page: dict[int, list[str]] = {}
    for a in meta["articles"]:
        for x in a["body"]:
            if x.get("text") and x["type"] in ("p", "h2", "caption", "pre"):
                by_page.setdefault(x["page"], []).append(x["text"])
    for n, info in meta["page_info"].items():
        if n in by_page:
            info["text"] = by_page[n]
    progs = json.loads((d / "prg" / "prg.json").read_text()) if (d / "prg" / "prg.json").exists() else []
    # Only programs whose every line passed its checksum are published.
    meta["programs"] = [p for p in progs if p["verified"]]
    return meta


def copy(src: Path, dst: Path) -> None:
    """Copy unless dst already is this file (same size, not older). Size matters too: a
    better scan can carry an older timestamp than the file it replaces."""
    if dst.exists() and dst.stat().st_size == src.stat().st_size and dst.stat().st_mtime >= src.stat().st_mtime:
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def grouped(articles: list[dict]) -> list[tuple[str, list[dict]]]:
    """Articles by magazine (magazines in order of their first issue), oldest first."""
    groups: dict[str, list[dict]] = {}
    for a in sorted(articles, key=lambda a: (a["date"] or "", a["number"], a["pages"][0])):
        groups.setdefault(a["magazine"], []).append(a)
    return list(groups.items())


def cloud(items: list[tuple[str, str, int]]) -> list[dict]:
    """(name, url, count) → cloud entries with a 0–1 weight on a log scale, by name."""
    if not items:
        return []
    top = max(c for _, _, c in items)
    return [{"name": n, "url": u, "count": c, "weight": round(math.log(c + 1) / math.log(top + 1), 2)}
            for n, u, c in sorted(items, key=lambda x: taxonomy.fold(x[0]))]


def build_taxonomy(render, only: set[str] | None) -> None:
    """Author, game and topic pages and their clouds; tags on each article page come from
    TAGS, filled here before the article pages are rendered."""
    t = TAX
    if not t["articles"]:
        return
    keep = (lambda a: True) if not only else (lambda a: a["slug"] in only)
    authors = [(a, [x for x in a["articles"] if keep(x)]) for a in t["authors"].values()]
    authors = [(a, arts) for a, arts in authors if arts]
    for a, arts in authors:
        other = [s for s in a["spellings"] if s != a["name"]]
        render(f"yazar/{a['slug']}", "tagpage.html", name=a["name"], parent="Yazarlar", parent_url="/yazarlar/",
               total=len(arts), groups=grouped(arts), show_author=False,
               extra=f"{len({x['magazine'] for x in arts})} dergi" + (f" · diğer yazılışlar: {', '.join(other)}" if other else ""))
    render("yazarlar", "cloud.html", heading="Yazarlar",
           lede="Dergilerde yazısı olan herkes; ne kadar çok yazısı varsa adı o kadar büyük. Aynı kişinin farklı yazılışları tek profilde toplanır.",
           sections=[{"title": None, "items": cloud([(a["name"], f"/yazar/{a['slug']}/", len(arts)) for a, arts in authors])}])

    by_game: dict[str, dict[str, list]] = {}
    for (name, platform), arts in t["games"].items():
        arts = [x for x in arts if keep(x)]
        if arts:
            by_game.setdefault(name, {})[platform] = arts
    for name, plats in by_game.items():
        order = sorted(plats, key=lambda p: list(taxonomy.PLATFORM_NAMES).index(p) if p in taxonomy.PLATFORM_NAMES else 9)
        render(f"oyun/{taxonomy.slugify(name)}", "tagpage.html", name=name, parent="Oyunlar", parent_url="/oyunlar/",
               total=sum(len(v) for v in plats.values()), groups=[],
               platforms=[(taxonomy.PLATFORM_NAMES.get(p, p), grouped(plats[p])) for p in order],
               extra=", ".join(f"{taxonomy.PLATFORM_NAMES.get(p, p)}: {len(plats[p])}" for p in order))
    sections = []
    for p, label in taxonomy.PLATFORM_NAMES.items():
        items = [(n, f"/oyun/{taxonomy.slugify(n)}/", len(pl[p])) for n, pl in by_game.items() if p in pl]
        if items:
            sections.append({"title": label, "items": cloud(items)})
    render("oyunlar", "cloud.html", heading="Oyunlar",
           lede="Hakkında yazı çıkmış oyunlar, makineye göre: Turrican (C64) ile Turrican (Amiga) ayrı ayrı. Oyun sayfasında bütün dergilerdeki yazılar bir arada.",
           sections=sections)

    topics = [(tp, [x for x in tp["articles"] if keep(x)]) for tp in t["topics"].values()]
    for tp, arts in topics:
        if arts:
            render(f"konu/{tp['slug']}", "tagpage.html", name=tp["ad"], parent="Konular", parent_url="/konular/",
                   total=len(arts), groups=grouped(arts), show_author=True, extra=None)
    render("konular", "cloud.html", heading="Konular",
           lede="Yazıların konuları: makine dilinden oyun hilelerine. Bir yazının birden çok konusu olabilir.",
           sections=[{"title": None, "items": cloud([(tp["ad"], f"/konu/{tp['slug']}/", len(arts)) for tp, arts in topics if arts])}])


TAX = taxonomy.collect()
BY_URL = {a["url"]: a for a in TAX["articles"]}
TOPIC_NAMES = {k: v["ad"] for k, v in TAX["topics"].items()}


def article_tags(url: str) -> dict | None:
    a = BY_URL.get(url)
    if not a:
        return None
    author = TAX["authors"].get(a.get("author_key")) if a.get("author_key") else None
    return {"author": author and {"name": author["name"], "url": f"/yazar/{author['slug']}/"},
            "author_raw": a.get("author"),
            "games": [{"name": f"{n} ({taxonomy.PLATFORM_NAMES.get(p, p)})", "url": f"/oyun/{taxonomy.slugify(n)}/"}
                      for n, p in a["games"]],
            "topics": [{"name": TOPIC_NAMES[s], "url": f"/konu/{s}/"} for s in a["topics"]]}


def without_byline(article: dict, tags: dict | None) -> dict:
    """The article without its byline paragraph ("SAMET KUMAŞ") once the author is known:
    the name is shown in the header instead."""
    if not tags or not tags.get("author_raw"):
        return article
    key = taxonomy.author_key(tags["author_raw"])
    body, seen = [], 0
    for x in article["body"]:
        if x["type"] == "p" and seen < 4:
            seen += 1
            if taxonomy.author_key(x["text"]) == key:
                continue
        body.append(x)
    return {**article, "body": body}


def build(asset_base: str, only: set[str] | None) -> None:
    env = Environment(loader=FileSystemLoader(SITE / "templates"), autoescape=select_autoescape())
    env.globals["A"] = asset_base.rstrip("/")
    env.filters["fold"] = fold
    env.filters["human_date"] = human_date
    env.filters["sayi"] = lambda n: f"{n:,}".replace(",", ".")  # Turkish thousands: 11.833
    # Start from a clean tree (assets under i/ are kept and synced by copy()): pages for
    # removed articles, changed slugs or titles left out with --only must not linger and
    # end up in the search index or the next deploy.
    if OUT.exists():
        for child in OUT.iterdir():
            if child.name != "i":
                shutil.rmtree(child) if child.is_dir() else child.unlink()

    def render(path: str, template: str, **ctx) -> None:
        out = OUT / path / "index.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(env.get_template(template).render(**ctx))

    titles = yaml.safe_load((ROOT / "catalog" / "titles.yaml").read_text())
    shelf = []
    for t in titles:
        if only and t["slug"] not in only:
            continue
        have = sorted(p.name for p in (ISSUES / t["slug"]).glob("*") if (p / "issue.json").exists()) \
            if (ISSUES / t["slug"]).exists() else []
        if not have:
            continue
        issues = [load_issue(t["slug"], nn) for nn in have]
        numbers = {i["number"] for i in issues}
        t["issues_have"] = issues
        t["missing"] = [n for n in known_issues(t) if n not in numbers]
        shelf.append(t)

        for issue in issues:
            d, base = issue["dir"], f"{t['slug']}/{issue['nn']}"
            # Assets: reader pages, thumbnails and figures. The scans themselves are not offered
            # for download (decided 2026-10-27).
            for sub in ("pages", "thumbs", "figures"):
                for f in (d / sub).glob("*.webp") if (d / sub).exists() else []:
                    copy(f, OUT / "i" / base / sub / f.name)
            for prog in issue["programs"]:
                for name in (prog["file"], prog.get("screenshot")):
                    if name:
                        copy(d / "prg" / name, OUT / "i" / base / "prg" / name)

            for a in issue["articles"]:
                # The author's profile name ("Can Öztürk"), not the byline as printed ("CAN ÖZTÜRK").
                tags = article_tags(f"/{base}/{a['slug']}/")
                a["author_name"] = (tags and tags["author"] and tags["author"]["name"]) or a.get("author")
            render(base, "issue.html", title=t, issue=issue)
            arts = issue["articles"]
            for i, a in enumerate(arts):
                tags = article_tags(f"/{base}/{a['slug']}/")
                render(f"{base}/{a['slug']}", "article.html", title=t, issue=issue, article=without_byline(a, tags),
                       tags=tags,
                       programs=[p for p in issue["programs"] if p["article"] == a["slug"]],
                       prev=arts[i - 1] if i else None, next=arts[i + 1] if i + 1 < len(arts) else None)
            by_slug = {a["slug"]: a for a in arts}
            empty = {"kind": "", "text": [], "articles": []}

            def articles_on(n: int) -> list[dict]:
                info = issue["page_info"].get(n, empty)
                return [by_slug[s] for s in dict.fromkeys(info["articles"]) if s in by_slug]

            for p in range(1, issue["pages"] + 1):
                # The facing page in a printed spread: even pages left, odd right, cover alone.
                q = None if p == 1 else (p + 1 if p % 2 == 0 else p - 1)
                facing = None
                if q and q <= issue["pages"]:
                    facing = {"page": q, "text": issue["page_info"].get(q, empty)["text"], "articles": articles_on(q)}
                render(f"{base}/sayfa/{p}", "page.html", title=t, issue=issue, page=p,
                       info=issue["page_info"].get(p, empty), page_articles=articles_on(p), facing=facing)
        render(t["slug"], "title.html", title=t)

    build_taxonomy(render, only)
    render("", "index.html", shelf=shelf)
    render("ara", "search.html")
    issues = [i for t in shelf for i in t["issues_have"]]
    stats = {"issues": len(issues), "pages": sum(i["pages"] for i in issues),
             "articles": sum(len(i["articles"]) for i in issues),
             "programs": sum(len(i["programs"]) for i in issues),
             "authors": len(TAX["authors"]), "games": len(TAX["games"])}
    render("hakkinda", "about.html", shelf=shelf, stats=stats)
    for f in (SITE / "static").iterdir():
        copy(f, OUT / "static" / f.name)
    print(f"built {OUT} ({sum(len(t['issues_have']) for t in shelf)} issues)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--asset-base", default="/i", help="URL prefix for images")
    ap.add_argument("--only", help="comma-separated title slugs")
    args = ap.parse_args()
    build(args.asset_base, set(args.only.split(",")) if args.only else None)

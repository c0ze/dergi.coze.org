"""Phase 4: build the static site from the catalogue and the assembled issues.

Output goes to data/site/ (git-ignored). Page images and figures are copied
under /i/; in production they move to the CDN host and ASSET_BASE points there.

Titles marked `private: true` in catalog/titles.yaml are left out unless --private is given;
the private build (all titles, noindex) goes to data/site-private and is served only behind
Cloudflare Access (workers/ozel).

Usage: build.py [--asset-base URL] [--only slug,slug] [--private]
Then:  npx -y pagefind@1.3.0 --site data/site   (search index)
"""

import argparse
import json
import re
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
    if len(date) == 4:  # year only (Matematik Dünyası)
        return date
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
    # The issue as it was published (PDF, or the diskmag's zip), for titles with `downloads: true`.
    meta["originals"] = [{"name": f.name, "mb": round(f.stat().st_size / 1e6, 1)}
                         for f in sorted((d / "original").glob("*")) if f.is_file()] if (d / "original").exists() else []
    progs = json.loads((d / "prg" / "prg.json").read_text()) if (d / "prg" / "prg.json").exists() else []
    # Every program is offered; one with failing or missing lines, or a BASIC program that stops
    # in VICE on an error saying it was put together from the wrong lines (test_prg.py), carries
    # what was found. A machine-code part may answer its bare SYS with an error: not the bytes.
    progs = [p for p in progs if (d / "prg" / p["file"]).exists()]
    for p in progs:
        p["issues"] = program_issues(p)
        p["notes"] = (["Dökümde kontrol toplamı yok: satırlar doğrulanamadı."] if p.get("format") == "basic" else []) + (
            ["Tek başına çalışan bir program değil: yazıdaki öteki parçalarıyla birlikte kullanılıyor."] if p.get("part") else []) + (
            printed_note(p))
    meta["programs"] = progs

    return meta


def program_issues(p: dict) -> list[str]:
    """What is known to be wrong with an extracted program, for the reader (pde.py `failed`:
    line numbers or addresses whose checksum fails, "gap A-B", "start $X", "no line N",
    "symbol N"). A line printed blank ("printed N") or a misprint ("misprint ADDR", "misprint N",
    "misprint no line N") is a note, not a fault: `printed_note`."""
    failed = p.get("failed") or []
    bad = [f for f in failed if not f.startswith(("gap ", "start ", "no line ", "symbol ", "printed ", "misprint "))]
    symbols = [f[7:] for f in failed if f.startswith("symbol ")]
    spans = [[int(x, 16) for x in f[4:].split("-")] for f in failed if f.startswith("gap ")]
    gaps = [f"{a:04X}–${b - 1:04X}" for a, b in spans if b > a and (b - a) % 8 == 0]
    odd = len(spans) - len(gaps)  # a line whose address is off the 8-byte steps: misread address
    starts = [f[6:] for f in failed if f.startswith("start ")]
    missing = [f[8:] for f in failed if f.startswith("no line ")]
    out = []
    if bad:
        out.append(f"{len(bad)} satırın kontrol toplamı tutmuyor: {', '.join(bad[:8])}{'…' if len(bad) > 8 else ''}.")
    if gaps:
        out.append(f"Eksik satırlar: ${', $'.join(gaps[:4])}{'…' if len(gaps) > 4 else ''}.")
    if odd:
        out.append(f"{odd} satırın adresi yanlış okunmuş (8 baytlık adımlara uymuyor).")
    if starts:
        out.append(f"Makine dilinin ilk satırları okunamadı (döküm {starts[0]} adresinden başlıyor).")
    if symbols:
        out.append(f"{len(symbols)} satırda okunamayan işaretler var: {', '.join(symbols[:8])}{'…' if len(symbols) > 8 else ''}.")
    if missing:
        out.append(f"Programın atladığı satırlar dökümde yok: {', '.join(missing[:8])}{'…' if len(missing) > 8 else ''}.")
    if p.get("start") == "RUN" and not p.get("part") and p.get("screen_error") and re.search(r"STATEMENT|WITHOUT|SYNTAX|PROC NOT FOUND",
                                                                         p["screen_error"]):
        out.append(f"VICE'ta hata verdi: {p['screen_error']}.")
    return out


def printed_note(p: dict) -> list[str]:
    """Lines printed as (almost) nothing whose checksum says there was more: kept as printed; the
    magazine's misprints (read/misprints.json): a clearly printed line under a wrong checksum, and
    a line the program jumps to that was never printed, machine-code rows never printed ("misprint gap
    A-B"); lines taken from errata in a later issue;
    box graphics the printer left out, put back in the .prg by checksum (read/graphics.json); C128
    keywords printed 76 tokens low ("misprint token N"), put right in the .prg."""
    printed = [f[8:] for f in p.get("failed") or [] if f.startswith("printed ")]
    misprint = [f[9:] for f in p.get("failed") or [] if f.startswith("misprint ")
                and not f.startswith(("misprint no line ", "misprint gap ", "misprint token "))]
    shifted = [f[15:] for f in p.get("failed") or [] if f.startswith("misprint token ")]
    skipped = [[int(a, 16) for a in f[13:].split("-")] for f in p.get("failed") or [] if f.startswith("misprint gap ")]
    unprinted = [f[17:] for f in p.get("failed") or [] if f.startswith("misprint no line ")]
    out = []
    if printed:
        out.append(f"{len(printed)} satır dergide eksik basılmış (kontrol toplamı fazlasını gösteriyor); basıldığı gibi alındı"
                   f" ({', '.join(printed[:8])}{'…' if len(printed) > 8 else ''}).")
    if misprint and p.get("format") == "mde":
        out.append(f"{len(misprint)} satırın kontrol toplamı dergide yanlış basılmış; baytları doğru"
                   f" (${', $'.join(misprint[:8])}{'…' if len(misprint) > 8 else ''}).")
    elif misprint:
        one = len(misprint) == 1
        out.append(f"{', '.join(misprint)} numaralı {'satırın' if one else 'satırların'} kontrol toplamı dergide yanlış basılmış;"
                   f" {'satır' if one else 'satırlar'} açıkça okunuyor, basıldığı gibi alındı.")
    if unprinted:
        one = len(unprinted) == 1
        out.append(f"Program {', '.join(unprinted)} numaralı {'satıra' if one else 'satırlara'} atlıyor, ama"
                   f" {'bu satır' if one else 'bu satırlar'} dergide hiç basılmamış; program basıldığı gibi alındı."
                   f" Çalışırken {'oraya' if one else 'oralara'} gelirse hata verip durur.")
    if skipped:
        spans = ", ".join(f"${a:04X}–${b - 1:04X}" for a, b in skipped)
        out.append(f"Programın {spans} arasındaki satırları dergide hiç basılmamış; PRG'de o satırlar sıfırla dolduruldu,"
                   " program basıldığı gibi alındı.")
    if shifted:
        one = len(shifted) == 1
        out.append(f"{', '.join(shifted)} numaralı {'satırda' if one else 'satırlarda'} dergi BASIC 7.0 komutlarını C64"
                   " komutu olarak basmış (DEC yerine INPUT, ELSE yerine GOTO: 76 kod aşağı); PRG'de düzeltildi.")
    if e := p.get("errata"):
        out.append(f"{e['lines']} satır {int(e['issue'])}. sayıda basılan düzeltmeden alındı.")
    if c := p.get("continued"):
        out.append(f"Programın {c['lines']} satırı {int(c['issue'])}. sayıda basılan devamından alındı.")
    if g := p.get("graphics"):
        out.append(f"{len(g)} satırdaki çerçeve grafikleri dergide basılmamış; PRG'ye kontrol toplamıyla geri kondu"
                   f" ({', '.join(map(str, g[:8]))}{'…' if len(g) > 8 else ''}).")
    return out


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

    bands = [(b, [x for x in b["articles"] if keep(x)]) for b in t["bands"].values()]
    bands = [(b, arts) for b, arts in bands if arts]
    for b, arts in bands:
        render(f"grup/{taxonomy.slugify(b['name'])}", "tagpage.html", name=b["name"], parent="Gruplar",
               parent_url="/gruplar/", total=len(arts), groups=grouped(arts), show_author=True, extra=None)
    if bands:
        render("gruplar", "cloud.html", heading="Gruplar",
               lede="Metal fanzinlerinde röportajı, incelemesi ya da haberi çıkan gruplar ve müzisyenler.",
               sections=[{"title": None, "items": cloud([(b["name"], f"/grup/{taxonomy.slugify(b['name'])}/", len(arts))
                                                         for b, arts in bands])}])

    topics = [(tp, [x for x in tp["articles"] if keep(x)]) for tp in t["topics"].values()]
    for tp, arts in topics:
        if arts:
            render(f"konu/{tp['slug']}", "tagpage.html", name=tp["ad"], parent="Konular", parent_url="/konular/",
                   total=len(arts), groups=grouped(arts), show_author=True,
                   extra=CORPORA[tp.get("corpus", "retro")]["ad"])
    sections = []
    for c in CORPORA.values():
        items = [(tp["ad"], f"/konu/{tp['slug']}/", len(arts)) for tp, arts in topics
                 if arts and tp.get("corpus", "retro") == c["slug"]]
        if items:
            sections.append({"title": c["ad"], "items": cloud(items)})
    render("konular", "cloud.html", heading="Konular",
           lede="Yazıların konuları, koleksiyonlara göre. Bir yazının birden çok konusu olabilir.", sections=sections)


CORPORA = {c["slug"]: c for c in yaml.safe_load((ROOT / "catalog" / "corpora.yaml").read_text())}
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
            "bands": [{"name": n, "url": f"/grup/{taxonomy.slugify(n)}/"} for n in a.get("bands", [])],
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


def build(asset_base: str, only: set[str] | None, private: bool) -> None:
    titles = yaml.safe_load((ROOT / "catalog" / "titles.yaml").read_text())
    titles = [t for t in titles if (not only or t["slug"] in only) and (private or not t.get("private"))]
    only = {t["slug"] for t in titles}  # also filters authors, games, bands and topics
    env = Environment(loader=FileSystemLoader(SITE / "templates"), autoescape=select_autoescape())
    env.globals["private_build"] = private
    env.globals["bands_on"] = any(x["slug"] in only for b in TAX["bands"].values() for x in b["articles"])
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

    shelf = []
    for t in titles:
        have = sorted(p.name for p in (ISSUES / t["slug"]).glob("*") if (p / "issue.json").exists()) \
            if (ISSUES / t["slug"]).exists() else []
        # A title published in batches shows only the issues checked so far (`site_upto`).
        if t.get("site_upto"):
            have = [nn for nn in have if int(nn) <= t["site_upto"]]
        if not have:
            continue
        corpus = CORPORA[t.get("corpus", "retro")]
        t["corpus"], t["corpus_ad"] = corpus["slug"], corpus["ad"]
        issues = [load_issue(t["slug"], nn) for nn in have]
        numbers = {i["number"] for i in issues}
        t["issues_have"] = issues
        t["missing"] = [n for n in known_issues(t) if n not in numbers and n <= t.get("site_upto", n)]
        shelf.append(t)

        for issue in issues:
            d, base = issue["dir"], f"{t['slug']}/{issue['nn']}"
            # Assets: reader pages, thumbnails and figures. The scans themselves are not offered
            # for download (decided 2026-10-27); a title with `downloads: true` offers its own
            # published files (Plazma's PDFs and diskmags) from the site, under indir/.
            kept = set()
            for sub in ("pages", "thumbs", "figures"):
                for f in (d / sub).glob("*.webp") if (d / sub).exists() else []:
                    kept.add(OUT / "i" / base / sub / f.name)
                    copy(f, OUT / "i" / base / sub / f.name)
            # Programs: the screenshot on the image host, the .prg on the site itself, where the
            # in-browser C64 (/c64/) can fetch it.
            for prog in issue["programs"]:
                if prog.get("screenshot"):
                    kept.add(OUT / "i" / base / "prg" / prog["screenshot"])
                    copy(d / "prg" / prog["screenshot"], OUT / "i" / base / "prg" / prog["screenshot"])
                copy(d / "prg" / prog["file"], OUT / base / "prg" / prog["file"])
            # Figures a re-assembly renamed or dropped must not linger here, nor in the bucket,
            # which the deploy syncs with deletion.
            for f in (OUT / "i" / base).rglob("*"):
                if f.is_file() and f not in kept:
                    f.unlink()

            for a in issue["articles"]:
                # The author's profile name ("Can Öztürk"), not the byline as printed ("CAN ÖZTÜRK").
                tags = article_tags(f"/{base}/{a['slug']}/")
                a["author_name"] = (tags and tags["author"] and tags["author"]["name"]) or a.get("author")
            # A supplement title (`supplement_of`) and its magazine link to each other, issue by
            # issue: the supplement's numbers are the magazine's.
            issue["companions"] = [
                {"slug": o["slug"], "name": o["name"], "nn": issue["nn"], "supplement": o.get("supplement_of") == t["slug"]}
                for o in titles if (o["slug"] == t.get("supplement_of") or o.get("supplement_of") == t["slug"])
                and (ISSUES / o["slug"] / issue["nn"] / "issue.json").exists()
                and int(issue["nn"]) <= o.get("site_upto", int(issue["nn"]))]
            # The issue as published, served by the site itself (the image host never serves PDFs).
            if not t.get("downloads"):
                issue["originals"] = []
            for f in issue["originals"]:
                copy(d / "original" / f["name"], OUT / base / "indir" / f["name"])
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
    corpora = [{**c, "titles": [t for t in shelf if t["corpus"] == c["slug"]]} for c in CORPORA.values()]
    corpora = [c for c in corpora if c["titles"]]
    for c in corpora:
        render(c["slug"], "corpus.html", corpus=c)
    render("", "index.html", shelf=shelf, corpora=corpora)
    render("ara", "search.html")
    issues = [i for t in shelf for i in t["issues_have"]]
    stats = {"issues": len(issues), "pages": sum(i["pages"] for i in issues),
             "articles": sum(len(i["articles"]) for i in issues),
             "programs": sum(len(i["programs"]) for i in issues),
             "authors": sum(any(x["slug"] in only for x in a["articles"]) for a in TAX["authors"].values()),
             "games": sum(any(x["slug"] in only for x in arts) for arts in TAX["games"].values())}
    render("hakkinda", "about.html", shelf=shelf, corpora=corpora, stats=stats)
    # The in-browser C64 for the programs (scripts/emulator.sh fetches it into data/emu).
    if (ROOT / "data" / "emu").exists():
        render("c64", "c64.html")
        shutil.copytree(ROOT / "data" / "emu", OUT / "emu", dirs_exist_ok=True)
    for f in (SITE / "static").iterdir():
        copy(f, OUT / "static" / f.name)
    copy(SITE / "favicon.ico", OUT / "favicon.ico")  # browsers ask for /favicon.ico on their own
    print(f"built {OUT} ({sum(len(t['issues_have']) for t in shelf)} issues)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--asset-base", default="/i", help="URL prefix for images")
    ap.add_argument("--only", help="comma-separated title slugs")
    ap.add_argument("--private", action="store_true", help="include private titles; output to data/site-private")
    args = ap.parse_args()
    if args.private:
        OUT = ROOT / "data" / "site-private"
    build(args.asset_base, set(args.only.split(",")) if args.only else None, args.private)

"""Plazma's diskmag issues (1-3, Windows programs) → a digital edition PDF the site pipeline reads.

The diskmags keep their articles as text files: issue 1 as one 80-column file (PLAZMA_text.txt,
justified with runs of spaces, sections between rows of asterisks) and BMP pictures in
data/images/<article>/ (stored bottom-up: the program flipped them); issues 2-3 as one file per
article (cp1254, [title = ...], [b]...[/b], [img = LFT|CTR|RGT path], [url = ...]...[/url],
[code]...[/code]) listed in data/articles/menu.txt under [cat] headings, with the author in
brackets after each entry. Each article starts on a new page, after a cover page; the PDF's
text layer is what normalize.py reads, and a sidecar JSON lists every article's first page,
author and section, from which segments.json is written (no OCR, no JEV, no split agent).

Usage: diskmag.py <diskmag dir> <NN> <YYYY-MM> <out.pdf>
       diskmag.py --segments <out.json> <issue dir>   (after normalize.py: writes segments.json)
"""

import hashlib
import html
import io
import json
import re
import sys
from pathlib import Path

import pymupdf
from PIL import Image

FONTS = Path("/usr/share/fonts/noto")
PAGE = pymupdf.paper_rect("a4")
MARGIN = 54
CSS = """
@font-face {font-family: sans; src: url(NotoSans-Regular.ttf);}
@font-face {font-family: sans; src: url(NotoSans-Bold.ttf); font-weight: bold;}
@font-face {font-family: mono; src: url(NotoSansMono-Regular.ttf);}
body {font-family: sans; font-size: 10.5pt; line-height: 1.45;}
.kicker {font-size: 8.5pt; color: #777; text-transform: uppercase; letter-spacing: 1pt;}
h1 {font-size: 20pt; font-weight: bold; margin: 0 0 2pt 0;}
.byline {font-size: 10pt; font-weight: bold; color: #444; margin: 0 0 16pt 0;}
h2 {font-size: 12.5pt; font-weight: bold; margin: 12pt 0 4pt 0;}
p {margin: 0 0 7pt 0; text-align: justify;}
pre {font-family: mono; font-size: 8.5pt; line-height: 1.3; margin: 4pt 0 8pt 0;}
img {margin: 6pt 0 8pt 0;}
"""


def read_text(path: Path) -> str:
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1254", "replace")


def tr_lower(text: str) -> str:
    return text.replace("I", "ı").replace("İ", "i").lower()


def tr_capitalize(text: str) -> str:
    text = tr_lower(text)
    return {"i": "İ", "ı": "I"}.get(text[:1], text[:1].upper()) + text[1:]


def flat(text: str) -> str:
    """A justified 80-column paragraph as one line of prose."""
    return re.sub(r"\s+", " ", text).strip()


def is_code(lines: list[str]) -> bool:
    """Source code, tables and ASCII art keep their lines; justified prose does not."""
    joined = "\n".join(lines)
    if len(lines) > 1 and re.search(r"[{};]\s*$|^\s*(#include|void |int |for ?\(|if ?\(|lda |sta |jsr |[0-9]+ (PRINT|POKE|GOTO))",
                 joined, re.M | re.I):
        return True
    return sum(1 for l in lines if re.match(r"\s{2,}\S", l) or re.search(r"[-=|+*]{4,}", l)) > len(lines) / 2


def blocks_plain(body: str) -> list[tuple[str, str]]:
    """Issue 1's text: blank-line separated paragraphs; code and tables kept as they are."""
    out = []
    for para in re.split(r"\n\s*\n", body.strip("\n")):
        lines = para.rstrip().split("\n")
        if not para.strip():
            continue
        if m := re.fullmatch(r"\s*\[b\]([\s\S]*?)\[/b\]\s*", para):
            out.append(("h2", flat(m.group(1))))
            continue
        para = re.sub(r"\[/?b\]", "", para)
        lines = para.rstrip().split("\n")
        out.append(("pre", "\n".join(lines)) if is_code(lines) else ("p", flat(para)))
    return out


def find(root: Path, rel: str) -> Path | None:
    """A file the Windows program found whatever the case of its path ("shudder2/" is "Shudder2/")."""
    f = root
    for part in rel.split("/"):
        if (f / part).exists():
            f = f / part
            continue
        f = next((c for c in f.iterdir() if c.name.lower() == part.lower()), None) if f.is_dir() else None
        if f is None:
            return None
    return f


def blocks_tagged(body: str, root: Path) -> list[tuple[str, str]]:
    """Issues 2-3: [b] lines are subheadings, [img] pictures, [code] kept as typed."""
    out = []
    parts = re.split(r"(\[code\][\s\S]*?\[/code\]|\[img\s*=\s*\w+\s+[^\]]+\])", body)
    for part in parts:
        if not part:
            continue
        if part.startswith("[code]"):
            out.append(("pre", part[6:-7].strip("\r\n")))
            continue
        if m := re.match(r"\[img\s*=\s*\w+\s+([^\]]+)\]", part):
            f = find(root, m.group(1).strip().replace("\\", "/"))
            if f:
                out.append(("img", str(f)))
            continue
        part = re.sub(r"\[url\s*=\s*([^\]]+)\]([^\[]*)\[/url\]",
                      lambda m: m.group(2) if m.group(2).strip() in m.group(1) else f"{m.group(2)} ({m.group(1).strip()})",
                      part)
        for para in re.split(r"\r?\n\s*\r?\n|\r?\n", part):
            para = para.strip()
            if not para:
                continue
            if m := re.fullmatch(r"\[b\](.*?)\[/b\]", para, re.S):
                out.append(("h2", flat(m.group(1))))
            else:
                out.append(("p", flat(re.sub(r"\[/?b\]", "", para))))
    return out


def issue_one(root: Path) -> list[dict]:
    text = read_text(root / "PLAZMA_text.txt").replace("\r\n", "\n")
    sections = re.split(r"\n?\*{20,}\n(.+?)\n\*{20,}\n", "\n" + text)
    pictures = {"IRRATIONAL": "irrational", "2005 PC DEMOLARI": "2005demo", "PİXEL ÇİZİM": "hydrogen_yazi",
                "AURA İLE BULUT": "gfxtutor", "SCENE 2015": "scene2015"}
    articles = []
    for head, body in zip(sections[1::2], sections[2::2]):
        head = head.strip()
        author = None
        if m := re.search(r"\(([^)]+)\)\s*$", head):
            # "Hydrogen/Glance": the handle; issues 2-3 name authors by handle alone.
            author, head = m.group(1).split("/")[0].strip(), head[:m.start()].strip()
        kicker, _, title = head.rpartition(" : ") if " : " in head else ("", "", head)
        blocks = blocks_plain(body)
        folder = next((d for k, d in pictures.items() if k in title), None)
        for f in sorted((root / "data" / "images" / folder).glob("*.bmp")) if folder else []:
            blocks.append(("bmp", str(f)))
        # Titles stay in the capitals they were set in; str.title() breaks the Turkish İ.
        articles.append({"title": title, "kicker": tr_capitalize(kicker),
                         "author": author, "blocks": blocks})
    return articles


def issue_tagged(root: Path) -> list[dict]:
    menu = read_text(root / "data" / "articles" / "menu.txt")
    articles, cat = [], ""
    for m in re.finditer(r"\[cat\]\s*(.*?)\[/cat\]|\[goto\s*=\s*([^\]]+)\](.*?)\[/goto\]", menu, re.S):
        if m.group(1) is not None:
            cat = m.group(1).strip()
            continue
        f = root / m.group(2).strip()
        if not f.exists():
            continue
        label = flat(m.group(3))
        author = None
        if am := re.search(r"\(([^()]+)\)\s*$", label):
            author = am.group(1).strip()
        body = read_text(f)
        title = label
        if tm := re.match(r"\s*\[title\s*=\s*([^\]]*)\]", body):
            title, body = flat(tm.group(1)) or label, body[tm.end():]
        if author and title.endswith(f"({author})"):
            title = title[:-len(author) - 2].strip()
        articles.append({"title": title, "kicker": cat, "author": author, "blocks": blocks_tagged(body, root)})
    return articles


def picture(path: str, flip: bool) -> tuple[str, bytes]:
    im = Image.open(path).convert("RGB")
    if flip:
        im = im.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    # Named by the path's hash: two articles can each have a "resonance.jpg".
    return hashlib.sha1(path.encode()).hexdigest()[:12] + ".png", buf.getvalue()


def render(articles: list[dict], cover: Path | None, out: Path) -> list[dict]:
    arch = pymupdf.Archive(str(FONTS))
    writer = pymupdf.DocumentWriter(str(out))
    starts = []
    page_no = 0
    if cover:
        name, data = picture(str(cover), cover.suffix.lower() == ".bmp")
        arch.add(data, name)
        story = pymupdf.Story(f'<img src="{name}" width="100%">', user_css=CSS, archive=arch)
        dev = writer.begin_page(PAGE)
        story.place(PAGE + (MARGIN, MARGIN, -MARGIN, -MARGIN))
        story.draw(dev)
        writer.end_page()
        page_no += 1
    for a in articles:
        # Title first: the article's mark is the first block on its page (segments()). The
        # menu's section name stays in the sidecar only: set above the text, it ran into it.
        parts = [f"<h1>{html.escape(a['title'])}</h1>"]
        if a["author"]:
            parts.append(f'<div class="byline">{html.escape(a["author"])}</div>')
        for kind, value in a["blocks"]:
            if kind in ("img", "bmp"):
                name, data = picture(value, kind == "bmp")
                arch.add(data, name)
                w, h = Image.open(io.BytesIO(data)).size
                width = min(w, PAGE.width - 2 * MARGIN)
                parts.append(f'<img src="{name}" width="{int(width)}">')
            else:
                parts.append(f"<{kind}>{html.escape(value)}</{kind}>")
        story = pymupdf.Story("\n".join(parts), user_css=CSS, archive=arch)
        starts.append({"title": a["title"], "author": a["author"], "section": a["kicker"], "page": page_no + 1})
        more = True
        while more:
            dev = writer.begin_page(PAGE)
            more, _ = story.place(PAGE + (MARGIN, MARGIN, -MARGIN, -MARGIN))
            story.draw(dev)
            writer.end_page()
            page_no += 1
    writer.close()
    return starts


def segments(sidecar: Path, issue_dir: Path) -> None:
    """Every page's blocks straight from the edition's own text and pictures (normalize.py takes
    a page with a picture and little text for a scan), then each article starts at its title,
    the first block on its first page."""
    sys.path.insert(0, str(Path(__file__).parent))
    import normalize
    issue = f"{issue_dir.parent.name}/{issue_dir.name}"
    for i, page in enumerate(pymupdf.open(sidecar.with_suffix(".pdf")), start=1):
        record = {"issue": issue, "page": i, "source": "pdf-text", "blocks": normalize.extract_blocks(page)}
        (issue_dir / "blocks" / f"{i:03d}.json").write_text(json.dumps(record, ensure_ascii=False, indent=1))
    # Labels in place of JEV: the edition's layout says it all. Bold single lines in body size
    # are the [b] subheadings, the title is the article's own, the byline is furniture.
    starts = {a["page"] for a in json.loads(sidecar.read_text())["articles"]}
    for f in sorted((issue_dir / "blocks").glob("*.json")):
        page = int(f.stem)
        answers = {"page_kind": {"choice": "cover" if page == 1 else "article", "confidence": 1.0}}
        for blk in json.loads(f.read_text())["blocks"]:
            text = blk.get("text", "")
            first = page in starts and blk["order"] == 0
            byline = page in starts and blk["order"] == 1 and blk.get("bold") and (blk.get("rel_size") or 1) < 1.1
            head = "article_title" if first else "section_heading" if (
                blk.get("bold") and 1.1 <= (blk.get("rel_size") or 1) < 1.6) else "not_heading"
            answers[f"role_{blk['id']}"] = {"choice": "furniture" if byline else "content", "confidence": 1.0}
            answers[f"heading_{blk['id']}"] = {"choice": head, "confidence": 1.0}
        (issue_dir / "labels").mkdir(exist_ok=True)
        (issue_dir / "labels" / f.name).write_text(json.dumps({"page": page, "model": "diskmag", "answers": answers},
                                                              ensure_ascii=False, indent=1))
    marks = []
    for a in json.loads(sidecar.read_text())["articles"]:
        blocks = json.loads((issue_dir / "blocks" / f"{a['page']:03d}.json").read_text())["blocks"]
        title = min((b for b in blocks if b.get("text")), key=lambda b: b["order"])
        marks.append({"title": a["title"], "author": a["author"],
                      "start": {"page": a["page"], "block": title["id"]}, "resumes": []})
    (issue_dir / "segments.json").write_text(json.dumps({"source": "diskmag", "articles": marks},
                                                        ensure_ascii=False, indent=1))
    print(f"{issue_dir}: {len(marks)} articles")


def main() -> None:
    if sys.argv[1:2] == ["--segments"]:
        return segments(Path(sys.argv[2]), Path(sys.argv[3]))
    if len(sys.argv) != 5:
        raise SystemExit(__doc__)
    root, nn, date, out = Path(sys.argv[1]), int(sys.argv[2]), sys.argv[3], Path(sys.argv[4])
    if (root / "PLAZMA_text.txt").exists():
        articles, cover = issue_one(root), root / "data" / "plazma.bmp"
    else:
        articles = issue_tagged(root)
        cover = next((root / "data" / "gfx" / n for n in ("kapak.jpg", "kapak.gif") if (root / "data" / "gfx" / n).exists()), None)
    starts = render(articles, cover, out)
    out.with_suffix(".json").write_text(json.dumps({"issue": nn, "date": date, "articles": starts},
                                                    ensure_ascii=False, indent=1))
    print(f"{out}: {len(articles)} articles, {pymupdf.open(out).page_count} pages")


if __name__ == "__main__":
    main()

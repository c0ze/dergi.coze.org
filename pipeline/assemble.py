"""Phase 3: turn an issue's blocks and JEV labels into articles.

Rules:
- A block is dropped (ad, page furniture) only when JEV is sure (confidence >= ACCEPT) or
  the image and text-only runs agree. Anything uncertain is kept and counted for review:
  losing article text is worse than keeping a stray line.
- An article starts at a heading JEV calls an article title (or, when unsure, at the
  page's largest heading). Contents pages form one article named by their largest heading.
  Other headings become section headings.
- A segmentation (segments.json: each article's title, author, first block and the blocks where
  it resumes after other pages) overrides the heading rules: only its marks start or resume
  articles, and headings become section headings. A mark on a page without text ("block":
  "page") starts or continues an article there with the whole page as its picture.
- Drop caps are put back in front of their paragraph; line-end hyphens are rejoined.
- Figures are cropped from the PDF and placed before the first text below them.

Output, under data/issues/<slug>/<NN>/:
  articles.json   [{slug, title, author, pages, body: [...], review}]
  pages.json      {page: {kind, text: [...], articles: [slug, ...]}}  for the page reader
  figures/        pNNN-bNNN.webp crops

Usage: assemble.py <slug>/<NN> [...]
"""

import json
import re
import sys
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

import pymupdf
import yaml
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
from fsutil import write_json  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ACCEPT = 0.6
DROP_ROLES = {"ad", "furniture"}
FIGURE_DPI = 150
HEADING_REL = 1.4  # a line this much larger than body text is heading text

BYLINE_RE = re.compile(r"^\s*(HAZIRLAYAN|YAZAN|YAZAR)\s*:\s*(.+)$", re.I)
# "İLETİŞİM : ..." (also misspelt "İLTİŞİM"), an e-mail line, or a ": value" continuation.
CONTACT_RE = re.compile(r"^\s*(İLE?TİŞİM|E-?MAIL|MAIL)\b|^\s*:|^\s*\S+@\S+\s*$", re.I)
TR_ASCII = str.maketrans("çğıöşüÇĞİÖŞÜâîûÂÎÛ", "cgiosuCGIOSUaiuAIU")
RUNNING_TOP = 0.08  # a section label sits at the very top of the page (bbox y0, page = 1)
RUNNING_PAGES = 3   # ... and repeats on this many pages of the issue
# A name alone on an article's first line: "CAN ÖZTÜRK", "Mehmet Ali Şahin", "A. Vedat Hallaç".
NAME_WORD = r"(?:[A-ZÇĞİÖŞÜ]\.|[A-ZÇĞİÖŞÜ][a-zçğıöşü]+|[A-ZÇĞİÖŞÜ]{2,})"
NAME_RE = re.compile(rf"^\s*{NAME_WORD}(?:\s+{NAME_WORD}){{1,3}}\s*$")
NOT_NAMES = {"amiga", "commodore", "oyun", "program", "basic", "bu", "bir", "ve", "yeni", "sayi",
             "sayı", "evet", "merhaba", "giriş", "c64", "workbench", "dos", "the", "game", "over",
             "çok", "space", "operatör", "işlem", "için", "nasıl", "ne", "en", "son"}


def tidy_title(title: str) -> str:
    """Undo letter-spacing and stray spaces from re-typeset titles: "N E D İ R BU" → "NEDİR BU",
    "Ben , AMIGA'yım" → "Ben, AMIGA'yım"."""
    title = re.sub(r"\b(?:\w ){2,}\w\b", lambda m: m.group().replace(" ", ""), title)
    title = re.sub(r"\s+([,.:;!?])", r"\1", title)
    return re.sub(r"\s+", " ", title).strip()


def prose_tail(title: str) -> tuple[str, str]:
    """A title with body text run into it ("SCULPT / ANIMATE4D can sıkıcı kısmın sonu"): the
    title, and the prose (three or more words, mostly in lower case) that follows it."""
    ws = title.split()
    for i in range(1, len(ws) - 2):
        tail = ws[i:]
        if ws[i][:1].islower() and sum(w[:1].islower() for w in tail) > len(tail) / 2:
            return " ".join(ws[:i]), " ".join(tail)
    return title, ""


def is_byline(line: str) -> bool:
    lower = line.replace("İ", "i").replace("I", "ı").lower()  # Turkish casing: "BİR" → "bir"
    return bool(NAME_RE.match(line)) and len(line.strip()) <= 32 and not any(
        w.strip(".") in NOT_NAMES for w in lower.split())


def top_label(block: dict) -> str | None:
    """The compact text of a short one-line block at the very top of the page, else None."""
    text = (block.get("text") or "").strip()
    if (block.get("kind") == "figure" or "\n" in text or not text or len(text) > 30
            or len(text.split()) > 4 or block["bbox"][1] > RUNNING_TOP):
        return None
    return compact(text) or None


def slugify(text: str) -> str:
    text = text.translate(TR_ASCII)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "yazi"


def clean_text(lines: list[str]) -> str:
    """Join a paragraph's lines, rejoining words hyphenated at line ends."""
    out = ""
    for line in (l.strip() for l in lines):
        if not line:
            continue
        if re.search(r"\w-$", out) and line[:1].islower():
            out = out[:-1] + line
        elif re.search(r"[A-ZÇĞİÖŞÜ]{2}-$", out) and re.match(r"[A-ZÇĞİÖŞÜ]{2}", line):
            out = out + line  # "GHOST-" + "BUSTERS": a capitalised word keeps its hyphen, no space
        else:
            out = f"{out} {line}" if out else line
    return re.sub(r"\s+", " ", out).strip()


STOPWORDS = {"bir", "ile", "icin", "olan", "gibi", "daha", "cok", "ama", "var", "olarak",
              "sonra", "kadar", "her", "nasil", "the", "and", "sayfa", "sayi"}


def title_clusters(texts: list[dict], labels: "Labels") -> tuple[dict[str, str], set[str]]:
    """Headings set on several lines that OCR read as separate blocks ("LOGO" over
    "Öğreniyoruz (4)", "Kartuşlar ve" over "Hızlandırıcılar"): display-size blocks stacked
    right above or below a heading, in the same column, join it. Clear page furniture or ads
    (JEV ≥ 0.9, e.g. the masthead strip) never join.
    Returns {heading block id: full title} and the ids of the blocks absorbed into one."""
    def size(b):
        return b.get("rel_size") or 1

    def joinable(b, head):
        if b is head or size(b) < max(2.0, 0.6 * size(head)) or split_heading(b)[1]:
            return False  # (a block with body text of its own is not part of a title)
        a = labels.img.get(f"role_{b['id']}")
        if a and a["choice"] in DROP_ROLES and a["confidence"] >= 0.9:
            return False
        hx0, hy0, hx1, hy1 = head["bbox"]
        x0, y0, x1, y1 = b["bbox"]
        overlap = min(x1, hx1) - max(x0, hx0)
        return overlap > 0.3 * min(x1 - x0, hx1 - hx0)

    clusters, absorbed = {}, set()
    for head in texts:
        h = labels.heading(head["id"])
        if not h or h[0] != "article_title" or head["id"] in absorbed or size(head) < 2.0:
            continue
        parts = [head]
        grown = True
        while grown:
            grown = False
            top = min(p["bbox"][1] for p in parts)
            bottom = max(p["bbox"][3] for p in parts)
            gap = 0.6 * max(p["bbox"][3] - p["bbox"][1] for p in parts)
            for b in texts:
                if b in parts or b["id"] in absorbed or not joinable(b, head):
                    continue
                if -gap <= top - b["bbox"][3] <= gap or -gap <= b["bbox"][1] - bottom <= gap:
                    parts.append(b)
                    grown = True
        if len(parts) > 1:
            parts.sort(key=lambda p: p["bbox"][1])
            clusters[head["id"]] = " ".join(split_heading(p)[0] for p in parts)
            absorbed |= {p["id"] for p in parts if p is not head}
    return clusters, absorbed


SENTENCE_END = re.compile(r"[.!?:;…)\"”'’]\s*$")


def join_broken_paragraphs(body: list[dict]) -> list[dict]:
    """Merge paragraphs split at a column or page edge: the first ends in a hyphenated word
    ("oy-" + "namışınızdır") or mid-sentence, and the next starts in lower case. A figure or
    caption between the two halves moves after the merged paragraph. A word in capitals split
    the same way ("SI-" + "LENT SERVİCE") is joined too, without its hyphen when the article
    spells the word whole elsewhere. Scraps of two letters or less (a page corner's "İk") go."""
    whole = compact(" ".join(x.get("text", "") for x in body))
    out: list[dict] = []
    for x in body:
        if x["type"] in ("p", "h2") and len(re.findall(r"\w", x["text"])) <= 2:
            continue
        caps = x["type"] == "p" and re.match(r"([A-ZÇĞİÖŞÜ]{2,})", x["text"])
        if x["type"] == "p" and (x["text"][:1].islower() or caps):
            # The paragraph this one continues: the last paragraph, past figures and captions.
            j = len(out) - 1
            while j >= 0 and out[j]["type"] in ("figure", "caption"):
                j -= 1
            if caps:
                m = j >= 0 and out[j]["type"] == "p" and re.search(r"([A-ZÇĞİÖŞÜ]+)-$", out[j]["text"])
                if m:
                    prev = out[j]
                    joined = m.group(1) + caps.group(1)
                    keep = "" if whole.count(compact(joined)) > 1 else "-"
                    prev["text"] = prev["text"][:-1] + keep + x["text"]
                    continue
                out.append(x)
                continue
            # The paragraph this one continues: the last paragraph, past figures and captions.
            j = len(out) - 1
            while j >= 0 and out[j]["type"] in ("figure", "caption"):
                j -= 1
            if j >= 0 and out[j]["type"] == "p":
                prev = out[j]
                if re.search(r"\w-$", prev["text"]):
                    prev["text"] = prev["text"][:-1] + x["text"]
                    continue
                if not SENTENCE_END.search(prev["text"]):
                    prev["text"] = f"{prev['text']} {x['text']}"
                    continue
        out.append(x)
    return out


def words(text: str) -> set[str]:
    folded = text.translate(TR_ASCII).lower()
    # Bare numbers are left out: contents pages are full of page numbers, and they would
    # match any heading that has a number in it (a review box's "Zorluk 18").
    return {w for w in re.findall(r"[a-z0-9]+", folded)
            if len(w) >= 3 and not w.isdigit() and w not in STOPWORDS}


def compact(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.translate(TR_ASCII).lower())


def in_toc(title: str, toc: list[tuple[set[str], str]], strict: bool = False) -> bool:
    """A contents line and the heading share most of the heading's words, or (unless strict)
    most of the line's: "1541-II & PSU" lists "1541-II'NİN PC PSU'SUYLA ÇALIŞTIRILMASI".
    Spacing is ignored too: "C64ASM V1.1" lists "C64 ASM V1.1"."""
    w, c = words(title), compact(title)
    for line_words, line_compact in toc:
        shared = len(w & line_words)
        if w and shared and shared >= 0.6 * (len(w) if strict else min(len(w), len(line_words))):
            return True
        if (len(c) >= 5 and len(line_compact) >= 5 and (c in line_compact or line_compact in c)
                and min(len(c), len(line_compact)) >= 0.5 * max(len(c), len(line_compact))):
            return True
    return False


class Labels:
    """JEV answers for one page: the image run first, the text-only run as a second opinion."""

    def __init__(self, issue_dir: Path, page: int):
        def load(name):
            f = issue_dir / name / f"{page:03d}.json"
            return json.loads(f.read_text())["answers"] if f.exists() else {}
        self.img, self.txt = load("labels"), load("labels-text")

    def page_kind(self) -> str:
        a = self.img.get("page_kind") or self.txt.get("page_kind")
        return a["choice"] if a else "article"

    def page_kind_sure(self) -> bool:
        """The page kind may decide the fate of every block on the page only when JEV is
        sure, or the image and text-only runs agree."""
        a, t = self.img.get("page_kind"), self.txt.get("page_kind")
        return bool(a) and (a["confidence"] >= ACCEPT or (t is not None and t["choice"] == a["choice"]))

    def role(self, bid: str) -> tuple[str, bool]:
        """(role, uncertain)"""
        a, t = self.img.get(f"role_{bid}"), self.txt.get(f"role_{bid}")
        if not a:
            return ("content", True)
        if a["confidence"] >= ACCEPT or (t and t["choice"] == a["choice"]):
            return (a["choice"], False)
        # Unsure: keep it, using whichever opinion keeps the text.
        for choice in (a["choice"], t and t["choice"]):
            if choice and choice not in DROP_ROLES:
                return (choice, True)
        return ("content", True)

    def heading(self, bid: str) -> tuple[str, float] | None:
        a = self.img.get(f"heading_{bid}")
        return (a["choice"], a["confidence"]) if a else None


def crop_figure(doc: pymupdf.Document, page: int, bbox: list[float], out: Path) -> None:
    p = doc[page - 1]
    r = p.rect
    clip = pymupdf.Rect(bbox[0] * r.width, bbox[1] * r.height, bbox[2] * r.width, bbox[3] * r.height)
    pix = p.get_pixmap(dpi=FIGURE_DPI, clip=clip, alpha=False)
    out.parent.mkdir(parents=True, exist_ok=True)
    Image.frombytes("RGB", (pix.width, pix.height), pix.samples).save(out, "WEBP", quality=82)


def attach_drop_caps(blocks: list[dict]) -> list[dict]:
    """Prepend single-letter display blocks to the paragraph on their right."""
    caps = [b for b in blocks if len(b.get("text", "").strip()) == 1 and (b.get("rel_size") or 0) >= 2]
    for cap in caps:
        x0, y0, x1, y1 = cap["bbox"]
        right = [b for b in blocks if b is not cap and b.get("text")
                 and b["bbox"][0] >= x0 and b["bbox"][1] <= y1 and b["bbox"][3] >= y0]
        if right:
            target = min(right, key=lambda b: (b["bbox"][0] - x1) ** 2 + (b["bbox"][1] - y0) ** 2)
            lines = target["text"].split("\n")
            # The letter belongs to the paragraph, not to a byline above it ("CAN ÖZTÜRK" / "miga").
            at = 1 if len(lines) > 1 and is_byline(lines[0]) else 0
            lines[at] = cap["text"].strip() + lines[at]
            target["text"] = "\n".join(lines)
            cap["text"] = ""
    return [b for b in blocks if b.get("kind") == "figure" or b.get("text")]


def split_heading(block: dict) -> tuple[str, list[str]]:
    """A block's leading heading-size lines, and the body lines after them."""
    lines = block["text"].split("\n")
    sizes = block.get("line_rel_sizes")
    if not sizes:
        # Older OCR blocks only know their largest line. A short display block (a title set
        # on two or three lines: "Kartuşlar ve / Hızlandırıcılar") is all heading; in a longer
        # block only the first line can be, so a heading never swallows its paragraph.
        rel = block.get("rel_size") or 1
        short = len(lines) <= 3 and all(len(l.strip()) <= 40 for l in lines)
        sizes = [rel] * len(lines) if rel >= 2.0 and short else [rel] + [1.0] * (len(lines) - 1)
    big = max(sizes)
    if sizes[0] < HEADING_REL and big >= 2.0:
        # Re-typeset pages can put a display title in the middle of a text block ("içinde neler
        # olduğunu…" / "CORRUPTION" / "Borsa odasında…"): the display lines are the heading.
        head = [l for l, s in zip(lines, sizes) if s >= big * 0.8]
        return clean_text(head), [l for l, s in zip(lines, sizes) if s < big * 0.8]
    n = 0
    while n < len(lines) and (sizes[n] >= HEADING_REL or not lines[n].strip()):
        n += 1
    if n == 0:  # bold or slightly larger heading with no size jump: the whole block is the heading
        return clean_text(lines), []
    return clean_text(lines[:n]), lines[n:]


def similar(a: str, b: str, ratio: float = 0.9) -> bool:
    return SequenceMatcher(None, compact(a), compact(b)).ratio() >= ratio


def assemble(issue: str, segments: Path | None = None, out_dir: Path | None = None) -> None:
    issue_dir = ROOT / "data" / "issues" / issue
    out_dir = out_dir or issue_dir
    meta = json.loads((issue_dir / "issue.json").read_text())
    doc = pymupdf.open(meta["source"])
    articles: list[dict] = []
    pages: dict[int, dict] = {}
    current: dict | None = None

    def start(title: str, page: int) -> dict:
        nonlocal current
        title = tidy_title(title)
        # A title set in two blocks ("C64 İÇİN PC ÜZERİNDE" / "GELİŞTİRME") is one title.
        if current is not None and not current["body"] and current["pages"][-1] == page:
            current["title"] = f"{current['title']} {title}"
            return current
        current = {"title": title, "author": None, "pages": [page], "body": [], "review": 0}
        articles.append(current)
        return current

    # Words from the issue's contents pages: a heading JEV calls an article title but that
    # isn't listed there (and isn't at the top of a page) is a section heading.
    titles = {t["slug"]: t["name"] for t in yaml.safe_load((ROOT / "catalog" / "titles.yaml").read_text())}
    masthead = compact(titles.get(meta["slug"], meta["slug"]))
    toc: list[tuple[set[str], str]] = []
    for page in range(1, meta["pages"] + 1):
        if Labels(issue_dir, page).page_kind() == "contents" or page == 1:
            bfile = issue_dir / "blocks" / f"{page:03d}.json"
            if bfile.exists():
                for b in json.loads(bfile.read_text())["blocks"]:
                    text = b.get("text") or b.get("ocr_text") or ""
                    # Several entries can share a line, split by bullets that OCR reads as
                    # "e", "©", "®" or "•" ("ELVİRA e SPACE GUN © HELİ RESCUE").
                    for line in re.split(r"\n|\s(?:e|©|®|•|\*)\s", text):
                        # Contents entries are short; long lines are the editorial's prose.
                        # The masthead ("(OMMODORE" on a cover) is not an entry.
                        lc = compact(line)
                        if (w := words(line)) and len(w) <= 6 and not (lc and lc in masthead):
                            toc.append((w, lc))

    # Section labels repeated at the top of many pages ("GRAFİK", "MÜZİK") are page furniture:
    # they never start an article, even where JEV took one for a title.
    label_pages: dict[str, set[int]] = {}
    for page in range(1, meta["pages"] + 1):
        bfile = issue_dir / "blocks" / f"{page:03d}.json"
        for b in json.loads(bfile.read_text())["blocks"] if bfile.exists() else []:
            if lab := top_label(b):
                label_pages.setdefault(lab, set()).add(page)
    running = {lab for lab, pgs in label_pages.items() if len(pgs) >= RUNNING_PAGES}

    # The segmentation's marks: (page, block) → ("start" | "resume", article number).
    seg_file = segments or issue_dir / "segments.json"
    seg_articles = json.loads(seg_file.read_text())["articles"] if seg_file.exists() else []
    marks: dict[tuple[int, str], tuple[str, int]] = {}
    for n, a in enumerate(seg_articles):
        marks[(a["start"]["page"], a["start"]["block"])] = ("start", n)
        for r in a.get("resumes", []):
            marks.setdefault((r["page"], r["block"]), ("resume", n))
    # A page JEV is sure is an ad or a cover is kept when the segmentation starts or resumes an
    # article on it (an opener page, a review above an ad, a news column running onto an ad page);
    # classifieds and the subscribers' dealer list stay ads.
    seg_starts = {m["page"] for a in seg_articles
                  if not re.search(r"(?i)küçük ilan|abonelerine tenzilat", a["title"])
                  for m in [a["start"], *a.get("resumes", [])]}
    made: dict[int, dict] = {}

    def follow(mark: tuple[str, int], page: int) -> str | None:
        """Start or resume the segmented article a mark names; the title of a new start."""
        nonlocal current
        what, n = mark
        if what == "start":
            a = seg_articles[n]
            current = made[n] = {"title": tidy_title(a["title"]), "author": a.get("author") or None,
                                 "pages": [page], "body": [], "review": 0}
            articles.append(current)
            return current["title"]
        if n in made:
            current = made[n]
        return None

    for page in range(1, meta["pages"] + 1):
        labels = Labels(issue_dir, page)
        kind = labels.page_kind()
        if page == 1 and kind in ("contents", "other"):
            kind = "cover"
        elif kind in ("cover", "ads") and not labels.page_kind_sure():
            kind = "mixed"  # not sure: let the block-level roles decide what is kept
        if kind in ("cover", "ads") and page in seg_starts:
            kind = "mixed"
        bfile = issue_dir / "blocks" / f"{page:03d}.json"
        blocks = json.loads(bfile.read_text())["blocks"] if bfile.exists() else []
        pages[page] = {"kind": kind, "text": [], "articles": []}
        if (mark := marks.get((page, "page"))) and not blocks:
            # A page without any text (a map, a full-page title picture) that the segmentation
            # starts or continues an article on: the whole page is that article's picture.
            follow(mark, page)
            name = f"p{page:03d}-page.webp"
            crop_figure(doc, page, [0, 0, 1, 1], out_dir / "figures" / name)
            current["body"].append({"type": "figure", "src": name, "page": page})
            if page not in current["pages"]:
                current["pages"].append(page)
            pages[page]["kind"] = "article"
            continue
        if kind in ("cover", "ads"):
            continue

        blocks = attach_drop_caps(blocks)
        texts = [b for b in blocks if b["kind"] != "figure"]
        figures = [b for b in blocks if b["kind"] == "figure"]
        biggest = max((b.get("rel_size") or 1 for b in texts), default=1)

        # Place each figure before the first text block (in reading order) that sits below it
        # in the same column.
        placed: dict[str, list[dict]] = {}
        for fig in figures:
            below = [b for b in texts if b["bbox"][1] >= fig["bbox"][1] - 0.01
                     and b["bbox"][0] < fig["bbox"][2] and b["bbox"][2] > fig["bbox"][0]]
            key = min(below, key=lambda b: b["order"])["id"] if below else "_end"
            placed.setdefault(key, []).append(fig)

        def add_figures(key: str) -> None:
            for fig in placed.get(key, []):
                # A picture whose text is a contents entry is a WordArt article title.
                title = fig.get("ocr_text", "")
                # WordArt titles are set in capitals; logos ("commoDore") are not.
                if (toc and not seg_articles and 3 <= len(title) <= 40 and title == title.upper()
                        and in_toc(title, toc, strict=True)):
                    start(title, page)
                    continue
                name = f"p{page:03d}-{fig['id']}.webp"
                crop_figure(doc, page, fig["bbox"], out_dir / "figures" / name)
                if current is not None:
                    current["body"].append({"type": "figure", "src": name, "page": page})

        contents_title = None
        if kind == "contents" and not seg_articles:
            heads =[b for b in texts if (h := labels.heading(b["id"])) and h[0] != "not_heading"
                     and labels.role(b["id"])[0] == "content"]
            if heads:
                head = max(heads, key=lambda b: b.get("rel_size") or 1)
                contents_title = head["id"]
                # The whole contents page is one article, named by its largest heading.
                start(split_heading(head)[0], page)

        clusters, absorbed = title_clusters(texts, labels)

        for b in texts:
            seg_title = None
            if mark := marks.get((page, b["id"])):
                seg_title = follow(mark, page)
            add_figures(b["id"])
            if b["id"] in absorbed:
                continue  # part of a heading set on several lines, used with that heading
            role, unsure = labels.role(b["id"])
            if b["id"] in clusters:
                role, unsure = "content", False
            if role in DROP_ROLES or top_label(b) in running:
                continue
            pages[page]["text"].append(b["text"])
            heading = labels.heading(b["id"])
            body_lines = b["text"].split("\n")

            if seg_articles:
                # The article's own title was read again (segments.json): drop its OCR reading
                # when it is display type or the same words; a title that opens a sentence
                # ("Kreator'ın yeni albümü...") stays. Other headings are section headings.
                # A "heading" is only its title words: prose run into it (a paragraph set large)
                # goes back to the body, and one of more than 12 words is body text.
                is_head = heading and heading[0] != "not_heading" and role == "content"
                if is_head:
                    h, rest = split_heading(b)
                    h, tail = prose_tail(tidy_title(clusters.get(b["id"]) or h))
                    is_head = len(h.split()) <= 12 and not h[:1].islower()
                    if is_head:
                        rest = [tail, *rest] if tail else rest
                if seg_title is not None:
                    if ((is_head and heading[1] >= ACCEPT and (b.get("rel_size") or 1) >= 2.0)
                            or similar(h if is_head else body_lines[0], seg_title)):
                        body_lines = rest if is_head else body_lines[1:]
                elif is_head:
                    if current is None:
                        start("Giriş", page)
                    current["body"].append({"type": "h2", "text": h, "page": page})
                    body_lines = rest
                heading = None

            if heading and heading[0] != "not_heading" and role == "content":
                title, body_lines = split_heading(b)
                if b["id"] in clusters:
                    title = clusters[b["id"]]
                title, tail = prose_tail(title)
                if tail:
                    body_lines = [tail, *body_lines]
            if (heading and heading[0] != "not_heading" and role == "content" and title[:1].islower()
                    and sum(w[:1].islower() for w in title.split()) > len(title.split()) / 2):
                # Running text set large by a re-typesetting ("içinde neler olduğunu…"), not a title.
                heading, body_lines = None, b["text"].split("\n")
            if heading and heading[0] != "not_heading" and role == "content":
                starts = (
                    b["id"] == contents_title if kind == "contents"
                    else heading[0] == "article_title" and (
                        # Listed in the contents: trust it even when JEV was unsure.
                        in_toc(title, toc)
                        # Not listed: if the issue has no contents to check against, or JEV is
                        # sure and it is set in display type (contents pages often list sections
                        # or highlights only).
                        or (not toc and (heading[1] >= ACCEPT or (b.get("rel_size") or 1) >= max(2.0, biggest)))
                        or (heading[1] >= 0.9 and (b.get("rel_size") or 1) >= 2.0)
                        # Display type Vision read cleanly (display.py) can be trusted a bit sooner.
                        or (b.get("display") == "vision" and heading[1] >= 0.8 and (b.get("rel_size") or 1) >= 4.0))
                )
                if b["id"] == contents_title:
                    pass  # already started at the top of the contents page
                elif starts or current is None:
                    start(title, page)
                else:
                    current["body"].append({"type": "h2", "text": tidy_title(title), "page": page})
            elif current is None:
                start("İçindekiler" if kind == "contents" else "Giriş", page)

            if page not in current["pages"]:
                current["pages"].append(page)
            if unsure:
                current["review"] += 1
            if not body_lines:
                continue
            first = not any(x["type"] == "p" for x in current["body"])
            if seg_articles:
                # The segmentation decided the author (or that there is none); its printed
                # byline at the start is metadata.
                if first and current["author"] and similar(body_lines[0], current["author"], 0.8):
                    body_lines = body_lines[1:]
                    if not body_lines:
                        continue
            elif current["author"] is None and role == "content" and first and is_byline(body_lines[0]):
                # A name alone at the start of an article, as its paragraph's first line or as a
                # block of its own ("Cem Tezer" under the title), is the byline.
                current["author"] = " ".join(body_lines[0].split())
                body_lines = body_lines[1:]
                if not body_lines:
                    continue
            m = BYLINE_RE.match(body_lines[0])
            if m:
                if not seg_articles:
                    current["author"] = current["author"] or m.group(2).strip()
                # The byline and its contact lines are metadata, not article text.
                while body_lines and (BYLINE_RE.match(body_lines[0]) or CONTACT_RE.match(body_lines[0])):
                    body_lines = body_lines[1:]
                if not body_lines:
                    continue
            if role == "listing":
                current["body"].append({"type": "pre", "text": "\n".join(body_lines).rstrip(), "page": page})
            elif role == "caption":
                current["body"].append({"type": "caption", "text": clean_text(body_lines), "page": page})
            else:
                text = clean_text(body_lines)
                if text:
                    current["body"].append({"type": "p", "text": text, "page": page})
        add_figures("_end")

    # Slugs, and which articles each page belongs to.
    seen: dict[str, int] = {}
    for a in articles:
        base = slugify(a["title"])
        seen[base] = seen.get(base, 0) + 1
        a["slug"] = base if seen[base] == 1 else f"{base}-{seen[base]}"
        for p in a["pages"]:
            pages[p]["articles"].append(a["slug"])
    for a in articles:
        a["body"] = join_broken_paragraphs(a["body"])
    # An article needs text, except one the segmentation started on a page of pictures only.
    page_starts = {id(made[n]) for (p, blk), (what, n) in marks.items() if blk == "page" and what == "start" and n in made}
    articles = [a for a in articles if any(x["type"] in ("p", "pre") for x in a["body"])
                or (id(a) in page_starts and a["body"])]

    write_json(out_dir / "articles.json", articles, indent=1)
    write_json(out_dir / "pages.json", pages, indent=1)
    print(f"{issue}: {len(articles)} articles, "
          f"{sum(a['review'] for a in articles)} uncertain blocks kept")
    for a in articles:
        print(f"   p{a['pages'][0]:>3}-{a['pages'][-1]:<3} {a['title'][:50]:50s} {a['author'] or ''}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    for arg in sys.argv[1:]:
        assemble(arg)

"""Article text read from the page images by agent readers, in place of the Tesseract text.

Tesseract reads the Program Döküm Ekleri's prose badly: wrong letters, columns run together, and
bits of program listings and screen output taken for paragraphs. Haiku agents read the pages
instead (a workflow, outside this script) and write read/pNNN.prose.txt: the page's paragraphs
in reading order, blank lines between them, and "## <title>" where a program article's title is
printed (any other heading as "### <heading>"). assemble.py then takes each article's text from
these files (apply).

prep <slug>/<NN> ...   read/pNNN-page.png, each page of an article at a size the reader takes in
                       whole; prints the pages as "NN|page,page,...".

Usage:  prose.py prep <slug>/<NN> [...]
"""

import re
import sys
from pathlib import Path

import pymupdf
from rapidfuzz import fuzz

sys.path.insert(0, str(Path(__file__).parent))
from fsutil import read_json  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DPI = 134  # an A4 page about 1560 pixels high: what the reader sees without scaling it down
TITLE = re.compile(r"^##\s+(.+)$")
HEADING = re.compile(r"^###\s+(.+)$")


def prep(issue: str) -> None:
    d = ROOT / "data" / "issues" / issue
    doc = pymupdf.open(ROOT / read_json(d / "issue.json")["source"])
    pages = sorted({p for a in read_json(d / "articles.json") or [] for p in a["pages"]})
    for p in pages:
        doc[p - 1].get_pixmap(dpi=DPI).save(d / "read" / f"p{p:03d}-page.png")
    print(f"{issue.split('/')[1]}|{','.join(map(str, pages))}")


def apply(issue_dir: Path, articles: list[dict]) -> None:
    """Each article's body from the readers' pages, where every page of it was read: the
    paragraphs from its title on to the next article's title. Titles are matched loosely
    (the readers and the segmentation spell them a little differently)."""
    files = {int(f.name[1:4]): f for f in (issue_dir / "read").glob("p*.prose.txt")}
    if not files:
        return
    order = sorted(range(len(articles)), key=lambda i: articles[i]["pages"][0])
    bodies: dict[int, list[dict]] = {i: [] for i in order}
    current = None
    for page in sorted(files):
        if current is not None and page not in articles[current]["pages"]:
            current = None
        if current is None:  # a page that goes on with an article begun earlier, or starts one
            current = next((i for i in order if page in articles[i]["pages"] and articles[i]["pages"][0] < page),
                           next((i for i in order if articles[i]["pages"][0] == page), None))
        for para in re.split(r"\n\s*\n", files[page].read_text()):
            para = para.strip()
            if not para:
                continue
            if m := TITLE.match(para.split("\n")[0]):
                title = m.group(1).strip()
                here = [i for i in order if page in articles[i]["pages"]]
                best = max(here, key=lambda i: fuzz.token_set_ratio(title.lower(), articles[i]["title"].lower()), default=None)
                if best is not None and fuzz.token_set_ratio(title.lower(), articles[best]["title"].lower()) >= 70:
                    current = best
                    para = "\n".join(para.split("\n")[1:]).strip()
                    if not para:
                        continue
                else:
                    para = "### " + para[2:].lstrip("#").strip()
            if current is None:
                continue
            if m := HEADING.match(para):
                bodies[current].append({"type": "h2", "text": m.group(1).strip(), "page": page})
            else:
                bodies[current].append({"type": "p", "text": " ".join(para.split()), "page": page})
    for i, body in bodies.items():
        a = articles[i]
        if all(p in files for p in a["pages"]):  # none at all: a page of listings only
            figures = [x for x in a["body"] if x["type"] not in ("p", "h2", "caption", "pre")]
            a["body"] = body + figures
            a["read"] = True


if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[1] != "prep":
        raise SystemExit(__doc__)
    for issue in sys.argv[2:]:
        prep(issue)

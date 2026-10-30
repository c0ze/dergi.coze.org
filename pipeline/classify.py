"""Phase 2: label each page and each text block with JEV (TypeSafe System One).

One request per page. The state holds the page's blocks (kind, position, size, text) and,
unless --text-only, the page image with each block's number drawn on it, so the model can
see which region each block is. Questions:
  page_kind          what the page is (article, ads only, mixed, listing, ...)
  role_<block id>    what each text block is (content, ad, furniture, caption, listing)
  heading_<block id> for large or bold short blocks: article title, section heading, or neither

Output: data/issues/<slug>/<NN>/labels/NNN.json with the chosen labels, probabilities,
confidence, model id and token usage. Existing label files are skipped unless --force.

Needs TYPESAFE_API_KEY in the environment (or in .env at the repo root).
Usage: classify.py <slug>/<NN> [<slug>/<NN> ...] [--text-only] [--force] [--pages 2,5]
"""

import argparse
import hashlib
import io
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml
from PIL import Image as PILImage, ImageDraw, ImageFont
from typesafe_sdk import Choice, Image, TypeSafeClient

sys.path.insert(0, str(Path(__file__).parent))
from fsutil import read_json, write_json  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
# Pinned so tuned thresholds don't move when jev-latest does.
MODEL = "jev-1.14.0"
IMAGE_SIDE = 1568  # the API's maximum width/height
TEXT_LIMIT = 600   # characters of each block's text sent in the state

PAGE_KINDS = {
    "cover": "The magazine's front cover.",
    "contents": "Table of contents, imprint, or editor's page.",
    "article": "Editorial content only: articles, reviews, news, letters, tips.",
    "listing": "Mostly program code or a program listing.",
    "ads": "Only advertisements, including the magazine's own subscription or issue promotions.",
    "mixed": "Both editorial content and one or more advertisements.",
    "other": "Blank, index, back cover without an ad, or anything else.",
}
BLOCK_ROLES = {
    "content": "Part of an article, review, news item, letter, editorial or table of contents.",
    "ad": "Part of an advertisement: product, shop, service, event, or subscription promotion. "
          "Prices, phone numbers, addresses and brand slogans are typical.",
    "furniture": "Repeated page furniture: running header or footer, page number, magazine name strip.",
    "caption": "A caption or label describing a picture or screenshot.",
    "listing": "Program source code, a hex dump or a type-in listing.",
}

HEADING_KINDS = {
    "article_title": "The title of an article, review, column or section that starts here "
                     "(often the largest text, sometimes with an author line nearby).",
    "section_heading": "A sub-heading inside an article that continues from before.",
    "not_heading": "Not a heading: a magazine name, page header, pull quote, label or body text.",
}

PHONE_RE =re.compile(r"\b(?:tel|telefon|fax|faks)\b|\(\s*\d{3}\s*\)\s*\d|\b\d{3}[ .]\d{2}[ .]\d{2}\b", re.I)
PRICE_RE = re.compile(r"\b\d[\d.,]*\s*(?:TL|₺|lira|DM|\$)|\bfiyat", re.I)
WEB_RE = re.compile(r"www\.|@\w+\.\w+|https?://", re.I)


def load_env() -> None:
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            key, _, value = line.partition("=")
            if key.strip() and value and key.strip() not in os.environ:
                os.environ[key.strip()] = value.strip().strip('"')


def marked_page(page_img: Path, blocks: list[dict]) -> Image:
    """The page image, scaled to the API limit, with each text block's number boxed in red."""
    img = PILImage.open(page_img).convert("RGB")
    scale = IMAGE_SIDE / max(img.size)
    img = img.resize((round(img.width * scale), round(img.height * scale)), PILImage.LANCZOS)
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=22)
    for n, blk in enumerate(blocks, start=1):
        x0, y0, x1, y1 = (blk["bbox"][0] * img.width, blk["bbox"][1] * img.height,
                          blk["bbox"][2] * img.width, blk["bbox"][3] * img.height)
        draw.rectangle((x0, y0, x1, y1), outline=(220, 0, 0), width=2)
        draw.text((x0 + 3, y0 + 1), str(n), fill=(220, 0, 0), font=font, stroke_width=3, stroke_fill="white")
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return Image(buf.getvalue())


def state_for(meta: dict, page: int, blocks: list[dict], image: Image | None) -> dict:
    state = {
        "magazine": meta["title"],
        "issue": meta["number"],
        "date": meta.get("date"),
        "page": f"{page} of {meta['pages']}",
        "blocks": [{
            "n": n,
            "kind": b["kind"],
            "box": {"left": b["bbox"][0], "top": b["bbox"][1], "right": b["bbox"][2], "bottom": b["bbox"][3]},
            "relative_font_size": b.get("rel_size"),
            "monospace": b.get("mono", False),
            "signals": [s for s, rx in (("phone", PHONE_RE), ("price", PRICE_RE), ("web_or_email", WEB_RE))
                        if rx.search(b["text"])],
            "text": b["text"][:TEXT_LIMIT],
        } for n, b in enumerate(blocks, start=1)],
    }
    if image is not None:
        state["page_image"] = image
    return state


def is_heading_candidate(b: dict) -> bool:
    short = len(b["text"]) <= 80
    # OCR knows no bold: a short line or two in capitals is a title set at body size
    # ("DÜZLEM GEOMETRİDE AÇILAR VE ÖLÇÜLERİ" in Matematik Dünyası).
    words = b["text"].split()
    caps = (short and b["text"].count("\n") <= 1 and len(words) >= 2
            and sum(c.isalpha() for c in b["text"]) >= 8 and b["text"] == b["text"].upper())
    return b["kind"] == "title" or caps or (short and b.get("bold") and (b.get("rel_size") or 0) >= 1.15)


def questions_for(blocks: list[dict], with_image: bool) -> dict:
    where = " Block numbers are drawn on `page_image`." if with_image else ""
    qs = {"page_kind": Choice(instructions="What kind of magazine page is this?" + where, criteria=PAGE_KINDS)}
    for n, b in enumerate(blocks, start=1):
        qs[f"role_{b['id']}"] = Choice(
            instructions=f"What is block {n} (`blocks[{n - 1}]`) on this page?" + where,
            criteria=BLOCK_ROLES)
        if is_heading_candidate(b):
            qs[f"heading_{b['id']}"] = Choice(
                instructions=f"Is block {n} (`blocks[{n - 1}]`) a heading, and of what?" + where,
                criteria=HEADING_KINDS)
    return qs


def blocks_sha(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def classify_issue(client: TypeSafeClient, issue_dir: Path, args) -> None:
    meta = json.loads((issue_dir / "issue.json").read_text())
    titles = {t["slug"]: t["name"] for t in yaml.safe_load((ROOT / "catalog" / "titles.yaml").read_text())}
    meta["title"] = titles.get(meta["slug"], meta["slug"])
    out_dir = issue_dir / ("labels-text" if args.text_only else "labels")
    out_dir.mkdir(exist_ok=True)
    wanted = {int(p) for p in args.pages.split(",")} if args.pages else None

    def one(page: int) -> None:
        blocks_file = issue_dir / "blocks" / f"{page:03d}.json"
        out = out_dir / blocks_file.name
        blocks = ([b for b in json.loads(blocks_file.read_text())["blocks"] if b["kind"] != "figure"]
                  if blocks_file.exists() else [])
        # A page without text (a picture-only ad, say) can still be judged from its image.
        if not blocks and args.text_only:
            return
        image = None if args.text_only else marked_page(issue_dir / "pages" / f"{page:03d}.webp", blocks)
        resp = client.system_one(
            state=state_for(meta, page, blocks, image),
            questions=questions_for(blocks, image is not None),
            extra_headers={} if image is None else {"TypeSafe-Danger-Zone-Images": "true"},
        )
        answers = {}
        for key, ans in resp.choices.items():
            answers[key] = {"choice": ans.choice, "confidence": ans.confidence,
                            "probabilities": dict(ans.probabilities)}
        record = {"page": page, "model": resp.model, "with_image": image is not None,
                  "blocks_sha": blocks_sha(blocks_file), "usage": resp.usage.model_dump(),
                  "answers": answers}
        write_json(out, record, indent=1, default=str)
        roles = [a["choice"] for k, a in answers.items() if k.startswith("role_")]
        print(f"{issue_dir.relative_to(ROOT)} p{page:03d}: {answers['page_kind']['choice']:8s} "
              f"(conf {answers['page_kind']['confidence']:.2f})  ads={roles.count('ad')}/{len(roles)}", flush=True)

    def done(p: int) -> bool:
        label = read_json(out_dir / f"{p:03d}.json")
        if label is None:
            return False
        # Labels from before blocks_sha was recorded are trusted; newer ones must match.
        return "blocks_sha" not in label or label["blocks_sha"] == blocks_sha(issue_dir / "blocks" / f"{p:03d}.json")

    todo = [p for p in range(1, meta["pages"] + 1) if (not wanted or p in wanted) and (args.force or not done(p))]
    # Requests are independent; a few in flight at once keeps a 60-page issue to a minute.
    with ThreadPoolExecutor(args.jobs) as pool:
        list(pool.map(one, todo))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("issues", nargs="+", help="<slug>/<NN>, e.g. c64-turkiye/04")
    ap.add_argument("--text-only", action="store_true", help="send blocks only, no page image")
    ap.add_argument("--force", action="store_true", help="redo pages that already have labels")
    ap.add_argument("--pages", help="comma-separated page numbers")
    ap.add_argument("--jobs", type=int, default=4, help="requests in flight at once")
    args = ap.parse_args()
    load_env()
    if not os.environ.get("TYPESAFE_API_KEY"):
        raise SystemExit("TYPESAFE_API_KEY is not set (environment or .env)")
    with TypeSafeClient(model=MODEL) as client:
        for issue in args.issues:
            classify_issue(client, ROOT / "data" / "issues" / issue, args)


if __name__ == "__main__":
    main()

"""Phase 0: find every scan of every issue of a title, measure them, pick the best one.

Sources are described in catalog/sources.yaml. A scan can be a PDF, a CBR/CBZ or ZIP/RAR of
images, a DjVu file or a folder of JPGs. For each issue the candidates are measured (page
count, median scan size, text layer) and the best is the most complete one, then the
sharpest. Supplements (inserts) compete among themselves and are appended to their issue.
Files matching a title's `extras` (disk images) are listed with their issue.

The report goes to catalog/sources/<slug>.yaml. `--build` writes one PDF per issue for the
site pipeline to data/sources/<slug>/best/<Name> - NN (MM-YYYY).pdf; the personal archive
is built by archive.py from the same choices.

Usage: sources.py <slug>|all [--build]
"""

import argparse
import io
import re
import shutil
import statistics
import subprocess
from functools import cache
from pathlib import Path

import pymupdf
import yaml
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".gif"}
ARCHIVE_EXT = {".cbr", ".cbz", ".zip", ".rar"}
CACHE = ROOT / "data" / "cache" / "unpacked"
MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8,
          "sep": 9, "oct": 10, "nov": 11, "dec": 12}


def config() -> dict:
    return yaml.safe_load((ROOT / "catalog" / "sources.yaml").read_text())


def natural(p: Path) -> list:
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", p.name)]


def issue_date(cfg: dict, key) -> str | None:
    dates = cfg.get("dates") or {}
    if cfg.get("key") == "date":
        return key
    if key in dates:
        return dates[key]
    if "first" in dates and isinstance(key, int):
        year, month = dates["first"]
        m = year * 12 + month - 1 + key - dates.get("first_number", 1)
        return f"{m // 12}-{m % 12 + 1:02d}"
    return None


def parse_key(cfg: dict, raw: str):
    if raw in (cfg.get("numbers") or {}):
        return int(cfg["numbers"][raw])
    kind = cfg.get("key", "number")
    if kind == "date":  # "sept_1992" → "1992-09"
        mon, year = raw.lower().split("_")
        return f"{year}-{MONTHS[mon[:3]]:02d}"
    if kind == "volume":  # "01_02" → "1-2"
        vol, no = raw.split("_")
        return f"{int(vol)}-{int(no)}"
    if kind == "single":
        return 1
    return int(raw)


def key_of(cfg: dict, rel: Path):
    """The issue key from the nearest path part (file first, then its folders) that names one."""
    for part in reversed(rel.parts):
        for rx in cfg["number"]:
            if m := re.search(rx, part):
                return parse_key(cfg, m.group(1) if m.groups() else Path(part).stem)
    return None


def archive_members(path: Path) -> list[str]:
    out = subprocess.run(["lsar", str(path)], capture_output=True, text=True, check=True).stdout
    return out.splitlines()[1:]


@cache
def unpack(path: Path) -> Path:
    """An archive's contents, unpacked once into the cache."""
    out = CACHE / re.sub(r"[^\w.-]", "_", str(path.relative_to(ROOT)))
    if not out.exists():
        tmp = out.with_suffix(".tmp")
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        subprocess.run(["unar", "-q", "-D", "-o", str(tmp), str(path)], check=True)
        tmp.rename(out)
    return out


def image_files(folder: Path, recursive: bool) -> list[Path]:
    files = folder.rglob("*") if recursive else folder.iterdir()
    return sorted((f for f in files if f.suffix.lower() in IMAGE_EXT and f.is_file()), key=natural)


def kind_of(p: Path) -> str | None:
    s = p.suffix.lower()
    if s == ".pdf":
        return "pdf"
    if s == ".djvu":
        return "djvu"
    if s in ARCHIVE_EXT:
        members = archive_members(p)
        return "cbr" if any(Path(m).suffix.lower() in IMAGE_EXT for m in members) else None
    if p.is_dir() and image_files(p, recursive=False):
        return "jpg"
    return None


def find(cfg: dict) -> tuple[list[dict], list[dict]]:
    """(scan candidates, extras) for one title."""
    exclude = re.compile(cfg["exclude"]) if cfg.get("exclude") else None
    extras_rx = re.compile(cfg["extras"]) if cfg.get("extras") else None
    supps = cfg.get("supplements") or {}
    cands, extras = [], []
    roots = [(ROOT / r, False) for r in cfg["roots"]]
    if cfg.get("extras_root"):
        roots.append((ROOT / cfg["extras_root"], True))
    for root, extras_only in roots:
        for p in sorted(root.rglob("*") if not extras_only else root.iterdir()):
            if any(part.startswith(("@", ".")) or part == "PaxHeader" for part in p.parts):
                continue
            rel = p.relative_to(root)
            if exclude and exclude.search(str(rel)):
                continue
            if extras_rx and extras_rx.search(p.name) and p.is_file():
                extras.append({"path": p, "key": key_of(cfg, rel)})
                continue
            if extras_only:
                continue
            key = key_of(cfg, rel)
            if key is None:
                continue
            kind = kind_of(p)
            if not kind:
                continue
            supplement = next((n for n, rx in supps.items() if re.search(rx, str(rel))), None)
            remake = bool(cfg.get("remakes") and re.search(cfg["remakes"], p.name))
            cands.append({"path": p, "kind": kind, "key": key, "supplement": supplement, "remake": remake})
    # De-duplicate extras found from two roots.
    extras = list({str(e["path"]): e for e in extras}.values())
    return cands, extras


def page_images(c: dict) -> list[Path]:
    """Image files of an image-based candidate, in page order."""
    if c["kind"] == "jpg":
        return image_files(c["path"], recursive=False)
    return image_files(unpack(c["path"]), recursive=True)


def is_scan(page: pymupdf.Page) -> bool:
    """Most of the visible page is covered by images (one scan, or a scan stored as tiles)."""
    r = page.rect
    covered = sum((b := pymupdf.Rect(i["bbox"]) & r).width * b.height for i in page.get_image_info())
    return covered >= 0.6 * r.width * r.height


def visible_scan(page: pymupdf.Page) -> tuple[bytes, str]:
    """The page as a reader sees it, as an image: the stored scan when it fits the page, else
    a render of the visible area at the scan's own resolution.

    Some PDFs place one two-page spread on two pages and let each page show half of it
    (64'ler 15–39): reading the stored image would give both pages' content twice.
    """
    doc, r = page.parent, page.rect
    infos = page.get_image_info(xrefs=True)
    if infos:
        big = max(infos, key=lambda i: i["width"] * i["height"])
        x0, y0, x1, y1 = big["bbox"]
        inside = x0 >= r.x0 - 3 and y0 >= r.y0 - 3 and x1 <= r.x1 + 3 and y1 <= r.y1 + 3
        covers = (x1 - x0) * (y1 - y0) >= 0.9 * r.width * r.height
        a, b, c, d = big["transform"][:4]
        upright = abs(b) < 1e-3 and abs(c) < 1e-3 and a > 0 and d > 0 and page.rotation == 0
        if len(infos) == 1 and inside and covers and upright and big["xref"]:
            img = doc.extract_image(big["xref"])
            if img["ext"] in ("jpeg", "jpg", "png"):
                return img["image"], img["ext"]
        dpi = min(600, max(150, 72 * big["width"] / max(1, x1 - x0)))
    else:
        dpi = 300
    pix = page.get_pixmap(dpi=round(dpi), alpha=False)
    return pix.tobytes("jpeg", jpg_quality=92), "jpeg"


def djvu_images(path: Path) -> list[Path]:
    """A DjVu file's pages as PNG files (lossless) in the unpack cache."""
    out = CACHE / re.sub(r"[^\w.-]", "_", str(path.relative_to(ROOT)))
    if not out.exists():
        tmp = out.with_suffix(".tmp")
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        for n in range(1, djvu_pages(path) + 1):
            ppm = tmp / f"{n:04d}.ppm"
            subprocess.run(["ddjvu", "-format=ppm", f"-page={n}", str(path), str(ppm)], check=True)
            Image.open(ppm).save(tmp / f"{n:04d}.png")
            ppm.unlink()
        tmp.rename(out)
    return sorted(out.glob("*.png"))


def djvu_pages(path: Path) -> int:
    return int(subprocess.run(["djvused", "-e", "n", str(path)], capture_output=True, text=True, check=True).stdout)


def measure(c: dict) -> None:
    c["text"] = 0
    if c["kind"] == "pdf":
        doc = pymupdf.open(c["path"])
        c["pages"] = doc.page_count
        sides = []
        for i in range(0, doc.page_count, max(1, doc.page_count // 6)):
            page = doc[i]
            # Effective resolution, not the largest image: some PDFs store a page as many
            # tiles. Image pixels per point², scaled to the page's long side.
            pixels = sum(im[2] * im[3] for im in page.get_images())
            area = page.rect.width * page.rect.height
            sides.append(int(max(page.rect.width, page.rect.height) * (pixels / area) ** 0.5) if area else 0)
            c["text"] += len(page.get_text().strip())
        c["px"] = int(statistics.median(sides)) if sides else 0
        # Born-digital: real text and pages that aren't covered by scans (a scan with
        # someone's OCR layer is still a scan).
        sampled = [doc[i] for i in range(0, doc.page_count, max(1, doc.page_count // 6))]
        c["digital"] = c["text"] > 500 * len(sides) and sum(map(is_scan, sampled)) < len(sampled) / 2
    elif c["kind"] == "djvu":
        c["pages"] = djvu_pages(c["path"])
        out = subprocess.run(["djvused", "-e", "select 1; size", str(c["path"])],
                             capture_output=True, text=True, check=True).stdout
        w, h = (int(v) for v in re.findall(r"=(\d+)", out)[:2])
        c["px"] = max(w, h)
        c["digital"] = False
    else:
        files = page_images(c)
        c["pages"] = len(files)
        sample = files[:: max(1, len(files) // 6)]
        c["px"] = int(statistics.median(max(Image.open(f).size) for f in sample)) if sample else 0
        c["digital"] = False


def pick(cands: list[dict]) -> dict:
    """A real scan beats a re-typeset remake; then the most complete copy (a copy with fewer
    pages may be missing one, so it never wins on sharpness alone — see shorter_sharper);
    then a born-digital PDF beats any scan; then the largest scans."""
    real = [c for c in cands if not c.get("remake")] or cands
    most = max(c["pages"] for c in real)
    complete = [c for c in real if c["pages"] == most]
    return max(complete, key=lambda c: (c.get("digital", False), c["px"], c["kind"] == "pdf"))


def shorter_sharper(chosen: dict, alts: list[dict]) -> list[dict]:
    """Alternatives that are clearly sharper but have fewer pages: worth a look by hand."""
    return [a for a in alts if not a.get("remake") and a["px"] > 1.3 * chosen["px"] and a["pages"] < chosen["pages"]]


def choose(slug: str, cfg: dict) -> dict:
    """{key: {date, chosen, supplements, alternatives, extras}} with candidate dicts."""
    cands, extras = find(cfg)
    for c in cands:
        measure(c)
    keys = sorted({c["key"] for c in cands} | {e["key"] for e in extras if e["key"] is not None},
                  key=lambda k: (str(type(k)), k))
    plan = {}
    for key in keys:
        mains = [c for c in cands if c["key"] == key and not c["supplement"]]
        supps = [c for c in cands if c["key"] == key and c["supplement"]]
        best = pick(mains) if mains else None
        plan[key] = {
            "date": issue_date(cfg, key),
            "chosen": best,
            "supplements": [pick([c for c in supps if c["supplement"] == n])
                            for n in sorted({c["supplement"] for c in supps})],
            "alternatives": [c for c in mains if c is not best],
            # A remake that lost to a real scan still has clean text: keep it alongside.
            "extras": [e for e in extras if e["key"] == key]
                      + [{"path": c["path"], "key": key, "remake": True}
                         for c in mains if c is not best and c.get("remake")],
        }
    loose = [e for e in extras if e["key"] is None]
    if loose:
        plan["_title"] = {"date": None, "chosen": None, "supplements": [], "alternatives": [], "extras": loose}
    return plan


def report_row(c: dict) -> dict:
    return {"path": str(c["path"].relative_to(ROOT)), "kind": c["kind"], "pages": c.get("pages"),
            "px": c.get("px"), **({"digital": True} if c.get("digital") else {}),
            **({"remake": True} if c.get("remake") else {})}


def write_report(slug: str, plan: dict) -> None:
    out = {}
    for key, p in plan.items():
        out[key] = {
            "date": p["date"],
            "chosen": report_row(p["chosen"]) if p["chosen"] else None,
            **({"review": "a sharper copy has fewer pages: " + ", ".join(
                f"{a['path'].name} ({a['pages']} p, {a['px']} px)" for a in sh)}
               if p["chosen"] and (sh := shorter_sharper(p["chosen"], p["alternatives"])) else {}),
            "supplements": [report_row(c) for c in p["supplements"]],
            "alternatives": [report_row(c) for c in p["alternatives"]],
            "extras": [str(e["path"].relative_to(ROOT)) for e in p["extras"]],
        }
    (ROOT / "catalog" / "sources").mkdir(exist_ok=True)
    (ROOT / "catalog" / "sources" / f"{slug}.yaml").write_text(yaml.safe_dump(out, allow_unicode=True, sort_keys=False))


def append_images(doc: pymupdf.Document, files: list[Path], turn_odd: bool = False) -> None:
    for n, f in enumerate(files, 1):
        w, h = Image.open(f).size
        # 300 dpi page size; the image goes in as stored, without re-encoding (turned if need be).
        page = doc.new_page(width=w * 72 / 300, height=h * 72 / 300)
        page.insert_image(page.rect, stream=f.read_bytes(), rotate=180 if turn_odd and n % 2 else 0)


def insert_pages(out: Path, inserts: list[dict]) -> None:
    """Pages the chosen copy lacks, taken from another one: {after: page in out, from: archive,
    pages: [page numbers in it]}. Later inserts count the pages added before them."""
    doc = pymupdf.open(out)
    for ins in inserts:
        files = image_files(unpack(ROOT / ins["from"]), recursive=True)
        add = pymupdf.open()
        append_images(add, [files[n - 1] for n in ins["pages"]])
        doc.insert_pdf(add, start_at=ins["after"])
    tmp = out.with_suffix(".tmp.pdf")
    doc.save(tmp, garbage=3, deflate=True)
    tmp.replace(out)


def split_spreads(files: list[Path], work: Path) -> list[Path]:
    """Two-page spreads cut into single pages, in reading order. The first spread is the
    back cover (left) and the front cover (right): the front opens the issue, the back ends it."""
    work.mkdir(parents=True, exist_ok=True)
    pages, back = [], None
    for i, f in enumerate(files):
        img = Image.open(f).convert("RGB")
        w, h = img.size
        if w < 1.2 * h:  # already a single page
            pages.append(f)
            continue
        halves = []
        for side, box in (("a", (0, 0, w // 2, h)), ("b", (w // 2, 0, w, h))):
            out = work / f"{f.stem}-{side}.jpg"
            img.crop(box).save(out, quality=92)
            halves.append(out)
        if i == 0:
            pages.append(halves[1])
            back = halves[0]
        else:
            pages.extend(halves)
    return pages + ([back] if back else [])


def join_strips(src: Path) -> pymupdf.Document:
    """A PDF whose scanner cut some pages into horizontal strips (one strip per PDF page, wider
    than tall, all the same width): runs of such strips stacked back into one page each."""
    doc, out = pymupdf.open(src), pymupdf.open()
    run: list[Image.Image] = []

    def flush() -> None:
        if not run:
            return
        page = Image.new("RGB", (run[0].width, sum(i.height for i in run)), "white")
        y = 0
        for i in run:
            page.paste(i, (0, y))
            y += i.height
        buf = io.BytesIO()
        page.save(buf, "JPEG", quality=92)
        p = out.new_page(width=page.width * 72 / 300, height=page.height * 72 / 300)
        p.insert_image(p.rect, stream=buf.getvalue())
        run.clear()

    for n, page in enumerate(doc):
        r = page.rect
        if r.width > 1.2 * r.height and (not run or round(r.width) == round(doc[n - 1].rect.width)):
            # At the embedded scan's own resolution: no resampling.
            image_width = max((i[2] for i in page.get_images()), default=round(r.width))
            scale = image_width / r.width
            pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
            run.append(Image.frombytes("RGB", (pix.width, pix.height), pix.samples))
            continue
        flush()
        out.insert_pdf(doc, from_page=n, to_page=n)
    flush()
    return out


def newest(path: Path) -> float:
    """Modification time of a scan: a file, or the newest file in a folder of page images."""
    return max([path.stat().st_mtime, *(f.stat().st_mtime for f in path.iterdir())]) if path.is_dir() else path.stat().st_mtime


def build_best(slug: str, cfg: dict, plan: dict) -> None:
    """One PDF per issue for the site pipeline: the chosen scan plus its supplements."""
    best_dir = ROOT / "data" / "sources" / slug / "best"
    best_dir.mkdir(parents=True, exist_ok=True)
    for key, p in plan.items():
        if not p["chosen"] or not isinstance(key, int):
            continue
        date = p["date"]
        stamp = (f" ({date[5:7]}-{date[:4]})" if len(date) > 4 else f" ({date})") if date else ""
        out = best_dir / f"{cfg['name']} - {key:02d}{stamp}.pdf"
        parts = [p["chosen"], *p["supplements"]]
        # Rebuilding is not byte-identical, and a new hash makes normalize.py drop the issue's
        # OCR and labels: keep a best PDF that is newer than every scan it was made from.
        inserts = (cfg.get("inserts") or {}).get(key, [])
        made_from = [Path(c["path"]) for c in parts] + [ROOT / i["from"] for i in inserts]
        if out.exists() and out.stat().st_mtime > max(newest(f) for f in made_from):
            continue
        if len(parts) == 1 and parts[0]["kind"] == "pdf" and cfg.get("join_strips"):
            join_strips(parts[0]["path"]).save(out, garbage=3, deflate=True)
        elif len(parts) == 1 and parts[0]["kind"] == "pdf":
            shutil.copy2(parts[0]["path"], out)
        else:
            build_doc(cfg, key, parts, best_dir).save(out, garbage=3, deflate=True)
        if inserts:
            insert_pages(out, inserts)


def build_doc(cfg: dict, key: int, parts: list[dict], best_dir: Path) -> pymupdf.Document:
    """The scan and its supplements as one PDF, from PDF, DjVu or image candidates."""
    doc = pymupdf.open()
    for c in parts:
        if c["kind"] == "pdf":
            doc.insert_pdf(pymupdf.open(c["path"]))
        elif c["kind"] == "djvu":
            append_images(doc, djvu_images(c["path"]))
        elif cfg.get("spreads"):
            append_images(doc, split_spreads(page_images(c), best_dir / ".pages" / f"{key:02d}"))
        else:
            append_images(doc, page_images(c), bool(cfg.get("turn_odd") and re.search(cfg["turn_odd"], c["path"].name)))
    return doc


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("slug")
    ap.add_argument("--build", action="store_true", help="write the chosen PDFs to data/sources/<slug>/best/")
    args = ap.parse_args()
    cfgs = config()
    for slug in (cfgs if args.slug == "all" else [args.slug]):
        cfg = cfgs[slug]
        plan = choose(slug, cfg)
        write_report(slug, plan)
        n = sum(1 for p in plan.values() if p["chosen"])
        alts = sum(len(p["alternatives"]) for p in plan.values())
        print(f"{slug}: {n} issues, {alts} alternative scans, "
              f"{sum(len(p['supplements']) for p in plan.values())} supplements, "
              f"{sum(len(p['extras']) for p in plan.values())} extras")
        if args.build:
            build_best(slug, cfg, plan)


if __name__ == "__main__":
    main()

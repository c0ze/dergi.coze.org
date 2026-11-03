"""Program listings on scanned pages → lines checked by their printed checksums.

The Program Döküm Ekleri print BASIC (with BDE's "<NNN>") and machine code (MDE's
"AAAA : 8 bytes  CC") on a faint dot-matrix printer that Tesseract cannot read. Haiku agents
read them instead (a workflow, outside this script), and every line is checked (c64basic.py).

prep  <slug>/<NN> ...   images for the readers in <issue>/read/: the whole page small (pNNN.png,
                        for the program titles) and, on listing pages, overlapping crops at a
                        size the reader sees without scaling (pNNN-c<col>b<band>.png, columns
                        left to right, bands top to bottom); read/manifest.json lists them (and
                        each crop's band where a page is split, see prep).
                        A full-width listing that is two columns after all is cut at its
                        gutter (gutter).
merge <slug>/<NN> ...   the readers' pNNN.txt files → listings.json: {page: [line, ...]} in
                        reading order, each {text, kind (mde, bde, basic, other), key (address
                        or line number), ok (true/false/null), crop}; a line read twice where
                        crops overlap is kept once, the reading that passes winning.

A reader's file:  "TITLE: <title> | <author>" lines, then "== c0b0" and that crop's lines, ...
More readings of a page (a second reader's pNNN.r2.txt, rereadings pNNN.fix1.txt, ...) have the
same "== <crop>" sections. A failing line takes any reading of it that passes; failing that, the
one mix of two readings that passes (combine). pNNN.hand.txt holds lines read by eye (hand_read).
tasks <slug>/<NN> ...   read/pNNN.task.txt for each page with failing lines (by crop; BASIC
                        without checksums fails where it cannot be typed), for the
                        readers' next pass; prints the pages as "NN|page:fix-round,...".

Usage:  listings.py prep|merge|tasks <slug>/<NN> [...]
"""

import itertools
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np
import pymupdf
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
from c64basic import bde, crunch, mde  # noqa: E402
from fsutil import read_json, write_json  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

# "??" for a checksum the reader could not make out (22 SÜPER ETİKET prints them as reverse blocks).
MDE_LINE = re.compile(r"^([0-9A-F]{4})\s*[:;]\s*((?:[0-9A-F]{2}\s+){1,8})([0-9A-F]{2}|\?\?)$")
BDE_LINE = re.compile(r"^(\d{1,5}) ?(.*?)\s*<\s*(\d{3})\s*>$")
BASIC_LINE = re.compile(r"^(\d{1,5}) ?(\S.*)$")

# Page selection from the Tesseract text: enough lines that look like a listing.
LOOKS_MDE = re.compile(r"^\s*[0-9A-FOoDSBIl]{4}\s*[:;!1*i|]\s+\S\S\s")
LOOKS_BDE = re.compile(r"<\s*\d{3}\s*>")
LOOKS_BASIC = re.compile(r"^\s*\d{1,5}\s*[A-Z?]")
BANDS = ((0, .38), (.31, .69), (.62, 1))


HEADER_LINE = re.compile(r"^(?:PROGRAM\s*(?:ADI)?\s*[:;]|SEARCHING FOR\b|PROGRAM\s+\d+\s*$)", re.I)


def check(text: str) -> dict:
    t = text.strip()
    if m := MDE_LINE.match(t):
        data = bytes(int(x, 16) for x in m.group(2).split())
        addr = int(m.group(1), 16)
        return {"kind": "mde", "key": addr, "ok": None if m.group(3) == "??" else mde(addr, data) == int(m.group(3), 16)}
    if m := BDE_LINE.match(t):
        tokens = crunch(m.group(2))
        number, want = int(m.group(1)), int(m.group(3))
        if tokens is None:
            return {"kind": "bde", "key": number, "ok": False}
        # Five-digit line numbers start with 0; a misread first digit (61180) still checks out as 01180.
        for n in [number] + ([int(m.group(1)[1:])] if len(m.group(1)) == 5 and m.group(1)[0] in "689" else []):
            if n < 64000 and bde(n, tokens) == want:
                return {"kind": "bde", "key": n, "ok": True}
        return {"kind": "bde", "key": number, "ok": False}
    if m := BASIC_LINE.match(t):
        return {"kind": "basic", "key": int(m.group(1)), "ok": None}
    return {"kind": "other", "key": None, "ok": None}


def typable(text: str) -> bool:
    """A BASIC line without a checksum can still be wrong in a way that shows: a symbol the
    reader could not name ("<?>", "■") cannot be typed."""
    m = BASIC_LINE.match(text.strip())
    return not m or crunch(m.group(2)) is not None


def listing_layout(blocks: list[dict]) -> int:
    """0 for no listing, else the number of listing columns (1 or 2)."""
    n, wide = 0, False
    for b in blocks:
        lines = b["text"].split("\n")
        k = sum(bool(LOOKS_MDE.match(x)) or bool(LOOKS_BASIC.match(x)) for x in lines) + len(LOOKS_BDE.findall(b["text"]))
        n += k
        if k >= 3 and b["bbox"][2] - b["bbox"][0] > 0.6:
            wide = True
    return 0 if n < 4 else 1 if wide else 2


def ink(im: Image.Image) -> np.ndarray:
    """Share of dark pixels in each column of pixels."""
    a = np.asarray(im)
    return (a < np.median(a) - 45).mean(axis=0)


def gutter(im: Image.Image) -> tuple[float, float] | None:
    """A full-width listing page (or part of one) that is two columns after all: where the left
    column ends and the right one starts (fractions of the width), else None. Two columns have a blank
    gutter near the middle with print on both sides, and the right column's line starts
    (addresses, line numbers) look like the left one's in the column ink profile; a single column
    with its checksums out at the right edge has the gutter but not the look."""
    im = im.convert("L")
    w, h = im.size

    p = np.convolve(ink(im.crop((int(w * .05), int(h * .12), int(w * .95), int(h * .92)))), np.ones(7) / 7, "same")
    n = len(p)
    left, right = p[int(n * .05):int(n * .35)].mean(), p[int(n * .65):int(n * .95)].mean()
    if not (p[int(n * .4):int(n * .6)].min() < 0.2 * min(left, right) and min(left, right) > 0.02):
        return None
    x0, best = int(w * .03), (0.5, None)
    for y0, y1 in ((.12, .52), (.52, .92), (.12, .92)):  # either half may be prose or a picture
        p = np.convolve(ink(im.crop((x0, int(h * y0), int(w * .97), int(h * y1)))), np.ones(3) / 3, "same")
        n = len(p)
        on = p > 0.25 * np.percentile(p, 90)
        start, k = int(np.argmax(on)), int(n * .10)
        for x in range(int(n * .35), int(n * .68)):
            if on[x] and not on[x - 4:x].any() and (c := np.corrcoef(p[start:start + k], p[x:x + k])[0, 1]) > best[0]:
                end = x - 4
                while end > 0 and not on[end - 1]:
                    end -= 1
                best = (c, ((x0 + end) / w - .006, (x0 + x) / w + .006))
    return best[1]


def cut_pieces(spec, small: Image.Image) -> list[tuple]:
    """A hand cut from layout.json "cuts" as (column, band, x0, x1, y0, y1) pieces, fractions
    of the page: "1" one full-width column in the three bands; [y0, y1] two columns from y0 to
    y1, split where gutter finds it in that stretch (else the middle), in one band to three by
    its height; [y0, y1, gx] the same with the gutter at gx; [[c, b, x0, x1, y0, y1], ...] the
    pieces themselves (mixed pages)."""
    if spec == "1":
        return [(0, b, 0, 1, y0, y1) for b, (y0, y1) in enumerate(BANDS)]
    if isinstance(spec[0], list):
        return [tuple(p) for p in spec]
    y0, y1 = spec[:2]
    if len(spec) > 2:
        g = (spec[2] - .03, spec[2] + .03)
    else:
        g = gutter(small.crop((0, int(small.height * y0), small.width, int(small.height * y1)))) or (.47, .53)
    nb = 1 if y1 - y0 <= .4 else 2 if y1 - y0 <= .72 else 3
    step = (y1 - y0) / nb
    bands = [(max(y0, y0 + k * step - .035), min(y1, y0 + (k + 1) * step + .035)) for k in range(nb)]
    return [(c, b, x0, x1, a, z) for c, (x0, x1) in enumerate([(0, g[1]), (g[0], 1)]) for b, (a, z) in enumerate(bands)]


def prep(issue: str) -> None:
    """Page images and listing crops for the readers. Which pages hold listings comes from the
    Tesseract blocks (listing_layout); read/layout.json, when there, adds what that misses on
    faint or busy scans ("listing": pages to crop anyway, full width), pages printed sideways
    for their long lines ("rotate": {page: degrees clockwise to turn it upright}) and pages with a
    full-width listing above two columns ("split": {page: where the columns start, a fraction of
    the page height}): bands c0b0... down to there, then each column's own band below; "whole"
    lists full-width pages that gutter takes for two columns but are not (assembler source with
    its comments out to the right), "two" pages that are two columns although gutter misses them
    (columns over the whole page, in the usual three bands), and "cuts" ({page: spec}) cuts a page
    by hand (see cut_pieces)."""
    d = ROOT / "data" / "issues" / issue
    doc = pymupdf.open(ROOT / read_json(d / "issue.json")["source"])
    out = d / "read"
    out.mkdir(exist_ok=True)
    layout = read_json(out / "layout.json") or {}
    extra, rotate = set(layout.get("listing", [])), {int(k): v for k, v in layout.get("rotate", {}).items()}
    split = {int(k): v for k, v in layout.get("split", {}).items()}
    whole, two = set(layout.get("whole", [])), set(layout.get("two", []))
    cuts = {int(k): v for k, v in layout.get("cuts", {}).items()}
    manifest = []
    for f in sorted((d / "blocks").glob("*.json")):
        pno = int(f.stem)
        page = doc[pno - 1]
        r = page.rect
        page.get_pixmap(dpi=100).save(out / f"p{pno:03d}.png")
        cols = listing_layout(read_json(f)["blocks"]) or (1 if pno in extra or pno in rotate else 0)
        if pno in rotate:
            cols = 1
        crops = []
        if pno in rotate:
            # Turned upright first, then cut into the same three bands.
            upright = out / f"p{pno:03d}-upright.png"
            page.get_pixmap(dpi=160).save(upright)
            im = Image.open(upright).rotate(-rotate[pno], expand=True)
            im.save(upright)
            for b, (y0, y1) in enumerate(BANDS):
                name = f"p{pno:03d}-c0b{b}.png"
                im.crop((0, int(im.height * y0), im.width, int(im.height * y1))).save(out / name)
                crops.append(name)
            manifest.append({"page": pno, "crops": crops, "rotate": rotate[pno]})
            continue
        small = Image.open(out / f"p{pno:03d}.png")
        for old in out.glob(f"p{pno:03d}-c*b*.png"):
            old.unlink()
        if pno in cuts:
            pieces = cut_pieces(cuts[pno], small)
        elif (y := split.get(pno)) is not None:
            g = gutter(small.crop((0, int(small.height * y), small.width, small.height))) or (.48, .52)
            top = [(y0, min(y1, y + .03)) for y0, y1 in BANDS if y0 < y]
            pieces = [(0, b, 0, 1, y0, y1) for b, (y0, y1) in enumerate(top)]
            pieces += [(c, len(top), x0, x1, max(0, y - .02), 1) for c, (x0, x1) in enumerate([(0, g[1]), (g[0], 1)])]
        else:
            # A full-width listing may still be two columns that Tesseract read across; "two" pages
            # are, even where gutter misses it (the columns' halves at the middle, then).
            g = None
            if pno in two:
                cols, g = 2, gutter(small)
            elif cols == 1 and pno not in whole and (g := gutter(small)):
                cols = 2
            spans = [] if not cols else [(0, 1)] if cols == 1 else [(0, g[1]), (g[0], 1)] if g else [(0, .52), (.48, 1)]
            pieces = [(c, b, x0, x1, y0, y1) for c, (x0, x1) in enumerate(spans) for b, (y0, y1) in enumerate(BANDS)]
        for c, b, x0, x1, y0, y1 in pieces:
            name = f"p{pno:03d}-c{c}b{b}.png"
            clip = pymupdf.Rect(r.width * x0, r.height * y0, r.width * x1, r.height * y1)
            page.get_pixmap(dpi=190 if x1 - x0 > .6 else 300, clip=clip).save(out / name)
            crops.append(name)
        manifest.append({"page": pno, "crops": crops} | ({"bands": {f"c{c}b{b}": [y0, y1] for c, b, _, _, y0, y1 in pieces}} if pno in split or pno in cuts else {}))
    write_json(out / "manifest.json", manifest, indent=1)
    print(f"{issue}: {len(manifest)} pages, {sum(bool(m['crops']) for m in manifest)} with listings")


def parse_reading(text: str) -> tuple[list[dict], dict[str, list[str]]]:
    titles, crops, cur = [], {}, None
    for line in text.splitlines():
        s = line.strip()
        if m := re.match(r"^==\s*(c\d+b\d+)\s*$", s):
            cur = crops.setdefault(m.group(1), [])
        elif m := re.match(r"^TITLE:\s*(.+?)\s*(?:\|\s*(.*))?$", s):
            titles.append({"title": m.group(1), "author": (m.group(2) or "").strip() or None})
        elif s and cur is not None:
            cur.append(re.sub(r"\s+", " ", s))
    return titles, crops


def values(text: str) -> int | None:
    """How many values a DATA line holds (None for other lines)."""
    m = re.search(r"\bDATA\b(.*?)(?:<\s*\d{3}\s*>)?\s*$", text)
    return len(m.group(1).split(",")) if m else None


def combine(readings: list[str]) -> str | None:
    """Two readings that both fail can still hold the line between them: try every mix of
    their differing stretches. The line only when all the mixes that pass agree (spaces
    aside); a lone reading, or two that are not mostly alike (more than 4 places apart, under
    80% the same, DATA with a different count of values) give nothing: with many mixes, one
    passes the 8-bit checksum by chance (Basic Backup 830 in 17 "passed" with 17 values of 20)."""
    found: dict[str, str] = {}
    for a, b in itertools.combinations(dict.fromkeys(readings), 2):
        sm = SequenceMatcher(None, a, b, autojunk=False)
        ops = sm.get_opcodes()
        diffs = [k for k, op in enumerate(ops) if op[0] != "equal"]
        if not diffs or len(diffs) > 4 or sm.ratio() < .8 or values(a) != values(b):
            continue
        for mask in range(1, (1 << len(diffs)) - 1):
            text = "".join(b[j1:j2] if tag != "equal" and mask >> diffs.index(k) & 1 else a[i1:i2]
                           for k, (tag, i1, i2, j1, j2) in enumerate(ops))
            if check(text)["ok"] and values(text) == values(a):
                found.setdefault(re.sub(r"\s", "", text), text)
    return next(iter(found.values())) if len(found) == 1 else None


GRAPHIC_RUN = re.compile(r"(?<![A-Z])(?:([A-Z])\1+|[A-Z])(?![A-Z])")


def graphics(text: str) -> str | None:
    """The listings print SHIFT graphics as underlined letters, which read as plain letters:
    PRINT "ACCCCS" is a box. In a failing BASIC line, try the letter runs inside strings that
    look like graphics (a letter alone, or one letter repeated) as SHIFT-letters, at most three
    runs (seven choices: a chance pass stays unlikely); the line only when exactly one passes."""
    m = BDE_LINE.match(text.strip())
    if not m or '"' not in m.group(2):
        return None
    body, runs, quote = m.group(2), [], False
    for i, c in enumerate(body):
        if c == '"':
            quote = not quote
        elif quote and c.isupper() and (i == 0 or not body[i - 1].isupper()):
            g = GRAPHIC_RUN.match(body, i)
            if g and not (body[:i].rfind("<") > body[:i].rfind(">")):  # not inside a <code>
                runs.append(g.span())
    if not runs or len(runs) > 3:
        return None
    found = []
    for mask in range(1, 1 << len(runs)):
        out, last = [], 0
        for k, (a, b) in enumerate(runs):
            if mask >> k & 1:
                out.append(body[last:a] + "".join(f"<SHIFT-{c}>" for c in body[a:b]))
                last = b
        cand = f"{m.group(1)} {''.join(out) + body[last:]} <{m.group(3)}>"
        if check(cand)["ok"]:
            found.append(cand)
    return found[0] if len(found) == 1 else None


# A count read as a letter in a code (<SRIGHT> for <5RIGHT>), and the digits it may be.
COUNT_LOOK = {"S": "5", "G": "69", "C": "60", "A": "4", "E": "86", "Z": "2", "I": "1", "L": "1", "T": "7", "D": "0", "Q": "0"}


def repair(text: str) -> str | None:
    """A failing BDE line whose bracketed codes are unknown, as the one reading of them that
    passes its checksum: a count misread as a letter (<SRIGHT>, <GRIGHT>), or the brackets
    printed as they are in a string (" <RETURN>: 'R' ILE AYNI", written <$3C>RETURN<$3E>)."""
    from c64basic import control
    end = text.rstrip().rfind("<")  # not the checksum
    spans = [m for m in re.finditer(r"<([^<>]*)>", text[:end]) if control(m.group(1)) is None]
    if not spans or len(spans) > 4:
        return None
    options = []
    for m in spans:
        name, opts = m.group(1), []
        if mm := re.fullmatch(r"([A-Z])\s*(RIGHT|LEFT|DOWN|UP|SPACE)", name.strip().upper()):
            opts += [f"<{d}{mm.group(2)}>" for d in COUNT_LOOK.get(mm.group(1), "")]
        opts.append(f"<$3C>{name}<$3E>")
        options.append(opts)
    passing = set()
    for combo in itertools.product(*options):
        t, last = "", 0
        for m, rep in zip(spans, combo):
            t += text[last:m.start()] + rep
            last = m.end()
        t += text[last:]
        if check(t)["ok"]:
            passing.add(t)
    return passing.pop() if len(passing) == 1 else None


def join_wrapped(lines: list[dict], before: list[dict]) -> None:
    """A line that runs from the bottom of one column (or page) to the top of the next: the
    first row there was read as a line of its own, numbered by its first digits (Futbol Ligi in
    17: "50+24,15 <..>" and "1 TO HS:..."). Joined to the unfinished last line before it when
    the two make a line that passes its checksum."""
    for col in sorted({x["crop"][:2] for x in lines}):
        mine = [i for i, x in enumerate(lines) if x["crop"][:2] == col]
        if not mine:
            continue
        first = lines[mine[0]]
        if col == "c0":
            head = next((x for x in reversed(before) if x["key"] is not None), None)
        else:
            left = [x for x in lines if x["crop"][:2] < col and x["key"] is not None]
            head = left[-1] if left else None
        if not head or head["ok"] or first["ok"] or first["key"] is None:
            continue
        # The rest may read as a higher number ("150) <201>" after "20 ...CHR$(", 34) when the
        # head is unfinished, with no checksum of its own.
        if first["key"] >= head["key"] and head["kind"] != "basic":
            continue
        for joined in (head["text"] + first["text"], head["text"] + " " + first["text"]):
            if check(joined)["ok"] and check(joined)["key"] == head["key"]:
                head.update({"text": joined, **check(joined), "via": "joined"})
                del lines[mine[0]]
                break


def hand_read(f: Path, lines: list[dict]) -> None:
    """Lines read by eye from the image (pNNN.hand.txt, the same "== crop" sections): each takes
    the place of the line with its number in that crop (the likest one, if two programs share
    the number), or goes in where its number belongs in that column
    when no reader had it; "-NNN" drops the line read under that number (the rest of a wrapped
    line taken for a line of its own). For lines no rereading can fix: unchecked BASIC that VICE
    stops at, DATA lines the readers skipped."""
    if not f.exists():
        return
    for crop, texts in parse_reading(f.read_text())[1].items():
        for text in texts:
            if m := re.fullmatch(r"-([0-9A-F]+)", text.strip()):
                lines[:] = [x for x in lines if not (x["crop"][:2] == crop[:2] and x["key"] is not None
                                                     and m.group(1) in (str(x["key"]), f"{x['key']:04X}"))]
                continue
            y = {"text": text, **check(text), "crop": crop, "via": "hand"}
            mine = [i for i, x in enumerate(lines) if x["crop"][:2] == crop[:2] and x["key"] is not None
                    and (x["kind"] == "mde") == (y["kind"] == "mde")]
            same = [i for i in mine if lines[i]["key"] == y["key"] and lines[i]["crop"] == crop]
            if same:  # two programs on a page can share a number: the line it reads like
                lines[max(same, key=lambda i: SequenceMatcher(None, lines[i]["text"], text).ratio())] = y
            else:
                before = [i for i in mine if lines[i]["crop"] <= crop and lines[i]["key"] < y["key"]]
                lines.insert(max(before) + 1 if before else min(mine, default=len(lines)), y)


LOOKALIKE = {frozenset(p) for p in ("39", "38", "89", "08", "0D", "8B", "BD", "CE", "EF", "17", "68")}


def readdress(lines: list[dict]) -> None:
    """A run of passing lines can still sit at a misread address: the checksum takes only the
    address's low byte (35 p19 2901-2919 read as 2301-2319). Where the runs before and after it
    put it elsewhere, every address there one look-alike digit away, the run moves there."""
    mde = [x for x in lines if x["kind"] == "mde"]
    runs: list[list[dict]] = []
    for x in mde:
        if runs and x["key"] == runs[-1][-1]["key"] + 8:
            runs[-1].append(x)
        else:
            runs.append([x])
    keys = {x["key"] for x in mde}
    for n, run in enumerate(runs):
        before, after = runs[n - 1] if n else None, runs[n + 1] if n + 1 < len(runs) else None
        near = ([before[-1]["key"] + 8] if before else []) + ([after[0]["key"] - 8 * len(run)] if after else [])
        # One neighbour alone is trusted only when its run is the longer one.
        if not near or len(set(near)) > 1 or (len(near) == 1 and len(before or after) <= len(run)):
            continue
        moved = [(x, f"{near[0] + 8 * i & 0xFFFF:04X}") for i, x in enumerate(run)]
        if all(int(a, 16) not in keys and sum(c != d for c, d in zip(a, f"{x['key']:04X}")) == 1
               and all(c == d or frozenset((c, d)) in LOOKALIKE for c, d in zip(a, f"{x['key']:04X}"))
               and check(a + x["text"].strip()[4:])["ok"] is not False for x, a in moved):
            for x, a in moved:
                t = a + x["text"].strip()[4:]
                x.update({"text": t, **check(t), "via": "address"})


def merge(issue: str) -> None:
    d = ROOT / "data" / "issues" / issue
    out, titles, missing, look = {}, {}, [], []
    split = (read_json(d / "read" / "layout.json") or {}).get("split", {})
    for m in read_json(d / "read" / "manifest.json"):
        pno = m["page"]
        f = d / "read" / f"p{pno:03d}.txt"
        if not f.exists():
            missing.append(pno)
            continue
        page_titles, crops = parse_reading(f.read_text())
        titles[str(pno)] = page_titles
        lines: list[dict] = []
        for crop in sorted(crops):
            col = crop[:2]
            # Bands overlap: lines at the top of this crop may repeat the end of the one above.
            prev = [i for i, x in enumerate(lines) if x["crop"][:2] == col][-8:]
            for text in crops[crop]:
                x = {"text": text, **check(text), "crop": crop, "readings": [text]}
                if x["kind"] == "other" and HEADER_LINE.match(text):
                    prev = []  # the lines after a header are another program's, whatever their numbers
                # The same line read twice: its number, and much the same text (two short programs
                # can share numbers: Sync and Ring 130 in 13, programs 6 and 7 in 20).
                dup = next((i for i in prev if lines[i]["kind"] == x["kind"] != "other" and lines[i]["key"] == x["key"]
                            and SequenceMatcher(None, lines[i]["text"], text).ratio() > .6), None)
                if dup is not None:
                    lines[dup]["readings"].append(text)
                    continue
                lines.append(x)
        # Other readings of the page: a second reader (pNNN.r2.txt) and rereadings of the
        # failing lines (pNNN.fix1.txt, ...), matched to the first reading by key and column,
        # the same band first.
        for other in sorted(d.glob(f"read/p{pno:03d}.r*.txt")) + sorted(d.glob(f"read/p{pno:03d}.fix*.txt")):
            for crop, texts in parse_reading(other.read_text())[1].items():
                for text in texts:
                    y = check(text)
                    i = min((i for i, x in enumerate(lines) if x["kind"] == y["kind"] != "other" and x["key"] == y["key"]
                             and x["crop"][:2] == crop[:2]),
                            key=lambda i: (lines[i]["crop"][2:] != crop[2:], i), default=None)
                    if i is None and y["ok"] and y["kind"] == "bde":
                        # A passing rereading under a number no line has: the failing line in that
                        # crop it reads like, its number misread (00476 for 00478).
                        body = text.split(None, 1)[-1]
                        near = [(SequenceMatcher(None, lines[j]["text"].split(None, 1)[-1], body).ratio(), j)
                                for j, x in enumerate(lines) if x["ok"] is False and x["kind"] == "bde" and x["crop"] == crop]
                        if near and max(near)[0] > .85:
                            i = max(near)[1]
                    if i is not None:
                        lines[i]["readings"].append(text)
        for x in lines:
            if x["ok"] is False:
                best = next((t for t in x["readings"] if check(t)["ok"]), None) or combine(x["readings"]) \
                    or next((g for t in dict.fromkeys(x["readings"]) if (g := graphics(t))), None) \
                    or next((r for t in dict.fromkeys(x["readings"]) if (r := repair(t))), None)
                if best:
                    x.update({"text": best, **check(best), "via": "readings"})
            elif x["kind"] == "basic" and not typable(x["text"]):
                if best := next((t for t in x["readings"] if typable(t)), None):
                    x.update({"text": best, **check(best), "via": "readings"})
            del x["readings"]
        join_wrapped(lines, out.get(str(pno - 1), []))
        hand_read(d / "read" / f"p{pno:03d}.hand.txt", lines)
        # A misread address (0D08 for 0D09) checks out at the address its neighbours imply.
        for i, x in enumerate(lines):
            if x["ok"] is False and x["kind"] == "mde":
                near = [(y["key"], d) for d, y in ((8, lines[i - 1] if i else None), (-8, lines[i + 1] if i + 1 < len(lines) else None))
                        if y and y["kind"] == "mde" and y["ok"] and y["crop"][:2] == x["crop"][:2]]
                for t in (f"{(k + d) & 0xFFFF:04X}{x['text'][4:]}" for k, d in near):
                    if check(t)["ok"]:
                        x.update({"text": t, **check(t), "via": "address"})
                        break
        readdress(lines)
        out[str(pno)] = lines
        # Worth a look: crops the reader wrote no section for (32 p8: no c1, the left column's 57
        # lines only), and listing lines in a split page's full-width bands (the split is too low).
        if skipped := [c[5:9] for c in m["crops"] if c[5:9] not in crops]:
            look.append(f"p{pno}: no reading for {', '.join(skipped)}")
        if str(pno) in split:
            wide = {c for c in m["bands"] if f"c1{c[2:]}" not in m["bands"]}
            if (n := sum(x["crop"] in wide and x["kind"] != "other" for x in lines)) >= 3:
                look.append(f"p{pno}: {n} listing lines in the full-width bands (split {split[str(pno)]})")
    write_json(d / "listings.json", out, indent=1)
    write_json(d / "titles.json", titles, indent=1)
    checked = [x for v in out.values() for x in v if x["kind"] in ("mde", "bde")]
    bad = sum(x["ok"] is False for x in checked)
    print(f"{issue}: {len(checked) - bad}/{len(checked)} checked lines ok, "
          f"{sum(x['kind'] == 'basic' for v in out.values() for x in v)} unchecked BASIC"
          + (f"; no reading for pages {missing}" if missing else ""))
    for x in look:
        print(f"  look: {x}")


def tasks(issue: str) -> None:
    """For each page with failing lines: read/pNNN.task.txt, the lines to read again by crop,
    and the reader's next fix file name; prints the pages as "NN|page:fixN,...". """
    d = ROOT / "data" / "issues" / issue
    todo = []
    for page, lines in (read_json(d / "listings.json") or {}).items():
        pno = int(page)
        bad: dict[str, list[str]] = {}
        for x in lines:
            if x["ok"] is False or not typable(x["text"]):
                bad.setdefault(x["crop"], []).append(x["text"])
        task = d / "read" / f"p{pno:03d}.task.txt"
        if not bad:
            task.unlink(missing_ok=True)
            continue
        n = len(list(d.glob(f"read/p{pno:03d}.fix*.txt"))) + 1
        task.write_text("".join(f"== {c} (image p{pno:03d}-{c}.png)\n" + "\n".join(t) + "\n" for c, t in sorted(bad.items())))
        todo.append(f"{pno}:{n}")
    if todo:
        print(f"{issue.split('/')[1]}|{','.join(todo)}")


if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[1] not in ("prep", "merge", "tasks"):
        raise SystemExit(__doc__)
    for issue in sys.argv[2:]:
        {"prep": prep, "merge": merge, "tasks": tasks}[sys.argv[1]](issue)

"""Commodore's Program Döküm Ekleri: one article per program, and the programs as .prg files.

Both steps work from what the readers wrote (listings.py merge): titles.json, the program
articles that start on each page, and listings.json, the checked listing lines.

segments <NN> ...  labels/ and segments.json for assemble.py: each article starts at its title's
                   block (the Tesseract block that best matches the title read). Tesseract's
                   unreadable listing text is furniture; prose is content.
programs <NN> ...  prg/*.prg and prg.json: the listing lines in page order, each given to the
                   article whose title comes last before it, split into programs (machine code
                   by address, a run per block of memory; BASIC by line number, a "PROGRAM ADI"
                   header starting a new one). Every program is written; it is `verified` when
                   every line passes its checksum (MDE or BDE), no line it jumps to is missing
                   and machine code starts where it should. The rest are offered marked with
                   what is wrong (failed), as is early BASIC without checksums.

Usage:  pde.py segments|programs <NN> [...]
"""

import re
import subprocess
import sys
from difflib import SequenceMatcher
from pathlib import Path

from rapidfuzz import fuzz

sys.path.insert(0, str(Path(__file__).parent))
from c64basic import basic_prg, crunch, missing_targets, petcat_line, scrub, unshift  # noqa: E402
from fsutil import read_json, write_json  # noqa: E402
from listings import BANDS, BASIC_LINE, BDE_LINE, MDE_LINE, check  # noqa: E402
from typeins import safe_name  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ISSUES = ROOT / "data" / "issues" / "commodore-pde"
# Keywords that only C128 BASIC 7.0 or Simons' BASIC have, as they appear in code (spaces
# dropped). A program is taken for one of them on two of its keywords, one of them a strong one
# (Turkish variable names such as BANKA would otherwise make a C64 program a C128 one), or
# when its title says so.
C128_WORDS = {"GRAPHIC", "SCNCLR", "SSHAPE", "GSHAPE", "MOVSPR", "SPRDEF", "SPRCOLOR", "SPRSAV", "DLOAD",
              "DSAVE", "DIRECTORY", "RDOT", "RGR", "BEND", "RSPPOS", "PUDEF", "DOPEN", "DCLOSE", "SCRATCH",
              "BANK", "FAST", "SLOW", "BEGIN", "TRAP", "WINDOW", "COLLISION", "TEMPO", "FILTER", "SLEEP",
              "GO64", "LOCATE", "SCALE", "BOX", "BLOAD", "BSAVE", "COLOR", "DRAW", "PAINT"}
C128_STRONG = {"GRAPHIC", "SCNCLR", "SSHAPE", "GSHAPE", "MOVSPR", "SPRDEF", "SPRCOLOR", "SPRSAV", "DLOAD",
               "DSAVE", "DIRECTORY", "RDOT", "RGR", "BEND", "RSPPOS", "PUDEF", "DOPEN", "DCLOSE"}
SIMON_WORDS = {"HIRES", "PLOT", "CENTRE", "PROC", "EXEC", "COLOUR", "LOWCOL", "HICOL", "NRM", "CSET",
               "BCKGNDS", "MMOB", "RLOCMOB", "DESIGN", "DISAPA", "SECURE", "RCOMP", "FCHR", "FCOL",
               "BFLASH", "ANGL", "MOBSET", "MOBOFF", "ENDPROC", "INKEY", "HRDCPY", "SCRSV", "SCRLD"}
SIMON_STRONG = {"HIRES", "CENTRE", "PROC", "EXEC", "COLOUR", "LOWCOL", "HICOL", "NRM", "CSET", "BCKGNDS",
                "MMOB", "RLOCMOB", "DESIGN", "MOBSET", "MOBOFF", "ENDPROC", "RCOMP"}
# petcat's names for the BASICs and where their programs load.
DIALECTS = {"c128": ("70", 0x1C01), "simon": ("simon", 0x0801)}
# Sound Master (13): BASIC with its keywords, typed with the extension running, as tokens $CC-$D6,
# matched before BASIC's own (its IF is $CD, so that THEN takes its commands). Read off VICE.
SOUND_MASTER = {b"\x8b": 0xCD, b"OFF": 0xCC, b"VOLUME": 0xCE, b"WAVE": 0xCF, b"ENVELOPE": 0xD0, b"OSCILLATE": 0xD1,
                b"TUNE": 0xD2, b"PLAY": 0xD3, b"FILTER": 0xD4, b"SOUNDCLEAR": 0xD5, b"HELP": 0xD6}
HEADER = re.compile(r"^PROGRAM\s*(?:ADI)?\s*[:;]\s*(.+?)\s*(?:\$?([0-9A-F]{4})\s*[-–]?\s*\$?([0-9A-F]{4}))?\s*$", re.I)
# Other lines a listing starts a program with: the cassette's "SEARCHING FOR EK-1*" printed
# above each piece (14), "PROGRAM 2" (the C 128 tips in 20).
PIECE = re.compile(r"^(?:SEARCHING FOR\s+(.+?)\*?|(PROGRAM\s+\d+))\s*$", re.I)
FURNITURE = re.compile(r"(?i)program\s*d[öo]k[üu]m|commod|^\W*\d{1,4}\W*$")


def tr_title(name: str) -> str:
    """MUSTAFA PAZARBAŞI → Mustafa Pazarbaşı (Turkish i's)."""
    def word(w: str) -> str:
        return w[:1] + w[1:].replace("I", "ı").replace("İ", "i").lower() if w.isupper() else w
    return " ".join(word(w) for w in name.split())


def is_prose(text: str) -> bool:
    words = re.findall(r"[^\W\d_]{3,}", text)
    return len(text) >= 20 and sum(map(len, words)) >= 0.55 * len(text.replace(" ", "").replace("\n", ""))


def position(page: int, bbox: list[float]) -> tuple:
    """Reading position on the PDE's two-column pages: page, column, height."""
    return (page, int(bbox[0] >= 0.45), bbox[1])


def segments(nn: str) -> None:
    d = ISSUES / nn
    found = read_json(d / "titles.json")
    marks = []
    for f in sorted((d / "blocks").glob("*.json")):
        page = int(f.stem)
        blocks = read_json(f)["blocks"]
        starts = set()
        if page > 1:  # page 1 is the supplement's cover with its contents
            for t in found.get(str(page), []):
                if HEADER.match(t["title"]):
                    continue
                # The title's block: the best fuzzy match among the larger lines, then any block.
                cand = [b for b in blocks if b.get("text") and b["id"] not in starts]
                key = lambda b: (fuzz.partial_ratio(t["title"].upper(), b["text"].upper()), b.get("rel_size") or 1)  # noqa: E731
                best = max(cand, key=key, default=None)
                if best is None or key(best)[0] < 60:
                    print(f"   ! p{page} title not found among the blocks: {t['title']}")
                    continue
                starts.add(best["id"])
                marks.append({"title": t["title"], "author": tr_title(t["author"]) if t["author"] else None,
                              "start": {"page": page, "block": best["id"]}, "resumes": [],
                              "at": position(page, best["bbox"])})
        answers = {"page_kind": {"choice": "cover" if page == 1 else "article", "confidence": 1.0}}
        for b in blocks:
            text = b.get("text", "")
            role = "content" if b["id"] in starts or is_prose(text) and not FURNITURE.search(text[:40]) else "furniture"
            answers[f"role_{b['id']}"] = {"choice": role, "confidence": 1.0}
            answers[f"heading_{b['id']}"] = {"choice": "article_title" if b["id"] in starts else "not_heading",
                                             "confidence": 1.0}
        write_json(d / "labels" / f.name, {"page": page, "model": "pde", "answers": answers}, indent=1)
    # Where a title really is, read off the page by hand (read/marks.json, title → [page, column,
    # height]) when the block matched is another one (ALFAQUICK in 17: right column, not left).
    for m in marks:
        if at := (read_json(d / "read" / "marks.json") or {}).get(m["title"]):
            m["at"] = tuple(at)
    marks.sort(key=lambda m: m["at"])
    write_json(d / "segments.json", {"source": "pde", "articles": marks}, indent=1)
    print(f"commodore-pde/{nn}: {len(marks)} articles")


def listing_lines(d: Path) -> list[dict]:
    """Every listing line in reading order (as merged), with an estimated position for
    placing it against the titles: its crop's column and band, and its place in the crop."""
    out = []
    pages = read_json(d / "listings.json") or {}
    bands = {str(m["page"]): m["bands"] for m in read_json(d / "read" / "manifest.json") or [] if "bands" in m}
    # read/graphics.json {"page:line": line}: a failing BDE line with the box graphics the printer
    # left out put back (data/pde-work/graphics.py, or by hand; Arda, 2026-11-03). The .prg is
    # built from it when it passes; listings.json, and the site's listing, keep the line as printed.
    graphics = read_json(d / "read" / "graphics.json") or {}
    for page in sorted(pages, key=int):
        lines = pages[page]
        count: dict[str, int] = {}
        for x in lines:
            count[x["crop"]] = count.get(x["crop"], 0) + 1
        two = any(c.startswith("c1") for c in count)
        seen: dict[str, int] = {}
        low: dict[int, float] = {}
        for x in lines:
            col, band = int(x["crop"][1]), int(x["crop"][3])
            i = seen[x["crop"]] = seen.get(x["crop"], -1) + 1
            y0, y1 = bands[page][x["crop"]] if page in bands else BANDS[band]
            y = y0 + (i + 0.5) / count[x["crop"]] * (y1 - y0)
            # Bands overlap, so a band's first lines would come out above the last ones of the
            # band before (20 p30: program 2's DATA 1060/1070 above program 3's title): in
            # reading order a column only goes down.
            y = max(y, low.get(col, 0) + 1e-4)
            low[col] = y
            if x["kind"] == "bde" and x["ok"] is False and (g := graphics.get(f"{page}:{x['key']}")) \
                    and check(g) == {"kind": "bde", "key": x["key"], "ok": True}:
                x = {**x, "text": g, "ok": True, "graphics": True}
            out.append({**x, "page": int(page), "at": (int(page), col if two else 0, y), "wide": not two})
    return out


def under_titles(lines: list[dict], marks: list[dict]) -> None:
    """A full-width listing under the columns of prose belongs to the title printed above it on
    its page (the last in reading order), whichever column that is in (E.T. in 11: its title in
    the middle column, its first lines across the page below)."""
    for x in lines:
        page, _, y = x["at"]
        above = [m["at"] for m in marks if m["at"][0] == page and m["at"][2] <= y]
        if x["wide"] and above:
            x["at"] = (page, max(above)[1], y)  # the last above it in reading order


def stacked(lines: list[dict]) -> None:
    """Two programs stacked on a two-column page, each across both columns (50 p4: ANIMATOR.BOOT
    over the top of both, ANIMATOR.DEMO below it over both; p5: the end of DEMO, then ANIMATOR.ML;
    31 p16: the end of EPROMMER, then MR.TINY EPROMM). Column by column, the right column's first
    lines, which finish the BASIC program above the left column's header, would come after that
    header. They move up to just before it: the BASIC lines at the top of the right column, before
    any header there, numbered on from the upper program's last line and not from the lower
    program (machine code, or BASIC that has already reached that number in the left column);
    a number read again where bands overlap goes with them. Machine code needs none of this: it
    is put together by address."""
    for page in sorted({x["page"] for x in lines}):
        idx = [i for i, x in enumerate(lines) if x["page"] == page]
        left = [i for i in idx if x_col(lines[i]) == 0]
        right = [i for i in idx if x_col(lines[i]) == 1]
        head = next((i for i in left if HEADER.match(lines[i]["text"].strip())
                     and any(lines[j]["kind"] != "other" for j in left if j < i)), None)
        if head is None or not right:
            continue
        upper = [lines[j] for j in left if j < head and lines[j]["kind"] != "other"]
        name = HEADER.match(lines[head]["text"].strip()).group(1)  # the same header read again (band overlap) is no end
        nxt = next((j for j in left if j > head and (m := HEADER.match(lines[j]["text"].strip())) and m.group(1) != name), len(lines))
        lower = [lines[j] for j in left if head < j < nxt and lines[j]["kind"] != "other"]
        if not lower or upper[-1]["kind"] == "mde":
            continue
        last, have = upper[-1]["key"], {x["key"] for x in upper}
        reach = None if lower[0]["kind"] == "mde" else max(x["key"] for x in lower)
        run, keys = [], set()
        for i in right:
            x = lines[i]
            if x["kind"] not in ("bde", "basic") or x["key"] in have:
                break
            if x["key"] not in keys and (x["key"] <= last or reach is not None and x["key"] > reach):
                break
            run.append(i)
            keys.add(x["key"])
            last = max(last, x["key"])
        if not run:
            continue
        at = lines[head]["at"]
        moved = [{**lines[i], "at": (at[0], at[1], at[2] - 1e-6 * (len(run) - k))} for k, i in enumerate(run)]
        rest = [x for i, x in enumerate(lines) if i not in set(run)]
        k = rest.index(lines[head])
        lines[:] = rest[:k] + moved + rest[k:]


def x_col(x: dict) -> int:
    """The column a listing line was read in (its crop's)."""
    return int(x["crop"][1])


def machine_code(lines: list[dict], title: str) -> list[dict]:
    """An article's machine code by address, not reading order: each address once (a passing
    reading first), runs split where the next line is more than 1K away; a smaller gap is a
    missing line. Listings under headers whose address ranges overlap ("PROGRAM : MPS801HRDCPY
    C174 C2BF", then "PROGRAM : MPS802HRDCPY C120 C3BF") are versions of one program at the
    same addresses: each header's lines (in reading order) make their own programs."""
    heads = [(i, m) for i, x in enumerate(lines) if (m := HEADER.match(x["text"].strip())) and m.group(2)]
    spans = [(int(m.group(2), 16), int(m.group(3), 16)) for _, m in heads]
    if any(a[0] < b[1] and b[0] < a[1] for j, a in enumerate(spans) for b in spans[j + 1:]):
        cuts = [0] + [i for i, _ in heads[1:]] + [len(lines)]
        return [p for a, b in zip(cuts, cuts[1:]) for p in machine_code(lines[a:b], title)]
    # A monitor dump (C128's "1A00:AD 04 03 AE 05 03 8D C8", 23's C 128 EK) has eight bytes a
    # line and no checksum: when no line has a ninth byte, the last one is data, unchecked.
    mde = [x for x in lines if x["kind"] == "mde"]
    if len(mde) >= 4 and all(len((m := MDE_LINE.match(x["text"].strip())).group(2).split()) < 8 and m.group(3) != "??"
                             for x in mde):
        lines = [{**x, "ok": None, "dump": True} if x["kind"] == "mde" else x for x in lines]
    by_addr: dict[int, dict] = {}
    for x in lines:
        if x["kind"] == "mde" and (x["key"] not in by_addr or x["ok"] and not by_addr[x["key"]]["ok"]):
            by_addr[x["key"]] = x
    names = [m.group(1).strip() for x in lines if (m := HEADER.match(x["text"].strip()))]
    progs: list[dict] = []
    for addr in sorted(by_addr):
        x = by_addr[addr]
        if progs and addr - progs[-1]["lines"][-1]["key"] <= 0x400:
            progs[-1]["lines"].append(x)
        else:
            name = names[len(progs)] if len(progs) < len(names) else title
            progs.append({"name": name, "kind": "mde", "lines": [x], "failed": []})
    for p in progs:
        p["failed"] = gaps(p["lines"])
    return progs


def misprinted(f: str, misprints: dict) -> bool:
    """A failure read/misprints.json lists: by its own name ("3000", "0911", "no line 98"), or for a
    gap in machine code ("gap 0F01-1001": the rows from $0F01 up to $1000 missing) any key naming a
    row in it, "gap 0F01-1001", "0F01-0FF9" or "0F01" (Arda, 2026-11-04: rows the magazine never
    printed count like a BASIC line never printed, when VICE runs the program)."""
    if f in misprints:
        return True
    if m := re.fullmatch(r"gap ([0-9A-F]{4})-([0-9A-F]{4})", f):
        lo, hi = int(m.group(1), 16), int(m.group(2), 16)
        return any(lo <= int(k.group(1), 16) < hi for key in misprints
                   if (k := re.fullmatch(r"(?:gap )?\$?([0-9A-F]{4})(?:\s*[-–]\s*\$?[0-9A-F]{4})?", key.strip().upper())))
    return False


def gaps(lines: list[dict]) -> list[str]:
    """The missing lines in a program's machine code, sorted by address: "gap A-B" from the
    first address missing to the next one there."""
    keys = [x["key"] for x in lines]
    return [f"gap {a + 8:04X}-{b:04X}" for a, b in zip(keys, keys[1:]) if b != a + 8]


def stored(x: dict) -> list[int] | str:
    """A BASIC line as the C64 stores it, spaces aside (its text if that cannot be typed)."""
    m = BDE_LINE.match(x["text"].strip()) or BASIC_LINE.match(x["text"].strip())
    tokens = crunch(m.group(2)) if m else None
    return [b for b in tokens if b != 0x20] if tokens is not None else re.sub(r"\s", "", x["text"])


def basic(lines: list[dict], name: str) -> list[dict]:
    """One header's worth of BASIC: a program is its line numbers, whatever order the page was
    read in, so the lines are sorted and each number kept once (a passing reading first). Only
    two different passing lines under one number (or two unchecked ones that read unalike) mean
    two programs: then the listing order decides, a new program starting where the numbers go down."""
    by_num: dict[int, dict] = {}
    clash = False
    for x in lines:
        y = by_num.get(x["key"])
        if y is None or x["ok"] and not y["ok"]:
            by_num[x["key"]] = x
        elif x["ok"] and y["ok"] and stored(x) != stored(y):  # "^" and "↑" are one byte
            clash = True
        elif x["ok"] is None and y["ok"] is None and stored(x) != stored(y) \
                and SequenceMatcher(None, x["text"], y["text"]).ratio() < .8 \
                and not SOURCE.match(x["text"].strip()) and not SOURCE.match(y["text"].strip()):
            clash = True  # unchecked lines that are not two readings of one line (11 TAKVIM, Şekil 1 and 3)
    if not clash:
        return [{"name": name, "kind": "basic", "lines": [by_num[k] for k in sorted(by_num)], "failed": []}]
    progs: list[dict] = []
    for x in lines:
        cur = progs[-1] if progs else None
        if cur and x["key"] == cur["lines"][-1]["key"]:
            if x["ok"] and not cur["lines"][-1]["ok"]:
                cur["lines"][-1] = x
        elif cur and x["key"] > cur["lines"][-1]["key"]:
            cur["lines"].append(x)
        else:
            progs.append({"name": name, "kind": "basic", "lines": [x], "failed": []})
    return progs


# Numbered lines that are not BASIC: Super Assembler source ("80 -; NAME", "100 - .EQ ...") or a
# monitor's disassembly read as line numbers ("8010 20 50 FD JSR $FD50").
SOURCE = re.compile(r"^\d+\s*(?:-|(?:[0-9A-F]{1,2} ){2,4}\s*[A-Z]{3}\b)")


def by_source(p: dict) -> list[dict]:
    """A BASIC loader printed under one header with its assembler source (Sentetik Melodi in 15):
    a program each, when each run of lines is at least three long."""
    runs: list[tuple[bool, list[dict]]] = []
    for x in p["lines"]:
        src = bool(SOURCE.match(x["text"].strip()))
        if runs and runs[-1][0] == src:
            runs[-1][1].append(x)
        else:
            runs.append((src, [x]))
    if len(runs) == 1 or any(len(r) < 3 for _, r in runs):
        return [p]
    return [{**p, "lines": r} for _, r in runs]


def split(lines: list[dict], title: str) -> list[dict]:
    progs: list[dict] = machine_code(lines, title)
    name, group = title, []
    lines = lines + [{"text": "PROGRAM ADI : -", "kind": "other"}]  # a last header ends the last group
    for i, line in enumerate(lines):
        if m := HEADER.match(line["text"].strip()) or PIECE.match(line["text"].strip()):
            new = (m.group(1) or m.group(2)).strip()
            # The same header read again where two crops overlap (lines read twice there may follow
            # it, a few lines placed above it may come before), unless a whole program came before
            # and the numbers start again below it: another program under the same name (23's
            # two TELSIRA).
            after = next((x["key"] for x in lines[i + 1:] if x["kind"] in ("bde", "basic")), None)
            if group and new == name and (len(group) < 10 or after is None or after >= min(x["key"] for x in group[-8:])):
                continue
            if group:
                progs += basic(group, name)
            name, group = new, []
        elif line["kind"] in ("bde", "basic"):
            group.append(line)
    progs = [q for p in progs for q in (by_source(p) if p["kind"] == "basic" else [p])]
    # Numbered rows of numbers alone are a table printed in the article, not a program.
    progs = [p for p in progs if p["kind"] == "mde" or not all(re.fullmatch(r"\d+[-\d., ]*", x["text"].strip()) for x in p["lines"])]
    for p in progs:
        p["failed"] += failing(p)
        p["checked"] = all(x["ok"] is not None for x in p["lines"])
    return [p for p in progs if len(p["lines"]) >= 2]


# What some lines print as: nothing, ":", a REM with at most punctuation, one keyword alone.
BARE = re.compile(r"^(?::|REM[\W_]*|RETURN|END|NEXT(?:\s*[A-Z][A-Z0-9]?[$%]?)?|RESTORE|PRINT|STOP|CLR|)$")


def failing(p: dict) -> list[str]:
    """The program's lines that fail their checksum, by address or number; "printed N" for a
    BDE line printed as (almost) nothing whose checksum says there was more ("00090 :", "00219
    RETURN"): the printer left characters out, the line is kept as printed (Arda, 2026-11-03)."""
    out = []
    for x in p["lines"]:
        if x["ok"] is not False:
            continue
        if p["kind"] == "mde":
            out.append(f"{x['key']:04X}")
            continue
        m = BDE_LINE.match(x["text"].strip())
        out.append(f"printed {x['key']}" if m and BARE.match(m.group(2).strip().upper()) or rem_symbols(x) else str(x["key"]))
    return out


def rem_symbols(x: dict) -> bool:
    """A line whose only untypable symbols ("<?>") sit in its REM: the page printed bytes there
    that no reader can name (23 TELSIRA 0-8). Running, the program never sees them: kept as printed."""
    m = BDE_LINE.match(x["text"].strip()) or BASIC_LINE.match(x["text"].strip())
    if not m or crunch(m.group(2)) is not None:
        return False
    body, quote = m.group(2), False
    for i, c in enumerate(body):
        if c == '"':
            quote = not quote
        elif not quote and body[i:i + 3].upper() == "REM":
            return crunch(body[:i]) is not None
    return False


def eighty(p: dict) -> bool:
    """C128 BASIC written for the 80-column screen: WINDOW out past column 39, or RGR(..) tested
    against 5 (the 80-column mode)."""
    code = " ".join(m.group(2).upper() for x in p["lines"] if (m := BDE_LINE.match(x["text"].strip()) or BASIC_LINE.match(x["text"].strip())))
    return any(int(c) > 39 for c in re.findall(r"WINDOW\s*\d+\s*,\s*\d+\s*,\s*(\d+)", code)) or bool(
        re.search(r"RGR\s*\(\s*\d*\s*\)\s*(?:<>|=)\s*5", code))


def program_bytes(p: dict) -> tuple[int, bytes]:
    if p["kind"] == "mde":
        # Rows missing between two addresses (never printed, or not read yet: "gap A-B") are written
        # as zeros, so every later byte loads at its printed address (Arda, 2026-11-04).
        load, data = p["lines"][0]["key"], bytearray()
        for x in p["lines"]:
            if (at := x["key"] - load) > len(data):
                data += bytes(at - len(data))
            data += bytes(int(v, 16) for v in " ".join(MDE_LINE.match(x["text"].strip()).groups()[1:3 if x.get("dump") else 2]).split())
        return load, bytes(data)
    return 0x0801, basic_prg(basic_lines(p))[2:]


def dialect(p: dict, title: str, text: str = "") -> str:
    """c64, c128 or simon: the BASIC a program is written in (machine code is c64). The article
    `text` can say so too: "128 modunda çalışan" (Oyun in 19). Machine code is C128's only under a
    128 title (23's C 128 EK)."""
    if p["kind"] == "mde":
        return "c128" if "128" in f"{p['name']} {title}" else "c64"
    code = ""
    for x in p["lines"]:
        m = BDE_LINE.match(x["text"].strip()) or BASIC_LINE.match(x["text"].strip())
        body = re.sub(r'"[^"]*"?', "", m.group(2).upper()).split("REM")[0] if m else ""
        code += body.replace(" ", "") + ":"
    simon = {w for w in SIMON_WORDS if w in code}
    # Listed on a C64 without the cartridge, Simons' BASIC's keywords print as the bytes of
    # their tokens: $64 and a second byte, "do280,180" (TEXT) in an upper-case listing.
    # (Not a listing in lower case mode, "data" and all: its code has no capitals.)
    bare = [re.sub(r'"[^"]*"?|<[^<>]*>', "", m.group(2)) for x in p["lines"] if (m := BASIC_LINE.match(x["text"].strip()))]
    raw = sum(len(re.findall(r"(?:^|:|THEN\s*)d[a-z]", c)) for c in bare if re.search("[A-Z]", c))
    c128 = {w for w in C128_WORDS if w in code}
    words = f"{p['name']} {title}".upper()
    if "SIMON" in words or simon & SIMON_STRONG and len(simon) >= 2 and len(simon) > len(c128) or raw >= 3:
        return "simon"
    if "128" in words or c128 & C128_STRONG and len(c128) >= 2 or re.search(r"128 MOD", text.upper()):
        return "c128"
    sm = {w.decode() for w in SOUND_MASTER if w.isalpha() and len(w) > 4 and w.decode() in code}
    if len(sm) >= 3 or len(sm) >= 2 and ("SOUND MASTER" in text.upper() or {"SOUNDCLEAR", "OSCILLATE"} & sm):
        return "soundmaster"
    return "c64"


def sound_master(data: bytes, load: int) -> bytes:
    """C64-tokenized lines as Sound Master stores them: its keywords outside strings and REM."""
    prg, lines, i = load.to_bytes(2, "little") + data, [], 2
    while i + 4 <= len(prg) and (prg[i] or prg[i + 1]) and (j := prg.find(0, i + 4)) > 0:
        body = prg[i + 4:j]
        code, rem = (body.split(b"\x8f", 1) + [None])[:2]
        parts = re.split(rb'("[^"]*"?)', code)
        for k in range(0, len(parts), 2):
            for w in sorted(SOUND_MASTER, key=len, reverse=True):
                parts[k] = parts[k].replace(w, bytes([SOUND_MASTER[w]]))
        body = b"".join(parts) + (b"\x8f" + rem if rem is not None else b"")
        lines.append((prg[i + 2] | prg[i + 3] << 8, list(body)))
        i = j + 1
    return basic_prg(lines, load)[2:]


# A C64 lister printing a C128 program shows a BASIC 7.0 token ($CC-$FE) as the V2 keyword $4C
# (76, the V2 keyword count) lower: DEC as INPUT, ELSE as GOTO (15 PROGRAM 4/5: "A=INPUT("D600")",
# ":GOTO PRINT "."";). Where the V2 keyword cannot stand (a statement used as a function, GOTO with
# no line number), the 7.0 one is meant: a misprint, put right in the .prg.
C128_SHIFTED = [(re.compile(r"\bINPUT#\s*\("), "RDOT("), (re.compile(r"\bINPUT\s*\("), "DEC("),
                (re.compile(r"\bDIM\s*\("), "HEX$("), (re.compile(r"\bREAD\s*\("), "ERR$("),
                (re.compile(r"\bLET\s*\("), "INSTR("), (re.compile(r"\bEND\s*\("), "RGR("),
                (re.compile(r"\bFOR\s*\("), "RCLR("), (re.compile(r"\bGOTO(?!\s*\d)"), "ELSE")]


def c128_shifted(body: str) -> str:
    """A C128 line's text with the keywords printed 76 tokens low put back (`C128_SHIFTED`);
    strings and REMs are left as they are."""
    parts = re.split(r'("[^"]*"?)', body)
    for i in range(0, len(parts), 2):
        code, rem, after = parts[i].partition("REM")
        for pattern, keyword in C128_SHIFTED:
            code = pattern.sub(keyword, code)
        parts[i] = code + rem + after
        if rem:
            break
    return "".join(parts)


def petcat(p: dict, which: str) -> bytes | None:
    """The program tokenized by VICE's petcat (on cachyos) in another BASIC: load address
    and all (untypable bits dropped, see `unreadable`). None when petcat fails. C128 keywords
    printed 76 tokens low are put right (`c128_shifted`), their lines in p["shifted"]."""
    version, load = DIALECTS[which]
    src, lower = [], lower_case(p)
    for x in p["lines"]:
        m = BDE_LINE.match(x["text"].strip()) or BASIC_LINE.match(x["text"].strip())
        body = m.group(2)
        if which == "c128" and (fixed := c128_shifted(body)) != body:
            p.setdefault("shifted", []).append(int(m.group(1)))
            body = fixed
        src.append(petcat_line(int(m.group(1)), body, lower) or petcat_line(int(m.group(1)), scrub(body), lower))
    cmd = f"f=$(mktemp); petcat -w{version} -l {load:x} -o $f -- /dev/stdin >/dev/null 2>&1 && cat $f; rm -f $f"
    out = subprocess.run(["ssh", "cachyos", cmd], input="\n".join(src).encode() + b"\n", capture_output=True).stdout
    bde = {int(m.group(1)) for x in p["lines"] if (m := BDE_LINE.match(x["text"].strip()))}
    return raw_bytes(out, bde) if len(out) > 4 else None


def raw_bytes(prg: bytes, bde: set[int] = frozenset()) -> bytes:
    """petcat writes a byte it was given as {$hh} out as the text "[$HH]": the token bytes read off
    the print (Simons' "$64 $6F", BASIC 7's $E7) put back as bytes, the lines linked again.
    The lines BDE listed (`bde`, by number) lose its spaces (`compact`)."""
    load, lines, i = prg[0] | prg[1] << 8, [], 2
    while i + 4 <= len(prg) and (prg[i] or prg[i + 1]) and (j := prg.find(0, i + 4)) > 0:
        body = re.sub(rb"\[\$([0-9A-Fa-f]{2})\]", lambda m: bytes([int(m.group(1), 16)]), prg[i + 4:j])
        number = prg[i + 2] | prg[i + 3] << 8
        lines.append((number, compact(list(body)) if number in bde else list(body)))
        i = j + 1
    return basic_prg(lines, load)


def lower_case(p: dict) -> bool | None:
    """True when most of a program's lines are listed in lower case mode (`unshift`): then all
    are, also those with no keyword to tell by."""
    texts = [m.group(2) for x in p["lines"] if (m := BDE_LINE.match(x["text"].strip()) or BASIC_LINE.match(x["text"].strip()))]
    lettered = [t for t in texts if re.search("[A-Za-z]", re.sub(r'"[^"]*"?|<[^<>]*>', "", t))]
    small = sum(unshift(t) != t for t in lettered)
    return True if lettered and small >= .6 * len(lettered) else None


def basic_lines(p: dict) -> list[tuple[int, list[int]]]:
    lines, lower = [], lower_case(p)
    for x in p["lines"]:
        m = BDE_LINE.match(x["text"].strip()) or BASIC_LINE.match(x["text"].strip())
        tokens = crunch(m.group(2), lower=lower) or crunch(scrub(m.group(2)), lower=lower) or []
        lines.append((int(m.group(1)), compact(tokens) if BDE_LINE.match(x["text"].strip()) else tokens))
    return lines


def compact(tokens: list[int]) -> list[int]:
    """A line BDE listed, without the spaces BDE's listing puts round keywords ("NEXT :PRINT"):
    the checksum skips spaces, the program as typed had none (18's V ends at $2D1D so, at $3039
    with them, over its own sprites at $3000). Spaces in strings, REM and DATA stay."""
    out, quote, keep = [], False, False
    for b in tokens:
        if b == 0x22:
            quote = not quote
        elif not quote and b in (0x8F, 0x83):  # REM, DATA
            keep = b == 0x8F or "data"
        elif not quote and b == 0x3A and keep == "data":
            keep = False
        if b != 0x20 or quote or keep:
            out.append(b)
    return out


def unreadable(p: dict) -> list[str]:
    """Line numbers whose text holds something that cannot be typed: a symbol the reader could
    not name ("<?>", "■"). The rest of such a line is kept (`scrub`)."""
    out = []
    for x in p["lines"]:
        m = BDE_LINE.match(x["text"].strip()) or BASIC_LINE.match(x["text"].strip())
        if m and crunch(m.group(2)) is None and not rem_symbols(x):
            out.append(m.group(1).lstrip("0") or "0")
    return out


def brief(issue_dir: Path, article: dict, number: int) -> str:
    """What search and the API get of a PDE article: its title, author and the programs it
    printed, not the listing text (titles.yaml `search: brief`)."""
    progs = [p["name"] for p in read_json(issue_dir / "prg" / "prg.json") or []
             if p["article"] == article["slug"] and p["verified"] and not (
                 p.get("start") == "RUN" and re.search(r"STATEMENT|WITHOUT|SYNTAX", p.get("screen_error") or ""))]
    lines = [article["title"], article.get("author") or "",
             f"Commodore {number} ile verilen Program Döküm Eki'nde program dökümü."]
    if progs:
        lines.append("Programlar: " + ", ".join(dict.fromkeys(progs)) + ".")
    return "\n".join(x for x in lines if x)


def article_text(a: dict) -> str:
    """The article's text, SYS spelled one way ("SYS 49152")."""
    body = " ".join(b.get("text", "") for b in a.get("body") or [] if isinstance(b, dict))
    return re.sub(r"SYS\s*\(?\s*(\d+)", r"SYS \1", body.upper())


def programs(nn: str) -> None:
    d = ISSUES / nn
    out_dir = d / "prg"
    before = {e["file"]: e for e in read_json(out_dir / "prg.json") or []}
    starts = read_json(out_dir / "starts.json") or {}
    # prg/dialects.json {file: "c128"} where the listing does not tell (27 THE WORD MAP);
    # read/misprints.json {key: why}, the magazine's own mistakes in a program otherwise right
    # (Arda, 2026-11-03): a clearly printed line under a wrong checksum, by address (27 AYNALAR
    # 0911) or line number (24 KDV 350), and a line the program jumps to that was never printed
    # ("no line 100", 23 JOYSTICK TEST). Kept as "misprint KEY", a note on the site, not a fault.
    dialects = read_json(out_dir / "dialects.json") or {}
    misprints = read_json(d / "read" / "misprints.json") or {}
    # prg/patches.json {file: base file}: lines to type over another program (23 mastermind.prg,
    # the C64 changes to the C128 MASTERMIND); the lines it jumps to may be the base program's.
    patches = read_json(out_dir / "patches.json") or {}
    numbers: dict[str, set[int]] = {}
    checked: dict[str, bool] = {}
    # A program printed over two issues (read/continued.json): in the first, file → the next
    # issue and its pages, whose lines it takes; in the next, file → "" (not a program there).
    continued = read_json(d / "read" / "continued.json") or {}
    # Errata printed in a later issue (read/errata.json): file → {"issue", "pages", "from", "to"} or a list of them;
    # that issue's lines in the range replace these, passing or not (37 p31 reprints $C6A0-$C6F8
    # of 31's EPROMMER, where 31 printed AA filler that passes).
    errata = read_json(d / "read" / "errata.json") or {}
    marks = read_json(d / "segments.json")["articles"]
    # The assembled article for each mark, by title in order (assemble.py makes the slugs).
    articles = read_json(d / "articles.json")
    pool = list(articles)
    for m in marks:
        a = next((a for a in pool if a["title"] == m["title"]), None)
        m["article"] = a
        if a:
            pool.remove(a)
    lines = listing_lines(d)
    under_titles(lines, marks)
    stacked(lines)
    found, used = [], set()
    prev, last = None, "c64"
    for i, m in enumerate(marks):
        end = marks[i + 1]["at"] if i + 1 < len(marks) else (10 ** 6,)
        mine = [x for x in lines if tuple(m["at"]) <= x["at"] < tuple(end)]
        # A title with nothing but its listing makes no article (PROSİL in 17): its program goes
        # with the article before it, under its own name.
        a = m["article"] or prev
        if not a:
            continue
        prev = a
        for p in split(mine, m["title"] if not m["article"] else a["title"]):
            base = safe_name(p["name"])
            fname, n = f"{base}.prg", 2
            while fname in used:
                fname, n = f"{base}-{n}.prg", n + 1
            used.add(fname)
            if fname in continued and not continued[fname]:
                used.discard(fname)  # its old file goes too
                continue
            if nxt := continued.get(fname):
                # Lines of the program's kind (machine code too: 70 COMOZOR goes on in 71), within
                # "from"/"to" when given (hex for machine code, as in errata.json: 75 p4 also prints
                # GRF KOMUTLARI below the rest of 74's SPRITE BASIC).
                mde = p["kind"] == "mde"
                lo, hi = (int(nxt[k], 16 if mde else 10) if k in nxt else None for k in ("from", "to"))
                more = [{**x, "from": nxt["issue"], "continued": True} for x in listing_lines(ISSUES / nxt["issue"])
                        if x["page"] in nxt["pages"] and (x["kind"] == "mde" if mde else x["kind"] in ("bde", "basic"))
                        and (lo is None or x["key"] >= lo) and (hi is None or x["key"] <= hi)]
                was = failing(p)
                have = {x["key"]: x for x in p["lines"]}
                for x in more:
                    if x["key"] not in have or x["ok"] and not have[x["key"]]["ok"]:
                        have[x["key"]] = x
                p["lines"] = [have[k] for k in sorted(have)]
                p["failed"] = [f for f in p["failed"] if f not in was] + failing(p)
                if mde:
                    p["failed"] = gaps(p["lines"]) + [f for f in p["failed"] if not f.startswith("gap ")]
                p["checked"] = all(x["ok"] is not None for x in p["lines"])
            if fixes := errata.get(fname):
                # One entry, or a list of them (58 BYTE-COMPACTOR from 59: p10-13 $0801-$15D1, then only
                # $15D9-$17E9 from p14, which also prints DİSK DOKTORU's first rows at $0801-).
                fixes = fixes if isinstance(fixes, list) else [fixes]
                mde, more = p["kind"] == "mde", []
                for fix in fixes:
                    lo, hi = (int(fix[k], 16 if mde else 10) for k in ("from", "to"))
                    more += [{**x, "from": fix["issue"]} for x in listing_lines(ISSUES / fix["issue"])
                             if x["page"] in fix["pages"] and (x["kind"] == "mde" if mde else x["kind"] in ("bde", "basic"))
                             and lo <= x["key"] <= hi]  # not headers or numberless rows (54 JUNGLE from 55 p28)
                was = failing(p)
                have = {x["key"]: x for x in p["lines"]} | {x["key"]: x for x in more}
                p["lines"] = [have[k] for k in sorted(have)]
                p["failed"] = [f for f in p["failed"] if f not in was] + failing(p)
                if mde:  # the lines may fill a gap (48 EX-MON C568-C578 from 50)
                    p["failed"] = gaps(p["lines"]) + [f for f in p["failed"] if not f.startswith("gap ")]
                p["checked"] = all(x["ok"] is not None for x in p["lines"])
            load, data = program_bytes(p)
            # C128 BASIC 7.0 and Simons' BASIC have their own tokens: petcat crunches those.
            which = dialects.get(fname) or dialect(p, a["title"], article_text(a))
            # "PROGRAM 2" under the C 128 tips (20) is in the BASIC of the programs before it.
            if re.fullmatch(r"PROGRAM \d+", (m["title"] if not m["article"] else a["title"]).upper()):
                which = which if which != "c64" else last
            elif p["kind"] != "mde":
                last = which
            if which == "soundmaster":
                data = sound_master(data, load)
            elif which != "c64" and p["kind"] != "mde" and (prg := petcat(p, which)):
                load, data = prg[0] | prg[1] << 8, prg[2:]
            # Machine code that starts off a page boundary is missing its first lines (the
            # checksums pass line by line, but the start went unread), unless the article itself
            # gives that address ("SYS 50000").
            if p["kind"] == "mde" and load != 0x0801 and load & 0xFF and f"SYS {load}" not in article_text(a):
                p["failed"].append(f"start ${load:04X}")
            # BASIC that jumps to a line it does not have is missing a line nobody read.
            if p["kind"] != "mde":
                numbers[fname], checked[fname] = {n for n, _ in basic_lines(p)}, p["checked"]
                p["failed"] += [f"no line {n}" for n in missing_targets(basic_lines(p))]
                p["failed"] += [f"symbol {n}" for n in unreadable(p)]
            p["failed"] = [f"misprint {f}" if misprinted(f, misprints) else f for f in p["failed"]]
            p["failed"] += [f"misprint token {n}" for n in p.get("shifted", [])]
            entry = {"name": p["name"], "file": fname, "article": a["slug"], "article_title": a["title"],
                     "pages": sorted({x["page"] for x in p["lines"] if "from" not in x}), "load": f"${load:04X}",
                     "end": f"${load + len(data):04X}", "bytes": len(data), "lines": len(p["lines"]),
                     "failed": p["failed"], "verified": p["checked"] and not p["failed"],
                     "format": "mde" if p["kind"] == "mde" else "bde" if p["checked"] else "basic"}
            # Every program is written, verified or not: the site offers them all, the ones with
            # failing or missing lines marked as such (someone may send a corrected one).
            if not data:
                continue
            entry["dialect"] = which
            # C128 BASIC for the 80-column screen: a WINDOW wider than 40 columns, a test of RGR's 5
            # (25 C-128 İÇİN PÜF NOKTALARI); test_prg runs it on the VDC.
            if which == "c128" and eighty(p):
                entry["columns"] = 80
            if fname in errata:
                first = errata[fname][0] if isinstance(errata[fname], list) else errata[fname]
                entry["errata"] = {"issue": first["issue"], "lines": sum("from" in x and not x.get("continued") for x in p["lines"])}
            if fname in continued and (n := sum(bool(x.get("continued")) for x in p["lines"])):
                entry["continued"] = {"issue": continued[fname]["issue"], "lines": n}
            if restored := [x["key"] for x in p["lines"] if x.get("graphics")]:
                entry["graphics"] = restored
            # How the article says to start it, where that is not plain RUN or the load address
            # (prg/starts.json, by file: Data Yaratıcı in 16 is "RUN 500").
            entry["start"] = starts.get(fname) or ("RUN" if p["kind"] != "mde" or load == 0x0801 else f"SYS {load}")
            out_dir.mkdir(exist_ok=True)
            prg = load.to_bytes(2, "little") + data
            f = out_dir / fname
            if f.exists() and f.read_bytes() == prg and fname in before:
                keep = ("tested", "screenshot", "screen_error", "standalone", "part")
                entry.update({k: before[fname][k] for k in keep if k in before[fname]})
            f.write_bytes(prg)
            found.append(entry)
    # Files of programs a re-split renamed or dropped (and their screenshots) go.
    for f in list(out_dir.glob("*.prg")) + list(out_dir.glob("*.png")) if out_dir.exists() else []:
        if f.with_suffix(".prg").name not in used:
            f.unlink()
    for e in found:
        if base := patches.get(e["file"]):
            e["failed"] = [f for f in e["failed"] if not (f.startswith("no line ") and int(f[8:]) in numbers.get(base, ()))]
            e["verified"] = checked[e["file"]] and not e["failed"]
    write_json(out_dir / "prg.json", found, indent=1)
    ok = sum(e["verified"] for e in found)
    print(f"commodore-pde/{nn}: {ok}/{len(found)} programs verified")
    for e in found:
        state = "ok" if e["verified"] else f"{e['format']}, {len(e['failed'])} failing: {' '.join(e['failed'][:8])}"
        print(f"   {e['file']:32s} {e['load']}-{e['end']} {e['lines']:4d} lines  {state}")


if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[1] not in ("segments", "programs"):
        raise SystemExit(__doc__)
    for nn in sys.argv[2:]:
        {"segments": segments, "programs": programs}[sys.argv[1]](nn)

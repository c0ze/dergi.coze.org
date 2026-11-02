"""Commodore 64 type-in listings: BASIC V2 tokenizing and the checksums printed with them.

- MDE (Commodore's Makine Dili Editörü, machine code "AAAA : 8 bytes  CC"): s = address low
  byte; for each byte s = (s + b) << 1 (16-bit); check = (lo(s) + hi(s) + 1) & 255. From MDE's
  own DATA lines (Program Döküm Eki 1).
- BDE (Basic Dili Editörü, "<NNN>" after each BASIC line): over the stored line, spaces
  skipped: line number lo, hi, then the tokens; the i-th byte rotated left by i & 7, all
  summed mod 256. From BDE's 6502 code.

Control codes are written the way the listings print them: <CLR>, <2DOWN>, <CTRL-2>, <C=3>,
<RVSON>, <SHIFT-A> (or with braces); <$3C> is the byte $3C itself.
"""

import re

KEYWORDS = (
    "END FOR NEXT DATA INPUT# INPUT DIM READ LET GOTO RUN IF RESTORE GOSUB RETURN REM STOP ON WAIT "
    "LOAD SAVE VERIFY DEF POKE PRINT# PRINT CONT LIST CLR CMD SYS OPEN CLOSE GET NEW TAB( TO FN SPC( "
    "THEN NOT STEP + - * / ^ AND OR > = < SGN INT ABS USR FRE POS SQR RND LOG EXP COS SIN TAN ATN "
    "PEEK LEN STR$ VAL ASC CHR$ LEFT$ RIGHT$ MID$ GO").split()
TOKEN = {k: 0x80 + i for i, k in enumerate(KEYWORDS)}

NAMED = {
    "CLR": 0x93, "HOME": 0x13, "UP": 0x91, "DOWN": 0x11, "LEFT": 0x9D, "RIGHT": 0x1D,
    "CRSR UP": 0x91, "CRSR DOWN": 0x11, "CRSR LEFT": 0x9D, "CRSR RIGHT": 0x1D,
    "RVSON": 0x12, "RVSOFF": 0x92, "RVS ON": 0x12, "RVS OFF": 0x92, "RVON": 0x12, "RVOFF": 0x92, "SPACE": 0x20,
    "SHIFT SPACE": 0xA0, "SHIFT-SPACE": 0xA0, "SHIFT RETURN": 0x8D, "SHIFT-RETURN": 0x8D, "INST": 0x94, "DEL": 0x14, "POUND": 0x5C, "PI": 0xFF,
    "F1": 0x85, "F3": 0x86, "F5": 0x87, "F7": 0x88, "F2": 0x89, "F4": 0x8A, "F6": 0x8B, "F8": 0x8C,
    "BLK": 0x90, "WHT": 0x05, "RED": 0x1C, "CYN": 0x9F, "PUR": 0x9C, "GRN": 0x1E, "BLU": 0x1F, "YEL": 0x9E,
    "ORNG": 0x81, "BRN": 0x95, "LRED": 0x96, "GRY1": 0x97, "GRY2": 0x98, "LGRN": 0x99, "LBLU": 0x9A, "GRY3": 0x9B,
}
CTRL = dict(zip("1234567890", (0x90, 0x05, 0x1C, 0x9F, 0x9C, 0x1E, 0x1F, 0x9E, 0x12, 0x92)))
CBM_COLOR = dict(zip("12345678", (0x81, 0x95, 0x96, 0x97, 0x98, 0x99, 0x9A, 0x9B)))
# Graphics on the keys: SHIFT gives the letter's code + $80, C= the left-hand symbol.
CBM_GRAPHIC = dict(zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ@*+-£",
                       (0xB0, 0xBF, 0xBC, 0xAC, 0xB1, 0xBB, 0xA5, 0xB4, 0xA2, 0xB5, 0xA1, 0xB6, 0xA7,
                        0xAA, 0xB9, 0xAF, 0xAB, 0xB2, 0xAE, 0xA3, 0xB8, 0xBE, 0xB3, 0xBD, 0xB7, 0xAD,
                        0xA4, 0xDF, 0xA6, 0xDC, 0xA8)))
SHIFT_SYMBOL = {"@": 0xBA, "*": 0xC0, "+": 0xDB, "-": 0xDD, "£": 0xA9, "^": 0xDE}


def control(name: str) -> list[int] | None:
    """The PETSCII bytes for one bracketed code ("2DOWN" → two cursor downs), or None."""
    n = re.sub(r"\s+", " ", name.strip().upper())
    if m := re.fullmatch(r"\$([0-9A-F]{2})", n):  # a byte as it is: <$3C> for a "<" printed in a string
        return [int(m.group(1), 16)]
    # The printer's 8 and 0 read as B and O in the counts: <BRIGHT> is <8RIGHT>.
    n = re.sub(r"^([\dBO]+)(?=\s*(?:RIGHT|LEFT|DOWN|UP|SPACE|CRSR|SHIFT SPACE)$)",
               lambda m: m.group(1).replace("B", "8").replace("O", "0"), n)
    count = 1
    if (m := re.match(r"^(\d+)\s*(\D.*)$", n)) and not n.startswith(("CTRL", "C=")):
        count, n = int(m.group(1)), m.group(2).strip()
    if n in NAMED:
        code = NAMED[n]
    elif m := re.match(r"^CTRL ?-? ?(\d)$", n):
        code = CTRL[m.group(1)]
    elif m := re.match(r"^CTRL ?-? ?([A-Z])$", n):
        code = ord(m.group(1)) - 0x40  # <CTRL-N>: $0E, lower case
    elif (m := re.match(r"^(?:C=|CBM) ?-? ?(\d)$", n)) and m.group(1) in CBM_COLOR:
        code = CBM_COLOR[m.group(1)]
    elif m := re.match(r"^(?:SHIFT|SH) ?-? ?([A-Z])$", n):
        code = ord(m.group(1)) | 0x80
    elif (m := re.match(r"^(?:SHIFT|SH) ?-? ?(.)$", n)) and m.group(1) in SHIFT_SYMBOL:
        code = SHIFT_SYMBOL[m.group(1)]
    elif (m := re.match(r"^(?:C=|CBM) ?-? ?(.)$", n)) and m.group(1) in CBM_GRAPHIC:
        code = CBM_GRAPHIC[m.group(1)]
    else:
        return None
    return [code] * count


def petscii(c: str) -> int:
    return 0x5C if c == "£" else 0x5E if c == "↑" else 0x5F if c == "←" else 0xFF if c == "π" else ord(c)




def unshift(text: str, lower: bool | None = None) -> str:
    """A line listed in the C64's lower case mode, where the printer shows unshifted letters
    (keywords too) small and shifted ones as capitals: as the upper case mode lists it, small
    letters capitals ($41-$5A) and capitals the shifted letters ($C1-$DA). Codes in brackets
    stay as they are. Such a line has its keywords small and no capitals outside strings; the
    small letters of Simons' BASIC's token bytes ("do280,180" for $64 $6F) are no keywords.
    `lower` True: the whole program is listed so (pde.py decides), even a line with no keyword
    ("55 d=53249:e=53251" in 01's Hyperscreen); False: it is not."""
    code = re.sub(r'"[^"]*("|$)', "", re.sub(r"<[^<>]*>|\{[^{}]*\}", "", text)).split("rem")[0]
    if lower is False or lower is None and (re.search(r"[A-Z]", code) or not any(k.lower() in code for k in KEYWORDS if len(k) > 2)
                                            and not text.lstrip().startswith("rem")):  # a line that is all remark ("rem ****")
        return text
    # Only real codes are kept as they are: the < of "ifa<20thennext" is a comparison.
    codes = {i for m in re.finditer(r"<([^<>]*)>|\{[^{}]*\}", text)
             if m.group(0)[0] == "{" or control(m.group(1)) is not None for i in range(*m.span())}
    out = []
    for i, c in enumerate(text):
        if i not in codes and "a" <= c <= "z":
            c = c.upper()
        elif i not in codes and "A" <= c <= "Z":
            c = chr(ord(c) + 0x80)
        out.append(c)
    return "".join(out)


RAW = re.compile(r"[{<]\$([0-9A-Fa-f]{2})[}>]")


def printed(name: str) -> bool:
    """Brackets in a string that are no code but printed as they are: "<>" (IF CS<>), "<RETURN>",
    "<Q>", "<EN FAZLA 129>". Not the readers' <GFX> or <?>, a code cut short (<SHIFT>, <CRSR>),
    or a count misread as a letter (<SRIGHT>): those stay unreadable."""
    n = name.strip().upper()
    if re.fullmatch(r"[A-Z]\s*(RIGHT|LEFT|DOWN|UP|SPACE)", n) or n == "GFX" or n.startswith(("SHIFT", "CRSR", "CBM", "C=", "CTRL")):
        return False
    return re.fullmatch(r"[A-Z0-9 .,:;'!+*/=()-]*", n) is not None and "?" not in n


def crunch(text: str, tokenize: bool = True, lower: bool | None = None) -> list[int] | None:
    """Tokenize a line's text (after the line number) as the C64 does when it is typed in:
    keywords outside strings, nothing after REM, nothing in DATA up to the next colon.
    None when a bracketed code is unknown (and not printed as it is) or a character is not
    PETSCII; {$hh} is that byte anywhere. Without `tokenize`,
    the line's PETSCII as typed (for petcat to tokenize in another BASIC)."""
    text = unshift(text, lower)
    out: list[int] = []
    i, quote, rem, data = 0, False, False, False
    while i < len(text):
        c = text[i]
        if m := RAW.match(text, i):  # a byte as it is, also outside strings (BASIC 7's $FE $0A)
            out.append(int(m.group(1), 16))
            i = m.end()
            continue
        if c in "<{" and (quote or rem):
            j = text.find(">" if c == "<" else "}", i)
            # "<<<<<C=7>": every < but the last is printed as it is
            if j > 0 and c in text[i + 1:j]:
                j = -1
            if j > 0:
                codes = control(text[i + 1:j])
                if codes is None and not printed(text[i + 1:j]):
                    return None
                if codes is not None:
                    out += codes
                    i = j + 1
                    continue
        if c == '"':
            quote = not quote
            out.append(0x22)
            i += 1
            continue
        if not tokenize and c == "?" and not (quote or rem or data):
            out += [ord(x) for x in "PRINT"]  # the shorthand, spelt out for petcat
            i += 1
            continue
        if tokenize and not (quote or rem or data):
            if c in "?↑":  # PRINT's shorthand; the up arrow is the power operator, a token too
                out.append(TOKEN["PRINT" if c == "?" else "^"])
                i += 1
                continue
            k = next((k for k in KEYWORDS if text.startswith(k, i)), None)
            if k:
                out.append(TOKEN[k])
                i += len(k)
                rem, data = k == "REM", k == "DATA"
                continue
        if data and c == ":" and not quote:
            data = False
        b = petscii(c)
        if b > 0xFF:
            return None
        out.append(b)
        i += 1
    return out


def bde(number: int, tokens: list[int]) -> int:
    total = 0
    for i, b in enumerate(b for b in [number & 255, number >> 8, *tokens] if b != 0x20):
        r = i & 7
        total += ((b << r) | (b >> (8 - r))) & 255
    return total & 255


def mde(address: int, data: bytes) -> int:
    s = address & 255
    for b in data:
        s = ((s + b) << 1) & 0xFFFF
    return ((s & 255) + (s >> 8) + 1) & 255


def basic_prg(lines: list[tuple[int, list[int]]], start: int = 0x0801) -> bytes:
    """A BASIC program file: load address, then linked lines, then the end marker."""
    out, addr = bytearray(start.to_bytes(2, "little")), start
    for number, tokens in lines:
        addr += 5 + len(tokens)
        out += addr.to_bytes(2, "little") + number.to_bytes(2, "little") + bytes(tokens) + b"\0"
    return bytes(out + b"\0\0")


JUMPS = re.compile(rb"[\x89\x8d\xa7\x8a\x9b]\s*(\d+(?:\s*,\s*\d+)*)")  # GOTO GOSUB THEN RUN LIST


def missing_targets(lines: list[tuple[int, list[int]]]) -> list[int]:
    """Line numbers the program jumps to (GOTO, GOSUB, THEN, ON ... GOTO lists, RUN, LIST) that
    it does not have: a line nobody read, which no per-line checksum can notice."""
    have, want = {n for n, _ in lines}, set()
    for _, tokens in lines:
        code = bytes(tokens).split(b"\x8f")[0]  # not in a REM
        code = re.sub(rb'"[^"]*"?', b"", code)
        for m in JUMPS.finditer(code):
            want.update(int(n) for n in re.findall(rb"\d+", m.group(1)))
    return sorted(want - have)


def scrub(text: str) -> str:
    """The line without what cannot be typed: bracketed codes nobody knows ("<?>") and
    characters that are not PETSCII ("■"). The line is still wrong, but the rest of it stays."""
    text = re.sub(r"<[^<>]*>", lambda m: m.group(0) if control(m.group(0)[1:-1]) is not None else "", text)
    return "".join(c for c in text if petscii(c) <= 0xFF)


def petcat_line(number: int, text: str, lower: bool | None = None) -> str | None:
    """A listing line as petcat's text input: letters lower case (petcat reads capitals as
    shifted), everything outside plain ASCII as {$hh}. None when the text is not PETSCII."""
    codes = crunch(text, tokenize=False, lower=lower)
    if codes is None:
        return None
    out = []
    for b in codes:
        if 0x41 <= b <= 0x5A:
            out.append(chr(b + 0x20))
        elif 0x20 <= b <= 0x40 or b in (0x5B, 0x5D, 0x5E):  # ^ for petcat is ↑, the power operator
            out.append(chr(b))
        else:
            out.append(f"{{${b:02x}}}")
    return f"{number} " + "".join(out)

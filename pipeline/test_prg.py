"""Run each extracted type-in in VICE and keep a screenshot as its preview.

VICE 3.10 runs on cachyos under xvfb (never a window): the program injected into memory, ~30 s
of C64 time in warp, then -exitscreenshot. A program loaded above BASIC (e.g. $C000) is started
by typing its SYS. The screen in the screenshot is read back (cell by cell against the C64
character ROM, data/emu-rom/, copied from VICE on cachyos) for a
BASIC "?... ERROR": a program assembled from the wrong lines stops with one. A program that
ends on a bare READY (a machine-code part waiting for its BASIC part, say) or on an error is not
`standalone`: the site offers it for download but not in the browser emulator. One that is a part
of something bigger is `part`: machine code that answers its bare SYS with nothing or with a
SYNTAX ERROR (its BASIC calls it with parameters), BASIC that loads or calls its other parts,
assembler source typed in as numbered lines, a few lines to add to another program; its error is not
a fault. Results go back into
prg/prg.json (tested, screenshot, screen_error, standalone, part).

Usage: test_prg.py <slug>/<NN> [...] [--dialect c128,simon]   (only programs in those BASICs)
"""

import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
from fsutil import write_json  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
HOST = "cachyos"
REMOTE = "/tmp/dergi-prg-test"
CYCLES = 30_000_000


def ssh(cmd: str) -> None:
    subprocess.run(["ssh", HOST, cmd], check=True, capture_output=True)


# What the screen shows when a program has only been loaded and given its RUN or SYS.
IDLE_LINE = re.compile(r"^(READY\.|RUN|SYS ?\d+|\**\s*COMMODORE 64 BASIC V2\s*\**|64K RAM SYSTEM .*|"
                       r"COMMODORE BASIC V7\.0 .*|\(C\)1977 MICROSOFT CORP\.|\(C\)1986 COMMODORE.*|ALL RIGHTS RESERVED|"
                       r"LOAD\".*|SEARCHING FOR .*|LOADING|.*SIMON.*BASIC.*|.*BYTES FREE)$")
# The C64's character ROM (copied from VICE on cachyos): the screen is read cell by cell from it.
CHARGEN = ROOT / "data" / "emu-rom" / "chargen-901225-01.bin"
# The Simons' BASIC cartridge (Arda's copy), for the programs written in it.
SIMONS = ROOT / "data" / "emu-rom" / "simons-basic.crt"


def charset() -> dict[tuple, str]:
    """Each 8x8 glyph of both character sets (also reversed) to its character; "▒" for graphics."""
    rom, table = CHARGEN.read_bytes(), {}
    for lower in (0, 1):
        for code in range(256):
            bits = tuple((rom[(lower * 256 + code) * 8 + r] >> (7 - c)) & 1 for r in range(8) for c in range(8))
            c = code & 127
            ch = ("@" if c == 0 else chr(64 + c) if c <= 26 and not lower else chr(96 + c).upper() if c <= 26
                  else "[£]^<"[c - 27] if c < 32 else chr(c) if c < 64 else chr(c) if lower and 65 <= c <= 90 else "▒")
            table.setdefault(bits, ch)
    return table


def screen_text(shot: Path, table: dict[tuple, str]) -> str:
    """The text screen in a VICE screenshot (384x272, the 320x200 screen at 32,35; x128's VDC
    856x288, 640x200 at 112,39): each 8x8 cell against the character ROM, the most common
    colour taken as background. A cell that matches no character (a bitmap screen, say) reads as '~'."""
    im = Image.open(shot).convert("RGB")
    px = im.load()
    x0, y0, cols = (112, 39, 80) if im.width == 856 else (32, 35, 40)
    bg = Counter(px[x0 + x, y0 + y] for x in range(cols * 8) for y in range(200)).most_common(1)[0][0]
    rows = []
    for r in range(25):
        line = ""
        for c in range(cols):
            bits = tuple(int(px[x0 + c * 8 + x, y0 + r * 8 + y] != bg) for y in range(8) for x in range(8))
            line += table.get(bits, "~") if any(bits) else " "
        rows.append(line.rstrip())
    return "\n".join(rows)


def screen_error(text: str) -> str | None:
    """The BASIC error on the screen, if any ("NEXT WITHOUT FOR  ERROR IN 2530"; Simons' BASIC
    words its own without ERROR: "?PROC NOT FOUND IN 250")."""
    m = re.search(r"\?([A-Z'][A-Z' ]*? ERROR(?: IN \d+)?)", text) or re.search(r"\?([A-Z'][A-Z' ]*? IN \d+)", text)
    return " ".join(m.group(1).split()) if m else None


def idle(text: str) -> bool:
    """Only READY, RUN/SYS and the start-up banner on the screen: loaded, started, nothing shown."""
    lines = [x.strip() for x in text.splitlines() if x.strip()]
    return all(IDLE_LINE.match(x) for x in lines)


def needs_other_part(prg: bytes) -> bool:
    """A BASIC program that loads another file (LOAD, token $93, or "LOAD" printed for the
    keyboard buffer), or calls SYS (token $9E) into RAM it does not load itself: below the BASIC
    ROM or at $C000-$CFFF. It runs only with the other parts printed beside it."""
    load = prg[0] | prg[1] << 8
    end, i = load + len(prg) - 2, 2
    while i + 4 <= len(prg) and (prg[i] or prg[i + 1]):
        j = prg.index(0, i + 4) if 0 in prg[i + 4:] else len(prg)
        line = prg[i + 4:j]
        for m in re.finditer(rb'"[^"]*"?', line):
            if b"LOAD" in m.group(0).upper():
                return True
        code = re.sub(rb'"[^"]*"?', b"", line)
        if b"\x93" in code:
            return True
        for m in re.finditer(rb"\x9e\s*\(?\s*(\d+)", code):
            a = int(m.group(1))
            if (0x0400 <= a < 0xA000 or 0xC000 <= a < 0xD000) and not load <= a < end:
                return True
        i = j + 1
    return False


# A monitor's disassembly typed as numbered lines: "8010 20 50 FD JSR $FD50" (keywords in the
# mnemonics, such as the OR of ORA, are tokens).
DISASSEMBLY = re.compile(rb"^(?:[0-9A-F]{1,2} ){2,4}\s*[A-Z\x80-\xcb]")
# Source for an assembler with ".EQ" and ";" lines (21's GRAPHIC+TEXT): a directive, a comment, or
# a mnemonic after an optional label ("INIT LDA#", AND typed in as BASIC's token).
MNEMONIC = (b"ADC|AND|ASL|BCC|BCS|BEQ|BIT|BMI|BNE|BPL|BRK|BVC|BVS|CLC|CLD|CLI|CLV|CMP|CPX|CPY|DEC|DEX|DEY|EOR|"
            b"INC|INX|INY|JMP|JSR|LDA|LDX|LDY|LSR|NOP|ORA|PHA|PHP|PLA|PLP|ROL|ROR|RTI|RTS|SBC|SEC|SED|SEI|STA|"
            b"STX|STY|TAX|TAY|TSX|TXA|TXS|TYA|\xaf|\xb0A")
SOURCE = re.compile(rb"^(?:[;.]|(?:[A-Z][A-Z0-9]* +)?(?:" + MNEMONIC + rb"))")


def assembler_source(prg: bytes) -> bool:
    """Numbered lines that start with "-": source for an assembler that lives in BASIC (Super
    Assembler), other assembler source (SOURCE), or a disassembly; to read, not a program of its own."""
    lines, i = 0, 2
    dashed = 0
    while i + 4 <= len(prg) and (prg[i] or prg[i + 1]):
        j = prg.index(0, i + 4) if 0 in prg[i + 4:] else len(prg)
        lines += 1
        text = prg[i + 4:j].lstrip(b" ")
        # typed in, the dash is BASIC's minus token
        dashed += text[:1] in (b"-", b"\xab") or bool(DISASSEMBLY.match(text) or SOURCE.match(text))
        i = j + 1
    return lines > 0 and dashed > lines / 2


def part(e: dict, text: str, prg: bytes) -> bool:
    load = prg[0] | prg[1] << 8
    err = screen_error(text) or ""
    if load != 0x0801 and e.get("format") == "mde":
        return idle(text) or err == "SYNTAX ERROR"
    if e.get("dialect") == "soundmaster":
        return True  # runs once Sound Master is loaded and started first (13)
    if assembler_source(prg):
        return True  # whatever BASIC it was taken for (21's GRAPHIC+TEXT came out c128)
    if e.get("dialect", "c64") != "c64":
        return False
    return patch(prg) or needs_other_part(prg) and (idle(text) or "FILE NOT FOUND" in err)


def patch(prg: bytes) -> bool:
    """A few lines numbered from 1000 up: a change to type into another program (the page-end
    warning for Türkçeyazar in 10), not a program of its own."""
    numbers, i = [], 2
    while i + 4 <= len(prg) and (prg[i] or prg[i + 1]) and (j := prg.find(0, i + 4)) > 0:
        numbers.append(prg[i + 2] | prg[i + 3] << 8)
        i = j + 1
    return 0 < len(numbers) <= 12 and numbers[0] >= 1000


def run_command(e: dict) -> str:
    """The VICE run for one program, by the BASIC it is written in (pde.py `dialect`):
    C64 programs injected into memory; C128 ones in x128's 40-column screen; Simons' BASIC
    ones the same with the Simons' BASIC cartridge attached."""
    name, load = e["file"][:-4], int(e["load"].lstrip("$"), 16)
    which = e.get("dialect", "c64")
    if which == "c128":
        keys = "" if load == 0x1C01 else f'-keybuf "sys{load}\\n"'  # machine code (23's C 128 EK)
        # A program for the 80-column screen (pde.py `eighty`) runs there; its screenshot is the VDC's.
        screen = "-80col" if e.get("columns") == 80 else "-40col"
        shot = "-exitscreenshot" if e.get("columns") == 80 else "-exitscreenshotvicii"
        return (f"timeout 120 xvfb-run -a x128 -default -silent +sound -warp {screen} -autostartprgmode 1 "
                f"-autostart {e['file']} {keys} -limitcycles {CYCLES} {shot} {name}.png >/dev/null 2>&1")
    if which == "simon":
        return (f"timeout 120 xvfb-run -a x64sc -default -silent +sound -warp -cartcrt simons.crt "
                f"-autostartprgmode 1 -autostart {e['file']} -limitcycles {CYCLES} -exitscreenshot {name}.png "
                ">/dev/null 2>&1")
    # One loaded above BASIC is started by typing its SYS (lowercase: the key buffer shifts capitals),
    # one the article starts otherwise ("RUN 500") is loaded and that typed.
    start = e.get("start") or "RUN"
    if start != "RUN" and not start.startswith("SYS"):
        return (f"timeout 120 xvfb-run -a x64sc -default -silent +sound -warp -autostartprgmode 1 "
                f'-autoload {e["file"]} -keybuf "{start.lower().replace(" ", "")}\\n" -limitcycles {CYCLES} '
                f"-exitscreenshot {name}.png >/dev/null 2>&1")
    keys = "" if load == 0x0801 else f'-keybuf "sys{load}\\n"'
    return (f"timeout 120 xvfb-run -a x64sc -default -silent +sound -warp -autostartprgmode 1 "
            f"-autostart {e['file']} {keys} -limitcycles {CYCLES} -exitscreenshot {name}.png >/dev/null 2>&1")


def test_issue(issue: str, dialects: set[str] | None = None) -> None:
    d = ROOT / "data" / "issues" / issue / "prg"
    meta_file = d / "prg.json"
    if not meta_file.exists():
        return
    entries = json.loads(meta_file.read_text())
    parts = json.loads((d / "parts.json").read_text()) if (d / "parts.json").exists() else {}
    table = charset()
    # Every program the site offers is tried, verified or not: the run shows what it does.
    progs = [e for e in entries if (d / e["file"]).exists() and (not dialects or e.get("dialect", "c64") in dialects)]
    if not progs:
        return
    # A directory per issue: runs of different issues can go on at the same time.
    remote = f"{REMOTE}/{issue.replace('/', '-')}"
    ssh(f"rm -rf {remote} && mkdir -p {remote}")
    files = [str(d / e["file"]) for e in progs]
    if any(e.get("dialect") == "simon" for e in progs):
        files.append(str(SIMONS))
    subprocess.run(["scp", "-q", *files, f"{HOST}:{remote}/"], check=True)
    if any(e.get("dialect") == "simon" for e in progs):
        ssh(f"mv {remote}/{SIMONS.name} {remote}/simons.crt")
    # ~30 s of machine time each, in warp; VICE exits 1 when the cycle limit ends the run.
    ssh(f"cd {remote} && " + "; ".join(run_command(e) for e in progs) + "; true")
    # Only a screenshot from this run counts: drop old ones before copying the new ones back.
    for e in progs:
        (d / e["file"].replace(".prg", ".png")).unlink(missing_ok=True)
    subprocess.run(["scp", "-q", f"{HOST}:{remote}/*.png", str(d) + "/"], check=False)
    for e in progs:
        shot = d / e["file"].replace(".prg", ".png")
        load = int(e["load"].lstrip("$"), 16)
        c64_basic = e.get("dialect", "c64") == "c64" and load == 0x0801
        e["tested"] = shot.exists()
        text = screen_text(shot, table) if shot.exists() else ""
        e["screen_error"] = screen_error(text)
        # Graphics on the screen ("▒" graphic characters, "~" cells of a bitmap) are proof enough that it runs.
        e["standalone"] = bool(e["tested"] and not e["screen_error"] and not idle(text) and (
            re.search("[▒~]", text) or not (c64_basic and needs_other_part((d / e["file"]).read_bytes()))))
        # prg/parts.json names the parts no test can tell (file → what they need: the
        # digitizer's Speech BASIC for its demos in 15).
        e["part"] = bool(e["tested"] and not e["standalone"] and (part(e, text, (d / e["file"]).read_bytes())
                                                                  or e["file"] in parts))
        e["screenshot"] = shot.name if shot.exists() else None
        # How to start it (pde.py sets it; C64 Türkiye's programs get it here): RUN for programs
        # with a BASIC stub at $0801, SYS for the rest.
        e.setdefault("start", "RUN" if load == 0x0801 else f"SYS {load}")
        print(f"{issue}  {e['file']:28s} {e.get('dialect', 'c64'):5s} {'screenshot' if e['tested'] else 'NO SCREENSHOT'}"
              f"  {e['screen_error'] or ''}")
    write_json(meta_file, entries, indent=1)


if __name__ == "__main__":
    args = sys.argv[1:]
    only = None
    if "--dialect" in args:
        i = args.index("--dialect")
        only = set(args[i + 1].split(","))
        del args[i:i + 2]
    if not args:
        raise SystemExit(__doc__)
    for issue in args:
        test_issue(issue, only)

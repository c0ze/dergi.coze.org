"""Run each extracted type-in in VICE and keep a screenshot as its preview.

VICE 3.10 runs on cachyos under xvfb (never a window): autostart, ~8 s of C64 time in warp,
then -exitscreenshot. A program loaded above BASIC (e.g. $C000) only shows READY, because it
is started with SYS; that still proves it loads. Results go back into prg/prg.json
(tested, screenshot).

Usage: test_prg.py <slug>/<NN> [...]
"""

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from fsutil import write_json  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
HOST = "cachyos"
REMOTE = "/tmp/dergi-prg-test"
CYCLES = 8_000_000


def ssh(cmd: str) -> None:
    subprocess.run(["ssh", HOST, cmd], check=True, capture_output=True)


def test_issue(issue: str) -> None:
    d = ROOT / "data" / "issues" / issue / "prg"
    meta_file = d / "prg.json"
    if not meta_file.exists():
        return
    entries = json.loads(meta_file.read_text())
    progs = [e for e in entries if e["verified"]]
    ssh(f"rm -rf {REMOTE} && mkdir -p {REMOTE}")
    subprocess.run(["scp", "-q", *[str(d / e["file"]) for e in progs], f"{HOST}:{REMOTE}/"], check=True)
    loop = (f"cd {REMOTE} && for f in *.prg; do timeout 60 xvfb-run -a x64sc -default -silent +sound -warp "
            f"-autostart \"$f\" -limitcycles {CYCLES} -exitscreenshot \"${{f%.prg}}.png\" >/dev/null 2>&1; done; "
            "true")  # VICE exits 1 when the cycle limit ends the run, which is the normal case
    ssh(loop)
    # Only a screenshot from this run counts: drop old ones before copying the new ones back.
    for e in progs:
        (d / e["file"].replace(".prg", ".png")).unlink(missing_ok=True)
    subprocess.run(["scp", "-q", f"{HOST}:{REMOTE}/*.png", str(d) + "/"], check=False)
    for e in progs:
        shot = d / e["file"].replace(".prg", ".png")
        e["tested"] = shot.exists()
        e["screenshot"] = shot.name if shot.exists() else None
        load = int(e["load"].lstrip("$"), 16)
        # How to start it: RUN for programs with a BASIC stub at $0801, SYS for the rest.
        e["start"] = "RUN" if load == 0x0801 else f"SYS {load}"
        print(f"{issue}  {e['file']:28s} {'screenshot' if e['tested'] else 'NO SCREENSHOT'}  {e['start']}")
    write_json(meta_file, entries, indent=1)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    for issue in sys.argv[1:]:
        test_issue(issue)

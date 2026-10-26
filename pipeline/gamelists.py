"""Known game titles per platform, for turning OCR'd game names into canonical ones.

Sources:
- Wikipedia's "List of Commodore 64 games" and "List of Amiga games" pages, from the offline
  wiki (kiwix on the NAS);
- the NAS game folders: WHDLoad packages (Amiga) and C64 disk images, whose file names give
  more titles ("AlienBreed2_v1.4_AGA.lha" → "Alien Breed 2").

Output: catalog/games/c64.txt and catalog/games/amiga.txt (one title per line, sorted).
Usage:  gamelists.py
"""

import html
import re
import os
import subprocess
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
# A Kiwix mirror of English Wikipedia (or Wikipedia itself) and an ssh destination for the NAS
# that holds the game collections ("-p 2222 nas"); both from the environment.
WIKI = os.environ.get("DERGI_WIKI", "https://en.wikipedia.org/wiki")
NAS_SSH = os.environ.get("DERGI_NAS_SSH", "nas").split()
PAGES = {
    "c64": ["List_of_Commodore_64_games_(A–M)", "List_of_Commodore_64_games_(N–Z)"],
    "amiga": ["List_of_Amiga_games_(A–H)", "List_of_Amiga_games_(I–O)", "List_of_Amiga_games_(P–Z)"],
}
NAS = {
    "c64": "cd ~/Retro/c64/games && find . -type f",
    "amiga": "cd /volume1/Retro/AmigaWHD/Games && find . -maxdepth 2 -type f",
}


def from_wiki(page: str) -> list[str]:
    r = requests.get(f"{WIKI}/{page}", timeout=60)
    if r.status_code != 200:
        print(f"  ! {page}: HTTP {r.status_code}")
        return []
    body = r.text.split('id="See_also"')[0]
    items = re.findall(r"<li>(.*?)</li>", body, re.S) + re.findall(r"<tr>\s*<t[dh][^>]*>(.*?)</t[dh]>", body, re.S)
    out = []
    for item in items:
        text = html.unescape(re.sub(r"<[^>]+>", "", item)).strip()
        text = re.split(r"\s+[–(]", text)[0].strip()  # drop "– publisher (year)" tails
        if 2 <= len(text) <= 60 and not text.lower().startswith(("list of", "lists of")):
            out.append(text)
    return out


def from_nas(cmd: str) -> list[str]:
    out = subprocess.run(["ssh", *NAS_SSH, cmd], capture_output=True, text=True).stdout
    names = []
    for line in out.splitlines():
        stem = Path(line).name.rsplit(".", 1)[0]
        stem = re.split(r"_v\d|_\d{4}$|_AGA|_ECS|_OCS|_CD32|_NTSC|_\d+Disk", stem)[0]
        stem = re.sub(r"(?<=[a-z])(?=[A-Z0-9])|(?<=[0-9])(?=[A-Z])", " ", stem).replace("_", " ").strip()
        if 2 <= len(stem) <= 60 and not stem.isupper() or len(stem) > 3:
            names.append(stem)
    return names


def main() -> None:
    out_dir = ROOT / "catalog" / "games"
    out_dir.mkdir(parents=True, exist_ok=True)
    for platform in PAGES:
        names = [n for p in PAGES[platform] for n in from_wiki(p)] + from_nas(NAS[platform])
        unique = sorted({n for n in names}, key=str.lower)
        (out_dir / f"{platform}.txt").write_text("\n".join(unique) + "\n")
        print(f"{platform}: {len(unique)} titles")


if __name__ == "__main__":
    main()

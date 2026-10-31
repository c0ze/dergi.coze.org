"""Carry segments.json marks over to blocks that were rebuilt (display.py's title pass adds,
merges and renumbers blocks): each mark moves to the new block on the same page that best
matches the old one, by overlap of their boxes and similarity of their text.

Usage: seg_remap.py <old blocks dir> <issue dir> [--write]
Prints every mark that moves (and any it cannot place); --write updates <issue dir>/segments.json,
unless two marks end up on one block.
"""

import json
import sys
from difflib import SequenceMatcher
from pathlib import Path


def iou(a: list[float], b: list[float]) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0.0
    inter = w * h
    return inter / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter)


def fold(s: str) -> str:
    return "".join(c for c in s.lower() if c.isalnum())


def score(old: dict, new: dict) -> float:
    # Figure blocks have no text.
    text = SequenceMatcher(None, " ".join(old.get("text", "").split())[:300],
                           " ".join(new.get("text", "").split())[:300]).ratio()
    return iou(old["bbox"], new["bbox"]) + text


def remap(old_dir: Path, issue_dir: Path, write: bool) -> int:
    seg_file = issue_dir / "segments.json"
    seg = json.loads(seg_file.read_text())
    blocks = {}

    def page(d: Path, n: int) -> dict:
        key = (d, n)
        if key not in blocks:
            blocks[key] = {b["id"]: b for b in json.loads((d / f"{n:03d}.json").read_text())["blocks"]}
        return blocks[key]

    moved = 0
    for a in seg["articles"]:
        for mark in [a["start"], *a.get("resumes", [])]:
            n, bid = mark["page"], mark["block"]
            old, new = page(old_dir, n), page(issue_dir / "blocks", n)
            if bid == "page":
                # A page without text that now has a title block: assemble.py only follows a
                # "page" mark on a page without blocks, so the mark moves to the first one.
                if new:
                    first = min(new.values(), key=lambda b: b["order"])["id"]
                    print(f"  p{n} page -> {first}  {a['title'][:40]}")
                    mark["block"] = first
                    moved += 1
                continue
            if bid not in old:
                print(f"  p{n} {bid}: not in the old blocks ({a['title']})")
                continue
            # Only blocks in the same place: a short garbled title ("Por") can read like any
            # other short block on the page.
            near = [b for b in new.values() if iou(old[bid]["bbox"], b["bbox"]) > 0]
            best = max(near, key=lambda b: score(old[bid], b), default=None)
            if best is None:
                print(f"  p{n} {bid}: no matching new block ({a['title']})")
                continue
            if best["id"] != bid:
                print(f"  p{n} {bid} -> {best['id']}  {a['title'][:40]}")
                mark["block"] = best["id"]
                moved += 1
    # Every mark is placed before any start walks back, so a walk stops at another article's mark.
    taken = {(m["page"], m["block"]) for a in seg["articles"] for m in [a["start"], *a.get("resumes", [])]}
    for a in seg["articles"]:
        # A title block the pass added just before an article's first block is that article's
        # title: start there, or the title ends up at the tail of the article before.
        n = a["start"]["page"]
        old, new = page(old_dir, n), page(issue_dir / "blocks", n)
        if a["start"]["block"] not in new:
            continue  # a "page" mark on a page still without text, or a mark it could not place
        ordered = sorted(new.values(), key=lambda b: b["order"])
        i = next(k for k, b in enumerate(ordered) if b["id"] == a["start"]["block"])
        added = [b["id"] not in old and b["kind"] == "title" for b in ordered]
        # The split skipped scraps between a title and its text (a page number, "| B |"); the
        # walk crosses them too.
        scrap = [sum(c.isalpha() for c in b.get("text", "")) < 4 for b in ordered]
        first = i
        while first and (added[first - 1] or scrap[first - 1]) and (n, ordered[first - 1]["id"]) not in taken:
            first -= 1
        title = fold(a["title"])

        def like(k: int) -> float:
            text = fold(" ".join(b.get("text", "") for j, b in enumerate(ordered[k:i], k) if added[j]))
            # Against the whole title, or its start (a long title's first line: "OpalVision: ...").
            return max(SequenceMatcher(None, text, title).ratio(),
                       SequenceMatcher(None, text, title[:len(text) + 2]).ratio()) if text else 0

        # The earliest added title block from which the added text still reads as the title: a
        # running header the pass also added above it ("64'ler" over "YOGI" / "BEAR") stays outside.
        first = next((k for k in range(first, i) if added[k] and like(k) >= 0.5), i)
        if first < i:
            print(f"  p{n} {a['start']['block']} -> {ordered[first]['id']}  title block: {a['title'][:40]}")
            # The old start stays taken: it is still this article's (and maybe a resume's) block.
            a["start"]["block"] = ordered[first]["id"]
            taken.add((n, a["start"]["block"]))
            moved += 1
    # Two marks on one block (the pass merged their blocks). Of one article (a title split over
    # two columns, "THE THREE" | "STOOGES"): the second says nothing new and goes. Of two
    # articles: assemble.py would keep only one of them and lose an article, so segments.json is
    # left alone; fix those marks by hand.
    seen, clash = {}, False
    for k, a in enumerate(seg["articles"]):
        resumes = []
        for m in [a["start"], *a.get("resumes", [])]:
            key = (m["page"], m["block"])
            if seen.get(key) == k:
                print(f"  p{key[0]} {key[1]}: second mark of {a['title'][:30]!r} dropped")
                moved += 1
                continue
            if key in seen:
                print(f"  p{key[0]} {key[1]}: marks of both {seg['articles'][seen[key]]['title'][:30]!r}"
                      f" and {a['title'][:30]!r}")
                clash = True
            seen.setdefault(key, k)
            if m is not a["start"]:
                resumes.append(m)
        if "resumes" in a:
            a["resumes"] = resumes
    if write and clash:
        print("  not written: two marks on one block")
    elif write and moved:
        seg_file.write_text(json.dumps(seg, ensure_ascii=False, indent=1) + "\n")
    return moved


if __name__ == "__main__":
    old_dir, issue_dir = Path(sys.argv[1]), Path(sys.argv[2])
    print(f"{issue_dir}: {remap(old_dir, issue_dir, '--write' in sys.argv)} marks moved")

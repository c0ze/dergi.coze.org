# Article split with Haiku agents

`assemble.py` uses `data/issues/<slug>/<NN>/segments.json` when it exists: each article's title as
printed, its byline (or null), the block it starts at and the blocks where it resumes. Those files
are written by one Claude Haiku agent per issue, which reads every page image and the OCR block
listing. Every issue on the site has one (2026-10-29/30).

Work folder: `data/segment/` (git-ignored), one folder per issue (`<slug>-<NN>`). Run everything from
the repository root.

1. Inputs (page JPEGs + `blocks.txt`):
   `.venv/bin/python pipeline/segment/seg_prep.py data/segment <slug>/<NN> [...]`
2. One agent per issue, about 4 at a time (general-purpose agent, model haiku), prompt:
   "Read pipeline/segment/INSTRUCTIONS-retro.md and follow it exactly for the issue folder
   data/segment/<slug>-<NN>" (`-md` for Matematik Dünyası, `-laneth` for Laneth). The agent writes
   `segments.json` and checks it with `seg_check.py`. Agents sometimes leave a stray background
   `cat`; stop it.
   After pages are OCR'd again, `INSTRUCTIONS-update.md` (plus a `changed.txt` listing the pages)
   updates an existing segments.json instead of starting over.
3. House rules (titles, bylines that are the magazine's name, translators):
   `.venv/bin/python pipeline/segment/seg_normalise.py data/segment`
4. Install, assemble and compare with the old text:
   `.venv/bin/python pipeline/segment/seg_install.py data/segment <slug>/<NN> [...]`
   then `.venv/bin/python pipeline/segment/lost_check.py data/segment <slug>/<NN> [...]`, which
   must say `0 really lost`. `seg_install` keeps the previous articles.json as
   `old-articles.json` in the work folder; a new issue gets an automatic baseline first.
5. Read the article list of the issue (titles, bylines, page spans) against the page images and
   fix `segments.json` by hand where needed, then install again.
6. `scripts/refresh-site.sh` (heavy), build, deploy.

`seg_compare.py` assembles into the work folder without touching the issue, for trying a
segmentation before installing it.

After `display.py`'s title pass rebuilds the blocks of issues that already have a segments.json
(it adds `v…` title blocks and merges a few), `seg_remap.py <copy of the old blocks dir> <issue
dir> --write` moves each mark to the matching new block and starts an article at the title
block the pass added just above it (across page numbers and other scraps); without it the title
lands at the end of the article before.
A `"page"` mark on a page that now has a title block moves to that block. Two marks of different
articles on one merged block stop it from writing; fix those by hand. Checked on Amiga Dünyası 28
against a Haiku update agent (same marks, bar one section label).

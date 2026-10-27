# dergi.coze.org

An ad-free archive of Turkish retro computer magazines: online reading, searchable OCR
text, clean article pages, author/game/topic indexes, and a public MCP endpoint
(https://dergi.coze.org/mcp). The scans themselves are not offered for download.
The plan, costs and pilot log are in [docs/PLAN.md](docs/PLAN.md).

## Layout

```
catalog/titles.yaml     titles and their known issue runs
pipeline/sources.py     all scans of a title (PDF/CBR/JPG) → best per issue → one PDF each
pipeline/normalize.py   source PDF → page images, thumbnails, text blocks (born-digital PDFs)
pipeline/bakeoff.py     OCR engine comparison on scanned pages
pipeline/classify.py    JEV labels: page kind, block roles (ad / content / ...), headings
pipeline/ocr.py         Tesseract OCR for scanned issues → blocks in the same schema
pipeline/display.py     Apple Vision re-reads display titles on scanned pages
pipeline/assemble.py    blocks + labels → articles.json, pages.json, figure crops
pipeline/batch.py       normalize → OCR → JEV → assemble over many issues, resumable
pipeline/correct.py     OCR/spelling correction with a small LLM, guarded against rewrites
pipeline/tags.py        JEV: author, games, platform and topics per article
pipeline/taxonomy.py    authors (merges in catalog/authors.yaml), games, topics
pipeline/embed.py       passage vectors for meaning search
pipeline/export_api.py  catalogue (article texts, indexes) for the API and MCP server
pipeline/typeins.py     type-in listings (CHECKSUMMER) → verified .prg files
pipeline/test_prg.py    run each .prg in VICE on cachyos, keep a screenshot
pipeline/cleanup.py     page cleanup for the archive (deskew; no cropping)
pipeline/archive.py     personal archive: best scan per issue, cleaned, OCR text layer
catalog/sources.yaml    where every title's scans live (used by sources.py and archive.py)
site/build.py           static site → data/site (templates/ and static/ beside it)
site/deploy.py          deploy data/site to Firebase Hosting (gcloud configuration `personal`)
api/main.py             search API and public MCP server (https://dergi.coze.org/mcp), Cloud Run
workers/img/            Cloudflare Worker: images on img.dergi.coze.org, only for the site's pages
scripts/                add-titles.sh, refresh-site.sh, deploy.sh (site titles in common.sh),
                        mcp_smoke.py, zip_archive.py
data/                   sources and pipeline output (git-ignored; originals live on the NAS)
```

## Setup

```bash
uv sync                  # base pipeline
uv sync --group ocr      # + Apple Vision and Surya for OCR
uv sync --group api      # + MCP SDK etc. to run api/main.py locally
```

Tesseract needs the Turkish model in `data/tessdata/` (`tur.traineddata` from tessdata_best).
JEV needs `TYPESAFE_API_KEY` in the environment or in `.env` (git-ignored).
Vertex AI (correction, embeddings) and deploys use a gcloud configuration named `personal`.
`gamelists.py` reads `DERGI_WIKI` (a Wikipedia or Kiwix base URL) and `DERGI_NAS_SSH`
(ssh arguments for the machine with the game collections, e.g. `-p 2222 nas`).
The magazine scans, page images and pipeline output live outside git (`data/`).

## Running the pilot

```bash
.venv/bin/python pipeline/normalize.py c64-turkiye "data/sources/c64-turkiye/C64 Türkiye/"*.pdf
.venv/bin/python pipeline/classify.py c64-turkiye/04            # needs TYPESAFE_API_KEY
.venv/bin/python pipeline/assemble.py c64-turkiye/04
.venv/bin/python site/build.py --only c64-turkiye
npx -y pagefind@1.3.0 --site data/site
.venv/bin/python -m http.server 8642 --directory data/site    # http://127.0.0.1:8642/
```

Heavy batches (OCR over many pages, Surya) go through `~/.local/bin/heavy-slot`.

## Adding titles, refreshing, deploying

```bash
~/.local/bin/heavy-slot nice -n 10 scripts/add-titles.sh <slug> [...]   # after adding to scripts/common.sh
~/.local/bin/heavy-slot nice -n 10 scripts/refresh-site.sh              # after a pipeline change
scripts/deploy.sh                                                        # site, images, API, MCP check
```

Images are served by the Worker in `workers/img` (`wrangler deploy --config workers/img/wrangler.toml`);
the image bucket is private and the Worker reads it with an HMAC key stored as Worker secrets.

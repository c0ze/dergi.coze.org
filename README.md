# dergi.coze.org

An ad-free archive of Turkish retro computer magazines: downloads, online reading,
searchable OCR text, clean article pages, and a public MCP endpoint.
The plan, costs and pilot log are in [docs/PLAN.md](docs/PLAN.md).

## Layout

```
catalog/titles.yaml     titles and their known issue runs
pipeline/sources.py     all scans of a title (PDF/CBR/JPG) → best per issue → one PDF each
pipeline/normalize.py   source PDF → page images, thumbnails, text blocks (born-digital PDFs)
pipeline/bakeoff.py     OCR engine comparison on scanned pages
pipeline/classify.py    JEV labels: page kind, block roles (ad / content / ...), headings
pipeline/ocr.py         Tesseract OCR for scanned issues → blocks in the same schema
pipeline/assemble.py    blocks + labels → articles.json, pages.json, figure crops
pipeline/batch.py       normalize → OCR → JEV → assemble over many issues, resumable
pipeline/typeins.py     type-in listings (CHECKSUMMER) → verified .prg files
pipeline/test_prg.py    run each .prg in VICE on cachyos, keep a screenshot
pipeline/cleanup.py     page cleanup for the archive (crop scanner bed, deskew)
pipeline/archive.py     personal archive: best scan per issue, cleaned, OCR text layer
catalog/sources.yaml    where every title's scans live (used by sources.py and archive.py)
site/build.py           static site → data/site (templates/ and static/ beside it)
site/deploy.py          deploy data/site to Firebase Hosting (gcloud configuration `personal`)
data/                   sources and pipeline output (git-ignored; originals live on the NAS)
```

## Setup

```bash
uv sync                  # base pipeline
uv sync --group ocr      # + Apple Vision and Surya for OCR
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

## Deploying

```bash
.venv/bin/python site/build.py --only c64-turkiye,commodore --asset-base https://storage.googleapis.com/dergi-coze-org-site/i
npx -y pagefind@1.3.0 --site data/site
gcloud --configuration=personal storage rsync -r data/site/i gs://dergi-coze-org-site/i --cache-control="public, max-age=86400"
.venv/bin/python site/deploy.py
```

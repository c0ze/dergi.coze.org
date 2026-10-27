# dergi.coze.org — plan

An ad-free archive of Turkish retro computer magazines (Commodore, 64'ler, Amiga Dünyası,
Amiga Dergisi, MegAmiga, ...). Every issue can be downloaded and read online, the text is
OCR'd and searchable, articles get a clean HTML view, and a public MCP endpoint lets agents
query the archive.

Source of truth: `synology:~/Retro/magazines` (6.3 GB as stored, ~175 issues, ~13k unique pages),
plus the new downloads: a complete Commodore 1–79 set (`Commodore.rar`, 2 GB), Commodore
Haberler 5/7/8, and C64 Türkiye 1–8.

**Scope: Turkish magazines only.** English titles (CU Amiga, Amiga Format, Total Amiga,
CommodoreFree, ...) are out.

**DNS: coze.org is on Cloudflare.** `dergi.coze.org` points at Firebase Hosting (DNS only,
grey cloud, so Firebase can issue its certificate). Images and downloads get their own
host, `cdn.dergi.coze.org`, proxied by Cloudflare (orange cloud) in front of the GCS bucket
with a "cache everything" rule. Cloudflare's cache then serves repeat downloads, and GCS
egress stays near zero even in a viral month.

## Principles

- **Static first.** The site is static files: Firebase Hosting for HTML/JS/search index,
  one public Cloud Storage bucket for images and downloads. No database, no server for the site.
- **The only server is the MCP endpoint**, a Cloud Run service that scales to zero.
- **Heavy lifting runs locally or on cheap models:** layout and OCR on the Mac mini or
  cachyos, ad/content decisions on JEV, and a cheap vision model only for pages JEV is unsure of.
  Claude writes the pipeline and reviews samples; it doesn't process pages.
- **Every stage is a re-runnable step** with its output on disk (JSON per page/issue), so a
  better OCR model or a new issue means re-running one stage, not the whole pipeline.
- **Measure before trusting:** a small hand-labelled ground-truth set decides the OCR engine,
  the JEV thresholds and when to stop tuning.

## Architecture

```
NAS originals ─► [0 normalize] ─► page images ─► [1 layout+OCR, local] ─► blocks.json
                                                              │
                         [2 classify: JEV, VLM fallback] ◄────┘
                                      │
                           [3 assemble articles] ─► articles.json / html / searchable PDF
                                      │
               ┌──────────────────────┼─────────────────────────┐
        [4 static site]        [4 Pagefind index]        [5 SQLite FTS5]
     Firebase Hosting + GCS     (in the browser)       baked into the MCP image
                                                          Cloud Run, scale to zero
```

## Phase 0 — Catalogue and normalize (NAS, local)

1. `catalog.yaml` in git: one entry per title (name, publisher, years, issue count) and per
   issue (number, date, sources found, chosen source, supplements). The inventory from this
   session is the starting point: known runs are Commodore 1–79, Commodore Haberler 1–8,
   64'ler 1–52, Amiga Dünyası 1–31, Amiga Dergisi 1–12, MegAmiga 0–6, Amiga Show 1–2.
   Missing issues are listed on the site as "wanted".
2. Extract page images without re-encoding: `pdfimages -all` for image PDFs, `unrar`/`7z` for
   CBR/CBZ, `ddjvu` for DjVu, and the existing JPG folders as they are.
3. Choose one canonical scan per issue (highest resolution, complete page count). Record
   the choice and the alternatives in the catalogue.
4. Derive: WebP for the reader (~1600 px tall, ~150 KB), thumbnails (~300 px), and one
   download PDF per issue (rebuilt in phase 3 with a text layer).
5. Clean-up list for the NAS (not automatic): PaxHeader files, the two byte-identical
   MegAmiga duplicates.

## Phase 1 — Layout and OCR (local, free)

Run a layout model on every page: it returns blocks (bbox, type: title / text / caption /
figure / table / page-header / page-footer), a reading order, and text for each text block.
Ad text is OCR'd like everything else, because local OCR costs nothing and ad text helps
the classifier.

**Bake-off first.** Hand-correct the text of ~30 pages (different titles and years, clean
and bad scans, a program listing page). Then compare on character error rate, Turkish
characters (ı ş ğ İ), block segmentation and speed:

- Surya (layout + reading order + OCR, Turkish supported; MPS on the Mac)
- PaddleOCR PP-StructureV3 (layout + OCR, Apache-2.0)
- Apple Vision text recognition on the Mac mini (fast and free, if its Turkish holds up)
- Tesseract `tur` as the baseline

A mix is fine, for example one engine's layout with another engine's OCR. Run batches
through `heavy-slot` with at most 4 workers. Expect roughly 1–3 days of machine time for
13k pages.

Output: `pages/<issue>/<page>.blocks.json` (bbox, type, order, text, OCR confidence).

## Phase 2 — Classify: what is an ad (JEV)

JEV takes text or JSON as `state` and answers typed questions (Choice / Score / Noul) with
calibrated probabilities. It costs $0.042 per million input tokens, output is free, and the
context is 64k tokens per request. It also accepts **images** (experimental, "dreamer-ready":
`TypeSafe-Danger-Zone-Images: true` header, same per-token price, max 1568 px per side), and
Jev 1.14 has a `Box` question that locates something in an image. Its primary language is
English: Turkish is accepted with lower accuracy, so the state carries language-neutral
features and the confidence decides what goes to a fallback.

`pipeline/classify.py` sends the page image with each block's number drawn on it, plus the
blocks as JSON. `--text-only` sends the blocks alone; the evaluation compares the two.

**State per page** (JSON): page number and position in the issue, then each block's type,
bbox as page fractions, area, font size estimate, text, and cheap features computed in code:
phone numbers, prices (TL, ₺, "fiyat"), addresses, the share of the page covered by
figures, and whether the block's words appear in the issue's table of contents.

**Questions per page, all in one request:**
- `page_kind`: Choice: cover / table of contents / editorial / article / ads only / mixed /
  program listing / reader letters / back cover.
- For each block: `block_<n>_role`: Choice: content / ad / page furniture (header, footer,
  page number) / caption.
- For mixed pages: `block_<n>_next`: Choice among the next few candidate blocks, plus
  "none". This lets JEV repair the layout model's reading order across columns and boxes.

**Routing on confidence:**
- High confidence: accept.
- Low confidence: send the block crop (or the page) to a cheap vision model (Gemini
  Flash-Lite batch) with the same labels.
- Still unsure: put it in a review queue (a tiny local web page; one keypress per block).

**Cost:** ~5–10k input tokens per page → 20k pages ≈ 100–200 M tokens ≈ **$4–8 for the
whole archive**. The vision fallback on maybe 10% of pages adds a few dollars.

**Evaluation:** label ~150 pages by hand (ads and roles), split them into tune and test
sets, and report how many content blocks are lost (target <1%) and how many ad blocks
leak into articles (target <2%). Pin `jev-1.14.0` rather than `jev-latest`, so thresholds
don't drift when the alias moves.

## Phase 3 — Assemble

- Group content blocks into articles. Titles start articles, `next` links continue them
  (also across pages: "devamı 34. sayfada"), and captions attach to the nearest figure.
- Output per article: title, issue, pages, plain text, Markdown/HTML with figure crops,
  and the source block ids so the reader can highlight where text came from.
- Turkish normalization for search: a folded copy of the text (ı/i, ş/s, ğ/g, ç/c, ö/o,
  ü/u, Turkish lower-casing), so "dunyasi" finds "Dünyası".
- Optional OCR clean-up pass with a cheap LLM, article by article, keeping the raw OCR
  next to the corrected text. Measure on the bake-off pages before turning it on.
- Program listing supplements: keep the images, add best-effort OCR text, and mark them as
  listings (OCR is unreliable on C64 BASIC).
- Download PDF per issue: the original page images plus an invisible text layer (hOCR →
  PDF), so downloads are searchable too.
- Ads are kept and tagged, not deleted: they stay on the page images and get their own
  search filter.

## Phase 4 — Website

- **Stack:** a small Python + Jinja2 generator (`site/build.py`), generated from
  `catalog.yaml` and the phase 3 JSON, with a few lines of plain JS. (Changed from SvelteKit
  in the pilot: every page is static text and images, the pipeline is already Python, and
  this way there's no Node build or framework runtime to maintain. Pagefind is the only
  Node tool, run with npx at deploy time.) Deployed to Firebase Hosting at `dergi.coze.org`. Images and downloads come from
  a public GCS bucket in us-central1, served as `cdn.dergi.coze.org` through Cloudflare's
  cache (long Cache-Control; objects are immutable, so new versions get new names).
- **Pages:**
  - `/` — titles, latest additions, "wanted" issues
  - `/commodore/` — all issues (covers, dates, what's missing)
  - `/commodore/42/` — cover, table of contents, articles, download
  - `/commodore/42/sayfa/17` — page reader: zoom, keyboard paging, a "text" toggle that
    overlays or shows the OCR text, a link to the article
  - `/commodore/42/<article-slug>` — clean, ad-free HTML article with figures and a link
    back to the scanned pages
  - `/ara?q=` — search
- **Search:** Pagefind, built at deploy time. It runs in the browser and loads only the
  index chunks it needs; its Snowball stemming supports Turkish. Filters: title, year,
  article/ad, listing.
- No ads, no tracking. Optional cookieless analytics (Firebase Hosting logs, or a small
  privacy-friendly counter).
- An `/hakkinda` page with sources, scanner credits (Perpetual and others named in the
  nfo/index files), a takedown contact and a "missing issues" call for scans.

## Phase 5 — MCP endpoint

- Cloud Run service (Python FastMCP or the TS SDK), stateless Streamable HTTP at
  `https://dergi.coze.org/mcp` (Firebase Hosting rewrite) or the Cloud Run URL.
- Data: one SQLite file with FTS5 (folded Turkish text, unicode61 tokenizer), baked into
  the container image and rebuilt whenever the corpus changes. A cold start is a few
  seconds; there's no database to pay for.
- Tools:
  - `search(query, title?, year_from?, year_to?, include_ads=false, limit)` → hits with
    snippets, issue, page and article links
  - `list_titles()`, `list_issues(title)`
  - `get_issue(title, number)` → table of contents and page list
  - `get_article(id)` → full text, pages, image links
  - `get_page(title, number, page)` → OCR text, block roles, image URL
- Guard rails: max instances 2, request timeout 30 s, per-IP rate limit in the app,
  read-only.
- Later, if wanted: semantic search with a small multilingual embedding model and sqlite-vec.
- Also publish `llms.txt` and a JSON catalogue on the static site for agents that don't use MCP.

## Phase 6 — Ops

- Billing budget with alerts at $5 / $20 / $50, plus the documented "disable billing at
  threshold" function if you want a hard stop.
- Monthly GCS backup copy of the NAS originals in Archive class.
- Adding an issue = drop it on the NAS, add a catalogue line, run the pipeline for that
  issue, rebuild, deploy (one `make issue ID=commodore-62` style command).

## Cost

| | |
|---|---|
| Hosting, normal month | ≈ $0.15–0.50 (storage; everything else in free tiers) |
| Hosting, viral month (~250 GB egress) | ≈ $18 without a cache; ≈ $0–3 with Cloudflare caching `cdn.dergi.coze.org` |
| One-time: layout + OCR | $0 (local machine time) |
| One-time: JEV over the whole archive | ≈ $4–8 |
| One-time: vision fallback, optional OCR clean-up | ≈ $5–20 |

## Order of work

1. Pilot title: C64 Türkiye 1–8 (born-digital, no OCR). ✅
2. OCR bake-off on scanned Commodore pages → Tesseract for now; Surya on cachyos to test. ✅
3. JEV labels for C64 Türkiye and Commodore. ✅ Ground truth for ~150 pages → thresholds →
   measured accuracy: still to do.
4. Assemble C64 Türkiye end to end; build the site locally with only it. ✅
5. Commodore 75 end to end ✅; all 79 Commodore issues through `pipeline/batch.py` (running).
6. Then the other titles from the NAS (64'ler, Amiga Dünyası, ...), choosing the best scan per issue.
7. Deploy the site, then the MCP endpoint.

## Pilot log

**2026-10-26 — C64 Türkiye 1–8 + OCR bake-off on Commodore 42 and 75**

- C64 Türkiye is born-digital (Word/QuarkXPress PDFs, 2002–2005, 190 pages): text comes
  straight out of the PDF, no OCR. `normalize.py` wrote page images, thumbnails and
  1,753 blocks (text/title/figure). Issue 04 had Windows-1252 font mojibake
  (ý þ ð for ı ş ğ), which is now fixed in code; 0 blocks are left with it.
- Commodore scans vary a lot: issues 01 and 42 are ~1000 px wide (~120 dpi), issue 75 is
  2296 px (~280 dpi). Choosing the best scan per issue matters; the NAS copies may be better
  for some issues.
- OCR bake-off (8 pages, no ground truth yet; the score is the share of words found in a
  Turkish word-form list): see `data/bakeoff/report.html`.

  | Engine | Known words | Time/page (M4 Mac) | Notes |
  |---|---|---|---|
  | Surya 2 (VLM via llama.cpp) | 92.2% | ~120 s (shared heavy slot) | rejoins hyphens, drop caps, reading order, layout labels, HTML |
  | Tesseract 5 `tur` (tessdata_best) | 91.1% | 2.6 s | good text, keeps columns, line-end hyphens kept |
  | Apple Vision `tr-TR` | 89.4% | ~1 s | mixes lines across columns |

  Surya's output is the most usable, but at Mac speed the whole archive would take about
  four weeks. Next: measure it on cachyos (llama.cpp Vulkan on the RX 9060 XT). Meanwhile
  `pipeline/ocr.py` uses Tesseract (Commodore 75: 52 pages in 2 min with 4 workers).
- JEV (`jev-1.14.0`) on 238 pilot pages (C64 Türkiye 1–8 + Commodore 75), image + blocks
  vs blocks only:
  - Cost: ~8.3–8.9k input tokens per page with the image, ~3.7–4.2k without.
    The whole run, both modes, cost ≈ $0.12. The full archive with images:
    ~8.6k tokens × 20k pages ≈ 172 M tokens ≈ **$7**.
  - Commodore 75: with the image, all 9 full-page ads found (confidence 0.91–1.00,
    including the picture-only back cover), the cover found, no article page called an ad.
    Text-only also found the ad pages but missed the cover (0.42).
  - Block level: the few "ad" labels on article pages all had confidence 0.21–0.51
    (imprint, an address, a price fragment). A 0.6 threshold sends them to the fallback
    instead of cutting text.
  - Not yet measured against hand labels; the ~150-page ground truth set is still to do.
- Assembly needs to merge drop caps (a big single-letter block) into the next paragraph.

**2026-10-26 (later) — C64 Türkiye assembled and on a local site**

- JEV gained a third question per heading-like block: article title / section heading /
  not a heading. Relabelling 8 issues took seconds.
- `assemble.py` → 62 articles over 8 issues, with titles and most bylines. What it took:
  - the issue's own contents (cover teaser or contents page) as the authority on what is an
    article: a heading JEV calls an article title but the contents don't list becomes a
    section heading (JEV confidently promoted sub-sections like "Vektörler ile 3D...");
  - WordArt titles (issues 03, 05) are pictures stored as strips: merged, then read with
    Apple Vision (Tesseract read 0 of 3, Vision 3 of 3);
  - QuarkXPress drop-shadow titles in issue 04 drawn twice: shadow spans dropped;
  - JEV's "listing" label decides code blocks (issue 07 is set entirely in Courier).
  - Known misses: issue 08's "İntro Efektleri" and "1x1 Text Fader" sit inside "BASIC
    Öğreniyorum" (its cover teaser doesn't list them); one repeated title in issue 06.
- Site: home, title, issue, article, page reader, search, about. 264 pages, 44 MB with
  images; Pagefind index 1.6 MB. Search folds Turkish letters through a hidden ASCII copy
  of each article ("kursor" finds 15 articles, "kursör" 14). Checked at 375 px (no
  horizontal scroll, listings scroll in their own box) and in the dark theme.
- The about page's contact address `dergi@coze.org` is a placeholder until Arda picks one.

## Open questions

- Site language: Turkish UI only, or Turkish + English?
- JEV key: in `.env` at the repo root (git-ignored) or the environment; the pipeline reads
  `TYPESAFE_API_KEY`.

**2026-10-26 (evening) — reader spreads, Commodore 75, batch run**

- Page reader: two-page spreads on screens ≥ 1200 px (cover alone, then 2–3, 4–5, …),
  on by default, toggle remembered in localStorage; arrows move a spread at a time.
  Two-page ads (Commodore 75 p26–27, EuroDisney) read as printed.
- Contact address on the about page: contact@coze.org.
- Commodore 75 (scanned, Tesseract) → 25 articles. Changes to assembly that it needed:
  - contents lines split on OCR'd bullets ("ELVİRA e SPACE GUN © HELİ RESCUE");
  - a heading JEV is sure of (≥ 0.9) in display type (≥ 2× body) starts an article even if
    the contents don't list it (Commodore's contents list sections and highlights);
  - the masthead and bare page numbers are not contents entries (a review box's
    "Zorluk 18" matched a page number).
  - Known: scanned drop caps are lost by OCR ("u yazıya"); scanned issues have no figure
    crops yet (Tesseract finds no picture regions; Surya does).
- `classify.py --jobs 4` sends pages in parallel; `pipeline/batch.py` runs
  normalize → OCR → JEV → assemble per issue and skips finished stages.
  Commodore 42: 68 pages in 39 s.
- Full Commodore run (79 issues, 5,351 pages) through `batch.py`: no errors, ~1 h on the Mac
  (heavy slot, nice). JEV: 50.6 M input tokens = **$2.12**. Page kinds: 3,632 article,
  979 ads only, 306 mixed, 185 contents, 105 cover, 92 listing, 52 other.
  1,243 articles (median 15 per issue); 6,776 uncertain blocks kept.
  - Issue 79's PDF in `Commodore.rar` has only 17 pages: incomplete. The NAS has a DjVu of 79
    to compare (best-scan step).
  - Issues 57 and 61 came out with 5 articles each: few headings reached 0.9 confidence or
    matched their contents pages, so their articles run long (text is all there).
  - Site with C64 Türkiye + Commodore: 87 issues, 6,941 pages, 3.6 GB (3.5 GB images and
    PDFs), Pagefind index 21 MB (chunked; a search loads a few hundred KB).

**2026-10-26 (late) — 64'ler, personal archive, type-ins**

- 64'ler: sources from the NAS (PDF, CBR, JPG folders) and a new `64ler.rar` (43 of 52
  issues; missing 24, 25, 36, 44, 46–50). `pipeline/sources.py` picks the best scan per issue
  (most complete, then sharpest) and appends supplements (Sega Magazine to 45, Professional
  to 51). 27 issues went through the whole pipeline (307 articles); then **JEV returned
  402: no TypeSafe credits left**. OCR for the rest runs; `batch.py --stages classify,assemble`
  finishes them once credits are topped up.
- `catalog/sources.yaml` now describes every title (Turkish and English), incl. DjVu,
  archives of images, month-named issues, supplements, extras (D64/ADF) and remakes.
  Findings: the NAS has far better scans than `Commodore.rar` for ~40 Commodore issues
  (e.g. 42: 3213 px CBR vs 1000 px); Amiga Dünyası's PDFs are ABBYY FineReader
  re-typesettings, not scans — real scans exist only for 16, 21, 25, 27–31.
  → The site's Commodore issues should be rebuilt from the better scans.
- Personal archive (`pipeline/archive.py` → `data/archive/`): one PDF per issue, best scan,
  visual cleanup (`pipeline/cleanup.py`: neutral scanner-bed bands cropped, tilts of
  0.4–3° straightened, untouched pages keep their original JPEG bytes, changed pages reuse
  the original quantisation tables), A4-height pages, OCRmyPDF text layer (tur/eng),
  supplements appended, extras copied, manifest.json with SHA-256. Commodore 42: 99 pages,
  8 straightened, 51 MB, ~4.6 s/page under load. Full run started (overnight).
- Type-ins → .prg (`pipeline/typeins.py`, `pipeline/test_prg.py`): CHECKSUMMER hex dumps
  in C64 Türkiye 4–8; checksum = Σ byte[i]×(17,19,23,29,31,37,41,59) mod 65536 (from
  CHECKSUMMER's own source). 23 programs, every line verified, all tested in VICE 3.10 on
  cachyos (xvfb, autostart, screenshot). On the site: "Bu sayıdaki programlar" on issue
  pages and "Bu yazıdaki programlar" on articles, with screenshot, download, and RUN/SYS.
  Next: BASIC loaders (C64 Türkiye 1–3), then scanned BASIC listings in Commodore and
  64'ler, which print a per-line checksum (`<213>`) that can drive OCR correction.
- Archive cleanup, correction: the first run cropped "pure white" margins, which on bright
  typewritten scans (Commodore Haberler) cut into titles and edge text. Stopped the run,
  removed its outputs; cleanup now crops only neutral dark scanner bed, leaves 0.5% of the
  side as a safety margin, and never crops white. In samples it now mostly straightens
  (8 of 48 Haberler pages, 0.4–0.7°); straightened pages grow (re-encoding a rotated scan).
- 64'ler 15–17, 22–23, 26–31, 33–35, 37–39 (the 3000 px rar scans) store each two-page
  spread as one image and let each PDF page show half of it. OCR and the archive read the
  stored image, so they saw both pages twice (the site's page renders were fine).
  `sources.visible_scan()` now returns the stored scan only when it fits the page, else a
  render of the visible area at the scan's own resolution; ocr.py and archive.py use it.
  Those 17 issues: OCR redone; their JEV labels and articles were removed and need JEV again
  (credits). The archive run was restarted with the fix (Commodore 1, 2, 42 already built
  and unaffected).
- Correction to the above: 15 of those 17 spread issues carry an Acrobat Paper Capture OCR
  layer, clipped per page, so normalize had called them born-digital and used that text (no
  doubling, but mediocre OCR and pages without a layer had no text). Only 17 and 34 were
  OCR'd from the full spread. normalize now calls a PDF born-digital only when its pages
  aren't covered by a scanned image (`is_scan`); the 17 issues are re-normalized and go
  through Tesseract. Commodore 24, 28, 31, 33, 55 were also taken from an existing OCR layer;
  re-OCR them together with the JEV re-run.
- GCP: personal gcloud configuration `personal`, project
  `dergi-coze-org` (no organisation), on the personal billing account. Always pass
  `--configuration=personal`; the `default` configuration is gand's.
- GCP resources (2026-10-26): Storage + Billing Budgets APIs enabled.
  - `gs://dergi-coze-org-archive`: us-central1, ARCHIVE, uniform access, public access
    prevention enforced, 7-day soft delete. Upload the archive once, when final: Archive
    class bills 365 days minimum per object, so re-uploads cost early-deletion fees.
  - `gs://dergi-coze-org-site`: us-central1, STANDARD, uniform access; made public (and CORS
    for the CDN) at deploy time.
  - Budget "dergi-coze-org monthly": ¥7,500/month (billing account is in JPY), alerts at 10%
    (¥750 ≈ $5), 40% (¥3,000 ≈ $20), 100% (¥7,500 ≈ $50), project-scoped.
- First deploy (2026-10-26): C64 Türkiye + Commodore. HTML/search on Firebase Hosting
  (`site/deploy.py`: Hosting REST API with the `personal` gcloud login, uploads only changed
  files; the Firebase CLI on this Mac is logged in to a work account), live at
  https://dergi-coze-org.web.app. Assets (3.76 GB) in `gs://dergi-coze-org-site/i/`, public,
  served from storage.googleapis.com for now (Cloudflare cache `cdn.dergi.coze.org` later).
  Custom domain dergi.coze.org registered on the Hosting site; needs one Cloudflare record:
  CNAME `dergi` → `dergi-coze-org.web.app`, DNS only.
- dergi.coze.org is live (2026-10-26 16:11 JST): Cloudflare CNAME `dergi` →
  `dergi-coze-org.web.app`, DNS only; Firebase-managed certificate (Google Trust Services,
  auto-renewing). Keep the proxy off for this record; Cloudflare's cache is for the separate
  `cdn.dergi.coze.org` asset host, still to do.

**2026-10-26 (evening) — reading themes, spread text, search**

- Themes: Otomatik / Açık / Sepya / Koyu and four text sizes ("Aa" menu, `static/prefs.js`,
  applied in <head> before paint, stored per browser).
- Two-page mode shows both pages' text side by side and both pages' articles (each page's
  HTML carries its facing page in a <template>).
- Search page is our own (`static/search.js` on Pagefind's JS API): result shows cover, title,
  magazine · issue · date, pages, author; filters by magazine and year; query in the URL.
- Semantic search: `pipeline/embed.py` embeds article passages (~1200 chars, listings left
  out) with Vertex AI gemini-embedding-001 (768 dims, cached by content hash). 17,980
  passages ≈ 4.8 M tokens ≈ $0.70. `api/` (Cloud Run `dergi-api`, us-central1, public,
  0–2 instances, own service account with aiplatform.user only, 30 req/min/client) embeds
  the query and returns the best article per passage score (min 0.55). Firebase rewrites
  /api/** to it and caches responses. The search page fuses keyword (Pagefind) and meaning
  results with reciprocal rank fusion; meaning-only results say "anlamca yakın".
  "assembler" → 187 results incl. "Makine Diline Merhaba"; "makine kodu" → "MAKİNA DİLİ
  ÖĞRENİYORUZ!" and OCR-garbled "MAKİNA DİİL".
- Site now: C64 Türkiye, Commodore (79), 64'ler (43): 130 issues, 1,832 articles indexed.

**2026-10-26 (night) — Codex review (gpt-6.1-sol, xhigh) and fixes**

21 findings, none critical. Fixed:
- Archive cleanup: deskew rotates onto an enlarged canvas (corners no longer clipped);
  cropping is off (`cleanup.CROP`), it could mistake dark printed bands for scanner bed.
  Each archive PDF records its recipe (cleanup version, OCR on/off, source files); a
  mismatch rebuilds it. The manifest merges partial runs. Archive rebuilt from scratch.
- Sources: the stored scan is used only when placed upright and unflipped; tiled scans
  count as scans (`sources.is_scan`, shared with normalize); the most complete copy wins
  outright, a sharper but shorter copy is flagged `review:` in catalog/sources/*.yaml
  (Commodore 3: DjVu 75 p vs PDF 76 p); DjVu works for the site's best-scan PDFs.
- Stages: normalize wipes derived outputs when the source hash changes; batch re-normalizes
  on a changed source and OCRs any page without text; labels record `blocks_sha` and are
  redone when blocks change; all JSON writes are atomic (`pipeline/fsutil.py`).
- Assembly: "ads"/"cover" pages drop their text only when JEV is sure (or both runs agree);
  OCR blocks record per-line sizes, and older ones treat only the first line as a possible
  heading. Re-assembly kept +54 k characters (Commodore) and +51 k (64'ler).
- Type-ins: an address gap inside one named listing fails the program; VICE tests count
  only a fresh screenshot.
- Site: clean build tree, size-aware asset copy, search race fix, cover paths, `requests`
  declared, /api rewrite (done during the review).
Not done: bucket sync doesn't delete remote assets that disappeared (low impact).

**2026-10-27 — text quality, authors, games, topics**

- Spelling/OCR correction (`pipeline/correct.py`): gemini-3.1-flash-lite on Vertex (global
  endpoint; $0.25/$1.50 per M tokens), chosen over 3.5-flash-lite after a 40-paragraph
  bake-off (both 96.0% → ~97.4% known words; 3.1 half the output price). Prose only, batches
  of 20, JSON in/out, guard: ≤ 20% edit distance and ±15% length, else the original stays.
  Raw text kept per item (`raw`, `title_raw`), corrections cached by (model, text) hash in
  data/corrections/. ~$9 for the site. Page text panels show the corrected article text.
- Line breaks: paragraphs split at a column/page edge are merged ("oy-" + "namışınızdır");
  capitalised words broken at a line end keep their hyphen ("GHOST-BUSTERS").
- Titles on several lines: display-size blocks stacked on an article title join it
  ("LOGO" + "Öğreniyoruz (4)"); short display blocks are all heading ("Kartuşlar ve /
  Hızlandırıcılar"). Bylines identified by tagging move from the text to the header.
- Tagging (`pipeline/tags.py`, JEV, ~2.4k tokens/article ≈ $0.18 total): author (choice among
  name-like lines), game per heading, platform, 21 topics (catalog/topics.yaml).
  `pipeline/taxonomy.py`: authors merged by folded name (case, Turkish letters, quoted
  nickname) plus `merge:` in catalog/authors.yaml (close matches listed under `suggested`);
  games matched to catalog/games/{c64,amiga}.txt (Wikipedia lists + NAS WHDLoad/C64 names,
  `pipeline/gamelists.py`), grouped by platform. Site: /yazarlar, /oyunlar, /konular clouds,
  /yazar/, /oyun/, /konu/ pages, tags on articles.
- Model names/prices checked against the Vertex model list and pricing page (Oct 2026).

**2026-10-27 — Amiga titles, display titles, MCP, image protection**

- Amiga Dünyası, Amiga Dergisi and MegAmiga on the site (six titles, 177 issues, 2,974 articles).
- Assembly: running section labels at the top of many pages ("GRAFİK", "TANITIM") are page
  furniture; a name alone on an article's first line is its byline; letter-spaced titles are
  tidied; display lines inside a text block become the heading; prose run into a title goes back
  to the body. Checked against nine Commodore/64'ler/C64 Türkiye issues: no regressions.
- `pipeline/display.py`: Apple Vision re-reads the tall lines of scanned pages (Tesseract reads
  stylised titles badly: "T FE CIAN" → "THE PATRICIAN"); changed pages are re-labelled by JEV.
  Amiga Dergisi 90 → 184 articles, MegAmiga 42 → 144. Used on the scanned Amiga issues only.
- Author matching ignores spaces inside names (OCR splits) and the "Editör" prefix; hand merges
  in catalog/authors.yaml.
- Public MCP server on the dergi-api Cloud Run service (MCP Python SDK 2.3.0, stateless
  streamable HTTP, JSON responses): 11 read-only tools over a catalogue exported by
  pipeline/export_api.py; 60 requests/min per client. Tested with the SDK client and the MCP
  Inspector (scripts/mcp_smoke.py).
- Scan downloads removed from the site. Images move behind a Cloudflare Worker
  (img.dergi.coze.org) that serves only requests whose Referer is the site and sends
  Cross-Origin-Resource-Policy: same-site; the image bucket is private (public access
  prevention), the old PDF copies deleted.
- About page: "Nasıl yapıldı", known problems, MCP note, and a graded assessment of each title.

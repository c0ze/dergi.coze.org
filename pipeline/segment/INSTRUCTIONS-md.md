# Segmenting a Matematik Dünyası issue into articles

Matematik Dünyası is a Turkish mathematics magazine (1991–2002): articles on geometry, number theory, analysis, history of mathematics, famous problems, olympiad news, problem sections (SORULAR) and solutions (ÇÖZÜMLER), puzzles, letters, book reviews.

## What counts as an article
A piece with its own headline that a reader would expect in the issue's table of contents. Headlines here are often set only slightly larger than body text, so the size column is a weak signal: trust the images. Inside an article, "İspat:", "Çözüm", "Teorem 2", "Lemma", numbered sections and similar are subheadings, NOT new articles. Section tabs and running headers at the top or in a corner of the page ("MATEMATİKLE DİNLENELİM", "GÖRÜŞ", "ÜNLÜ PROBLEMLER") label the section; they are not article titles and an article continues under them. The editorial "MATEMATİK DÜNYASINDAN" (the editors' column, often boxed) and the volume index "DİZİN" are articles. A problems section or a solutions section is ONE article; without a printed headline call them "SORULAR" and "ÇÖZÜMLER" (an evaluator line such as "Değerlendiren: ..." is neither the title nor the author). Small boxed fillers of a few lines may stay inside the surrounding article. Recurring house notices ("Sayın Okurlarımız", "YAZARLARA", solution-submission rules, subscription boxes) are never articles: they stay with the article before them. Do not make articles out of captions, figure labels or page numbers. No articles for the cover or for full-page ads. Avoid both mistakes: one article swallowing many pieces, and subheadings split into fake articles.

## Files in the issue folder
- blocks.txt: every OCR text block of every page, in reading order, with its id (b003, v001...), kind, size relative to body text, an automatic heading guess with confidence, an automatic role guess (content/ad/furniture/caption) and the first 160 characters of its OCR text. The OCR is noisy, especially for display type.
- pages/p001.jpg ...: the page images.

## Work
Read blocks.txt in full, then look at EVERY page image with the Read tool, in order. Do not skip pages: the images are how you see the real headlines. Decide which articles the issue has and where each starts.

## How your marks are used
Blocks are assigned in reading order: every block after an article start (or resume) mark belongs to that article until the next mark. An article running over several pages needs no extra mark. If an article is interrupted (a different piece in between, then it continues), add a "resumes" mark at the block where it continues. Pages without any blocks cannot be marked; they stay with the article before them.
- start.block: the article headline block if the headline is one of the text blocks; otherwise the first text block of the article (if the byline, an intro or body text of the article comes before the headline in reading order, start at the earliest of those blocks, so they stay with their article).
- title: the headline as printed, read from the IMAGE (correct Turkish letters ç ğ ı İ ö ş ü, no OCR garbage), with the printed capitalisation. Stylised logos: look closely, letters may be rotated, spread around a picture or drawn; spell out what they say. If you really cannot read it, give a short descriptive Turkish title and add "title_unsure": true.
- author: only a byline or signature actually printed for that article, as printed; otherwise null. Never guess an author from the page or a neighbouring article.

## Output
Write segments.json in the issue folder, exactly this shape, articles in order of their start:
{"source": "haiku", "articles": [
  {"title": "...", "author": null, "start": {"page": 19, "block": "v003"}, "resumes": []},
  {"title": "...", "author": "...", "start": {"page": 21, "block": "b000"}, "resumes": [{"page": 24, "block": "b004"}]}
]}
Then run: python3 pipeline/segment/seg_check.py <issue folder> (from the repository root)
and fix segments.json until it prints OK. Write only segments.json: never modify, crop or overwrite the page images or any other file (no image tools that write files).

## Reply
One line only: "<issue>: OK, N articles, M resumes" plus at most two short notes about anything you were unsure of.


# Segmenting a Turkish computer magazine issue into articles

The magazine is a Turkish home-computer magazine from 1986–2005 (Commodore 64/128 and Amiga: Commodore, Commodore Amiga, 64'ler, Amiga Dünyası, Amiga Dergisi, MegAmiga, C64 Türkiye): game reviews, game solutions and maps (çözüm, ipuçları, POKE'lar), programming courses (BASIC, makine dili, kurs), program listings, hardware and software reviews, news, letters, contests, interviews.

## What counts as an article
A piece with its own headline that a reader would expect in the issue's table of contents. Each game review, game solution or program with its own headline is its OWN article titled with the game or program name as printed, even when several sit in one reviews section, because the site indexes games: do not merge separate game reviews into one section article. A program listing belongs to the article that introduces it. Inside an article, subheadings ("GRAFİK", "SES", "OYNANIŞ", "SONUÇ", "1. BÖLÜM", rating boxes, "Yapımcı/Fiyat" info boxes) are not new articles. Section tabs and running headers at the top or in a corner of the page ("OYUN", "HABERLER", "PROGRAM", "KURS", "MEKTUPLAR", the magazine name) label the section; they are not article titles and an article continues under them. The editor's letter (editorial, often next to the contents or on page 3, usually signed) is its own article even without a headline: use its printed heading if it has one, otherwise title it "Editörden". The contents list is its own article titled "İçindekiler" (start at its first block; the masthead/künye next to it goes with it), so it does not run into the editorial or the first article. A news section of many short items, a letters page and a short tips column are each ONE article. Recurring house notices (subscription forms, "abone olun", masthead/künye, contest forms without content) are not articles: they stay with the article before them. Do not make articles out of captions, screenshots' captions, page numbers or fillers of a few lines. No articles for the cover or for full-page ads. Avoid both mistakes: one article swallowing many pieces, and subheadings split into fake articles.

## Files in the issue folder
- blocks.txt: every OCR text block of every page, in reading order, with its id (b003, v001...), kind, size relative to body text, an automatic heading guess with confidence, an automatic role guess (content/ad/furniture/caption) and the first 160 characters of its OCR text. The OCR is noisy, especially for display type.
- pages/p001.jpg ...: the page images.

## Work
Read blocks.txt in full, then look at EVERY page image with the Read tool, in order. Do not skip pages: the images are how you see the real headlines. Look at each image ONCE: images fill your memory fast, so after every few pages append what you saw to notes.txt in the issue folder (per page: printed headlines with the block id each starts at, bylines, continuations). Decide from notes.txt and blocks.txt; re-open an image only to check one specific doubt, never re-read runs of pages. Decide which articles the issue has and where each starts.

## How your marks are used
Blocks are assigned in reading order: every block after an article start (or resume) mark belongs to that article until the next mark. An article running over several pages needs no extra mark. If an article is interrupted (a different piece in between, then it continues), add a "resumes" mark at the block where it continues. A page without any text blocks (a map, a full-page title picture, a photo story) can still start or continue an article: use "block": "page" for it (e.g. {"page": 44, "block": "page"}); the whole page then becomes that article's picture. Use it when such a page opens a new article or belongs to a different article than the one before it; otherwise leave the page unmarked and it stays with the article before it.
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
and fix segments.json until it prints OK. Write only segments.json (and notes.txt): never modify, crop or overwrite the page images or any other file (no image tools that write files). Use only the Read, Write and Bash tools; never kill or stop processes, and do not run anything in the background.

## Reply
One line only: "<issue>: OK, N articles, M resumes" plus at most two short notes about anything you were unsure of.


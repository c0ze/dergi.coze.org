# Updating an issue's segmentation after pages were read again

The issue folder already has segments.json (articles with start and resume marks) made from an
earlier OCR. Some pages had (almost) no OCR text then; they have now been read again and have
new text blocks. Those pages are listed in changed.txt. blocks.txt now shows the new blocks.
The magazine's own rules are in the INSTRUCTIONS file named in your prompt; follow them.

## Work
1. Read changed.txt, segments.json and blocks.txt (at least the changed pages and the pages
   around them).
2. Look at the image of every changed page (pages/pNNN.jpg) with the Read tool, and at the
   page before and after when you need them to see where a piece starts or ends.
3. Update segments.json, touching only what the new blocks change:
   - a new article that begins on a changed page: add it (title read from the image, author
     only if a byline is printed), in order of its start;
   - an article whose first page was a changed page but which was marked later (on the next
     page) because its first page had no blocks: move its start to the right block on the
     changed page;
   - an earlier article that continues on a changed page after something else came between:
     add a "resumes" mark there;
   - a mark on a changed page whose block id no longer fits the content: fix it;
   - if a changed page simply continues the article before it, change nothing for it.
   Keep every other article and mark as it is.
4. Run: python3 pipeline/segment/seg_check.py <issue folder> (from the repository root)
   and fix segments.json until it prints OK. Write only segments.json: never modify, crop or
   overwrite the page images or any other file.

## Reply
One line only: "<issue>: OK, N articles (was M)" plus at most one short note.

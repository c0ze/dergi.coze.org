"""Fix OCR errors, typos and spelling in article text with a cheap LLM, guarded.

Prose only (paragraphs, headings, captions, titles); program listings are never touched.
Each article's items go to the model in batches as a JSON array, and come back as an array
of the same length. A correction is accepted only when it stays close to the original
(edit distance ≤ MAX_CHANGE of its length): the model may fix letters and words, not rewrite.
The original text stays in the article as `raw`; results are cached by (model, text) hash,
so re-assembling an issue and correcting again costs nothing.

Output: data/issues/<slug>/<NN>/articles.json, corrected in place (items gain `raw`).
Usage:  correct.py <slug>/<NN> [...] [--model gemini-3.1-flash-lite] [--jobs 4]
"""

import argparse
import hashlib
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
import yaml
from rapidfuzz.distance import Levenshtein

sys.path.insert(0, str(Path(__file__).parent))
from fsutil import read_json, write_json  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "corrections"
MODEL = "gemini-3.1-flash-lite"
MAX_CHANGE = 0.20
BATCH = 20
URL = "https://aiplatform.googleapis.com/v1/projects/dergi-coze-org/locations/global/publishers/google/models/{model}:generateContent"

RETRO = """You proofread text from Turkish computer magazines (1985–2005) that was read by OCR.
You get a JSON array of text pieces from one article. Return a JSON array of the same length,
each piece corrected:
- Fix OCR errors: wrong or missing Turkish letters (ı/i, ş/s, ğ/g, ü/u, ö/o, ç/c, İ/I),
  confused characters (0/O, 1/l/I, rn/m), words split or joined wrongly, line-break hyphens.
- Fix spelling mistakes and obvious typos to standard Turkish (TDK) spelling, and add missing
  apostrophes before suffixes on numbers and names ("1990'da", "C64'ün").
- Do NOT rephrase, shorten, translate, reorder or add anything. Keep the author's words.
- Keep English words, game and product names, technical terms, code, commands (POKE, SYS,
  LDA), numbers, addresses ($D018) and people's names exactly, except obvious OCR damage
  (e.g. "TURRIİCAN" → "TURRICAN").
- Keep the original capitalisation style (an all-caps title stays all caps).
- If a piece is unreadable noise, return it unchanged."""

COMMON = """
You get a JSON array of text pieces from one article. Return a JSON array of the same length,
each piece corrected:
- Fix OCR errors: wrong or missing Turkish letters (ı/i, ş/s, ğ/g, ü/u, ö/o, ç/c, İ/I),
  confused characters (0/O, 1/l/I, rn/m), words split or joined wrongly, line-break hyphens.
- Do NOT rephrase, shorten, translate, reorder or add anything. Keep the author's words.
- Keep the original capitalisation style (an all-caps title stays all caps).
- If a piece is unreadable noise, return it unchanged."""

# Per corpus (catalog/corpora.yaml). Retro keeps the wording its corrections were made with.
INSTRUCTIONS = {
    "retro": RETRO,
    "bilim": """You proofread text from Turkish science and mathematics magazines (1990s) that was read by OCR.""" + COMMON + """
- Fix obvious spelling mistakes to standard Turkish (TDK) spelling.
- Keep mathematical notation exactly: formulas, variables, symbols, sets, intervals, indices,
  numbers and units. Never "correct" mathematics, only letters garbled by OCR in plain words.
- Keep people's names and foreign terms as written.""",
    "metal": """You proofread text from a Turkish heavy metal fanzine (early 1990s, typewritten) that was read by OCR.""" + COMMON + """
- Fix only clear OCR damage and obvious typos. The fanzine writes informally: keep its slang,
  exclamations, swearing, playful spellings and English words as they are.
- Keep band, album, song, label and place names exactly, as well as English lyrics and English
  sentences, except obvious OCR damage (e.g. "MEGADETII" → "MEGADETH").
- Keep addresses, PK (post box) numbers, prices and dates exactly.""",
}


_TOKEN = {"value": "", "at": 0.0}


def token(refresh: bool = False) -> str:
    """A gcloud access token, renewed every 45 minutes (they last an hour) or on demand."""
    if refresh or not _TOKEN["value"] or time.time() - _TOKEN["at"] > 45 * 60:
        _TOKEN["value"] = subprocess.run(["gcloud", "--configuration=personal", "auth", "print-access-token"],
                                         capture_output=True, text=True, check=True).stdout.strip()
        _TOKEN["at"] = time.time()
    return _TOKEN["value"]


def key(model: str, text: str) -> str:
    return hashlib.sha1(f"{model}\0{text}".encode()).hexdigest()


def ask(model: str, pieces: list[str], tok: str, corpus: str = "retro") -> list[str] | None:
    body = {
        "systemInstruction": {"parts": [{"text": INSTRUCTIONS[corpus]}]},
        "contents": [{"role": "user", "parts": [{"text": json.dumps(pieces, ensure_ascii=False)}]}],
        "generationConfig": {"temperature": 0, "responseMimeType": "application/json",
                             "responseSchema": {"type": "ARRAY", "items": {"type": "STRING"}}},
    }
    for attempt in range(6):
        r = requests.post(URL.format(model=model), json=body, timeout=180,
                          headers={"Authorization": f"Bearer {token()}"})
        if r.status_code == 200:
            try:
                data = r.json()
                out = json.loads(data["candidates"][0]["content"]["parts"][0]["text"])
            except (KeyError, IndexError, json.JSONDecodeError):
                return None
            usage = data.get("usageMetadata", {})
            ask.tokens[0] += usage.get("promptTokenCount", 0)
            ask.tokens[1] += usage.get("candidatesTokenCount", 0) + usage.get("thoughtsTokenCount", 0)
            return out if isinstance(out, list) and len(out) == len(pieces) else None
        if r.status_code == 401:
            token(refresh=True)
            continue
        if r.status_code in (429, 500, 503):
            time.sleep(min(60, 2 ** attempt))
            continue
        if r.status_code == 400 and attempt < 2:  # seen once, transient: try again
            time.sleep(2)
            continue
        # Anything else: keep this batch's original text (nothing is cached) rather than stop the run.
        print(f"  ! {r.status_code}: {r.text[:200]}", file=sys.stderr, flush=True)
        return None
    return None


ask.tokens = [0, 0]


def accept(raw: str, fixed: str) -> bool:
    """A correction, not a rewrite: close to the original and about as long."""
    if not fixed.strip():
        return False
    change = Levenshtein.distance(raw, fixed) / max(1, len(raw))
    return change <= MAX_CHANGE and 0.85 <= len(fixed) / max(1, len(raw)) <= 1.15


def correct_pieces(model: str, pieces: list[str], tok: str, corpus: str = "retro") -> list[str]:
    """Corrected pieces (cached); a rejected or failed correction keeps the original."""
    CACHE.mkdir(parents=True, exist_ok=True)
    out = list(pieces)
    todo = [i for i, p in enumerate(pieces) if read_json(CACHE / f"{key(model, p)}.json") is None]
    for start in range(0, len(todo), BATCH):
        idx = todo[start:start + BATCH]
        fixed = ask(model, [pieces[i] for i in idx], tok, corpus)
        for n, i in enumerate(idx):
            f = fixed[n] if fixed else pieces[i]
            ok = accept(pieces[i], f)
            write_json(CACHE / f"{key(model, pieces[i])}.json",
                       {"raw": pieces[i], "fixed": f if ok else pieces[i], "accepted": ok, "proposed": f})
    for i, p in enumerate(pieces):
        out[i] = read_json(CACHE / f"{key(model, p)}.json")["fixed"]
    return out


def corpus_of(slug: str) -> str:
    titles = yaml.safe_load((ROOT / "catalog" / "titles.yaml").read_text())
    return next((t.get("corpus", "retro") for t in titles if t["slug"] == slug), "retro")


def correct_issue(issue: str, model: str, tok: str) -> tuple[int, int]:
    corpus = corpus_of(issue.split("/")[0])
    path = ROOT / "data" / "issues" / issue / "articles.json"
    articles = read_json(path) or []
    changed = total = 0
    for a in articles:
        # Work from the raw text, so correcting twice (or with another model) starts clean.
        a["title_raw"] = a.get("title_raw", a["title"])
        items = [x for x in a["body"] if x["type"] in ("p", "h2", "caption") and x.get("text")]
        pieces = [a["title_raw"]] + [x.get("raw", x["text"]) for x in items]
        fixed = correct_pieces(model, pieces, tok, corpus)
        a["title"] = fixed[0]
        for x, f in zip(items, fixed[1:]):
            x["raw"] = x.get("raw", x["text"])
            x["text"] = f
        total += len(pieces)
        changed += sum(f != p for f, p in zip(fixed, pieces))
    write_json(path, articles, indent=1)
    return changed, total


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("issues", nargs="+")
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--jobs", type=int, default=4)
    args = ap.parse_args()
    tok = token()
    with ThreadPoolExecutor(args.jobs) as pool:
        for issue, (changed, total) in zip(args.issues, pool.map(lambda i: correct_issue(i, args.model, tok), args.issues)):
            print(f"{issue}: {changed}/{total} pieces corrected", flush=True)
    print(f"tokens: {ask.tokens[0]} in, {ask.tokens[1]} out")


if __name__ == "__main__":
    main()

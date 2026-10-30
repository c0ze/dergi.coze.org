"""Meaning vectors for semantic search: article passages → Vertex AI embeddings.

Each article is split into passages of about CHUNK characters (title first; code listings
left out, they would drown the meaning). Each passage is embedded once with
gemini-embedding-001 (multilingual, RETRIEVAL_DOCUMENT) and cached by content hash in
data/search/cache/, so a re-run only pays for new or changed text.

Output, read by api/ (the search service):
  data/search/vectors.npy    float16 [passages × DIMS], L2-normalised
  data/search/passages.json  per passage: article url, title, source, pages, cover, text

Usage: embed.py <slug> [<slug> ...]      (titles that are on the site)
"""

import hashlib
import json
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "search"
CACHE = OUT / "cache"
MODEL = "gemini-embedding-001"
DIMS = 768
CHUNK = 1200
BATCH = 16
URL = (f"https://us-central1-aiplatform.googleapis.com/v1/projects/dergi-coze-org/locations/us-central1/"
       f"publishers/google/models/{MODEL}:predict")
MONTHS = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]


def token() -> str:
    return subprocess.run(["gcloud", "--configuration=personal", "auth", "print-access-token"],
                          capture_output=True, text=True, check=True).stdout.strip()


def passages(article: dict) -> list[str]:
    """The article's prose in passages of about CHUNK characters, each led by the title."""
    parts = [x["text"] for x in article["body"] if x["type"] in ("p", "h2", "caption") and x.get("text")]
    out, cur = [], ""
    for p in parts:
        if cur and len(cur) + len(p) > CHUNK:
            out.append(cur)
            cur = ""
        cur = f"{cur}\n{p}" if cur else p
        while len(cur) > 2 * CHUNK:  # one very long paragraph
            out.append(cur[:CHUNK])
            cur = cur[CHUNK:]
    if cur:
        out.append(cur)
    return [f"{article['title']}\n{t}" for t in out if len(t) > 80]


def embed(texts: list[str], tok: str) -> np.ndarray:
    """Texts in requests of BATCH, a few requests in flight, retried on 429/5xx."""
    def call(group: list[str]) -> list[list[float]]:
        body = {"instances": [{"content": t, "task_type": "RETRIEVAL_DOCUMENT"} for t in group],
                "parameters": {"outputDimensionality": DIMS}}
        for attempt in range(8):
            r = requests.post(URL, json=body, headers={"Authorization": f"Bearer {tok}"}, timeout=120)
            if r.status_code == 200:
                return [p["embeddings"]["values"] for p in r.json()["predictions"]]
            if r.status_code in (429, 500, 503):
                time.sleep(min(60, 2 ** attempt))
                continue
            raise RuntimeError(f"{r.status_code}: {r.text[:300]}")
        raise RuntimeError("embedding kept failing")
    groups = [texts[i:i + BATCH] for i in range(0, len(texts), BATCH)]
    with ThreadPoolExecutor(4) as pool:
        return np.array([v for vs in pool.map(call, groups) for v in vs], dtype=np.float32)


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    titles = {t["slug"]: t["name"] for t in yaml.safe_load((ROOT / "catalog" / "titles.yaml").read_text())}
    corpora = {c["slug"]: c["ad"] for c in yaml.safe_load((ROOT / "catalog" / "corpora.yaml").read_text())}
    corpus_of = {t["slug"]: corpora[t.get("corpus", "retro")]
                 for t in yaml.safe_load((ROOT / "catalog" / "titles.yaml").read_text())}
    private = {t["slug"] for t in yaml.safe_load((ROOT / "catalog" / "titles.yaml").read_text()) if t.get("private")}
    meta, texts = [], []
    for slug in sys.argv[1:]:
        if slug in private:  # the search API is public: private titles are not embedded
            print(f"{slug}: private, skipped")
            continue
        for issue_dir in sorted((ROOT / "data" / "issues" / slug).iterdir()):
            arts = issue_dir / "articles.json"
            if not arts.exists():
                continue
            issue = json.loads((issue_dir / "issue.json").read_text())
            date = issue.get("date")
            when = (f" · {MONTHS[int(date[5:7]) - 1]} {date[:4]}" if len(date) > 4 else f" · {date}") if date else ""
            for a in json.loads(arts.read_text()):
                pages = f"s. {a['pages'][0]}" + (f"–{a['pages'][-1]}" if len(a["pages"]) > 1 else "")
                for t in passages(a):
                    meta.append({"url": f"/{slug}/{issue_dir.name}/{a['slug']}/", "title": a["title"],
                                 "source": f"{titles.get(slug, slug)} · Sayı {issue['number']}{when}",
                                 "pages": pages, "author": a.get("author"), "dergi": titles.get(slug, slug),
                                 "koleksiyon": corpus_of.get(slug),
                                 "year": date[:4] if date else None,
                                 "cover": f"/{slug}/{issue_dir.name}/thumbs/001.webp", "text": t[:400]})
                    texts.append(t)

    CACHE.mkdir(parents=True, exist_ok=True)
    keys = [hashlib.sha1(f"{MODEL}:{DIMS}:{t}".encode()).hexdigest() for t in texts]
    missing = [i for i, k in enumerate(keys) if not (CACHE / f"{k}.npy").exists()]
    print(f"{len(texts)} passages, {len(missing)} to embed (~{sum(len(texts[i]) for i in missing) / 4 / 1e6:.2f} M tokens)")
    tok = token()
    for start in range(0, len(missing), 200):
        batch = missing[start:start + 200]
        vecs = embed([texts[i] for i in batch], tok)
        for i, v in zip(batch, vecs):
            np.save(CACHE / f"{keys[i]}.npy", v)
        print(f"  {start + len(batch)}/{len(missing)}", flush=True)
        if start and start % 2000 == 0:
            tok = token()  # access tokens last an hour

    vectors = np.stack([np.load(CACHE / f"{k}.npy") for k in keys])
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    np.save(OUT / "vectors.npy", vectors.astype(np.float16))
    (OUT / "passages.json").write_text(json.dumps(meta, ensure_ascii=False))
    print(f"wrote {vectors.shape} → {OUT / 'vectors.npy'} ({(OUT / 'vectors.npy').stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()

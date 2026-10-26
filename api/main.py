"""dergi.coze.org search API (Cloud Run): semantic search over article passages.

The passages' meaning vectors are computed offline (pipeline/embed.py, gemini-embedding-001)
and shipped in the image (data/vectors.npy, data/passages.json). A query is embedded with the
same model (RETRIEVAL_QUERY), scored against every passage by cosine similarity, and the best
passage per article decides the article's score.

GET /api/search?q=...&dergi=...&yil=...&limit=20  → {"results": [{url, title, source, pages,
    author, cover, score, snippet}]}
GET /api/health

Served behind Firebase Hosting (/api/** rewrite), which also caches responses at its CDN.
"""

import json
import os
import time
from collections import OrderedDict, defaultdict
from pathlib import Path

import google.auth
import google.auth.transport.requests
import numpy as np
import requests
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

DATA = Path(__file__).parent / "data"
PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "dergi-coze-org")
MODEL = "gemini-embedding-001"
DIMS = 768
URL = (f"https://us-central1-aiplatform.googleapis.com/v1/projects/{PROJECT}/locations/us-central1/"
       f"publishers/google/models/{MODEL}:predict")
MIN_SCORE = 0.55     # below this a passage is unrelated (measured: unrelated ≈ 0.5, related ≈ 0.65+)
MAX_Q = 200
RATE = 30            # queries per minute per client address

VECTORS = np.load(DATA / "vectors.npy").astype(np.float32)
PASSAGES = json.loads((DATA / "passages.json").read_text())
CREDS, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
QUERY_CACHE: OrderedDict[str, np.ndarray] = OrderedDict()
HITS: dict[str, list[float]] = defaultdict(list)


def fold(q: str) -> str:
    return " ".join(q.replace("I", "ı").replace("İ", "i").lower().split())


def embed_query(q: str) -> np.ndarray:
    key = fold(q)
    if key in QUERY_CACHE:
        QUERY_CACHE.move_to_end(key)
        return QUERY_CACHE[key]
    if not CREDS.valid:
        CREDS.refresh(google.auth.transport.requests.Request())
    r = requests.post(URL, timeout=10, headers={"Authorization": f"Bearer {CREDS.token}"},
                      json={"instances": [{"content": q, "task_type": "RETRIEVAL_QUERY"}],
                            "parameters": {"outputDimensionality": DIMS}})
    r.raise_for_status()
    v = np.array(r.json()["predictions"][0]["embeddings"]["values"], dtype=np.float32)
    v /= np.linalg.norm(v)
    QUERY_CACHE[key] = v
    if len(QUERY_CACHE) > 2000:
        QUERY_CACHE.popitem(last=False)
    return v


def limited(client: str) -> bool:
    now = time.time()
    recent = [t for t in HITS[client] if now - t < 60]
    recent.append(now)
    HITS[client] = recent
    return len(recent) > RATE


async def search(request: Request) -> JSONResponse:
    q = (request.query_params.get("q") or "").strip()
    if not q or len(q) > MAX_Q:
        return JSONResponse({"error": "q must be 1–200 characters"}, status_code=400)
    client = (request.headers.get("x-forwarded-for") or request.client.host or "").split(",")[0].strip()
    if limited(client):
        return JSONResponse({"error": "too many requests"}, status_code=429, headers={"Retry-After": "60"})
    dergi = request.query_params.get("dergi") or None
    yil = request.query_params.get("yil") or None
    limit = max(1, min(50, int(request.query_params.get("limit") or 20)))

    try:
        qv = embed_query(q)
    except requests.RequestException:
        return JSONResponse({"error": "embedding unavailable"}, status_code=503)
    scores = VECTORS @ qv
    best: dict[str, tuple[float, int]] = {}
    for i in np.argsort(-scores)[:2000]:
        s = float(scores[i])
        if s < MIN_SCORE:
            break
        p = PASSAGES[i]
        if (dergi and p["dergi"] != dergi) or (yil and p["year"] != yil):
            continue
        if p["url"] not in best:
            best[p["url"]] = (s, int(i))
        if len(best) >= limit:
            break
    results = []
    for url, (s, i) in best.items():
        p = PASSAGES[i]
        results.append({k: p.get(k) for k in ("url", "title", "source", "pages", "author", "cover")}
                       | {"score": round(s, 3), "snippet": p["text"].split("\n", 1)[-1][:240]})
    return JSONResponse({"query": q, "results": results},
                        headers={"Cache-Control": "public, max-age=3600"})


async def health(request: Request) -> JSONResponse:
    return JSONResponse({"ok": True, "passages": len(PASSAGES)})


app = Starlette(routes=[Route("/api/search", search), Route("/api/health", health)])

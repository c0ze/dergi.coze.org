"""dergi.coze.org API (Cloud Run): search, and a public read-only MCP server.

The passages' meaning vectors are computed offline (pipeline/embed.py, gemini-embedding-001)
and shipped in the image (data/vectors.npy, data/passages.json), with the catalogue of
magazines, issues, article texts, authors, games and topics (pipeline/export_api.py,
data/catalog.json.gz). A query is embedded with the same model (RETRIEVAL_QUERY), scored
against every passage by cosine similarity, and the best passage per article decides the
article's score.

GET  /api/search?q=...&dergi=...&yil=...&limit=20  → {"results": [{url, title, source, pages,
     author, cover, score, snippet}]}      (the site's "meaning" search)
GET  /api/health
POST /mcp    MCP over streamable HTTP, stateless, JSON responses, no login; read-only tools.

Served behind Firebase Hosting (/api/** and /mcp rewrites).
"""

import gzip
import json
import os
import re
import time
import unicodedata
from collections import OrderedDict, defaultdict
from pathlib import Path
from typing import Any

import google.auth
import google.auth.exceptions
import google.auth.transport.requests
import numpy as np
import requests
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from starlette.requests import Request
from starlette.responses import JSONResponse

DATA = Path(__file__).parent / "data"
SITE = "https://dergi.coze.org"
PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "dergi-coze-org")
MODEL = "gemini-embedding-001"
DIMS = 768
URL = (f"https://us-central1-aiplatform.googleapis.com/v1/projects/{PROJECT}/locations/us-central1/"
       f"publishers/google/models/{MODEL}:predict")
MIN_SCORE = 0.55     # below this a passage is unrelated (measured: unrelated ≈ 0.5, related ≈ 0.65+)
MAX_Q = 200
RATE = 30            # /api/search queries per minute per client address
MCP_RATE = 60        # MCP requests per minute per client address
RRF_K = 60           # reciprocal rank fusion, as in the site's search.js

VECTORS = np.load(DATA / "vectors.npy").astype(np.float32)
PASSAGES = json.loads((DATA / "passages.json").read_text())
CATALOG = json.loads(gzip.decompress((DATA / "catalog.json.gz").read_bytes()))
CREDS, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
QUERY_CACHE: OrderedDict[str, np.ndarray] = OrderedDict()
HITS: dict[str, list[float]] = defaultdict(list)
TR_ASCII = str.maketrans("çğıöşüâîûÇĞİÖŞÜÂÎÛ", "cgiosuaiuCGIOSUAIU")


def fold(q: str) -> str:
    return " ".join(q.replace("I", "ı").replace("İ", "i").lower().split())


def ascii_fold(text: str) -> str:
    """For keyword matching: "Sayı", "SAYI" and "sayi" are one word."""
    text = unicodedata.normalize("NFKD", text.translate(TR_ASCII)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9$]+", " ", text.lower())


FOLDED = [f" {ascii_fold(p['text'])} " for p in PASSAGES]


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


def limited(client: str, rate: int, bucket: str = "") -> bool:
    now = time.time()
    key = f"{bucket}:{client}"
    recent = [t for t in HITS[key] if now - t < 60]
    recent.append(now)
    HITS[key] = recent
    return len(recent) > rate


def client_of(headers: dict[str, str], fallback: str) -> str:
    return (headers.get("x-forwarded-for") or fallback or "").split(",")[0].strip()


def semantic(q: str, dergi: str | None, yil: str | None, limit: int,
             koleksiyon: str | None = None) -> dict[str, tuple[float, int]]:
    """{article path: (score, best passage index)}, best first."""
    scores = VECTORS @ embed_query(q)
    best: dict[str, tuple[float, int]] = {}
    for i in np.argsort(-scores)[:2000]:
        s = float(scores[i])
        if s < MIN_SCORE:
            break
        p = PASSAGES[i]
        if (dergi and p["dergi"] != dergi) or (yil and p["year"] != yil) \
                or (koleksiyon and p.get("koleksiyon") != koleksiyon):
            continue
        if p["url"] not in best:
            best[p["url"]] = (s, int(i))
        if len(best) >= limit:
            break
    return best


def keyword(q: str, dergi: str | None, yil: str | None, limit: int,
            koleksiyon: str | None = None) -> dict[str, tuple[int, int]]:
    """{article path: (hits, best passage index)} for passages containing every query word."""
    terms = [f" {t} " for t in ascii_fold(q).split() if len(t) > 1]
    if not terms:
        return {}
    best: dict[str, tuple[int, int]] = {}
    for i, text in enumerate(FOLDED):
        if all(t in text for t in terms):
            p = PASSAGES[i]
            if (dergi and p["dergi"] != dergi) or (yil and p["year"] != yil) \
                    or (koleksiyon and p.get("koleksiyon") != koleksiyon):
                continue
            n = sum(text.count(t) for t in terms)
            if p["url"] not in best or n > best[p["url"]][0]:
                best[p["url"]] = (n, i)
    return dict(sorted(best.items(), key=lambda kv: -kv[1][0])[:limit])


async def search_endpoint(request: Request) -> JSONResponse:
    q = (request.query_params.get("q") or "").strip()
    if not q or len(q) > MAX_Q:
        return JSONResponse({"error": "q must be 1–200 characters"}, status_code=400)
    if limited(client_of(dict(request.headers), request.client.host if request.client else ""), RATE, "search"):
        return JSONResponse({"error": "too many requests"}, status_code=429, headers={"Retry-After": "60"})
    dergi = request.query_params.get("dergi") or None
    yil = request.query_params.get("yil") or None
    koleksiyon = request.query_params.get("koleksiyon") or None
    limit = max(1, min(50, int(request.query_params.get("limit") or 20)))
    try:
        best = semantic(q, dergi, yil, limit, koleksiyon)
    except requests.RequestException:
        return JSONResponse({"error": "embedding unavailable"}, status_code=503)
    results = []
    for url, (s, i) in best.items():
        p = PASSAGES[i]
        results.append({k: p.get(k) for k in ("url", "title", "source", "pages", "author", "cover")}
                       | {"score": round(s, 3), "snippet": p["text"].split("\n", 1)[-1][:240]})
    return JSONResponse({"query": q, "results": results},
                        headers={"Cache-Control": "public, max-age=3600"})


async def health(request: Request) -> JSONResponse:
    return JSONResponse({"ok": True, "passages": len(PASSAGES), "articles": len(CATALOG["articles"])})


# ---------------------------------------------------------------------------------------------
# MCP

# Only the corpora that have magazines in this catalogue (titles waiting for permission are left
# out of it by export_api.py).
CORPUS_ABOUT = {"retro": "retro (Commodore 64 and Amiga magazines, 1985–2005)",
                "bilim": "bilim (science and maths magazines)", "metal": "metal (heavy metal fanzines)"}
LIVE_CORPORA = ", ".join(CORPUS_ABOUT.get(k, k) for k, c in CATALOG["corpora"].items() if c["magazines"])

mcp = MCPServer(
    name="dergi.coze.org",
    title="Türk retro bilgisayar dergileri arşivi",
    website_url=SITE,
    instructions=(
        f"An archive of old Turkish magazines and fanzines, in corpora (see `list_corpora`): {LIVE_CORPORA}. "
        "Article texts are in Turkish, read by OCR and proofread by "
        "a language model, so expect occasional OCR errors; each article links the reader pages that show "
        "the original scans. Start with `search` (Turkish or English queries; it matches words and meaning; "
        "`corpus` narrows it), then `get_article` to read. Browse with `list_corpora`, `list_magazines`, "
        "`list_issues`, `get_issue`, `find_authors`/`get_author`, `find_games`/`get_game` (retro), "
        "`find_bands`/`get_band` (metal) and `list_topics`/`get_topic`. Cite article URLs when you quote. "
        "Read-only; no login."),
)
READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
MAGS = CATALOG["magazines"]


def magazine_slug(name: str | None) -> str | None:
    """A magazine slug from a slug or a name, however it is spelt ("Amiga Dunyasi", "64ler")."""
    if not name:
        return None
    key = ascii_fold(name).replace(" ", "")
    for slug, m in MAGS.items():
        if key in (ascii_fold(slug).replace(" ", ""), ascii_fold(m["name"]).replace(" ", "")):
            return slug
    raise ToolError(f"unknown magazine {name!r}; known: " + ", ".join(m["name"] for m in MAGS.values()))


def corpus_name(corpus: str | None) -> str | None:
    """The corpus's Turkish name (as stored on passages) from its slug or name."""
    if not corpus:
        return None
    for slug, c in CATALOG["corpora"].items():
        if ascii_fold(corpus).strip() in (slug, ascii_fold(c["name"]).strip()):
            return c["name"]
    raise ToolError(f"unknown corpus {corpus!r}; known: " + ", ".join(CATALOG["corpora"]))


def article_url(ref: str) -> str:
    """Full article URL from a URL, a path or "magazine/issue/slug"."""
    path = re.sub(r"^https?://[^/]+", "", ref.strip()).strip("/")
    parts = path.split("/")
    if len(parts) == 3 and parts[1].isdigit():
        parts[1] = f"{int(parts[1]):02d}"
    url = f"{SITE}/{'/'.join(parts)}/"
    if url not in CATALOG["articles"]:
        raise ToolError(f"no article at {ref!r}; use a URL from search or get_issue")
    return url


def summary(url: str) -> dict[str, Any]:
    a = CATALOG["articles"][url]
    return {"url": url, "title": a["title"], "corpus": a.get("corpus", "retro"), "magazine": a["magazine_name"],
            "issue": a["issue"],
            "date": a["date"], "pages": a["pages"], "author": a["author"]}


def pick(urls: list[str], dergi: str | None = None, offset: int = 0, limit: int = 50) -> dict[str, Any]:
    if dergi:
        urls = [u for u in urls if CATALOG["articles"][u]["magazine"] == dergi]
    urls = sorted(urls, key=lambda u: (CATALOG["articles"][u]["date"] or "", u))
    limit = max(1, min(100, limit))
    return {"total": len(urls), "offset": offset, "articles": [summary(u) for u in urls[offset:offset + limit]]}


def matching(index: dict[str, dict], query: str | None, limit: int, count) -> list[tuple[str, dict]]:
    items = list(index.items())
    if query:
        q = ascii_fold(query).replace(" ", "")
        items = [(k, v) for k, v in items if q in ascii_fold(v["name"]).replace(" ", "")
                 or any(q in ascii_fold(s).replace(" ", "") for s in v.get("spellings", []))]
    return sorted(items, key=lambda kv: (-count(kv[1]), kv[1]["name"]))[:max(1, min(100, limit))]


@mcp.tool(annotations=READ_ONLY)
def search(query: str, corpus: str | None = None, magazine: str | None = None, year: str | None = None,
           limit: int = 10) -> dict[str, Any]:
    """Search every article by words and by meaning (e.g. "assembler" also finds "makine dili").

    query: Turkish or English, up to 200 characters. corpus: "retro", "bilim" or "metal" (see
    list_corpora). magazine: a magazine name or slug to stay within. year: e.g. "1990".
    limit: 1–30 results. Each result has the article URL (pass it to
    get_article), title, magazine, issue, date, pages, author and a snippet of the best passage.
    """
    query = query.strip()
    if not query or len(query) > MAX_Q:
        raise ToolError("query must be 1–200 characters")
    slug = magazine_slug(magazine)
    dergi = MAGS[slug]["name"] if slug else None
    koleksiyon = corpus_name(corpus)
    limit = max(1, min(30, limit))
    words = keyword(query, dergi, year, 100, koleksiyon)
    try:
        meaning = semantic(query, dergi, year, 100, koleksiyon)
    except (requests.RequestException, google.auth.exceptions.GoogleAuthError):
        meaning = {}  # the embedding service is unreachable: words only
    fused: dict[str, float] = defaultdict(float)
    for ranking in (words, meaning):
        for rank, path in enumerate(ranking):
            fused[path] += 1 / (RRF_K + rank + 1)
    results = []
    for path in sorted(fused, key=lambda p: -fused[p])[:limit]:
        url = SITE + path
        if url not in CATALOG["articles"]:
            continue
        i = (meaning.get(path) or words.get(path))[1]
        how = [n for n, r in (("words", words), ("meaning", meaning)) if path in r]
        results.append(summary(url) | {"matched_by": how,
                                       "snippet": PASSAGES[i]["text"].split("\n", 1)[-1][:300]})
    return {"query": query, "results": results}


@mcp.tool(annotations=READ_ONLY)
def get_article(url: str, offset: int = 0, max_chars: int = 12000) -> dict[str, Any]:
    """Read an article: its proofread text with author, issue, pages, games and topics.

    url: an article URL from search or get_issue (a "magazine/issue/slug" path works too).
    Long articles come in parts: pass next_offset back as offset to read on. max_chars: up to
    40000. Headings are "## ", program listings are fenced code. page_links point to the reader
    pages that show the original scans.
    """
    u = article_url(url)
    a = CATALOG["articles"][u]
    issue = CATALOG["issues"][f"{a['magazine']}/{a['issue']:02d}"]
    max_chars = max(1000, min(40000, max_chars))
    text = a["text"][offset:offset + max_chars]
    end = offset + len(text)
    return summary(u) | {
        "author_url": a["author_url"], "games": a["games"], "topics": a["topics"],
        "issue_url": issue["url"],
        "page_links": [issue["page_url"].format(page=p) for p in a["pages"]],
        "total_chars": len(a["text"]), "offset": offset,
        "next_offset": end if end < len(a["text"]) else None, "text": text}


@mcp.tool(annotations=READ_ONLY)
def list_corpora() -> dict[str, Any]:
    """The archive's corpora (retro computing, science, metal) and the magazines in each."""
    return {"corpora": [{"slug": k} | c for k, c in CATALOG["corpora"].items() if c["magazines"]]}


@mcp.tool(annotations=READ_ONLY)
def list_magazines(corpus: str | None = None) -> dict[str, Any]:
    """The magazines in the archive (optionally one corpus), with their corpus, years, publisher,
    description and issue numbers."""
    name = corpus_name(corpus)
    wanted = {k for k, c in CATALOG["corpora"].items() if c["name"] == name} if name else None
    return {"magazines": [{k: m.get(k) for k in ("slug", "name", "corpus", "years", "publisher", "description", "url")}
                          | {"issues": sorted(m["issues"])} for m in MAGS.values()
                          if not wanted or m.get("corpus") in wanted]}


@mcp.tool(annotations=READ_ONLY)
def list_issues(magazine: str) -> dict[str, Any]:
    """Every issue of one magazine: number, date, page and article counts, and its URL."""
    slug = magazine_slug(magazine)
    issues = [i for i in CATALOG["issues"].values() if i["magazine"] == slug]
    return {"magazine": MAGS[slug]["name"], "issues": [
        {"number": i["number"], "date": i["date"], "pages": i["pages"], "articles": len(i["articles"]),
         "url": i["url"]} for i in sorted(issues, key=lambda i: i["number"])]}


@mcp.tool(annotations=READ_ONLY)
def get_issue(magazine: str, number: int) -> dict[str, Any]:
    """One issue's contents (articles with title, author, pages and URL), and any
    type-in programs extracted from it as runnable C64 .prg files (downloadable from
    their article's page on the site)."""
    slug = magazine_slug(magazine)
    issue = CATALOG["issues"].get(f"{slug}/{number:02d}")
    if not issue:
        raise ToolError(f"{MAGS[slug]['name']} has no issue {number} here; see list_issues")
    return {k: issue[k] for k in ("magazine_name", "number", "date", "pages", "url", "programs")} | {
        "articles": [summary(u) for u in issue["articles"]]}


@mcp.tool(annotations=READ_ONLY)
def find_authors(query: str | None = None, limit: int = 30) -> dict[str, Any]:
    """Authors, most prolific first; query narrows by name (any spelling, Turkish letters optional)."""
    found = matching(CATALOG["authors"], query, limit, lambda a: len(a["articles"]))
    return {"authors": [{"slug": k, "name": a["name"], "articles": len(a["articles"]), "url": a["url"],
                         "spellings": a["spellings"]} for k, a in found]}


@mcp.tool(annotations=READ_ONLY)
def get_author(author: str, magazine: str | None = None, offset: int = 0, limit: int = 50) -> dict[str, Any]:
    """An author's articles across all magazines, oldest first. author: a name or slug."""
    found = CATALOG["authors"].get(author) or next(iter(dict(matching(CATALOG["authors"], author, 1,
                                                                      lambda a: len(a["articles"]))).values()), None)
    if not found:
        raise ToolError(f"no author matches {author!r}; try find_authors")
    return {"name": found["name"], "url": found["url"], "spellings": found["spellings"]} | pick(
        found["articles"], magazine_slug(magazine), offset, limit)


@mcp.tool(annotations=READ_ONLY)
def find_games(query: str | None = None, platform: str | None = None, limit: int = 30) -> dict[str, Any]:
    """Games written about, most covered first. platform: "C64", "Amiga", "PC", "Konsol" or "Diğer"."""
    index = CATALOG["games"]
    if platform:
        index = {k: g for k, g in index.items() if any(p.lower() == platform.lower() for p in g["platforms"])}
    found = matching(index, query, limit, lambda g: sum(len(v) for v in g["platforms"].values()))
    return {"games": [{"slug": k, "name": g["name"], "url": g["url"],
                       "articles": {p: len(v) for p, v in g["platforms"].items()}} for k, g in found]}


@mcp.tool(annotations=READ_ONLY)
def get_game(game: str, platform: str | None = None) -> dict[str, Any]:
    """Articles about one game (reviews, tips, walkthroughs), grouped by platform."""
    g = CATALOG["games"].get(game) or next(iter(dict(matching(
        CATALOG["games"], game, 1, lambda g: sum(len(v) for v in g["platforms"].values()))).values()), None)
    if not g:
        raise ToolError(f"no game matches {game!r}; try find_games")
    return {"name": g["name"], "url": g["url"], "platforms": {
        p: [summary(u) for u in urls] for p, urls in g["platforms"].items()
        if not platform or p.lower() == platform.lower()}}


@mcp.tool(annotations=READ_ONLY)
def find_bands(query: str | None = None, limit: int = 30) -> dict[str, Any]:
    """Bands and musicians covered by the metal fanzines, most covered first."""
    found = matching(CATALOG["bands"], query, limit, lambda b: len(b["articles"]))
    return {"bands": [{"slug": k, "name": b["name"], "articles": len(b["articles"]), "url": b["url"]} for k, b in found]}


@mcp.tool(annotations=READ_ONLY)
def get_band(band: str) -> dict[str, Any]:
    """Articles about one band or musician (interviews, reviews, news). band: a name or slug."""
    b = CATALOG["bands"].get(band) or next(iter(dict(matching(CATALOG["bands"], band, 1,
                                                              lambda b: len(b["articles"]))).values()), None)
    if not b:
        raise ToolError(f"no band matches {band!r}; try find_bands")
    return {"name": b["name"], "url": b["url"]} | pick(b["articles"])


@mcp.tool(annotations=READ_ONLY)
def list_topics(corpus: str | None = None) -> dict[str, Any]:
    """Topics with article counts, per corpus: assembly, BASIC, game reviews (retro); geometry,
    number theory, problems (bilim); interviews, album reviews, the Turkish scene (metal)."""
    name = corpus_name(corpus)
    wanted = {k for k, c in CATALOG["corpora"].items() if c["name"] == name} if name else None
    return {"topics": [{"slug": k, "name": t["name"], "corpus": t.get("corpus", "retro"), "about": t["about"],
                        "articles": len(t["articles"]), "url": t["url"]} for k, t in CATALOG["topics"].items()
                       if not wanted or t.get("corpus", "retro") in wanted]}


@mcp.tool(annotations=READ_ONLY)
def get_topic(topic: str, magazine: str | None = None, offset: int = 0, limit: int = 50) -> dict[str, Any]:
    """Articles on one topic (slug from list_topics), oldest first; page with offset."""
    t = CATALOG["topics"].get(topic)
    if not t:
        raise ToolError(f"unknown topic {topic!r}; see list_topics")
    return {"name": t["name"], "url": t["url"]} | pick(t["articles"], magazine_slug(magazine), offset, limit)


mcp.custom_route("/api/search", methods=["GET"])(search_endpoint)
mcp.custom_route("/api/health", methods=["GET"])(health)
# Stateless with plain JSON answers: any instance can serve any request, nothing to keep alive.
# No login and read-only, so DNS-rebinding protection (meant for servers on localhost) is off.
_app = mcp.streamable_http_app(
    streamable_http_path="/mcp", json_response=True, stateless_http=True, host="0.0.0.0",
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))


async def app(scope, receive, send):
    """The MCP and API app, with a per-client rate limit on /mcp ("/mcp/" works too)."""
    if scope["type"] == "http" and scope["path"].rstrip("/") == "/mcp":
        scope = dict(scope, path="/mcp", raw_path=b"/mcp")
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        client = client_of(headers, scope["client"][0] if scope.get("client") else "")
        if scope["method"] == "POST" and limited(client, MCP_RATE, "mcp"):
            response = JSONResponse({"jsonrpc": "2.0", "id": None,
                                     "error": {"code": -32000, "message": "too many requests; wait a minute"}},
                                    status_code=429, headers={"Retry-After": "60"})
            await response(scope, receive, send)
            return
    await _app(scope, receive, send)

"""Deploy data/site to Firebase Hosting through the Hosting REST API.

Uses the gcloud configuration `personal` for credentials, so no Firebase CLI login is needed
(the CLI on this Mac is logged in to a work account). Settings come from firebase.json:
`ignore` (the /i/ assets live in the GCS bucket), cleanUrls, trailingSlash, headers, rewrites.
Only files whose content changed are uploaded.

Usage: deploy.py [--site dergi-coze-org] [--dry-run]
"""

import argparse
import gzip
import hashlib
import json
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
API = "https://firebasehosting.googleapis.com/v1beta1"
PROJECT = "dergi-coze-org"


def token() -> str:
    return subprocess.run(["gcloud", "--configuration=personal", "auth", "print-access-token"],
                          capture_output=True, text=True, check=True).stdout.strip()


def session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {token()}", "X-Goog-User-Project": PROJECT})
    return s


def glob_re(pattern: str) -> re.Pattern:
    """firebase.json ignore globs: ** crosses folders, * doesn't."""
    out = ""
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif pattern.startswith("**", i):
            out, i = out + ".*", i + 2
        elif pattern[i] == "*":
            out, i = out + "[^/]*", i + 1
        else:
            out, i = out + re.escape(pattern[i]), i + 1
    return re.compile(out + "$")


def ignored(rel: str, patterns: list[str]) -> bool:
    return any(glob_re(p).match(rel) for p in patterns)



def version_config(cfg: dict) -> dict:
    out = {"cleanUrls": cfg.get("cleanUrls", False),
           "trailingSlashBehavior": "ADD" if cfg.get("trailingSlash") else "TRAILING_SLASH_BEHAVIOR_UNSPECIFIED",
           # firebase.json's extglob sources ("**/*.@(css|js)") are what the API calls `glob`.
           "headers": [{"glob": h["source"],
                        "headers": {x["key"]: x["value"] for x in h["headers"]}} for h in cfg.get("headers", [])],
           # /api/** goes to the Cloud Run search service (api/).
           "rewrites": [{"glob": r["source"], "run": r["run"]} for r in cfg.get("rewrites", [])]}
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--site", default=PROJECT)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    cfg = json.loads((ROOT / "firebase.json").read_text())["hosting"]
    public = ROOT / cfg["public"]

    files: dict[str, tuple[str, bytes]] = {}  # "/path" -> (sha256 of gzip, gzip bytes)
    for f in sorted(public.rglob("*")):
        rel = str(f.relative_to(public))
        if f.is_dir() or ignored(rel, cfg.get("ignore", [])):
            continue
        gz = gzip.compress(f.read_bytes(), compresslevel=9, mtime=0)
        files["/" + rel] = (hashlib.sha256(gz).hexdigest(), gz)
    total = sum(len(g) for _, g in files.values())
    print(f"{len(files)} files, {total / 1e6:.1f} MB gzipped")
    if args.dry_run:
        return

    s = session()
    site = f"sites/{args.site}"
    r = s.post(f"{API}/{site}/versions", json={"config": version_config(cfg)})
    r.raise_for_status()
    version = r.json()["name"]

    # Tell Hosting every file's hash; it answers with the hashes it doesn't have yet.
    need, upload_url = set(), None
    items = list(files.items())
    for i in range(0, len(items), 1000):
        r = s.post(f"{API}/{version}:populateFiles", json={"files": {p: h for p, (h, _) in items[i:i + 1000]}})
        r.raise_for_status()
        body = r.json()
        need |= set(body.get("uploadRequiredHashes", []))
        upload_url = body["uploadUrl"]
    by_hash = {h: gz for h, gz in files.values()}
    print(f"uploading {len(need)} changed files")

    def put(h: str) -> None:
        for attempt in range(4):
            r = s.post(f"{upload_url}/{h}", data=by_hash[h], headers={"Content-Type": "application/octet-stream"})
            if r.ok:
                return
            time.sleep(2 ** attempt)
        r.raise_for_status()

    with ThreadPoolExecutor(16) as pool:
        list(pool.map(put, need))

    r = s.patch(f"{API}/{version}?update_mask=status", json={"status": "FINALIZED"})
    r.raise_for_status()
    r = s.post(f"{API}/{site}/releases?versionName={version}", json={"message": "site/deploy.py"})
    r.raise_for_status()
    print(f"released {version} → https://{args.site}.web.app")


if __name__ == "__main__":
    main()

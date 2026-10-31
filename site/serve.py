"""Serve a local build for checking it before a deploy.

The public build points its images at the image Worker (img.dergi.coze.org), which refuses
local origins and may still hold older copies in its edge cache, so text responses get that
prefix rewritten to the build's own i/ folder.

Usage: serve.py [port] [site dir]   (default 8642, data/site)
"""

import http.server
import sys
from functools import partial
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSET_BASE = b"https://img.dergi.coze.org/i"
TEXT = {".html", ".js", ".json", ".css", ".xml"}


class Handler(http.server.SimpleHTTPRequestHandler):
    def send_head(self):
        path = Path(self.translate_path(self.path))
        if path.is_dir():
            if not self.path.split("?")[0].endswith("/"):
                return super().send_head()  # redirects to the trailing slash
            path = path / "index.html"
        if path.suffix not in TEXT or not path.is_file():
            return super().send_head()
        body = path.read_bytes().replace(ASSET_BASE, b"/i")
        self.send_response(200)
        self.send_header("Content-Type", self.guess_type(str(path)))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8642
    site = ROOT / (sys.argv[2] if len(sys.argv) > 2 else "data/site")
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), partial(Handler, directory=str(site)))
    print(f"serving {site} on http://127.0.0.1:{port}/", flush=True)
    server.serve_forever()

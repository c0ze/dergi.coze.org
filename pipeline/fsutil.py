"""Small file helpers shared by the pipeline stages."""

import hashlib
import json
import os
from pathlib import Path


def write_json(path: Path, obj, **kw) -> None:
    """Write JSON atomically: an interrupted run never leaves a half-written file behind
    that a resumed run would mistake for finished work."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, **kw))
    os.replace(tmp, path)


def read_json(path: Path):
    """The file's JSON, or None if it is missing or unreadable (treated as not done)."""
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

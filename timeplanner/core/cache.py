"""On-disk cache invalidated by file mtime.

Only caches **expensive** operations (whole-vault deadline scan). Cache files live in
the repo's .cache/ (gitignored). key = file mtime_ns:size — editing a note changes its
mtime, so the entry auto-invalidates and "edits take effect immediately" is preserved.
Never writes to your Obsidian vault.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..config import config


def _store_path(name: str) -> Path:
    return config.cache_dir / f"{name}.json"


def file_key(path: Path) -> str:
    """File fingerprint: mtime + size. Any change counts as invalidation."""
    st = path.stat()
    return f"{st.st_mtime_ns}:{st.st_size}"


def load_store(name: str) -> dict:
    p = _store_path(name)
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}  # treat a corrupt cache as empty; rebuild next time


def save_store(name: str, data: dict) -> None:
    config.cache_dir.mkdir(parents=True, exist_ok=True)
    p = _store_path(name)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)  # atomic replace to avoid a half-written, corrupt file


def clear() -> int:
    """Clear all caches; return the number of files removed."""
    if not config.cache_dir.is_dir():
        return 0
    n = 0
    for f in config.cache_dir.glob("*.json"):
        f.unlink()
        n += 1
    return n

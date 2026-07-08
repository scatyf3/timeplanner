"""按文件 mtime 失效的落盘缓存。

只缓存**贵**的操作（全库 deadline 扫描）。缓存文件在仓库内的 .cache/（gitignore），
key = 文件 mtime_ns:size —— 改笔记 mtime 变 → 自动失效，「改即生效」不丢。
绝不写你的 Obsidian 库。
"""

from __future__ import annotations

import json
from pathlib import Path

from ..config import config


def _store_path(name: str) -> Path:
    return config.cache_dir / f"{name}.json"


def file_key(path: Path) -> str:
    """文件指纹：mtime + size。任一变化即视为失效。"""
    st = path.stat()
    return f"{st.st_mtime_ns}:{st.st_size}"


def load_store(name: str) -> dict:
    p = _store_path(name)
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}  # 缓存坏了就当空，下次重建


def save_store(name: str, data: dict) -> None:
    config.cache_dir.mkdir(parents=True, exist_ok=True)
    p = _store_path(name)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)  # 原子替换，避免半写坏文件


def clear() -> int:
    """清空所有缓存，返回删除的文件数。"""
    if not config.cache_dir.is_dir():
        return 0
    n = 0
    for f in config.cache_dir.glob("*.json"):
        f.unlink()
        n += 1
    return n

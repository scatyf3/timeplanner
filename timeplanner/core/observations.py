"""Observation library —— a searchable store of *distilled* observations the planner accumulates.

The fourth memory layer, sitting below the others:
  principles.md (git, curated truth) > memory.json candidate principles/thoughts (linear, recent-N injected)
  > **observations (this file): many distilled observations, recalled on demand by keyword query.**

Only curated observations land here (the agent's `remember_observation` during weekly/reflect), never raw
今日感想 chatter. Retrieval mirrors cubox.search: keyword scoring + snippets, no embeddings — the dumbest
explainable thing that works; a vector index can slot in later behind the same search() interface.

Store is data/observations.json (gitignored, same corpus tier as cubox.json). Never touches the vault.
"""

from __future__ import annotations

import datetime as dt
import json

from ..config import config


def _store_path():
    return config.data_dir / "observations.json"


def _load() -> dict:
    p = _store_path()
    if not p.is_file():
        return {"observations": []}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"observations": []}
    d.setdefault("observations", [])
    return d


def _save(d: dict) -> None:
    config.data_dir.mkdir(parents=True, exist_ok=True)
    p = _store_path()
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)  # atomic


def _week_of(date: dt.date) -> str:
    iso = date.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def add(text: str, theme: str = "", source: str = "weekly",
        date: dt.date | None = None) -> bool:
    """Persist one distilled observation. Exact-text duplicates are ignored. Returns whether it was added.

    date = when the observation originated (defaults to today); its ISO week is derived for grouping/recall.
    """
    text = text.strip()
    if not text:
        return False
    d = _load()
    if any(o["text"].strip() == text for o in d["observations"]):
        return False
    date = date or dt.date.today()
    d["observations"].append({
        "added": dt.date.today().isoformat(),
        "date": date.isoformat(),
        "week": _week_of(date),
        "theme": theme.strip(),
        "source": source,
        "text": text,
    })
    _save(d)
    return True


def all_observations() -> list[dict]:
    return _load()["observations"]


def clear() -> None:
    _save({"observations": []})


def search(query: str, limit: int = 8) -> list[dict]:
    """Keyword search over stored observations (text + theme). Mirrors cubox.search's scoring shape.

    Returns hits [{text, theme, date, week, source, score}], highest score first.
    """
    terms = [t for t in query.lower().split() if t]
    if not terms:
        return []
    hits = []
    for o in all_observations():
        text_low = (o.get("text") or "").lower()
        theme_low = (o.get("theme") or "").lower()
        score = sum(3 * text_low.count(t) + 2 * theme_low.count(t) for t in terms)
        if score:
            hits.append({**o, "score": score})
    hits.sort(key=lambda h: (-h["score"], h.get("date", "")))
    return hits[:limit]


def search_summary(query: str, limit: int = 8) -> str:
    """Human/agent-readable recall over the observation library."""
    obs = all_observations()
    if not obs:
        return "（observation 库还是空的 —— reflect/weekly 时 agent 用 remember_observation 攒。）"
    hits = search(query, limit)
    if not hits:
        return f"（observation 库里没搜到「{query}」相关的观察，共 {len(obs)} 条。）"
    lines = [f"# 🔭 observation 命中「{query}」（{len(hits)}/{len(obs)} 条）"]
    for h in hits:
        theme = f"[{h['theme']}] " if h.get("theme") else ""
        lines.append(f"- （{h.get('date','?')}·{h.get('source','')}）{theme}{h['text']}")
    return "\n".join(lines)


def render(limit: int = 40) -> str:
    """CLI display: recent observations grouped by ISO week (newest week first)."""
    obs = all_observations()
    if not obs:
        return "# 🔭 observation 库\n（空 —— reflect/weekly 时 agent 用 remember_observation 攒，或看 README。）"
    lines = [f"# 🔭 observation 库（{len(obs)} 条，显示最近 {min(limit, len(obs))}）"]
    recent = sorted(obs, key=lambda o: (o.get("week", ""), o.get("date", "")), reverse=True)[:limit]
    cur = None
    for o in recent:
        if o.get("week") != cur:
            cur = o.get("week")
            lines.append(f"\n## {cur}")
        theme = f"[{o['theme']}] " if o.get("theme") else ""
        lines.append(f"- （{o.get('date','?')}·{o.get('source','')}）{theme}{o['text']}")
    return "\n".join(lines)

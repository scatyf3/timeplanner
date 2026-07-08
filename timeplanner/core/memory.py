"""Planner memory cache: the **thoughts** of time-planning and the **principles** gradually accumulated.

Corresponds to plan Phase 2's "self-evolution": things the agent works out during plan/reflect and the new
principles it distills land in .cache/memory.json (gitignored), read back at the start of the next session ——
so the planner has continuity and grows experience.

Positioning:
- principles.md (git-tracked) = the **source of truth** you've curated
- memory.json (.cache, cache) = the **candidate thoughts/principles** the agent accumulates; the good ones you
  manually promote into principles.md

Doesn't touch the Obsidian vault; external actions like writing the calendar still go through the assistive gate,
this is just local memory.
"""

from __future__ import annotations

import datetime as dt

from . import cache

STORE = "memory"


def _load() -> dict:
    d = cache.load_store(STORE)
    d.setdefault("principles", [])
    d.setdefault("thoughts", [])
    return d


def _save(d: dict) -> None:
    cache.save_store(STORE, d)


def clear() -> None:
    _save({"principles": [], "thoughts": []})


def add_thought(text: str, kind: str = "plan", date: dt.date | None = None) -> None:
    """Record a planning thought (a judgment, trade-off, or observation during plan/reflect)."""
    text = text.strip()
    if not text:
        return
    d = _load()
    d["thoughts"].append({
        "date": (date or dt.date.today()).isoformat(),
        "kind": kind,
        "text": text,
    })
    _save(d)


def add_principle(text: str, source: str = "reflect", date: dt.date | None = None) -> bool:
    """Accumulate a candidate principle. Exact duplicates aren't recorded twice. Returns whether it was actually added."""
    text = text.strip()
    if not text:
        return False
    d = _load()
    if any(p["text"].strip() == text for p in d["principles"]):
        return False
    d["principles"].append({
        "added": (date or dt.date.today()).isoformat(),
        "source": source,
        "text": text,
    })
    _save(d)
    return True


def recent_thoughts(n: int = 8) -> list[dict]:
    return _load()["thoughts"][-n:]


def principles() -> list[dict]:
    return _load()["principles"]


def prompt_addendum() -> str:
    """A block to append to the system prompt: the candidate principles the agent has accumulated so far. Returns empty string if none."""
    ps = principles()
    if not ps:
        return ""
    lines = ["\n## 你在对话中积累的候选原则（memory.json，逐步和 principles.md 合流）"]
    for p in ps:
        lines.append(f"- （{p['added']}｜来自 {p['source']}）{p['text']}")
    return "\n".join(lines)


def context_block(n_thoughts: int = 5) -> str:
    """A block for plan/reflect tasks: recent planning thoughts, giving the agent continuity."""
    ths = recent_thoughts(n_thoughts)
    if not ths:
        return ""
    lines = ["## 最近的规划思考（planner 记忆）"]
    for t in ths:
        lines.append(f"- [{t['date']}·{t['kind']}] {t['text']}")
    return "\n".join(lines)


def render() -> str:
    """CLI display."""
    d = _load()
    lines = ["# 🧠 Planner 记忆缓存"]
    lines.append(f"\n## 候选原则（{len(d['principles'])}）")
    for p in d["principles"]:
        lines.append(f"- （{p['added']}｜{p['source']}）{p['text']}")
    if not d["principles"]:
        lines.append("（空）")
    lines.append(f"\n## 规划思考（{len(d['thoughts'])}，显示最近 12）")
    for t in d["thoughts"][-12:]:
        lines.append(f"- [{t['date']}·{t['kind']}] {t['text']}")
    if not d["thoughts"]:
        lines.append("（空）")
    return "\n".join(lines)

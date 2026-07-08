"""Planner 记忆缓存：时间规划的**思考**与逐步积累的**原则**。

对应 plan Phase 2 的「自进化」：agent 在 plan/reflect 里想通的东西、提炼出的新原则，
落到 .cache/memory.json（gitignore），下次开工先读回来 —— 于是 planner 有连续性、会长经验。

定位：
- principles.md（git 追踪）= 你curate过的**源头真相**
- memory.json（.cache，缓存）= agent 积累的**候选思考/原则**，你觉得好的再手动提升进 principles.md

不碰 Obsidian 库；写日历那种外部动作仍走辅助式闸门，这里只是本地记忆。
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
    """记一条规划思考（plan/reflect 时的判断、取舍、观察）。"""
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
    """积累一条候选原则。完全重复的不重复记。返回是否真的新增。"""
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
    """拼给 system prompt 的一段：agent 至今积累的候选原则。空则返回空串。"""
    ps = principles()
    if not ps:
        return ""
    lines = ["\n## 你在对话中积累的候选原则（memory.json，逐步和 principles.md 合流）"]
    for p in ps:
        lines.append(f"- （{p['added']}｜来自 {p['source']}）{p['text']}")
    return "\n".join(lines)


def context_block(n_thoughts: int = 5) -> str:
    """拼给 plan/reflect 任务的一段：最近的规划思考，给 agent 连续性。"""
    ths = recent_thoughts(n_thoughts)
    if not ths:
        return ""
    lines = ["## 最近的规划思考（planner 记忆）"]
    for t in ths:
        lines.append(f"- [{t['date']}·{t['kind']}] {t['text']}")
    return "\n".join(lines)


def render() -> str:
    """CLI 展示。"""
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

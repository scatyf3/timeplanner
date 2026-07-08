"""Storage backend routing: per config.backend, dispatch reads/writes to the local timeline or the real GCal.

- staging (proposed drafts) is always local, a backend-agnostic staging area.
- confirm is orchestrated here: read local proposals → assistive diff → land into the current backend.
- the local and gcal modules implement the same set of verbs (list_events / commit_plan / append_actual / summary),
  so switching backends only changes one env var, and core/agent doesn't change a line.
"""

from __future__ import annotations

import datetime as dt

from ..config import config
from . import gcal, timeline
from .gcal import Event


def _m():
    return gcal if config.backend == "gcal" else timeline


def name() -> str:
    return "gcal" if config.backend == "gcal" else "local"


def list_events(date: dt.date | None = None, which: str = "plan") -> list[Event]:
    return _m().list_events(date, which)


def summary(date: dt.date | None = None, which: str = "plan", color: bool | None = None) -> str:
    return _m().summary(date, which, color)


def confirm(date: dt.date | None = None, dry_run: bool = True) -> str:
    """Land the day's local proposal into the current backend's Plan. dry_run only echoes the diff (assistive gate)."""
    date = date or dt.date.today()
    proposed = timeline.list_events(date, timeline.PROPOSED)
    if not proposed:
        return f"（{date:%Y-%m-%d} 没有待确认的 plan 提案；先跑 `timeplanner plan`。）"

    tgt = "GCal Plan 日历" if name() == "gcal" else "本地 Plan timeline"
    head = f"# ✅ 确认写入 {tgt} —— {date:%Y-%m-%d}" + ("  （DRY RUN，未写）" if dry_run else "")
    lines = [head] + [f"+ {e.line()}" for e in proposed]
    if dry_run:
        lines.append("\n加 --yes 落地：`timeplanner confirm --yes`")
        return "\n".join(lines)

    _m().commit_plan(date, proposed)
    timeline.clear_proposed(date)
    lines.append(f"\n✅ 已写入 {len(proposed)} 个事件到 {tgt}")
    return "\n".join(lines)


def log_actual(date: dt.date, start: str, end: str, bucket: str, summary_text: str) -> Event:
    """Log one Actual to the current backend."""
    e = timeline.make_event(date, start, end, bucket, summary_text)
    _m().append_actual(e)
    return e

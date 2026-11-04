"""Storage backend routing: per config.backend, dispatch reads/writes to the local timeline or the real GCal.

- staging (proposed drafts) is always local, a backend-agnostic staging area.
- confirm is orchestrated here: read local proposals → assistive diff → land into the current backend.
- the local and gcal modules implement the same set of verbs (list_events / commit_plan / append_actual / summary),
  so switching backends only changes one env var, and core/agent doesn't change a line.
"""

from __future__ import annotations

import datetime as dt

from ..config import config
from . import classify, gcal, recovery, timeline
from .gcal import Event, norm_bucket


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
    warns = recovery.lint_plan(recovery.day_mode(date), proposed)
    if warns:
        lines.append("\n⚠️ 休息与恢复提醒（不拦截，你决定）：")
        lines += [f"  - {w}" for w in warns]
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


def reclassify(date: dt.date | None = None, dry_run: bool = True) -> str:
    """Re-bucket our own logged Actual events by keyword-classifying their summary. Never touches external
    (hand-made Time record) events. dry_run only previews the color changes; otherwise patches them."""
    date = date or dt.date.today()
    evs = _m().list_events(date, "actual")
    ours = [e for e in evs if not e.external and e.event_id]
    ext = [e for e in evs if e.external]

    changes = []
    for e in ours:
        new = classify.guess_bucket(e.summary)
        if new != norm_bucket(e.bucket):
            changes.append((e, new))

    head = f"# 🎨 重标记 Actual bucket（{name()}）—— {date:%Y-%m-%d}" + ("  （DRY RUN，未改）" if dry_run else "")
    lines = [head, f"我们标记过的事件 {len(ours)} 个，其中需改色 {len(changes)} 个；外部固定约束 {len(ext)} 个（不动）。"]
    if not changes:
        lines.append("（没有需要重标记的 —— 要么已对，要么当天没有我们记的事件。）")
        return "\n".join(lines)
    for e, new in changes:
        lines.append(f"  {e.start:%H:%M}–{e.end:%H:%M} 「{e.summary}」：{norm_bucket(e.bucket)} → {new}")
    if dry_run:
        lines.append("\n加 --yes 落地：`timeplanner reclassify --yes`")
        return "\n".join(lines)

    ok = 0
    for e, new in changes:
        try:
            _m().set_bucket(e.event_id, new)
            ok += 1
        except Exception as ex:  # noqa: BLE001 — one bad patch shouldn't abort the rest
            lines.append(f"  ⚠️ 「{e.summary}」改色失败：{ex}")
    lines.append(f"\n✅ 已重标记 {ok}/{len(changes)} 个事件。")
    return "\n".join(lines)

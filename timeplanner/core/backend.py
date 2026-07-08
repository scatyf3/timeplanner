"""存储后端路由：按 config.backend 把读写分派到本地 timeline 或真 GCal。

- staging（proposed 草案）永远本地，是 backend 无关的暂存区。
- confirm 编排在这里：读本地提案 → 辅助式 diff → 落到当前 backend。
- local 与 gcal 两个模块实现同一套动词（list_events / commit_plan / append_actual / summary），
  于是切后端只改一个 env，core/agent 一行不动。
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


def summary(date: dt.date | None = None, which: str = "plan") -> str:
    return _m().summary(date, which)


def confirm(date: dt.date | None = None, dry_run: bool = True) -> str:
    """把当天本地提案落进当前 backend 的 Plan。dry_run 只回显 diff（辅助式闸门）。"""
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
    """录一条 Actual 到当前 backend。"""
    e = timeline.make_event(date, start, end, bucket, summary_text)
    _m().append_actual(e)
    return e

"""本地 timeline —— GCal 的同 schema 替身，先跑通「确认写入」闭环，不依赖 Google。

三层里的 ①Plan、②Actual 两层先落到本地 JSON（data/plan.json、data/actual.json），
事件 schema 与 gcal.Event 完全一致（含 bucket 标记）。以后配好 OAuth，换成 gcal
后端即可，core/agent 逻辑一行不动。

辅助式闸门：plan 出草案 → stage 到 proposed → `timeplanner confirm` 才真写。
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from pathlib import Path

from ..config import config
from .gcal import BUCKETS, Event

# which ∈ {"plan", "actual", "proposed"}
PLAN, ACTUAL, PROPOSED = "plan", "actual", "proposed"


def _file(which: str) -> Path:
    return config.data_dir / f"{which}.json"


def _load_rows(which: str) -> list[dict]:
    p = _file(which)
    if not p.is_file():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def _save_rows(which: str, rows: list[dict]) -> None:
    config.data_dir.mkdir(parents=True, exist_ok=True)
    p = _file(which)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)  # 原子写


def _row(e: Event) -> dict:
    return {
        "id": e.event_id or uuid.uuid4().hex[:8],
        "summary": e.summary,
        "start": e.start.isoformat(),
        "end": e.end.isoformat(),
        "bucket": e.bucket if e.bucket in BUCKETS else "main",
    }


def _event(r: dict) -> Event:
    return Event(
        summary=r["summary"],
        start=dt.datetime.fromisoformat(r["start"]),
        end=dt.datetime.fromisoformat(r["end"]),
        bucket=r.get("bucket", ""),
        event_id=r.get("id", ""),
        external=False,  # 本地 timeline 全是 planner 写的；外部约束以后并 GCal 时才有
    )


def _hhmm(date: dt.date, s: str) -> dt.datetime:
    """把 'HH:MM' 或完整 ISO 解析成带本地 tz 的 datetime。"""
    s = s.strip()
    if "T" in s or len(s) > 5:
        return dt.datetime.fromisoformat(s).astimezone()
    h, m = s.split(":")
    return dt.datetime.combine(date, dt.time(int(h), int(m))).astimezone()


def events_from_spec(date: dt.date, spec: list[dict]) -> list[Event]:
    """agent 给的结构化草案 → Event 列表。spec 每项：{start,end,bucket,summary}。"""
    out = []
    for it in spec:
        out.append(Event(
            summary=it.get("summary", "(无标题)"),
            start=_hhmm(date, it["start"]),
            end=_hhmm(date, it["end"]),
            bucket=it.get("bucket", "main"),
        ))
    out.sort(key=lambda e: e.start)
    return out


# ---- 读 ----

def list_events(date: dt.date | None = None, which: str = PLAN) -> list[Event]:
    date = date or dt.date.today()
    evs = [_event(r) for r in _load_rows(which)]
    evs = [e for e in evs if e.start.date() == date]
    evs.sort(key=lambda e: e.start)
    return evs


# ---- 写（辅助式：先 stage，再 confirm）----

# -- staging（永远本地，是 backend 无关的暂存区）--

def stage_plan(date: dt.date, spec: list[dict]) -> list[Event]:
    """把 agent 的草案存成 proposed（覆盖当天旧提案），等 confirm。"""
    events = events_from_spec(date, spec)
    rows = [r for r in _load_rows(PROPOSED)
            if dt.datetime.fromisoformat(r["start"]).date() != date]  # 清掉当天旧提案
    rows += [_row(e) for e in events]
    _save_rows(PROPOSED, rows)
    return events


def clear_proposed(date: dt.date) -> None:
    rest = [r for r in _load_rows(PROPOSED)
            if dt.datetime.fromisoformat(r["start"]).date() != date]
    _save_rows(PROPOSED, rest)


def make_event(date: dt.date, start: str, end: str, bucket: str, summary: str) -> Event:
    return Event(summary=summary, start=_hhmm(date, start), end=_hhmm(date, end), bucket=bucket)


# -- 本地 backend 实现（与 gcal 同名接口：commit_plan / append_actual）--

def commit_plan(date: dt.date, events: list[Event]) -> None:
    """当天 Plan 用 events 替换（本地暂无外部事件要保留）。"""
    rows = [r for r in _load_rows(PLAN)
            if dt.datetime.fromisoformat(r["start"]).date() != date]
    rows += [_row(e) for e in events]
    _save_rows(PLAN, rows)


def append_actual(event: Event) -> None:
    rows = _load_rows(ACTUAL)
    rows.append(_row(event))
    _save_rows(ACTUAL, rows)


# ---- 汇总（与 gcal.summary 同格式）----

def summary(date: dt.date | None = None, which: str = PLAN) -> str:
    date = date or dt.date.today()
    label = {"plan": "Plan", "actual": "Actual", "proposed": "待确认提案"}.get(which, which)
    evs = list_events(date, which)
    lines = [f"# 🗓️ 本地 {label} timeline —— {date:%Y-%m-%d}"]
    if not evs:
        lines.append("（空）")
        return "\n".join(lines)
    for e in evs:
        lines.append(e.line())
    return "\n".join(lines)


if __name__ == "__main__":
    print(summary())

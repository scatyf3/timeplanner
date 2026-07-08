"""Local timeline —— a same-schema stand-in for GCal, to close the "confirmed write" loop without depending on Google.

Of the three layers, ①Plan and ②Actual first land in local JSON (data/plan.json, data/actual.json);
the event schema is identical to gcal.Event (including the bucket marker). Once OAuth is set up,
just switch to the gcal backend — the core/agent logic doesn't change a line.

Assistive gate: plan produces a draft → stage into proposed → `timeplanner confirm` actually writes.
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
    tmp.replace(p)  # atomic write


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
        external=False,  # local timeline is all planner-written; external constraints only appear once GCal is merged in
    )


def _hhmm(date: dt.date, s: str) -> dt.datetime:
    """Parse 'HH:MM' or a full ISO string into a datetime with local tz."""
    s = s.strip()
    if "T" in s or len(s) > 5:
        return dt.datetime.fromisoformat(s).astimezone()
    h, m = s.split(":")
    return dt.datetime.combine(date, dt.time(int(h), int(m))).astimezone()


def events_from_spec(date: dt.date, spec: list[dict]) -> list[Event]:
    """Structured draft from the agent → list of Events. Each spec item: {start,end,bucket,summary}."""
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


# ---- read ----

def list_events(date: dt.date | None = None, which: str = PLAN) -> list[Event]:
    date = date or dt.date.today()
    evs = [_event(r) for r in _load_rows(which)]
    evs = [e for e in evs if e.start.date() == date]
    evs.sort(key=lambda e: e.start)
    return evs


# ---- write (assistive: stage first, then confirm) ----

# -- staging (always local, a backend-agnostic staging area) --

def stage_plan(date: dt.date, spec: list[dict]) -> list[Event]:
    """Store the agent's draft as proposed (overwriting the day's old proposal), awaiting confirm."""
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


# -- local backend implementation (same-named interface as gcal: commit_plan / append_actual) --

def commit_plan(date: dt.date, events: list[Event]) -> None:
    """Replace the day's Plan with events (locally there are no external events to preserve for now)."""
    rows = [r for r in _load_rows(PLAN)
            if dt.datetime.fromisoformat(r["start"]).date() != date]
    rows += [_row(e) for e in events]
    _save_rows(PLAN, rows)


def append_actual(event: Event) -> None:
    rows = _load_rows(ACTUAL)
    rows.append(_row(event))
    _save_rows(ACTUAL, rows)


# ---- summary (same format as gcal.summary) ----

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

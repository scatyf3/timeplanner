"""Daily write-back: upsert one day's Actual (Time-record) timeline into its Obsidian Daily note.

Mirrors core.weekly's assisted-gate shape — `write_back` upserts a single own-marked section
and never touches anything else. Like the weekly rollup it carries a machine-readable
companion comment (`tp-day`) so dashboards parse data instead of prose.
"""
from __future__ import annotations

import datetime as dt
import json
import re

from . import backend, notes, weekly
from .rollover import _upsert_section

SECTION_TITLE = "⏱ 今日 timeline（timeplanner）"
DAY_RE = re.compile(r"<!--\s*tp-day\s*(\{.*?\})\s*-->", re.S)


def day_comment(bucket_minutes: dict) -> str:
    """Serialize {bucket: minutes} into the tp-day HTML comment line."""
    clean = {str(k): int(round(float(v))) for k, v in (bucket_minutes or {}).items()}
    return "<!-- tp-day " + json.dumps(clean, ensure_ascii=False, separators=(",", ":")) + " -->"


def parse_day(text: str) -> dict:
    """Pull {bucket: minutes} back out of a note. {} when absent/malformed."""
    m = DAY_RE.search(text or "")
    if not m:
        return {}
    try:
        return {str(k): int(v) for k, v in json.loads(m.group(1)).items()}
    except (ValueError, TypeError, AttributeError):
        return {}


def _fmt_h(minutes: int) -> str:
    h, m = divmod(int(minutes), 60)
    return f"{h}h{m:02d}" if m else f"{h}h"


def gather(date: dt.date) -> tuple[list, dict]:
    """A day's Actual events (all-day ones dropped) + {bucket: minutes}."""
    events = [e for e in backend.list_events(date, which="actual") if not e.all_day]
    events.sort(key=lambda e: e.start)
    mins: dict[str, int] = {}
    for e in events:
        b = weekly.bucket_of(e)
        mins[b] = mins.get(b, 0) + int((e.end - e.start).total_seconds() // 60)
    return events, mins


def render_body(date: dt.date) -> str:
    """Human-readable timeline + bucket totals, with the tp-day comment appended."""
    events, mins = gather(date)
    if not events:
        return "（这天没有 Time record 事件）"
    lines = [
        f"- {e.start:%H:%M}–{e.end:%H:%M}　`{weekly.bucket_label(weekly.bucket_of(e))}`　{e.summary}"
        for e in events
    ]
    total = sum(mins.values())
    parts = " · ".join(
        f"{weekly.bucket_label(b)} {_fmt_h(m)}"
        for b, m in sorted(mins.items(), key=lambda kv: -kv[1])
    )
    lines += ["", f"合计 **{_fmt_h(total)}** —— {parts}", "", day_comment(mins)]
    return "\n".join(lines)


def write_back(date: dt.date, dry_run: bool = True) -> str:
    """Upsert the timeline section into the Daily note. dry_run only echoes the diff."""
    path = notes.daily_path(date)
    body = render_body(date)
    head = f"# ✅ 写回 Daily 笔记 {date:%Y-%m-%d} —— {path.name}" + ("  （DRY RUN，未写）" if dry_run else "")
    if dry_run:
        return (f"{head}\n目标：{path}\n\n## {SECTION_TITLE}\n\n{body}\n\n"
                "加 --yes 落地：`timeplanner confirm-daily --yes`")
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_upsert_section(text, SECTION_TITLE, body), encoding="utf-8")
    return f"{head}\n✅ 已 upsert `## {SECTION_TITLE}` 到 {path}"

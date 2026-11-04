"""Weekly review: aggregate a week's Time-record (②Actual) time into projects.

Read-only gather + deterministic bucket-hour totals here; the fuzzy event→project
attribution and the "what got done" narrative are the agent's job (see agent.py's
weekly_context / stage_weekly tools). Write-back into the Obsidian Weekly note follows
the same assistive gate as plan→confirm: the agent stages markdown, `timeplanner
confirm-weekly` previews the diff, `--yes` upserts one clearly-marked section.

Weekly note path convention (periodic-para plugin): {vault}/{weekly_glob}/{ISO-year}-W{WW}.md,
Monday-start. Uses the ISO year (isocalendar().year), which can differ from the calendar
year for late-December / early-January dates.
"""

from __future__ import annotations

import json
import re
import datetime as dt
from pathlib import Path

from ..config import config
from . import backend, classify, notes
from .gcal import BUCKET_HEX, Event
from .rollover import _upsert_section

SECTION_TITLE = "⏱ 本周项目时间（timeplanner）"
INSIGHTS_TITLE = "💡 本周想法 & 洞见（timeplanner）"
# write-back kinds → the markdown section title each upserts. "review" = per-project time;
# "insights" = the week's noteworthy ideas distilled from 今日感想.
KINDS = {"review": SECTION_TITLE, "insights": INSIGHTS_TITLE}

# Machine-readable companion to the review section: one HTML comment carrying
# {project_folder_name: minutes} (plus "_unclassified"). Obsidian doesn't render it;
# the Weekly dashboard parses it instead of scraping the agent's prose.
ALLOC_RE = re.compile(r"<!--\s*tp-alloc\s*(\{.*?\})\s*-->", re.S)


def alloc_comment(alloc: dict) -> str:
    """Serialize {folder: minutes} into the tp-alloc HTML comment line."""
    clean = {str(k): int(round(float(v))) for k, v in (alloc or {}).items()}
    return "<!-- tp-alloc " + json.dumps(clean, ensure_ascii=False, separators=(",", ":")) + " -->"


def parse_alloc(text: str) -> dict:
    """Pull {folder: minutes} back out of a note (or staged body). {} when absent/malformed."""
    m = ALLOC_RE.search(text or "")
    if not m:
        return {}
    try:
        return {str(k): int(v) for k, v in json.loads(m.group(1)).items()}
    except (ValueError, TypeError, AttributeError):
        return {}

# Time-record events carry no timeplanner bucket marker (external=True, bucket="").
# Reverse-map their GCal colorId → bucket. Mirrors gcal.BUCKET_COLOR_ID
# (main/side=7 blue, life=5 yellow, health=2 sage, fun=10 basil).
_COLOR_BUCKET = {"7": "work", "5": "life", "2": "health", "10": "fun"}
_BUCKET_LABEL = {"work": "工作", "life": "生活", "health": "健康", "fun": "娱乐", "other": "其它"}
# hex for rendering; "work" reuses main's blue, others map straight through.
_BUCKET_HEX = {"work": BUCKET_HEX["main"], "life": BUCKET_HEX["life"],
               "health": BUCKET_HEX["health"], "fun": BUCKET_HEX["fun"], "other": "#8899a6"}


def bucket_of(e: Event) -> str:
    """A Time-record event's bucket: its own marker if present, else reverse-mapped from GCal color, else 'other'."""
    if e.bucket:
        return "work" if e.bucket in ("main", "side") else e.bucket
    if e.color_id in _COLOR_BUCKET:
        return _COLOR_BUCKET[e.color_id]
    b = classify.explicit_bucket(e.summary)   # e.g. an Apple Watch shortcut's "[fun] 打塔"
    if b:
        return "work" if b in ("main", "side") else b
    return "other"


def bucket_label(b: str) -> str:
    return _BUCKET_LABEL.get(b, b)


def bucket_hex(b: str) -> str:
    return _BUCKET_HEX.get(b, "#8899a6")


def week_bounds(date: dt.date) -> tuple[dt.date, dt.date]:
    """(Monday, Sunday) of the ISO week containing `date`."""
    monday = date - dt.timedelta(days=date.weekday())   # weekday(): Mon=0
    return monday, monday + dt.timedelta(days=6)


def target_week(args_date: str | None) -> dt.date:
    """Pick the date whose week we aggregate: --date if given, else default to last completed week (today − 7d)."""
    if args_date:
        return dt.date.fromisoformat(args_date)
    return dt.date.today() - dt.timedelta(days=7)


def weekly_note_path(date: dt.date) -> Path:
    """Absolute path to the Weekly note for `date`'s ISO week ({ISO-year}-W{WW}.md)."""
    iso = date.isocalendar()
    folder = config.weekly_glob.format(year=iso.year)
    return config.vault / folder / f"{iso.year}-W{iso.week:02d}.md"


def _hours(events: list[Event]) -> float:
    return sum((e.end - e.start).total_seconds() for e in events) / 3600.0


def gather_week(date: dt.date) -> dict:
    """Deterministic gather for the ISO week of `date`. Returns:
      days: [{date, weekday, events:[Event], done_todos, reflections, projects}]
      bucket_hours: {bucket: hours} over the whole week
      total_hours, projects (roster), deadlines, monday, sunday.
    Loops backend.list_events(day, 'actual') 7× (Time record = ②Actual)."""
    monday, sunday = week_bounds(date)
    days = []
    bucket_hours: dict[str, float] = {}
    for i in range(7):
        d = monday + dt.timedelta(days=i)
        try:
            events = backend.list_events(d, "actual")
        except Exception:  # noqa: BLE001 — a bad day (network/auth) must not sink the whole week
            events = []
        for e in events:
            b = bucket_of(e)
            bucket_hours[b] = bucket_hours.get(b, 0.0) + (e.end - e.start).total_seconds() / 3600.0
        dn = notes.parse_daily(d)
        days.append({
            "date": d,
            "weekday": "一二三四五六日"[d.weekday()],
            "events": events,
            "done_todos": dn.done_todos,
            "reflections": dn.reflections,
        })

    projects = notes.list_projects()
    deadlines = notes.scan_deadlines(within_days=21, today=sunday)
    return {
        "monday": monday,
        "sunday": sunday,
        "days": days,
        "bucket_hours": bucket_hours,
        "total_hours": sum(bucket_hours.values()),
        "projects": projects,
        "deadlines": deadlines,
    }


def _fmt_h(h: float) -> str:
    return f"{h:.1f}h"


def context_blob(date: dt.date) -> str:
    """Agent/human-readable text: the week's Time-record events by day + bucket-hour totals +
    project roster (the event→project vocabulary) + done TODOs / 今日感想 + near deadlines."""
    g = gather_week(date)
    iso = date.isocalendar()
    lines = [f"# 📆 本周 Time record —— {iso.year}-W{iso.week:02d}"
             f"（{g['monday']:%m-%d} 周一 ~ {g['sunday']:%m-%d} 周日）"]

    has_events = any(d["events"] for d in g["days"])
    if not has_events:
        lines.append("\n（本周无 Time record 数据 —— GCal Actual 日历这一周为空。）")

    # bucket-hour totals (deterministic — agent should not re-sum these)
    if g["bucket_hours"]:
        lines.append(f"\n**各 bucket 小时数（共 {_fmt_h(g['total_hours'])}）：**")
        for b, h in sorted(g["bucket_hours"].items(), key=lambda kv: -kv[1]):
            lines.append(f"- {_BUCKET_LABEL.get(b, b)}：{_fmt_h(h)}")

    # per-day events + reflections
    for d in g["days"]:
        if not d["events"] and not d["done_todos"] and not d["reflections"]:
            continue
        lines.append(f"\n## {d['date']:%m-%d} 周{d['weekday']}")
        for e in d["events"]:
            b = _BUCKET_LABEL.get(bucket_of(e), "其它")
            lines.append(f"- {e.start:%H:%M}–{e.end:%H:%M} [{b}] {e.summary}")
        for t in d["done_todos"]:
            lines.append(f"- ✅ {t}")
        for r in d["reflections"]:
            lines.append(f"- 💭 {r}")

    # project roster — the disambiguation vocabulary for event→project attribution
    if g["projects"]:
        lines.append("\n**项目名册（把事件归到这些 project）：**")
        for p in g["projects"]:
            tag = f" #{p.tag}" if p.tag else ""
            subs = "、".join(p.subnotes[:12])
            more = f" …(+{len(p.subnotes) - 12})" if len(p.subnotes) > 12 else ""
            lines.append(f"- **{p.folder}**（{p.slug}{tag}）：{subs}{more}" if subs
                         else f"- **{p.folder}**（{p.slug}{tag}）")

    if g["deadlines"]:
        lines.append("\n**近期 deadline（21 天内）：**")
        for dl in g["deadlines"][:10]:
            days = (dl.date - g["sunday"]).days
            lines.append(f"- {dl.date} (T{days:+d}) {dl.text}  ⟨{dl.source}⟩")

    return "\n".join(lines)


# ---- assistive stage → confirm write-back ----

def _stage_dir() -> Path:
    return config.data_dir / "weekly_proposed"


def _stage_path(date: dt.date, kind: str = "review") -> Path:
    iso = date.isocalendar()
    suffix = "" if kind == "review" else f".{kind}"   # keep review filenames stable
    return _stage_dir() / f"{iso.year}-W{iso.week:02d}{suffix}.md"


def stage(date: dt.date, markdown: str, kind: str = "review") -> None:
    """Persist a staged weekly section (review | insights), awaiting `timeplanner confirm-weekly`."""
    _stage_dir().mkdir(parents=True, exist_ok=True)
    _stage_path(date, kind).write_text(markdown, encoding="utf-8")


def staged(date: dt.date, kind: str = "review") -> str | None:
    """The staged markdown for this week's given kind, or None if nothing's staged."""
    p = _stage_path(date, kind)
    return p.read_text(encoding="utf-8") if p.is_file() else None


def clear_staged(date: dt.date, kind: str = "review") -> None:
    _stage_path(date, kind).unlink(missing_ok=True)


def write_back(date: dt.date, body: str, dry_run: bool = True, kind: str = "review") -> str:
    """Upsert one own-marked section (review | insights) into the Weekly note, never touching other content.
    dry_run only echoes the diff; otherwise reads/upserts/writes (creating dirs & note if missing)."""
    title = KINDS.get(kind, SECTION_TITLE)
    path = weekly_note_path(date)
    iso = date.isocalendar()
    head = f"# ✅ 写回 Weekly 笔记 {iso.year}-W{iso.week:02d} —— {path.name}" + ("  （DRY RUN，未写）" if dry_run else "")
    section = f"## {title}\n\n{body.strip()}"
    if dry_run:
        return (f"{head}\n目标：{path}\n\n{section}\n\n"
                "加 --yes 落地：`timeplanner confirm-weekly --yes`")

    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_upsert_section(text, title, body.strip()), encoding="utf-8")
    return f"{head}\n✅ 已 upsert `## {title}` 到 {path}"

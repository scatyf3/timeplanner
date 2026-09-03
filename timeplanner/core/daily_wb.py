"""Daily write-back: upsert one day's Actual (Time-record) timeline into its Obsidian Daily note.

Mirrors core.weekly's assisted-gate shape — `write_back` upserts a single own-marked section
and never touches anything else. Like the weekly rollup it carries a machine-readable
companion comment (`tp-day`) so dashboards parse data instead of prose.
"""
from __future__ import annotations

import datetime as dt
import json
import re

from ..config import config
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


# ---------------------------------------------------------------- inline backfill
# Deterministic event→project matching, using the same vocabulary the weekly agent is
# told to disambiguate with: folder name, README slug, the tag's last segment, and the
# stems of the project folder's sibling notes. Longest match wins; no match → unassigned.
PROJ_LINE = re.compile(r"^(\s*\d+\.\s*\[\[[^\]|]+\|([^\]]+)\]\])(.*)$")
HR_TAIL = re.compile(r"\s*\d+\s*hr\s*\d*\s*$")
TOTAL_LINE = re.compile(r"^\s*\d+\s*hr\s*\d*\s*$")


def _fmt_hr(minutes: int) -> str:
    """LifeOS's own time syntax: `2hr` / `1hr30` (its regex is /(\\d+)hr?(\\d+)?/)."""
    h, m = divmod(int(minutes), 60)
    return f"{h}hr{m:02d}" if m else f"{h}hr"


def match_project(summary: str, projects: list) -> object | None:
    s = (summary or "").lower()
    best, best_len = None, 0
    for p in projects:
        cands = [p.folder, p.slug] + ([p.tag.split("/")[-1]] if p.tag else []) + list(p.subnotes or [])
        for c in cands:
            c = (c or "").strip().lower()
            if len(c) >= 3 and c in s and len(c) > best_len:
                best, best_len = p, len(c)
    return best


def project_minutes(date: dt.date) -> tuple[dict, int]:
    """{project_folder: minutes} for a day's Actual events, plus unmatched minutes.
    An agent-staged allocation (see `timeplanner daily`) wins over the literal matcher."""
    st = staged_alloc(date)
    if st is not None:
        unassigned = int(st.pop("_unassigned", 0))
        return st, unassigned
    events, _ = gather(date)
    projects = notes.list_projects()
    per: dict[str, int] = {}
    unmatched = 0
    for e in events:
        mins = int((e.end - e.start).total_seconds() // 60)
        p = match_project(e.summary, projects)
        if p:
            per[p.folder] = per.get(p.folder, 0) + mins
        else:
            unmatched += mins
    return per, unmatched


def backfill_project_list(text: str, per_project: dict) -> tuple[str, int]:
    """Append `Nhr` to each `## Project List` entry, replacing any existing one.
    Only lines matching the strict numbered-wikilink shape are touched; everything else
    in the note is left byte-for-byte alone. Returns (new_text, lines_changed)."""
    lines = text.splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.strip() == "## Project List"), None)
    if start is None:
        return text, 0
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].startswith("## ") or lines[j].startswith("# "):
            end = j
            break
    changed, total = 0, 0
    for i in range(start + 1, end):
        m = PROJ_LINE.match(lines[i])
        if not m:
            continue
        head, folder, tail = m.group(1), m.group(2).strip(), HR_TAIL.sub("", m.group(3))
        mins = int(per_project.get(folder, 0))
        new = f"{head}{tail} {_fmt_hr(mins)}" if mins else head + tail
        total += mins
        if new != lines[i]:
            lines[i], changed = new, changed + 1
    # section total on its own line (LifeOS's totalTimeReg), replacing a previous one
    body = [ln for ln in lines[start + 1:end] if not TOTAL_LINE.match(ln)]
    while body and not body[-1].strip():
        body.pop()
    if total:
        body += ["", _fmt_hr(total)]
    lines = lines[:start + 1] + body + [""] + lines[end:]
    return "\n".join(lines) + ("\n" if text.endswith("\n") else ""), changed


def write_inline(date: dt.date, dry_run: bool = True) -> str:
    """Backfill per-project hours into the Daily note's `## Project List`."""
    path = notes.daily_path(date)
    head = f"# ⏱ 回填 Project List 时长 {date:%Y-%m-%d} —— {path.name}" + ("  （DRY RUN，未写）" if dry_run else "")
    if not path.is_file():
        return f"{head}\n（找不到 {path}）"
    per, unmatched = project_minutes(date)
    text = path.read_text(encoding="utf-8")
    new, changed = backfill_project_list(text, per)
    if not changed:
        return f"{head}\n（Project List 没有可回填的行；匹配到 {len(per)} 个项目）"
    detail = "\n".join(f"  {k}  {_fmt_hr(v)}" for k, v in sorted(per.items(), key=lambda kv: -kv[1]))
    note = f"\n未归到任何项目：{_fmt_hr(unmatched)}" if unmatched else ""
    if dry_run:
        section = "\n".join(new.splitlines()[
            next(i for i, l in enumerate(new.splitlines()) if l.strip() == "## Project List"):][:12])
        return f"{head}\n{detail}{note}\n\n{section}\n\n加 --yes 落地：`timeplanner confirm-daily --inline --yes`"
    path.write_text(new, encoding="utf-8")
    return f"{head}\n{detail}{note}\n✅ 已回填 {changed} 行 → {path}"


# ------------------------------------------------------- agent-assisted attribution
# The deterministic matcher above only fires on literal vocabulary. For everything else
# (口语 like "main project收菜", "spec compact" vs slug `speccompact`, "lc") an agent maps
# the day's *work-bucket* events onto the roster and stages {folder: minutes}; the staged
# allocation then wins over the deterministic one. Same assisted gate as weekly.
LIFECYCLE_RE = re.compile(r"^lifecycle:\s*(\S+)\s*$", re.M)


def _lifecycle_of(project) -> str:
    try:
        return (LIFECYCLE_RE.search(project.readme.read_text(encoding="utf-8", errors="ignore")) or [None, ""])[1]
    except (OSError, TypeError, IndexError):
        return ""


def context_blob(date: dt.date) -> str:
    """What the agent needs to attribute a day: the work-bucket events + the project roster
    (with each project's lifecycle, so the allocation rules can be stated in terms of it)."""
    events, _ = gather(date)
    work = [e for e in events if weekly.bucket_of(e) == "work"]
    lines = [f"# {date:%Y-%m-%d} 的 Time record（只列 work bucket，其余 bucket 不参与项目归属）"]
    if not work:
        lines.append("（这天没有 work bucket 事件）")
    for e in work:
        mins = int((e.end - e.start).total_seconds() // 60)
        lines.append(f"- {e.start:%H:%M}–{e.end:%H:%M}（{mins} 分钟）{e.summary}")
    total = sum(int((e.end - e.start).total_seconds() // 60) for e in work)
    lines.append(f"\nwork 合计 **{total} 分钟**（分配结果的总和必须等于它）")

    projects = notes.list_projects()
    if projects:
        lines.append("\n**项目名册（folder 是分配时要用的键）：**")
        for p in projects:
            lc = _lifecycle_of(p)
            tag = f" #{p.tag}" if p.tag else ""
            subs = "、".join(p.subnotes[:12])
            more = f" …(+{len(p.subnotes) - 12})" if len(p.subnotes) > 12 else ""
            head = f"- **{p.folder}**（{p.slug}{tag}｜lifecycle: {lc or '未标'}）"
            lines.append(f"{head}：{subs}{more}" if subs else head)
    return "\n".join(lines)


def _stage_dir():
    return config.data_dir / "daily_proposed"


def _stage_path(date: dt.date):
    return _stage_dir() / f"{date:%Y-%m-%d}.json"


def stage_alloc(date: dt.date, alloc: dict) -> None:
    _stage_dir().mkdir(parents=True, exist_ok=True)
    clean = {str(k): int(round(float(v))) for k, v in (alloc or {}).items()}
    _stage_path(date).write_text(json.dumps(clean, ensure_ascii=False, indent=1), encoding="utf-8")


def staged_alloc(date: dt.date) -> dict | None:
    p = _stage_path(date)
    if not p.is_file():
        return None
    try:
        return {str(k): int(v) for k, v in json.loads(p.read_text(encoding="utf-8")).items()}
    except (ValueError, TypeError, AttributeError):
        return None


def clear_staged(date: dt.date) -> None:
    _stage_path(date).unlink(missing_ok=True)

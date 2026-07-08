"""Parse Obsidian daily notes / project notes → workload signals.

Read-only. Treat the vault as a read-only data source, never write. Infer workload
in the spirit of `plan`: start with the dumbest but explainable rules, calibrate
later against actual completion, don't reach for a model too early.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..config import config

# Tasks plugin deadline syntax: 📅 YYYY-MM-DD
DEADLINE_RE = re.compile(r"📅\s*(\d{4}-\d{2}-\d{2})")
# Unchecked todo (checkbox)
UNCHECKED_RE = re.compile(r"^\s*-\s*\[\s\]\s*(.+)$")
CHECKED_RE = re.compile(r"^\s*-\s*\[[xX]\]\s*(.+)$")
# Top-level "## section"
HEADING_RE = re.compile(r"^#{1,6}\s+(.*)$")


def _daily_path(date: dt.date) -> Path:
    """Build the absolute path to a given day's note following the vault's path convention."""
    folder = config.daily_glob.format(year=f"{date:%Y}", month=f"{date:%m}")
    return config.vault / folder / f"{date:%Y-%m-%d}.md"


def _split_sections(text: str) -> dict[str, str]:
    """Split markdown by top-level headings into {title: body}."""
    sections: dict[str, str] = {}
    current = "_preamble"
    buf: list[str] = []
    for line in text.splitlines():
        m = HEADING_RE.match(line)
        if m:
            sections[current] = "\n".join(buf).strip()
            current = m.group(1).strip()
            buf = []
        else:
            buf.append(line)
    sections[current] = "\n".join(buf).strip()
    return sections


def _numbered_or_bullet_items(block: str) -> list[str]:
    """Extract items from a block: accepts both `1. xxx` and `- xxx`, skips blank lines and quote blocks."""
    items: list[str] = []
    for line in block.splitlines():
        s = line.strip()
        if not s or s.startswith(">") or s.startswith("#"):
            continue
        m = re.match(r"^(?:\d+\.|[-*])\s+(.+)$", s)
        if m:
            items.append(m.group(1).strip())
    return items


def _todo_done(content: str) -> tuple[str, bool]:
    """A bullet's content → (clean text, done?). Done = checked `[x]` OR whole item struck `~~…~~`.

    Supports both completion styles: Tasks-style checkbox and Markdown strikethrough.
    """
    done = False
    cb = re.match(r"^\[([ xX])\]\s*(.*)$", content)   # `[x] …` / `[ ] …`
    if cb:
        done = cb.group(1).lower() == "x"
        content = cb.group(2).strip()
    stripped = content.strip()
    if len(stripped) >= 4 and stripped.startswith("~~") and stripped.endswith("~~"):
        done = True
        content = stripped[2:-2].strip()
    return content, done


@dataclass
class DailyNote:
    date: dt.date
    exists: bool
    path: Path
    todos: list[str] = field(default_factory=list)          # unfinished items under ## TODO
    done_todos: list[str] = field(default_factory=list)     # finished ones (checked [x] or ~~struck~~)
    unchecked_budget: list[str] = field(default_factory=list)  # unchecked items in the scoreboard
    checked_budget: list[str] = field(default_factory=list)
    priorities: dict[str, str] = field(default_factory=dict)  # Main/Side/exploration
    takeaway: str = ""


def _parse_daily_text(text: str) -> dict:
    """Pure function: daily note body → structured content (todos/budget/priorities/takeaway). This is what's cacheable."""
    sections = _split_sections(text)

    todos: list[str] = []
    done_todos: list[str] = []
    for name, body in sections.items():
        if "TODO" in name.upper():
            for raw in _numbered_or_bullet_items(body):
                text, done = _todo_done(raw)
                (done_todos if done else todos).append(text)

    unchecked, checked = [], []
    for line in text.splitlines():
        m = UNCHECKED_RE.match(line)
        if m:
            unchecked.append(m.group(1).strip())
            continue
        m = CHECKED_RE.match(line)
        if m:
            checked.append(m.group(1).strip())

    priorities: dict[str, str] = {}
    for name, body in sections.items():
        if "优先级" in name or "work" in name.lower():
            for line in body.splitlines():
                pm = re.match(r"^\s*-\s*\*\*(.+?)\*\*[：:]\s*(.*)$", line)
                if pm:
                    priorities[pm.group(1).strip()] = pm.group(2).strip()

    takeaway = ""
    m = re.search(r"今日\s*takeaway[^\n]*[:：]?\s*(.*?)(?:\n|$)", text)
    if m:
        cand = m.group(1).strip()
        # when takeaway is empty, don't mistake the next template label (e.g. "next action before stopping…") for content
        bad = ("#", ">")
        labels = ("next action", "停手前", "今日感想", "感想")
        if cand and not cand.startswith(bad) and not any(k in cand for k in labels):
            takeaway = cand

    return {
        "todos": todos,
        "done_todos": done_todos,
        "unchecked_budget": unchecked,
        "checked_budget": checked,
        "priorities": priorities,
        "takeaway": takeaway,
    }


def parse_daily(date: dt.date | None = None) -> DailyNote:
    date = date or dt.date.today()
    path = _daily_path(date)
    if not path.is_file():
        return DailyNote(date=date, exists=False, path=path)
    # read live; the vault is a read-only data source, edits take effect immediately
    return DailyNote(date=date, exists=True, path=path,
                     **_parse_daily_text(path.read_text(encoding="utf-8")))


@dataclass
class Deadline:
    date: dt.date
    text: str
    source: str


def _extract_deadlines(md: Path) -> list[dict]:
    """Extract all unfinished 📅 deadlines in a file (no date filtering, so it's cache-reusable)."""
    try:
        text = md.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    out: list[dict] = []
    for line in text.splitlines():
        if "📅" not in line or CHECKED_RE.match(line):
            continue
        m = DEADLINE_RE.search(line)
        if not m:
            continue
        try:
            dt.date.fromisoformat(m.group(1))  # validate
        except ValueError:
            continue
        clean = DEADLINE_RE.sub("", line).strip(" -*[]x")
        out.append({"date": m.group(1), "text": clean[:120], "source": md.name})
    return out


def scan_deadlines(within_days: int = 14, today: dt.date | None = None) -> list[Deadline]:
    """Scan Tasks `📅` deadlines in project/README notes, keep those within the next within_days. Read live."""
    today = today or dt.date.today()
    horizon = today + dt.timedelta(days=within_days)
    if not config.vault_ok():
        return []

    found: list[Deadline] = []
    # only scan Projects (the main source of deadlines) to avoid a slow full-vault scan
    roots = [config.vault / "1. Projects", config.vault / "1. Project"]
    for root in roots:
        if not root.is_dir():
            continue
        for md in root.rglob("*.md"):
            for item in _extract_deadlines(md):
                d = dt.date.fromisoformat(item["date"])
                if today <= d <= horizon:
                    found.append(Deadline(date=d, text=item["text"], source=item["source"]))
    found.sort(key=lambda x: x.date)
    return found


@dataclass
class WorkloadEstimate:
    focus_blocks: int
    todo_count: int
    near_deadlines: int
    urgent_deadlines: int
    rationale: str


def estimate_workload(date: dt.date | None = None) -> WorkloadEstimate:
    """v1 rules: explainable, calibratable. Don't jump straight to a complex model.

    - unfinished TODO count + deadlines in the next 3 days → decide how many focus blocks today
    - the template's default budget is 3x 90min blocks; here we suggest a range of 2~4
    """
    date = date or dt.date.today()
    daily = parse_daily(date)
    deadlines = scan_deadlines(within_days=14, today=date)
    urgent = [d for d in deadlines if (d.date - date).days <= 3]

    todo_count = len(daily.todos)
    near = len(deadlines)
    urgent_n = len(urgent)

    # simple monotonic function, clamped to [2, 4]
    score = todo_count + 2 * urgent_n + max(0, near - urgent_n)
    if score <= 2:
        blocks = 2
    elif score <= 5:
        blocks = 3
    else:
        blocks = 4

    bits = [f"未完成 TODO {todo_count} 条", f"14 天内 deadline {near} 个"]
    if urgent_n:
        bits.append(f"其中 3 天内 {urgent_n} 个（加权）")
    rationale = "；".join(bits) + f" → 建议 {blocks} 个专注 block（模板默认 3）。"

    return WorkloadEstimate(
        focus_blocks=blocks,
        todo_count=todo_count,
        near_deadlines=near,
        urgent_deadlines=urgent_n,
        rationale=rationale,
    )


def summary(date: dt.date | None = None) -> str:
    """Human-readable workload summary (M1 read-only output & agent tool return)."""
    date = date or dt.date.today()
    daily = parse_daily(date)
    est = estimate_workload(date)
    deadlines = scan_deadlines(within_days=14, today=date)

    lines = [f"# 📓 笔记 workload —— {date:%Y-%m-%d}"]
    if not daily.exists:
        lines.append(f"（今天还没有日记：{daily.path}）")
    else:
        lines.append(f"日记：{daily.path.name}")

    lines.append("")
    lines.append(f"**workload 估计**：{est.rationale}")

    if daily.todos or daily.done_todos:
        lines.append("")
        head = f"**今日 TODO（剩 {len(daily.todos)}"
        if daily.done_todos:
            head += f"，已完成 {len(daily.done_todos)}"
        lines.append(head + "）：**")
        lines += [f"- {t}" for t in daily.todos]
        lines += [f"- ~~{t}~~" for t in daily.done_todos]

    if daily.priorities:
        lines.append("")
        lines.append("**work 优先级：**")
        for k, v in daily.priorities.items():
            lines.append(f"- {k}：{v or '（空）'}")

    if deadlines:
        lines.append("")
        lines.append("**临近 deadline（14 天内）：**")
        for d in deadlines[:12]:
            days = (d.date - date).days
            flag = "🔴" if days <= 3 else "🟡"
            lines.append(f"- {flag} {d.date} (T-{days}) {d.text}  ⟨{d.source}⟩")

    if daily.takeaway:
        lines.append("")
        lines.append(f"**昨/今 takeaway：** {daily.takeaway}")

    return "\n".join(lines)


if __name__ == "__main__":
    print(summary())

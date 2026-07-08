"""解析 Obsidian 日记 / project 笔记 → workload 信号。

只读。库当只读数据源，绝不写。workload 推断按 plan 的基调：先用最笨但可解释的规则，
事后按实际完成度校准，别过早上模型。
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..config import config
from . import cache

# Tasks 插件 deadline 语法：📅 YYYY-MM-DD
DEADLINE_RE = re.compile(r"📅\s*(\d{4}-\d{2}-\d{2})")
# 未完成 todo（复选框）
UNCHECKED_RE = re.compile(r"^\s*-\s*\[\s\]\s*(.+)$")
CHECKED_RE = re.compile(r"^\s*-\s*\[[xX]\]\s*(.+)$")
# 顶层「## 段」
HEADING_RE = re.compile(r"^#{1,6}\s+(.*)$")


def _daily_path(date: dt.date) -> Path:
    """按库里的路径约定拼出某天日记的绝对路径。"""
    folder = config.daily_glob.format(year=f"{date:%Y}", month=f"{date:%m}")
    return config.vault / folder / f"{date:%Y-%m-%d}.md"


def _split_sections(text: str) -> dict[str, str]:
    """把 markdown 按顶层 heading 切成 {标题: 正文}。"""
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
    """从一段里抽出条目：既认 `1. xxx` 也认 `- xxx`，忽略空行与引用块。"""
    items: list[str] = []
    for line in block.splitlines():
        s = line.strip()
        if not s or s.startswith(">") or s.startswith("#"):
            continue
        m = re.match(r"^(?:\d+\.|[-*])\s+(.+)$", s)
        if m:
            items.append(m.group(1).strip())
    return items


@dataclass
class DailyNote:
    date: dt.date
    exists: bool
    path: Path
    todos: list[str] = field(default_factory=list)          # ## TODO 下的条目
    unchecked_budget: list[str] = field(default_factory=list)  # 记分板里没打勾的项
    checked_budget: list[str] = field(default_factory=list)
    priorities: dict[str, str] = field(default_factory=dict)  # Main/Side/探索
    takeaway: str = ""


def _parse_daily_text(text: str) -> dict:
    """纯函数：日记正文 → 结构化内容（todos/预算/优先级/takeaway）。可缓存的就是它。"""
    sections = _split_sections(text)

    todos: list[str] = []
    for name, body in sections.items():
        if name.upper().startswith("TODO") or "TODO" in name.upper():
            todos.extend(_numbered_or_bullet_items(body))

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
        # 空 takeaway 时别把下一个模板标签（如「停手前的 next action…」）误当内容
        bad = ("#", ">")
        labels = ("next action", "停手前", "今日感想", "感想")
        if cand and not cand.startswith(bad) and not any(k in cand for k in labels):
            takeaway = cand

    return {
        "todos": todos,
        "unchecked_budget": unchecked,
        "checked_budget": checked,
        "priorities": priorities,
        "takeaway": takeaway,
    }


def _cached_daily_text(path: Path) -> dict:
    """按 mtime 缓存日记解析内容到 .cache/daily.json。文件没变就不重读不重解析。"""
    if not config.cache_enabled:
        return _parse_daily_text(path.read_text(encoding="utf-8"))
    store = cache.load_store("daily")
    key = str(path)
    fk = cache.file_key(path)
    entry = store.get(key)
    if entry and entry.get("k") == fk:
        return entry["v"]                # 命中：直接用缓存里的内容
    fields = _parse_daily_text(path.read_text(encoding="utf-8"))
    store[key] = {"k": fk, "v": fields}
    cache.save_store("daily", store)
    return fields


def parse_daily(date: dt.date | None = None) -> DailyNote:
    date = date or dt.date.today()
    path = _daily_path(date)
    if not path.is_file():
        return DailyNote(date=date, exists=False, path=path)
    return DailyNote(date=date, exists=True, path=path, **_cached_daily_text(path))


@dataclass
class Deadline:
    date: dt.date
    text: str
    source: str


def _extract_deadlines(md: Path) -> list[dict]:
    """抽一个文件里所有未完成的 📅 deadline（不按日期过滤，便于缓存复用）。"""
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
            dt.date.fromisoformat(m.group(1))  # 校验合法
        except ValueError:
            continue
        clean = DEADLINE_RE.sub("", line).strip(" -*[]x")
        out.append({"date": m.group(1), "text": clean[:120], "source": md.name})
    return out


def scan_deadlines(within_days: int = 14, today: dt.date | None = None) -> list[Deadline]:
    """扫库里 project/README 的 Tasks `📅` deadline，取未来 within_days 内的。

    贵操作，走 mtime 缓存：每个文件的 deadline 列表按 mtime+size 缓存到 .cache/，
    文件没变就不重读。缓存里存**全部** deadline，按日期过滤在读缓存之后做。
    """
    today = today or dt.date.today()
    horizon = today + dt.timedelta(days=within_days)
    if not config.vault_ok():
        return []

    use_cache = config.cache_enabled
    old = cache.load_store("deadlines") if use_cache else {}
    new: dict[str, dict] = {}  # 重建 → 顺带剔除已删除的文件

    found: list[Deadline] = []
    # 只扫 Projects（deadline 的主要来源），避免全库慢扫
    roots = [config.vault / "1. Projects", config.vault / "1. Project"]
    for root in roots:
        if not root.is_dir():
            continue
        for md in root.rglob("*.md"):
            key = str(md)
            try:
                fk = cache.file_key(md)
            except OSError:
                continue
            entry = old.get(key)
            if use_cache and entry and entry.get("k") == fk:
                raw = entry["v"]                    # 命中：跳过重读
            else:
                raw = _extract_deadlines(md)         # 未命中：重读
            new[key] = {"k": fk, "v": raw}
            for item in raw:
                d = dt.date.fromisoformat(item["date"])
                if today <= d <= horizon:
                    found.append(Deadline(date=d, text=item["text"], source=item["source"]))

    if use_cache and new != old:
        cache.save_store("deadlines", new)
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
    """v1 规则：可解释、可校准。别一上来搞复杂模型。

    - 未完成 TODO 数 + 近 3 天 deadline 数 → 决定今天摆几个专注 block
    - 模板默认预算是 3 个 90min block；这里给 2~4 的建议区间
    """
    date = date or dt.date.today()
    daily = parse_daily(date)
    deadlines = scan_deadlines(within_days=14, today=date)
    urgent = [d for d in deadlines if (d.date - date).days <= 3]

    todo_count = len(daily.todos)
    near = len(deadlines)
    urgent_n = len(urgent)

    # 简单单调函数，clamp 到 [2, 4]
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
    """人读的 workload summary（M1 只读输出 & agent 工具返回）。"""
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

    if daily.todos:
        lines.append("")
        lines.append("**今日 TODO：**")
        lines += [f"- {t}" for t in daily.todos]

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

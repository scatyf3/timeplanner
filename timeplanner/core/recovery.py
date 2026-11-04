"""Rest & recovery —— decide what kind of day today is, and lint a plan draft against it.

Rules (see principles.md「休息与恢复」):
- **rest**: the weekly full day off (config.rest_weekday). No main/side blocks at all.
- **post_event**: a big event (onsite / final round / deadline / submission…) is today →
  after it ends, the rest of the day is off; write the next action before playing.
- **evening_off**: a minor event (phone screen / one interview round) is today →
  after it ends, that evening is off; tomorrow is a normal day.
- **reentry**: a big event was yesterday → morning off, afternoon back to the anchor
  block, the bar for the day is one task that starts in 5 minutes.
  If today is also the rest day, the two merge into a single rest day (no extra day off).
- **normal**: none of the above.

Plus one rule for every day: a `fun` block that comes straight after a main/side block
gets a 10–15 min `life` wind-down in between (write next action → tidy the room → play).

Everything here is deterministic and read-only; detection failures (no GCal, no vault)
degrade to "normal" and never crash the CLI.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

from ..config import config
from .gcal import Event, norm_bucket

REST, POST_EVENT, EVENING_OFF, REENTRY, NORMAL = "rest", "post_event", "evening_off", "reentry", "normal"
WORK_BUCKETS = {"main", "side"}
RITUAL_GAP = dt.timedelta(minutes=30)   # fun starting within this of a work block → wants a wind-down
NOON = dt.time(12, 0)
# prep/review blocks mention the event but aren't the event ("TikTok 面试准备", "mock interview")
_NOT_THE_EVENT = ["准备", "prep", "mock", "模拟", "复盘", "review"]

_WEEKDAYS = {
    "mon": 0, "monday": 0, "周一": 0, "星期一": 0,
    "tue": 1, "tuesday": 1, "周二": 1, "星期二": 1,
    "wed": 2, "wednesday": 2, "周三": 2, "星期三": 2,
    "thu": 3, "thursday": 3, "周四": 3, "星期四": 3,
    "fri": 4, "friday": 4, "周五": 4, "星期五": 4,
    "sat": 5, "saturday": 5, "周六": 5, "星期六": 5,
    "sun": 6, "sunday": 6, "周日": 6, "周天": 6, "星期日": 6, "星期天": 6,
}


def rest_weekday() -> int | None:
    """config.rest_weekday → Python weekday (Mon=0), or None when unset/unparseable (feature off)."""
    s = (config.rest_weekday or "").strip().lower()
    if not s:
        return None
    if s.isdigit() and 0 <= int(s) <= 6:
        return int(s)
    return _WEEKDAYS.get(s)


def _kw_match(text: str, keywords: list[str]) -> str | None:
    """First keyword found in text. ASCII keywords match on word boundaries (so `oa` ≠ `load`), CJK as substrings."""
    low = text.lower()
    for kw in keywords:
        if kw.isascii():
            if re.search(rf"(?<![a-z0-9]){re.escape(kw)}(?![a-z0-9])", low):
                return kw
        elif kw in low:
            return kw
    return None


@dataclass
class MarkedEvent:
    date: dt.date
    summary: str
    tier: str                 # "big" | "minor"
    end: dt.datetime | None   # None for an all-day / deadline-only marker


def _calendar_events(date: dt.date) -> list[Event]:
    """Calendar events that day that can mark a real event: subscribed refs, external (hand-made) Plan
    events, and logged Actual events. Planner-written Plan blocks are skipped — they're our own drafts.
    Never raises."""
    from . import backend, gcal  # local import: backend imports gcal/timeline, keep module load light
    out: list[Event] = []
    for fetch in (lambda: gcal.list_ref_events(date) if config.gcal_ref_ids else [],
                  lambda: [e for e in backend.list_events(date, "plan") if e.external],
                  lambda: backend.list_events(date, "actual")):
        try:
            out.extend(fetch())
        except Exception:  # noqa: BLE001 — missing OAuth / network / corrupt file → just skip that source
            continue
    return out


def _deadline_texts(date: dt.date) -> list[str]:
    """📅 deadlines that fall exactly on `date` (project notes in the vault). Never raises.
    They still go through the keyword tiers — a homework 📅 doesn't make the day a big event."""
    from . import notes
    try:
        return [d.text for d in notes.scan_deadlines(within_days=0, today=date)]
    except Exception:  # noqa: BLE001
        return []


def marked_events(date: dt.date, events: list[Event] | None = None,
                  deadlines: list[str] | None = None) -> list[MarkedEvent]:
    """Big/minor events on `date`. `events`/`deadlines` can be injected (tests, or a draft being linted)."""
    events = _calendar_events(date) if events is None else events
    deadlines = _deadline_texts(date) if deadlines is None else deadlines
    out: list[MarkedEvent] = []
    seen: set[str] = set()
    items = [(e.summary, None if e.all_day else e.end) for e in events]
    items += [(t, None) for t in deadlines]
    for text, end in items:
        key = text.strip().lower()
        if key in seen or _kw_match(text, _NOT_THE_EVENT):
            continue
        tier = _tier(text)
        if tier:
            seen.add(key)
            out.append(MarkedEvent(date, text, tier, end))
    return out


def _tier(text: str) -> str | None:
    if _kw_match(text, config.big_event_keywords):
        return "big"
    if _kw_match(text, config.minor_event_keywords):
        return "minor"
    return None


@dataclass
class DayMode:
    date: dt.date
    kind: str
    reason: str
    off_after: dt.datetime | None = None   # post_event / evening_off: work blocks after this are flagged
    rules: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return {REST: "休息日", POST_EVENT: "大事件当天", EVENING_OFF: "小事件当天",
                REENTRY: "回来日", NORMAL: "普通日"}[self.kind]


def _evening_cutoff(date: dt.date) -> dt.datetime:
    return dt.datetime.combine(date, dt.time(18, 0)).astimezone()


def day_mode(date: dt.date | None = None,
             today_events: list[MarkedEvent] | None = None,
             yesterday_events: list[MarkedEvent] | None = None) -> DayMode:
    """Classify `date`. Priority: big event today > rest day > reentry (yesterday big) > minor today > normal."""
    date = date or dt.date.today()
    today_ev = marked_events(date) if today_events is None else today_events
    yest_ev = marked_events(date - dt.timedelta(days=1)) if yesterday_events is None else yesterday_events
    is_rest = rest_weekday() == date.weekday()

    big_today = [m for m in today_ev if m.tier == "big"]
    if big_today:
        ends = [m.end for m in big_today if m.end]
        off = max(ends) if ends else None
        names = "、".join(m.summary for m in big_today)
        rules = ["事件结束后今天剩下的时间全 off，不排 main/side。",
                 "收工前先写下回来后的第一件事（5 分钟能开始），再玩。"]
        if is_rest:
            rules.append("今天本来就是休息日：事件之外不排工作，明天正常回来，不额外再休。")
        elif rest_weekday() == (date + dt.timedelta(days=1)).weekday():
            rules.append("明天本来就是休息日，恢复并进去，后天正常回来。")
        else:
            rules.append("明天上午也 off，下午回到主场。")
        return DayMode(date, POST_EVENT, f"今天有大事件：{names}", off, rules)

    if is_rest:
        reason = "每周固定休息日"
        if any(m.tier == "big" for m in yest_ev):
            reason += "（昨天的大事件恢复期并进来了，不额外再休）"
        return DayMode(date, REST, reason, None, [
            "全天不排 main/side，也不排「半办公」——半休休不够。",
            "只排 life / fun / health；出门最好，人在外面自然碰不到工作。",
            "不用愧疚：这是排好的休息，不是逃避。"])

    big_yest = [m for m in yest_ev if m.tier == "big"]
    if big_yest:
        names = "、".join(m.summary for m in big_yest)
        return DayMode(date, REENTRY, f"昨天有大事件：{names}", None, [
            "上午 off（或只做浅活），中午前不排 main/side。",
            "下午回到主场那一段；今天的底线只有一件 5 分钟能开始的小事，做完就算成功。",
            "别补「落下的进度」——回来比补回来重要。"])

    minor_today = [m for m in today_ev if m.tier == "minor"]
    if minor_today:
        ends = [m.end for m in minor_today if m.end]
        off = max(max(ends), _evening_cutoff(date)) if ends else _evening_cutoff(date)
        names = "、".join(m.summary for m in minor_today)
        return DayMode(date, EVENING_OFF, f"今天有面试/小事件：{names}", off, [
            "面完那个晚上 off 就够，明天照常。",
            "晚上之前的时段正常排。"])

    return DayMode(date, NORMAL, "没有休息日/面试/deadline 标记", None, [])


def lint_plan(mode: DayMode, events: list[Event]) -> list[str]:
    """Soft warnings for a plan draft against the day mode + the wind-down ritual. Never blocks anything."""
    warns: list[str] = []
    work = [e for e in events if norm_bucket(e.bucket) in WORK_BUCKETS and not e.external]

    if mode.kind == REST and work:
        warns.append(f"休息日排了 {len(work)} 个工作块（{_names(work)}）——休息日不排 main/side。")
    elif mode.kind == REENTRY:
        early = [e for e in work if e.start.time() < NOON]
        if early:
            warns.append(f"回来日上午排了工作（{_names(early)}）——上午 off，下午再回主场。")
    elif mode.kind in (POST_EVENT, EVENING_OFF) and mode.off_after:
        late = [e for e in work if e.start >= mode.off_after]
        if late:
            warns.append(f"{mode.off_after:%H:%M} 之后还排了工作（{_names(late)}）——事件结束后这段是 off 的。")

    ordered = sorted(events, key=lambda e: e.start)
    for prev, nxt in zip(ordered, ordered[1:]):
        if (norm_bucket(prev.bucket) in WORK_BUCKETS and norm_bucket(nxt.bucket) == "fun"
                and nxt.start - prev.end <= RITUAL_GAP):
            warns.append(f"{prev.end:%H:%M} 从「{prev.summary}」直接进「{nxt.summary}」——"
                         "中间加 10–15min life 收尾：写 next action → 收拾房间 → 再玩。")
    return warns


def _names(evs: list[Event]) -> str:
    return "、".join(f"{e.start:%H:%M} {e.summary}" for e in evs[:3]) + ("…" if len(evs) > 3 else "")


def summary(date: dt.date | None = None, mode: DayMode | None = None) -> str:
    """Text block for `timeplanner summary` and the agent context."""
    date = date or dt.date.today()
    mode = mode or day_mode(date)
    rw = rest_weekday()
    rest_txt = "未设置" if rw is None else ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][rw]
    lines = [f"# 🛌 休息与恢复 —— {date:%Y-%m-%d}",
             f"今天：**{mode.label}**（{mode.reason}）；每周休息日：{rest_txt}"]
    if mode.off_after:
        lines.append(f"off 起点：{mode.off_after:%H:%M}")
    lines += [f"- {r}" for r in mode.rules]
    return "\n".join(lines)


if __name__ == "__main__":
    print(summary())

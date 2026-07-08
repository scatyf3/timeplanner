"""查本地 ActivityWatch REST → 专注 block / 分类时长。

这是「③ Observed（机器观测）」层，只读，用来交叉验证自报的 Actual。
AW 通常在 localhost:5600。多台机器/多 hostname 时自动挑当天有事件的 bucket。
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field

import requests

from ..config import config

TIMEOUT = 5


def _day_range(date: dt.date) -> tuple[str, str]:
    """本地整天 [00:00, 次日00:00) 的 ISO 字符串（带本地 tz offset）。"""
    start = dt.datetime.combine(date, dt.time.min).astimezone()
    end = start + dt.timedelta(days=1)
    return start.isoformat(), end.isoformat()


def is_up() -> bool:
    try:
        requests.get(f"{config.aw_host}/api/0/info", timeout=2).raise_for_status()
        return True
    except requests.RequestException:
        return False


def _buckets() -> dict[str, dict]:
    r = requests.get(f"{config.aw_host}/api/0/buckets/", timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def _events(bucket_id: str, start: str, end: str) -> list[dict]:
    r = requests.get(
        f"{config.aw_host}/api/0/buckets/{bucket_id}/events",
        params={"start": start, "end": end, "limit": 10000},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    return r.json()


def _pick_bucket(buckets: dict[str, dict], bucket_type: str, start: str, end: str) -> tuple[str, list[dict]]:
    """在给定 type 的 bucket 里挑当天事件最多的那个（处理多 hostname）。"""
    best_id, best_events = "", []
    for bid, meta in buckets.items():
        if meta.get("type") != bucket_type:
            continue
        try:
            ev = _events(bid, start, end)
        except requests.RequestException:
            continue
        if len(ev) > len(best_events):
            best_id, best_events = bid, ev
    return best_id, best_events


@dataclass
class FocusBlock:
    start: dt.datetime
    end: dt.datetime

    @property
    def minutes(self) -> float:
        return (self.end - self.start).total_seconds() / 60


@dataclass
class Observed:
    date: dt.date
    active_minutes: float = 0.0
    focus_blocks: list[FocusBlock] = field(default_factory=list)
    top_apps: list[tuple[str, float]] = field(default_factory=list)   # (app, minutes)
    top_titles: list[tuple[str, float]] = field(default_factory=list)
    available: bool = True
    note: str = ""


def _merge_notafk(afk_events: list[dict], merge_gap_s: int = 300, min_block_min: int = 25) -> tuple[float, list[FocusBlock]]:
    """把 not-afk 事件合并成专注 block：间隔 < merge_gap 的接起来，≥ min_block 的算一个 block。"""
    spans: list[tuple[dt.datetime, dt.datetime]] = []
    for e in afk_events:
        if e.get("data", {}).get("status") != "not-afk":
            continue
        # AW 时间戳是 UTC，转本地时区再用（否则显示的 block 时间会偏时差）
        ts = dt.datetime.fromisoformat(e["timestamp"]).astimezone()
        dur = float(e.get("duration", 0))
        spans.append((ts, ts + dt.timedelta(seconds=dur)))
    spans.sort()

    active_s = sum((b - a).total_seconds() for a, b in spans)

    blocks: list[FocusBlock] = []
    for a, b in spans:
        if blocks and (a - blocks[-1].end).total_seconds() <= merge_gap_s:
            blocks[-1].end = max(blocks[-1].end, b)
        else:
            blocks.append(FocusBlock(start=a, end=b))
    blocks = [bl for bl in blocks if bl.minutes >= min_block_min]
    return active_s / 60, blocks


def observe(date: dt.date | None = None) -> Observed:
    date = date or dt.date.today()
    start, end = _day_range(date)

    if not is_up():
        return Observed(date=date, available=False, note=f"ActivityWatch 未响应（{config.aw_host}）")

    try:
        buckets = _buckets()
    except requests.RequestException as e:
        return Observed(date=date, available=False, note=f"读 bucket 失败：{e}")

    _, afk_events = _pick_bucket(buckets, "afkstatus", start, end)
    active_min, blocks = _merge_notafk(afk_events)

    _, win_events = _pick_bucket(buckets, "currentwindow", start, end)
    app_min: dict[str, float] = defaultdict(float)
    title_min: dict[str, float] = defaultdict(float)
    for e in win_events:
        d = e.get("data", {})
        dur = float(e.get("duration", 0)) / 60
        app_min[d.get("app", "?")] += dur
        title_min[d.get("title", "?")] += dur

    top_apps = sorted(app_min.items(), key=lambda x: -x[1])[:8]
    top_titles = sorted(title_min.items(), key=lambda x: -x[1])[:8]

    return Observed(
        date=date,
        active_minutes=active_min,
        focus_blocks=blocks,
        top_apps=top_apps,
        top_titles=top_titles,
        available=True,
    )


def summary(date: dt.date | None = None) -> str:
    date = date or dt.date.today()
    obs = observe(date)
    lines = [f"# 🖥️ ActivityWatch 观测 —— {date:%Y-%m-%d}"]
    if not obs.available:
        lines.append(obs.note)
        return "\n".join(lines)

    h, m = divmod(int(obs.active_minutes), 60)
    lines.append(f"**在机时长（非 AFK）：** {h}h{m:02d}m")
    lines.append(f"**专注 block（≥25min）：** {len(obs.focus_blocks)} 个")
    for b in obs.focus_blocks:
        lines.append(f"  - {b.start:%H:%M}–{b.end:%H:%M}（{int(b.minutes)}min）")

    if obs.top_apps:
        lines.append("")
        lines.append("**Top 应用：**")
        for app, mins in obs.top_apps:
            if mins >= 1:
                lines.append(f"  - {app}: {int(mins)}min")
    return "\n".join(lines)


if __name__ == "__main__":
    print(summary())

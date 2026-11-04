"""Demo for core/recovery.py — runs on made-up events, touches nothing real.

    python scripts/demo_recovery.py

Uses a throwaway data dir and no vault/GCal, so your data/*.json, calendars and notes
are never read or written. Walks one week: a normal day, a phone screen, a big final
round → the re-entry day after it, a Friday deadline that merges into the Saturday
rest day, and the wind-down lint between work and play.
"""

from __future__ import annotations

import datetime as dt
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["TIMEPLANNER_VAULT"] = "/nonexistent"      # no vault → no real 📅 deadlines
os.environ["TIMEPLANNER_GCAL_REFS"] = ""              # no subscribed calendars
os.environ["TIMEPLANNER_BACKEND"] = "local"

from timeplanner.config import config  # noqa: E402

config.vault = Path("/nonexistent")
config.gcal_ref_ids = []
config.backend = "local"
config.data_dir = Path(tempfile.mkdtemp(prefix="tp-demo-"))  # throwaway timeline

from timeplanner.core import recovery as r  # noqa: E402
from timeplanner.core.gcal import Event  # noqa: E402

MON = dt.date(2026, 11, 2)
DAYS = {name: MON + dt.timedelta(days=i) for i, name in enumerate("一二三四五六日")}


def ev(d: dt.date, start: str, end: str, summary: str, bucket: str = "", external: bool = True) -> Event:
    t = lambda s: dt.datetime.combine(d, dt.time(*map(int, s.split(":")))).astimezone()  # noqa: E731
    return Event(summary, t(start), t(end), bucket, external=external)


# what's on the calendar each day (external events: interviews, deadlines)
CALENDAR = {
    DAYS["一"]: [],
    DAYS["二"]: [ev(DAYS["二"], "15:00", "15:45", "Qualcomm phone screen")],
    DAYS["三"]: [ev(DAYS["三"], "13:00", "17:00", "TikTok 终面")],
    DAYS["四"]: [],
    DAYS["五"]: [ev(DAYS["五"], "23:00", "23:59", "Workshop 投稿 deadline")],
    DAYS["六"]: [],
    DAYS["日"]: [],
}

# a plan draft per day, deliberately with a few mistakes so the lint has something to say
DRAFTS = {
    DAYS["一"]: [("10:00", "11:00", "side", "投两份简历"), ("14:00", "17:00", "main", "跑 KV pruning 实验"),
                 ("17:10", "20:00", "fun", "打塔")],
    DAYS["二"]: [("14:00", "15:00", "main", "看论文"), ("19:00", "20:30", "side", "刷 LC")],
    DAYS["三"]: [("10:00", "12:00", "side", "面试前过一遍项目"), ("18:00", "19:30", "main", "改代码")],
    DAYS["四"]: [("10:00", "12:00", "main", "补这两天落下的进度"), ("14:00", "16:00", "main", "看实验结果")],
    DAYS["五"]: [("14:00", "18:00", "main", "写 paper"), ("20:00", "23:00", "main", "写 paper")],
    DAYS["六"]: [("10:00", "12:00", "side", "顺手投几份简历"), ("13:00", "18:00", "life", "出门骑车")],
    DAYS["日"]: [("10:00", "11:00", "life", "大扫除 30min + 买菜"), ("14:00", "17:00", "main", "写 method"),
                 ("17:00", "17:15", "life", "收尾：写 next action + 收拾桌面"), ("17:15", "21:00", "fun", "打塔")],
}


def main() -> None:
    names = {d: n for n, d in DAYS.items()}
    print(f"（demo：假数据，临时目录 {config.data_dir}，不碰你的真实 timeline / 日历 / 笔记）\n")
    for d in sorted(CALENDAR):
        today = r.marked_events(d, CALENDAR[d], [])
        yest = r.marked_events(d - dt.timedelta(days=1), CALENDAR.get(d - dt.timedelta(days=1), []), [])
        mode = r.day_mode(d, today, yest)
        cal = "、".join(f"{e.start:%H:%M} {e.summary}" for e in CALENDAR[d]) or "无"
        print("=" * 64)
        print(f"周{names[d]} {d}   日历：{cal}")
        print(r.summary(d, mode))
        draft = [ev(d, a, b, s, bucket, external=False) for a, b, bucket, s in DRAFTS[d]]
        print("\n草案：")
        for e in draft:
            print(f"  {e.start:%H:%M}–{e.end:%H:%M} [{e.bucket}] {e.summary}")
        warns = r.lint_plan(mode, draft)
        print("提醒：" if warns else "提醒：无 ✅")
        for w in warns:
            print(f"  ⚠️ {w}")
        print()


if __name__ == "__main__":
    main()

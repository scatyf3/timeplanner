"""The in-progress activity ("soft recording") shared by the softbar and `timeplanner rec`.

One small state file `data/running.json` holds what you're doing right now:
`{"bucket": "main", "summary": "写 method", "start": "<iso>"}`. Switching closes the running
segment into ②Actual through `backend.log_actual` (same path as `timeplanner log` and the TUI
recorder, so local and gcal backends both work) and starts the next one. No forms: a bucket alone
is enough, the summary defaults to the bucket's name.

The state is per machine (each softbar keeps its own); the segments it writes land in the shared
Actual timeline. Qt-free on purpose so it can be tested and scripted without a GUI.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass

from ..config import config
from . import backend, recovery
from .gcal import BUCKETS, Event, norm_bucket

MIN_SEGMENT = dt.timedelta(minutes=1)
WIND_DOWN = dt.timedelta(minutes=12)          # how long the 收尾 segment runs before nudging to fun
DEFAULT_SUMMARY = {"main": "main", "side": "side", "life": "生活", "health": "健康", "fun": "玩"}
WIND_DOWN_SUMMARY = "收尾：写 next action + 收拾桌面"


@dataclass
class Running:
    bucket: str
    summary: str
    start: dt.datetime

    def elapsed(self, now: dt.datetime | None = None) -> dt.timedelta:
        return (now or _now()) - self.start


def _now() -> dt.datetime:
    return dt.datetime.now().astimezone()


def _file():
    return config.data_dir / "running.json"


def current() -> Running | None:
    p = _file()
    if not p.is_file():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return Running(d["bucket"], d["summary"], dt.datetime.fromisoformat(d["start"]))
    except (OSError, ValueError, KeyError):
        return None


def _save(r: Running | None) -> None:
    p = _file()
    if r is None:
        p.unlink(missing_ok=True)
        return
    config.data_dir.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"bucket": r.bucket, "summary": r.summary, "start": r.start.isoformat()},
                              ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


def _log(r: Running, end: dt.datetime) -> list[Event]:
    """Write [r.start, end] to Actual, split at midnight (log_actual takes one date + HH:MM)."""
    if end - r.start < MIN_SEGMENT:
        end = r.start + MIN_SEGMENT
    out, s = [], r.start
    while s.date() < end.date():
        out.append(backend.log_actual(s.date(), s.strftime("%H:%M"), "23:59", r.bucket, r.summary))
        s = dt.datetime.combine(s.date() + dt.timedelta(days=1), dt.time(0, 0)).astimezone()
    if end - s >= MIN_SEGMENT or not out:
        out.append(backend.log_actual(s.date(), s.strftime("%H:%M"), end.strftime("%H:%M"), r.bucket, r.summary))
    return out


def stop(now: dt.datetime | None = None) -> list[Event]:
    """End the running segment (if any) and write it to Actual. Returns the events written."""
    r = current()
    if not r:
        return []
    written = _log(r, now or _now())
    _save(None)
    return written


def start(bucket: str, summary: str = "", now: dt.datetime | None = None) -> tuple[Running, list[Event]]:
    """Start `bucket` now, closing whatever was running. Returns (new running, events written)."""
    bucket = (bucket or "").strip().lower()
    if bucket not in BUCKETS:
        raise ValueError(f"未知 bucket：{bucket}（可选 {', '.join(sorted(BUCKETS))}）")
    now = now or _now()
    written = stop(now)
    r = Running(bucket, summary.strip() or DEFAULT_SUMMARY[bucket], now)
    _save(r)
    return r, written


# ---- auto-stop: sleep / idle / the bar was gone (the softbar calls these) ----

SLEEP_GAP = dt.timedelta(minutes=2)       # wall clock jumped this much between 1-second ticks → the machine slept


def autostop_reason(r: Running | None, now: dt.datetime, last_tick: dt.datetime | None,
                    idle_s: float | None) -> tuple[dt.datetime, str] | None:
    """(when the segment really ended, why) if it should auto-stop now, else None. Pure, for testing.

    Sleep wins over idle: after a wake the idle counter also covers the sleep, but the last tick is exact."""
    if not r or r.bucket not in config.autostop_buckets:
        return None
    if last_tick and now - last_tick > SLEEP_GAP and last_tick > r.start:
        return last_tick, "电脑睡眠/待机"
    limit = config.autostop_idle_min
    if limit > 0 and idle_s is not None and idle_s >= limit * 60:
        end = now - dt.timedelta(seconds=idle_s)
        if end > r.start:
            return end, f"{int(idle_s // 60)} 分钟没操作电脑"
    return None


def _beat_file():
    return config.data_dir / "softbar.heartbeat"


def heartbeat(now: dt.datetime | None = None) -> None:
    """The bar says it's alive (every minute), so a later start can tell when it — or the computer — went away."""
    config.data_dir.mkdir(parents=True, exist_ok=True)
    _beat_file().write_text((now or _now()).isoformat(), encoding="utf-8")


def recover_on_start(now: dt.datetime | None = None) -> tuple[list[Event], str] | None:
    """On bar start: a segment that was running while the bar (or the computer) was gone for a while ends at
    the last heartbeat, e.g. after a shutdown. Only when the bar was alive during that segment."""
    now = now or _now()
    r = current()
    p = _beat_file()
    if not r or r.bucket not in config.autostop_buckets or not p.is_file():
        return None
    try:
        last = dt.datetime.fromisoformat(p.read_text(encoding="utf-8").strip())
    except ValueError:
        return None
    if last <= r.start or now - last <= SLEEP_GAP:
        return None
    return stop(last), f"浮动条/电脑在 {last:%H:%M} 之后就没在跑了"


# ---- soft prompts (the softbar asks; nothing here blocks) ----

def wants_wind_down(new_bucket: str) -> bool:
    """Switching from work straight to fun → offer the 10–15 min 收尾 first."""
    r = current()
    return bool(r and norm_bucket(r.bucket) in recovery.WORK_BUCKETS and norm_bucket(new_bucket) == "fun")


def work_on_off_day(new_bucket: str, date: dt.date | None = None,
                    now: dt.datetime | None = None) -> str | None:
    """A gentle note when starting main/side on a rest/off stretch; None when nothing to say."""
    if norm_bucket(new_bucket) not in recovery.WORK_BUCKETS:
        return None
    date = date or dt.date.today()
    now = now or _now()
    mode = recovery.day_mode(date)
    if mode.kind == recovery.REST:
        return "今天是休息日，确定要干活？"
    if mode.kind == recovery.REENTRY and now.time() < recovery.NOON:
        return "回来日上午是 off 的，下午再回主场？"
    if mode.kind in (recovery.POST_EVENT, recovery.EVENING_OFF) and mode.off_after and now >= mode.off_after:
        return f"{mode.off_after:%H:%M} 之后是 off 的，确定要干活？"
    return None


def plan_now(date: dt.date | None = None, now: dt.datetime | None = None) -> tuple[Event | None, Event | None]:
    """(the Plan block covering now, the next Plan block today). Never raises."""
    date = date or dt.date.today()
    now = now or _now()
    try:
        evs = [e for e in backend.list_events(date, "plan") if not e.all_day]
    except Exception:  # noqa: BLE001 — no OAuth / offline → the bar just shows less
        return None, None
    cur = next((e for e in evs if e.start <= now < e.end), None)
    nxt = next((e for e in evs if e.start > now), None)
    return cur, nxt


def status_text(now: dt.datetime | None = None) -> str:
    """One-line status for `timeplanner rec status` (the softbar draws the same parts)."""
    now = now or _now()
    r = current()
    rec = f"▶ [{r.bucket}] {r.summary} · {_fmt(r.elapsed(now))}" if r else "⏸ 未在记录"
    cur, nxt = plan_now(now=now)
    if cur:
        plan = f"计划 [{cur.bucket or 'ext'}] {cur.summary} · 剩 {_fmt(cur.end - now)}"
    elif nxt:
        plan = f"下一块 {nxt.start:%H:%M} [{nxt.bucket or 'ext'}] {nxt.summary}"
    else:
        plan = "今天没有后续计划块"
    return f"{recovery.day_mode(now.date()).label} · {rec} · {plan}"


def _fmt(td: dt.timedelta) -> str:
    m = max(0, int(td.total_seconds() // 60))
    return f"{m // 60}h{m % 60:02d}" if m >= 60 else f"{m}min"

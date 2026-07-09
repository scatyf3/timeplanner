"""Google Calendar read/write (Plan / Actual are two separate calendars).

The foundation is a "marker mechanism": every event the planner writes carries
    extendedProperties.private = {timeplanner: "1", bucket: <main|side|life|fit>}
When read back, anything without this marker → treated as an external fixed constraint
(meetings / activities others created); the planner only schedules into the gaps.

Assistive: all write ops default to dry_run=True, only echoing the diff, not really writing.
In CLI/TG you confirm before dry_run=False. When OAuth isn't configured, both read
and write degrade gracefully (return a hint), without breaking the M1 read-only path.
"""

from __future__ import annotations

import datetime as dt
import sys
from dataclasses import dataclass
from pathlib import Path

from ..config import config

SCOPES = ["https://www.googleapis.com/auth/calendar"]
MARKER_KEY = "timeplanner"
BUCKETS = {"main", "side", "life", "health", "fun"}
# "fit" is the legacy fitness bucket; still accepted on read/rewrite, colored as health.
VALID_BUCKETS = BUCKETS | {"fit"}


def norm_bucket(b: str) -> str:
    """Coerce an unknown bucket to 'main'; legacy 'fit' is kept as-is."""
    return b if b in VALID_BUCKETS else "main"


# Bucket → Google Calendar event colorId, matching the user's Time Record scheme:
#   工作=蓝 · 生活杂务=黄 · 健康=浅绿 · 娱乐=深绿
BUCKET_COLOR_ID = {
    "main": "7",     # Peacock (blue)    — 工作
    "side": "7",     # Peacock (blue)    — 工作
    "life": "5",     # Banana (yellow)   — 生活杂务
    "health": "2",   # Sage (light green)— 健康
    "fun": "10",     # Basil (dark green)— 娱乐
    "fit": "2",      # legacy → 健康
}

# Bucket → hex color for rich renderables (same scheme, matching the GCal palette).
BUCKET_HEX = {
    "main": "#039be5",   # Peacock (blue)     — 工作
    "side": "#039be5",   # Peacock (blue)     — 工作
    "life": "#f6bf26",   # Banana (yellow)    — 生活杂务
    "health": "#33b679", # Sage (light green) — 健康
    "fun": "#0b8043",    # Basil (dark green) — 娱乐
    "fit": "#33b679",    # legacy → 健康
}

# Bucket → ANSI SGR code for the terminal timeline lines (same scheme).
_BUCKET_ANSI = {
    "main": "34",    # blue
    "side": "34",    # blue
    "life": "33",    # yellow
    "health": "92",  # bright (light) green
    "fun": "32",     # green (dark)
    "fit": "92",     # legacy → light green
}


def _use_color() -> bool:
    return sys.stdout.isatty()


class GCalNotConfigured(RuntimeError):
    pass


@dataclass
class Event:
    summary: str
    start: dt.datetime
    end: dt.datetime
    bucket: str = ""          # empty = external event
    event_id: str = ""
    external: bool = False    # no timeplanner marker → True
    all_day: bool = False     # all-day event (holiday/birthday) → informational, not a time block

    def line(self, color: bool | None = None) -> str:
        tag = f"[{self.bucket or 'ext'}]"
        lock = "🔒" if self.external else "  "
        when = "全天" if self.all_day else f"{self.start:%H:%M}–{self.end:%H:%M}"
        text = f"{lock} {when} {tag} {self.summary}"
        if color is None:
            color = _use_color()
        code = _BUCKET_ANSI.get(self.bucket) if (color and self.bucket) else None
        return f"\033[{code}m{text}\033[0m" if code else text


def _service():
    """Lazily build the Google Calendar service. Raises GCalNotConfigured when deps/credentials are missing."""
    creds_path = Path(config.gcal_credentials)
    token_path = Path(config.gcal_token)
    if not creds_path.is_file() and not token_path.is_file():
        raise GCalNotConfigured(
            f"未配置 Google OAuth：把客户端凭据放到 {creds_path}（见 .env.example / README）。")
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError as e:
        raise GCalNotConfigured(f"缺 Google 依赖：pip install -e .（{e}）") from e

    creds = None
    if token_path.is_file():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
            creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json(), encoding="utf-8")
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def _parse_dt(node: dict) -> dt.datetime:
    raw = node.get("dateTime") or node.get("date")
    if "T" in raw:
        return dt.datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone()
    return dt.datetime.fromisoformat(raw)


def _is_all_day(item: dict) -> bool:
    return "date" in item.get("start", {}) and "dateTime" not in item.get("start", {})


def _to_event(item: dict) -> Event:
    priv = (item.get("extendedProperties", {}) or {}).get("private", {}) or {}
    is_ours = priv.get(MARKER_KEY) == "1"
    return Event(
        summary=item.get("summary", "(无标题)"),
        start=_parse_dt(item["start"]),
        end=_parse_dt(item["end"]),
        bucket=priv.get("bucket", "") if is_ours else "",
        event_id=item.get("id", ""),
        external=not is_ours,
        all_day=_is_all_day(item),
    )


def is_configured() -> bool:
    return Path(config.gcal_credentials).is_file() or Path(config.gcal_token).is_file()


def _cal_id(which: str) -> str:
    if which == "actual":
        return config.gcal_actual_id or "primary"
    return config.gcal_plan_id or "primary"


def list_events(date: dt.date | None = None, which: str = "plan") -> list[Event]:
    """Read a day's Plan/Actual calendar events, sorted by start time. Unmarked ones count as external constraints."""
    date = date or dt.date.today()
    svc = _service()
    start = dt.datetime.combine(date, dt.time.min).astimezone()
    end = start + dt.timedelta(days=1)
    resp = svc.events().list(
        calendarId=_cal_id(which),
        timeMin=start.isoformat(),
        timeMax=end.isoformat(),
        singleEvents=True,
        orderBy="startTime",
    ).execute()
    return [_to_event(it) for it in resp.get("items", [])]


def list_calendars() -> list[dict]:
    """List every calendar in the account's calendarList (id / name / primary / accessRole).

    Use this to discover the calendar IDs to put in TIMEPLANNER_GCAL_REFS.
    """
    svc = _service()
    resp = svc.calendarList().list().execute()
    out = []
    for it in resp.get("items", []):
        out.append({
            "id": it.get("id", ""),
            "name": it.get("summaryOverride") or it.get("summary", ""),
            "primary": bool(it.get("primary", False)),
            "access": it.get("accessRole", ""),
        })
    return out


def _events_on(svc, cal_id: str, date: dt.date) -> list[dict]:
    start = dt.datetime.combine(date, dt.time.min).astimezone()
    end = start + dt.timedelta(days=1)
    resp = svc.events().list(
        calendarId=cal_id,
        timeMin=start.isoformat(),
        timeMax=end.isoformat(),
        singleEvents=True,
        orderBy="startTime",
    ).execute()
    return resp.get("items", [])


def list_ref_events(date: dt.date | None = None) -> list[Event]:
    """Read a day's events from all subscribed reference calendars (config.gcal_ref_ids).

    All are treated as external fixed constraints (🔒, read-only), regardless of any marker,
    sorted by start time. Returns [] when no ref calendars are configured. A single bad
    calendar ID is skipped (not fatal) so one broken ref never hides the others.
    """
    if not config.gcal_ref_ids:
        return []
    date = date or dt.date.today()
    svc = _service()
    out: list[Event] = []
    for cal_id in config.gcal_ref_ids:
        try:
            for it in _events_on(svc, cal_id, date):
                e = _to_event(it)
                e.bucket = ""          # subscribed calendars are never planner-owned
                e.external = True
                out.append(e)
        except Exception:  # noqa: BLE001 — a wrong/inaccessible ref id shouldn't sink the rest
            continue
    out.sort(key=lambda e: (e.all_day is False, e.start))  # all-day first, then by start
    return out


def refs_summary(date: dt.date | None = None, color: bool | None = None) -> str:
    """Read-only text summary of the subscribed reference calendars (external constraints)."""
    date = date or dt.date.today()
    lines = [f"# 🔒 订阅日历（只读外部约束）—— {date:%Y-%m-%d}"]
    if not config.gcal_ref_ids:
        return ""  # feature off → contribute nothing to the summary
    if not is_configured():
        lines.append("（配了订阅日历，但未配置 Google OAuth —— 见 README。）")
        return "\n".join(lines)
    try:
        events = list_ref_events(date)
    except GCalNotConfigured as e:
        lines.append(f"（{e}）")
        return "\n".join(lines)
    except Exception as e:  # noqa: BLE001 — network/auth errors must never crash the CLI
        lines.append(f"（读订阅日历出错：{e}）")
        return "\n".join(lines)
    if not events:
        lines.append(f"（{len(config.gcal_ref_ids)} 个订阅日历今天无事件）")
        return "\n".join(lines)
    lines.append(f"来自 {len(config.gcal_ref_ids)} 个订阅日历的 {len(events)} 个事件（planner 只在其空隙排块）：")
    for e in events:
        lines.append(e.line(color))
    return "\n".join(lines)


def _insert(svc, cal: str, e: Event) -> None:
    bucket = norm_bucket(e.bucket)
    body = {
        "summary": e.summary,
        "start": {"dateTime": e.start.isoformat()},
        "end": {"dateTime": e.end.isoformat()},
        "extendedProperties": {"private": {MARKER_KEY: "1", "bucket": bucket}},
    }
    color = BUCKET_COLOR_ID.get(bucket)
    if color:
        body["colorId"] = color  # color-code by bucket to match the Time Record scheme
    svc.events().insert(calendarId=cal, body=body).execute()


def commit_plan(date: dt.date, events: list[Event]) -> None:
    """Replace the day's Plan with events: delete only the old blocks we marked (never touch external events), then insert the new ones."""
    svc = _service()
    cal = _cal_id("plan")
    for old in list_events(date, "plan"):
        if not old.external and old.event_id:      # only delete what timeplanner itself wrote
            svc.events().delete(calendarId=cal, eventId=old.event_id).execute()
    for e in events:
        _insert(svc, cal, e)


def append_actual(event: Event) -> None:
    """Log one Actual event to the Actual calendar (with marker)."""
    _insert(_service(), _cal_id("actual"), event)


def summary(date: dt.date | None = None, which: str = "plan", color: bool | None = None) -> str:
    """Read back the Plan/Actual calendar, distinguishing planner events from external fixed constraints."""
    date = date or dt.date.today()
    label = {"plan": "Plan", "actual": "Actual"}.get(which, which)
    lines = [f"# 📅 GCal {label} —— {date:%Y-%m-%d}"]
    if not is_configured():
        lines.append("（未配置 Google OAuth —— 见 README。）")
        return "\n".join(lines)
    try:
        events = list_events(date, which)
    except GCalNotConfigured as e:
        lines.append(f"（{e}）")
        return "\n".join(lines)
    except Exception as e:  # noqa: BLE001 — network/auth exceptions of all kinds must never crash the CLI
        lines.append(f"（读日历出错：{e}）")
        return "\n".join(lines)

    if not events:
        lines.append("（今天日历为空）")
        return "\n".join(lines)
    ext = [e for e in events if e.external]
    ours = [e for e in events if not e.external]
    lines.append(f"planner 事件 {len(ours)} 个，外部固定约束 {len(ext)} 个（🔒 只读）：")
    for e in events:
        lines.append(e.line(color))
    return "\n".join(lines)


if __name__ == "__main__":
    print(summary())

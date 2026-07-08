"""Google Calendar 读写（Plan / Actual 两个独立日历）。

地基是「标记机制」：planner 写的每个事件都带
    extendedProperties.private = {timeplanner: "1", bucket: <main|side|life|fit>}
读回来时没这个标的 → 一律当外部固定约束（会议/别人拉的活动），planner 只在空隙里排。

辅助式：所有写操作默认 dry_run=True，只回显 diff，不真写。CLI/TG 里你确认后才 dry_run=False。
OAuth 未配置时，只读/写都优雅降级（返回提示），不影响 M1 只读跑通。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

from ..config import config

SCOPES = ["https://www.googleapis.com/auth/calendar"]
MARKER_KEY = "timeplanner"
BUCKETS = {"main", "side", "life", "fit"}


class GCalNotConfigured(RuntimeError):
    pass


@dataclass
class Event:
    summary: str
    start: dt.datetime
    end: dt.datetime
    bucket: str = ""          # 空 = 外部事件
    event_id: str = ""
    external: bool = False    # 没带 timeplanner 标 → True

    def line(self) -> str:
        tag = f"[{self.bucket or 'ext'}]"
        lock = "🔒" if self.external else "  "
        return f"{lock} {self.start:%H:%M}–{self.end:%H:%M} {tag} {self.summary}"


def _service():
    """惰性建 Google Calendar service。缺依赖/缺凭据时抛 GCalNotConfigured。"""
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
    )


def is_configured() -> bool:
    return Path(config.gcal_credentials).is_file() or Path(config.gcal_token).is_file()


def list_events(date: dt.date | None = None, calendar_id: str | None = None) -> list[Event]:
    """读某天某日历的事件（默认 Plan 日历）。返回按开始时间排序。"""
    date = date or dt.date.today()
    cal = calendar_id or config.gcal_plan_id or "primary"
    svc = _service()
    start = dt.datetime.combine(date, dt.time.min).astimezone()
    end = start + dt.timedelta(days=1)
    resp = svc.events().list(
        calendarId=cal,
        timeMin=start.isoformat(),
        timeMax=end.isoformat(),
        singleEvents=True,
        orderBy="startTime",
    ).execute()
    return [_to_event(it) for it in resp.get("items", [])]


def write_events(events: list[Event], calendar_id: str | None = None, dry_run: bool = True) -> str:
    """把 planner 的块写进日历，每个都打 timeplanner 标。dry_run=True 只回显不写。"""
    cal = calendar_id or config.gcal_plan_id or "primary"
    diff = ["# 📅 待写入事件（Plan 日历）" + ("  —— DRY RUN，未真写" if dry_run else "")]
    for e in events:
        diff.append("+ " + e.line())

    if dry_run:
        diff.append("\n（确认后以 dry_run=False 落地）")
        return "\n".join(diff)

    svc = _service()
    for e in events:
        bucket = e.bucket if e.bucket in BUCKETS else "main"
        svc.events().insert(calendarId=cal, body={
            "summary": e.summary,
            "start": {"dateTime": e.start.isoformat()},
            "end": {"dateTime": e.end.isoformat()},
            "extendedProperties": {"private": {MARKER_KEY: "1", "bucket": bucket}},
        }).execute()
    diff.append(f"\n✅ 已写入 {len(events)} 个事件到 {cal}")
    return "\n".join(diff)


def summary(date: dt.date | None = None) -> str:
    """读回 Plan 日历，区分 planner 事件与外部固定约束。"""
    date = date or dt.date.today()
    lines = [f"# 📅 GCal Plan —— {date:%Y-%m-%d}"]
    if not is_configured():
        lines.append("（未配置 Google OAuth —— M1 只读阶段可跳过；配置见 README。）")
        return "\n".join(lines)
    try:
        events = list_events(date)
    except GCalNotConfigured as e:
        lines.append(f"（{e}）")
        return "\n".join(lines)
    except Exception as e:  # noqa: BLE001 — 网络/授权各种异常都别让 CLI 崩
        lines.append(f"（读日历出错：{e}）")
        return "\n".join(lines)

    if not events:
        lines.append("（今天日历为空）")
        return "\n".join(lines)
    ext = [e for e in events if e.external]
    ours = [e for e in events if not e.external]
    lines.append(f"planner 事件 {len(ours)} 个，外部固定约束 {len(ext)} 个（🔒 只读）：")
    for e in events:
        lines.append(e.line())
    return "\n".join(lines)


if __name__ == "__main__":
    print(summary())

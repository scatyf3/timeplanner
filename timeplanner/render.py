"""Rich terminal rendering for `summary`.

Notes/weather render as Markdown; ActivityWatch shows as an app-share bar chart; and
①Plan / ②Actual / ③Observed(AW) render as three parallel timelines sharing one time
axis — the "收工 4 问" comparison made visual. Bucket colors match the GCal / Time
record scheme (工作=蓝 · 生活杂务=黄 · 健康=浅绿 · 娱乐=深绿).
"""

from __future__ import annotations

import datetime as dt

from .core import activitywatch, aw_sync, backend, gcal, notes, weather
from .core.gcal import BUCKET_HEX, norm_bucket

SLOT_MIN = 30                     # timeline row granularity
_OBSERVED_HEX = "#8899a6"         # neutral gray for AW active spans (no bucket)
_EMPTY_HEX = "#3a3f44"            # faint bar for an idle slot
# rotating palette for the AW app-share bars (apps have no bucket)
_APP_PALETTE = ["#039be5", "#f6bf26", "#33b679", "#0b8043", "#8e24aa", "#e67c73", "#f4511e", "#616161"]

# One timeline item = (start, end, color, label).
_Item = tuple[dt.datetime, dt.datetime, str, str]


def _bucket_color(bucket: str) -> str:
    return BUCKET_HEX.get(norm_bucket(bucket), _OBSERVED_HEX)


def _short(s: str, n: int = 16) -> str:
    s = s.strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def _floor_slot(t: dt.datetime) -> dt.datetime:
    base = t.replace(minute=0, second=0, microsecond=0)
    steps = (t - base).total_seconds() // (SLOT_MIN * 60)
    return base + dt.timedelta(minutes=SLOT_MIN * steps)


def _ceil_slot(t: dt.datetime) -> dt.datetime:
    f = _floor_slot(t)
    return f if f == t else f + dt.timedelta(minutes=SLOT_MIN)


def _collect(date: dt.date):
    """Gather the three timelines' items + AW breakdown. Returns (plan, actual, observed, obs)."""
    plan = [(e.start, e.end, _bucket_color(e.bucket), e.summary)
            for e in backend.list_events(date, "plan")]
    actual = [(e.start, e.end, _bucket_color(e.bucket), e.summary)
              for e in backend.list_events(date, "actual")]
    obs = aw_sync.merged_observe(date)                 # local live AW + other machines' snapshots
    observed = [(b.start, b.end, _OBSERVED_HEX, "在机")
                for b in obs.focus_blocks] if obs.available else []
    return plan, actual, observed, obs


def _cell(slot_start: dt.datetime, slot_end: dt.datetime, items: list[_Item]):
    from rich.text import Text
    for start, end, color, label in items:
        if start < slot_end and end > slot_start:          # item overlaps this slot
            is_head = slot_start <= start < slot_end
            txt = f"▐ {_short(label)}" if is_head else "▐"
            return Text(txt, style=color)
    return Text("▕", style=_EMPTY_HEX)


def _timeline_table(date: dt.date, plan, actual, observed):
    from rich import box
    from rich.table import Table

    spans = plan + actual + observed
    if not spans:
        from rich.text import Text
        return Text("（今天 Plan / Actual / AW 三条线都空）", style="dim")

    lo = _floor_slot(min(s for s, *_ in spans))
    hi = _ceil_slot(max(e for _, e, *_ in spans))

    table = Table(box=box.SIMPLE_HEAD, padding=(0, 1), title=f"🗓️  时间线 —— {date:%Y-%m-%d}")
    table.add_column("", justify="right", style="dim", no_wrap=True)
    table.add_column("① Plan", no_wrap=True)
    table.add_column("② Actual", no_wrap=True)
    table.add_column("③ AW", no_wrap=True)

    cur = lo
    while cur < hi:
        nxt = cur + dt.timedelta(minutes=SLOT_MIN)
        gutter = f"{cur:%H:%M}" if cur.minute == 0 else ""   # label only on the hour
        table.add_row(gutter, _cell(cur, nxt, plan), _cell(cur, nxt, actual), _cell(cur, nxt, observed))
        cur = nxt
    return table


def _aw_breakdown(obs):
    from rich.console import Group
    from rich.text import Text

    if not obs.available:
        return Text(f"🖥️  ActivityWatch：{obs.note}", style="dim")

    h, m = divmod(int(obs.active_minutes), 60)
    header = Text.assemble(
        ("🖥️  在机 ", "bold"), (f"{h}h{m:02d}m", "bold cyan"),
        ("　专注 block(≥25min) ", "bold"), (f"{len(obs.focus_blocks)}", "bold cyan"), (" 个", "bold"),
    )

    apps = [(a, mn) for a, mn in obs.top_apps if mn >= 1]
    if not apps:
        return Group(header, Text("（无应用记录）", style="dim"))

    total = sum(mn for _, mn in apps)
    maxw, name_w = 28, 16
    rows = [header, Text("按应用占比：", style="dim")]
    for i, (app, mn) in enumerate(apps):
        frac = mn / total
        bar = "█" * max(1, round(frac * maxw))
        color = _APP_PALETTE[i % len(_APP_PALETTE)]
        rows.append(Text.assemble(
            (f"{_short(app, name_w):<{name_w}} ", "default"),
            (bar, color),
            (f" {int(mn)}m {frac*100:.0f}%", "dim"),
        ))
    return Group(*rows)


def _refs_section(date: dt.date):
    """A compact 🔒 list of subscribed-calendar events (external constraints). None if off/empty."""
    from .config import config
    if not config.gcal_ref_ids or not gcal.is_configured():
        return None
    from rich.console import Group
    from rich.text import Text
    try:
        events = gcal.list_ref_events(date)
    except Exception:  # noqa: BLE001 — never let a calendar hiccup break the whole summary
        return None
    if not events:
        return None
    rows = [Text.assemble(("🔒 订阅日历", "bold"), ("（只读外部约束，planner 只在空隙排块）", "dim"))]
    for e in events:
        when = "全天" if e.all_day else f"{e.start:%H:%M}–{e.end:%H:%M}"
        rows.append(Text.assemble((f"  {when}  ", "dim"), (_short(e.summary, 40), _OBSERVED_HEX)))
    return Group(*rows)


def render(date: dt.date) -> None:
    """Pretty terminal summary. Raises ImportError if rich is unavailable (caller falls back to plain)."""
    from rich.console import Console
    from rich.markdown import Markdown
    from rich.rule import Rule

    console = Console()
    plan, actual, observed, obs = _collect(date)

    console.print(Markdown(notes.summary(date)))
    console.print()
    console.print(Markdown(weather.summary(date)))
    console.print(Rule(style="dim"))
    console.print(_timeline_table(date, plan, actual, observed))
    console.print(_aw_breakdown(obs))
    refs = _refs_section(date)
    if refs is not None:
        console.print(Rule(style="dim"))
        console.print(refs)

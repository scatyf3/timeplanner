"""Rich terminal rendering for `summary`.

Notes/weather render as Markdown; ActivityWatch shows as an app-share bar chart; and
①Plan / ②Actual / ③Observed(AW) render as three parallel timelines sharing one time
axis — the "收工 4 问" comparison made visual. Bucket colors match the GCal / Time
record scheme (工作=蓝 · 生活杂务=黄 · 健康=浅绿 · 娱乐=深绿).
"""

from __future__ import annotations

import datetime as dt

import unicodedata

from .core import activitywatch, aw_sync, backend, gcal, notes, weather, weekly

SLOT_MIN = 30                     # timeline row granularity
_OBSERVED_HEX = "#8899a6"         # neutral gray for AW active spans (no bucket)
_EMPTY_HEX = "#3a3f44"            # faint bar for an idle slot
# rotating palette for the AW app-share bars (apps have no bucket)
_APP_PALETTE = ["#039be5", "#f6bf26", "#33b679", "#0b8043", "#8e24aa", "#e67c73", "#f4511e", "#616161"]

# One timeline item = (start, end, color, label).
_Item = tuple[dt.datetime, dt.datetime, str, str]


def _dwidth(s: str) -> int:
    """Display width in terminal cells (CJK / fullwidth chars count as 2)."""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def _short(s: str, n: int = 16) -> str:
    """Truncate to n *display* cells (CJK-aware), appending … when clipped."""
    s = s.strip()
    if _dwidth(s) <= n:
        return s
    out, w = "", 0
    for c in s:
        cw = 2 if unicodedata.east_asian_width(c) in "WF" else 1
        if w + cw > n - 1:
            break
        out, w = out + c, w + cw
    return out + "…"


def _floor_slot(t: dt.datetime) -> dt.datetime:
    base = t.replace(minute=0, second=0, microsecond=0)
    steps = (t - base).total_seconds() // (SLOT_MIN * 60)
    return base + dt.timedelta(minutes=SLOT_MIN * steps)


def _ceil_slot(t: dt.datetime) -> dt.datetime:
    f = _floor_slot(t)
    return f if f == t else f + dt.timedelta(minutes=SLOT_MIN)


def _collect(date: dt.date):
    """Gather the three timelines' items + AW breakdown. Returns (plan, actual, observed, obs)."""
    plan = [(e.start, e.end, e.color_hex() or _OBSERVED_HEX, e.summary)
            for e in backend.list_events(date, "plan")]
    actual = [(e.start, e.end, e.color_hex() or _OBSERVED_HEX, e.summary)
              for e in backend.list_events(date, "actual")]
    obs = aw_sync.merged_observe(date)                 # local live AW + other machines' snapshots
    observed = [(b.start, b.end, _OBSERVED_HEX, "在机")
                for b in obs.focus_blocks] if obs.available else []
    return plan, actual, observed, obs


def _slot_color(slot_start: dt.datetime, slot_end: dt.datetime, items: list[_Item]) -> str | None:
    """Color of the item covering this slot in one lane, or None if idle."""
    for start, end, color, *_ in items:
        if start < slot_end and end > slot_start:
            return color
    return None


def _lane_bar(slots: list[dt.datetime], items: list[_Item], cell_w: int = 1):
    """One horizontal strip: each time slot drawn `cell_w` cells wide, running left→right across the day."""
    from rich.text import Text
    bar = Text(no_wrap=True)
    for cur in slots:
        color = _slot_color(cur, cur + dt.timedelta(minutes=SLOT_MIN), items)
        bar.append(("█" if color else "·") * cell_w, style=color or _EMPTY_HEX)
    return bar


def _hour_axis(slots: list[dt.datetime], cell_w: int = 1):
    """A dim ruler above the lanes: a 2-cell hour label at the start of each on-the-hour slot (slots are cell_w wide)."""
    from rich.text import Text
    cells = [" "] * (len(slots) * cell_w)
    for i, cur in enumerate(slots):
        if cur.minute == 0:
            h = f"{cur.hour:02d}"
            pos = i * cell_w
            cells[pos] = h[0]
            if pos + 1 < len(cells):
                cells[pos + 1] = h[1]
    return Text("".join(cells), style="dim", no_wrap=True)


def _event_lines(items: list[_Item]):
    """Full-label event rows for one lane (labels wrap, so nothing is ever clipped)."""
    from rich.text import Text
    if not items:
        return [Text("  （空）", style="dim")]
    rows = []
    for start, end, color, label in sorted(items):
        rows.append(Text.assemble(
            (f"  {start:%H:%M}–{end:%H:%M} ", "dim"),
            ("█ ", color),
            (label.strip() or "(无标题)", color),
        ))
    return rows


def _timeline_table(date: dt.date, plan, actual, observed):
    from rich import box
    from rich.console import Group
    from rich.table import Table
    from rich.text import Text

    spans = plan + actual + observed
    if not spans:
        return Text("（今天 Plan / Actual / AW 三条线都空）", style="dim")

    lo = _floor_slot(min(s for s, *_ in spans))
    hi = _ceil_slot(max(e for _, e, *_ in spans))

    slots = []
    cur = lo
    while cur < hi:
        slots.append(cur)
        cur += dt.timedelta(minutes=SLOT_MIN)

    # Lanes run horizontally: one row per timeline, time on the x-axis.
    table = Table(box=box.SIMPLE_HEAD, padding=(0, 1), show_header=False,
                  title=f"🗓️  时间线 —— {date:%Y-%m-%d}")
    table.add_column("", justify="right", style="dim", no_wrap=True)
    table.add_column("", no_wrap=True)
    table.add_row("", _hour_axis(slots))
    table.add_row("① Plan", _lane_bar(slots, plan))
    table.add_row("② Actual", _lane_bar(slots, actual))
    table.add_row("③ AW", _lane_bar(slots, observed))

    return Group(
        table,
        Text("① Plan", style="bold"), *_event_lines(plan),
        Text("② Actual", style="bold"), *_event_lines(actual),
    )


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


def _weekly_bucket_chart(g: dict):
    """Per-bucket hours as a horizontal bar chart (mirrors _aw_breakdown), colored by the bucket scheme."""
    from rich.console import Group
    from rich.text import Text

    buckets = sorted(g["bucket_hours"].items(), key=lambda kv: -kv[1])
    if not buckets:
        return Text("（本周 Time record 为空）", style="dim")

    total = g["total_hours"] or 1.0
    h, m = divmod(int(round(total * 60)), 60)
    rows = [Text.assemble(("本周投入 ", "bold"), (f"{h}h{m:02d}m", "bold cyan"),
                          ("　按 bucket 占比：", "dim"))]
    maxw, name_w = 28, 6
    for b, hours in buckets:
        frac = hours / total
        bar = "█" * max(1, round(frac * maxw))
        rows.append(Text.assemble(
            (f"{weekly.bucket_label(b):<{name_w}} ", "default"),
            (bar, weekly.bucket_hex(b)),
            (f" {hours:.1f}h {frac*100:.0f}%", "dim"),
        ))
    return Group(*rows)


def render_weekly(date: dt.date) -> None:
    """Deterministic weekly bucket-hours chart, printed before the agent's per-project narration.

    Raises ImportError if rich is unavailable (caller skips it)."""
    from rich.console import Console
    from rich.rule import Rule

    g = weekly.gather_week(date)
    iso = date.isocalendar()
    console = Console()
    console.print(Rule(f"🗓️  本周回顾 {iso.year}-W{iso.week:02d}"
                       f"（{g['monday']:%m-%d} 周一 ~ {g['sunday']:%m-%d} 周日）", style="dim"))
    console.print(_weekly_bucket_chart(g))
    console.print(Rule(style="dim"))


def render_weekly_allocation(date: dt.date) -> None:
    """Print the currently-staged per-project time allocation (called after each interactive weekly round).

    The bucket-hour totals are fixed for the week; what updates turn-to-turn is the agent's project
    attribution, which lives in the staged markdown. No-op if nothing's staged. ImportError if rich is absent."""
    from rich.console import Console
    from rich.markdown import Markdown
    from rich.rule import Rule

    body = weekly.staged(date)
    if not body:
        return
    console = Console()
    console.print()
    console.print(Rule("📊 当前项目时间分配（已暂存，最新一轮）", style="dim"))
    console.print(Markdown(body))
    console.print(Rule(style="dim"))


# ---- day-scale renderables for the TUI dashboard (`timeplanner tui`) ----

def _day_bucket_hours(date: dt.date) -> dict:
    """Today's ②Actual time summed per bucket (Time-record colors reverse-mapped via weekly.bucket_of)."""
    hours: dict[str, float] = {}
    for e in backend.list_events(date, "actual"):
        b = weekly.bucket_of(e)
        hours[b] = hours.get(b, 0.0) + (e.end - e.start).total_seconds() / 3600.0
    return hours


def collect_day(date: dt.date):
    """Fetch a day's three lanes once (network for the gcal backend). Cache this; pass into
    day_timeline_renderable so the moving now-cursor can redraw without refetching."""
    plan, actual, observed, _obs = _collect(date)
    return plan, actual, observed


_NOW_HEX = "#ff5555"      # current-time cursor color


def _now_caret(slots: list[dt.datetime], cell_w: int, now: dt.datetime):
    """A caret row aligned with the lanes, a `▼` at the column of `now` (or None if now is off-scale)."""
    from rich.text import Text

    total = len(slots) * cell_w
    lo, hi = slots[0], slots[-1] + dt.timedelta(minutes=SLOT_MIN)
    if not (lo <= now < hi):
        return None
    idx = int((now - lo).total_seconds() // (SLOT_MIN * 60))
    frac = ((now - lo).total_seconds() % (SLOT_MIN * 60)) / (SLOT_MIN * 60)
    col = min(total - 1, idx * cell_w + int(frac * cell_w))
    cells = [" "] * total
    cells[col] = "▼"
    return Text("".join(cells), style=f"bold {_NOW_HEX}", no_wrap=True)


def day_timeline_renderable(data, width: int | None = None, now: dt.datetime | None = None, live=None):
    """The dashboard's top panel: ①Plan / ②Actual / ③AW lanes on one shared axis, stretched horizontally
    to fill `width`. `data` = collect_day(date). `now` draws a current-time cursor (▼). `live` =
    (start, end, bucket, summary) overlays the in-progress recording on the ②Actual lane."""
    from rich import box
    from rich.table import Table
    from rich.text import Text

    plan, actual, observed = data

    # now/live come from the wall clock (naive); events may be tz-aware (gcal). Coerce to match so
    # min()/comparisons don't mix naive & aware datetimes.
    sample = next((s for s, *_ in (plan + actual + observed)), None)

    def _coerce(t):
        if sample is None or t is None:
            return t
        if sample.tzinfo and t.tzinfo is None:
            return t.astimezone()
        if sample.tzinfo is None and t.tzinfo:
            return t.replace(tzinfo=None)
        return t

    now = _coerce(now)
    if live:
        ls, le, lb, lsum = live
        actual = actual + [(_coerce(ls), _coerce(le),
                            gcal.BUCKET_HEX.get(gcal.norm_bucket(lb), _OBSERVED_HEX), lsum)]

    spans = plan + actual + observed
    starts = [s for s, *_ in spans] + ([now] if now else [])
    ends = [e for _, e, *_ in spans] + ([now] if now else [])
    if not starts:
        return Text("今天 Plan / Actual / AW 三条线都空。", style="dim")

    lo = _floor_slot(min(starts))
    hi = _ceil_slot(max(ends))
    slots, cur = [], lo
    while cur < hi:
        slots.append(cur)
        cur += dt.timedelta(minutes=SLOT_MIN)

    cell_w = 1
    if width:                                        # widen each slot to fill the panel (label col + padding ≈ 12)
        cell_w = max(1, min(8, (width - 12) // len(slots)))

    table = Table(box=box.SIMPLE_HEAD, padding=(0, 1), show_header=False)
    table.add_column("", justify="right", style="dim", no_wrap=True)
    table.add_column("", no_wrap=True)
    table.add_row("", _hour_axis(slots, cell_w))
    if now:
        caret = _now_caret(slots, cell_w, now)
        if caret is not None:
            table.add_row(f"🕐{now:%H:%M}", caret)
    table.add_row("① Plan", _lane_bar(slots, plan, cell_w))
    table.add_row("② Actual", _lane_bar(slots, actual, cell_w))
    table.add_row("③ AW", _lane_bar(slots, observed, cell_w))
    return table


# Status cat-face kaomoji by mood — a curated subset of github.com/melaniecebula/cat-ascii-faces.
_CAT_FACES = {
    "sleepy": ["( =ω=)..nyaa", "₍ᵔ·͈༝·͈ᵔ₎", "(=ｘェｘ=)", "(＝ФᆽФ＝)"],
    "happy":  ["(=^･ω･^=)", "(=ↀωↀ=)✧", "ヽ(=^･ω･^=)丿", "ฅ(^ω^ฅ)"],
    "work":   ["(=･ω･=)", "(^・ω・^ )", "d(=^･ω･^=)b", "(p`･ω･´) q"],
    "relax":  ["(≚ᄌ≚)", "❤(´ω｀*)", "(=⌒‿‿⌒=)", "~(=^‥^)ノ"],
}
_MOOD_LABEL = {"sleepy": "待机", "happy": "在状态", "work": "搬砖中", "relax": "回血中"}


def day_mood(date: dt.date) -> tuple[str, str]:
    """Pick a (cat-face, mood-label) from today's ②Actual split — the dumb explainable rule set.

    no data → 待机 · work≥4h → 在状态 · fun/health/life 主导（或 work=0）→ 回血 · 否则 搬砖。
    Face is random within the mood, so `r` (refresh) gives a fresh face.
    """
    import random

    hours = _day_bucket_hours(date)
    total = sum(hours.values())
    if total <= 0:
        mood = "sleepy"
    else:
        work = hours.get("work", 0.0)
        top = max(hours, key=hours.get)
        if work >= 4:
            mood = "happy"
        elif top in ("fun", "health", "life") or work == 0:
            mood = "relax"
        else:
            mood = "work"
    return random.choice(_CAT_FACES[mood]), _MOOD_LABEL[mood]


def expression_renderable(date: dt.date):
    """The left 'expression bar': a status-driven cat face + mood + a one-line day stat. No portrait art."""
    from rich.console import Group
    from rich.text import Text

    face, label = day_mood(date)
    hours = _day_bucket_hours(date)
    total = sum(hours.values())
    rows = [Text(), Text(face, justify="center", style="bold"),
            Text(), Text(label, justify="center", style="bold cyan")]
    if total > 0:
        top = max(hours, key=hours.get)
        h, m = divmod(int(round(total * 60)), 60)
        rows += [Text(), Text(f"今天 {h}h{m:02d}m · 最多 {weekly.bucket_label(top)}",
                              justify="center", style="dim")]
    return Group(*rows)


def day_bars_renderable(date: dt.date):
    """Today's bucket split as a horizontal bar chart (hours + percent), colored by bucket. For the right panel."""
    from rich.console import Group
    from rich.text import Text

    hours = _day_bucket_hours(date)
    total = sum(hours.values())
    if total <= 0:
        return Text("今天还没有 Actual 时间记录。", style="dim")

    h, m = divmod(int(round(total * 60)), 60)
    rows = [Text.assemble(("共 ", "dim"), (f"{h}h{m:02d}m", "bold cyan")), Text()]
    maxw, name_w = 22, 3      # bucket labels are all 2 CJK chars → equal display width
    for b, hrs in sorted(hours.items(), key=lambda kv: -kv[1]):
        frac = hrs / total
        bar = "█" * max(1, round(frac * maxw))
        rows.append(Text.assemble(
            (f"{weekly.bucket_label(b):<{name_w}} ", "default"),
            (bar, weekly.bucket_hex(b)),
            (f" {hrs:.1f}h {frac * 100:.0f}%", "dim"),
        ))
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

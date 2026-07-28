"""Interactive terminal dashboard (`timeplanner tui`) —— a day-scale cockpit with a live clock + recorder.

Layout:
    ┌───────────────── timeline plan/actual ─────────────────┐
    │  ① Plan / ② Actual / ③ AW  共轴泳道（今天，横向铺满）  │
    ├──────────────────────┬──────────────────────────────────┤
    │  表情栏（猫脸随状态变）│  today time breakdown（bucket 占比）│
    ├──────────────────────┴──────────────────────────────────┤
    │  🕐 实时时钟 + ▶ 活动录制器（写进 ②Actual / Time record）│
    └──────────────────────────────────────────────────────────┘

Built on Textual (soft dependency, `pip install -e '.[tui]'`). Panel content reuses render.py's rich
renderables. The recorder is a live front-end to backend.log_actual (same path as `timeplanner log`):
`s` starts/switches the current activity, `e` stops it — each finished segment [start, now] is written to
the Actual timeline and the panels refresh. The clock ticks once a second.
"""

from __future__ import annotations

import datetime as dt

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Input, Static

from . import render
from .core import backend, classify
from .core.gcal import BUCKETS


class Dashboard(App):
    """Day dashboard: timeline on top, expression + breakdown in the middle, live clock + recorder at the bottom."""

    CSS = """
    Screen { layout: vertical; }
    #timeline { height: 8;   border: round $accent;  padding: 0 1; }
    #mid      { height: 1fr; }
    #left     { width: 2fr;  padding: 0 1; content-align: center middle; }
    #right    { width: 1fr;  border: round $accent;  padding: 1 2; content-align: left middle; }
    #bottom   { height: 6;   border: round $accent;  padding: 0 1; }
    #status   { height: 1; }
    #rec      { display: none; }
    #rec.armed { display: block; }
    """

    BINDINGS = [
        ("s", "start_rec", "开始/切换记录"),
        ("e", "stop_rec", "停止记录"),
        ("r", "refresh_panels", "刷新"),
        ("q", "quit", "退出"),
    ]

    def __init__(self, date: dt.date):
        super().__init__()
        self.date = date
        self.rec_start: dt.datetime | None = None      # running activity start (None = not recording)
        self.rec_bucket = ""
        self.rec_summary = ""
        self.flash = ""                                # transient one-line note under the status
        self._day_data = None                          # cached (plan, actual, observed); refetched on refresh only

    def compose(self) -> ComposeResult:
        yield Static(id="timeline")
        with Horizontal(id="mid"):
            yield Static(id="left")
            yield Static(id="right")
        with Vertical(id="bottom"):
            yield Static(id="status")
            yield Input(id="rec", placeholder="敲活动即可，自动归类（如：磕盐 / walk / L4D2）；前置 bucket 可覆盖 · Esc 取消")

    def on_mount(self) -> None:
        self.query_one("#timeline", Static).border_title = "timeline plan/actual"
        self.query_one("#right", Static).border_title = "today time breakdown"
        self.query_one("#bottom").border_title = "🕐 clock · ▶ record activity"
        self.query_one("#rec", Input).can_focus = False  # don't let the input steal keys until armed with `s`
        self.action_refresh_panels()
        self.call_after_refresh(self._render_timeline)   # width is known only after first layout
        self.call_after_refresh(lambda: self.set_focus(None))
        self.set_interval(1.0, self._render_status)      # live clock + running-timer, once a second
        self.set_interval(15.0, self._render_timeline)   # move the now-cursor (uses cached data, no refetch)

    def on_resize(self, event) -> None:
        self._render_timeline()                          # re-stretch the timeline to the new width

    # ---- panels ----

    def _render_timeline(self) -> None:
        if self._day_data is None:
            self._day_data = render.collect_day(self.date)
        now = dt.datetime.now()
        show_now = self.date == now.date()               # cursor only makes sense when viewing today
        live = (self.rec_start, now, self.rec_bucket, self.rec_summary) if (self.rec_start and show_now) else None
        w = self.query_one("#timeline", Static).size.width or 100
        self.query_one("#timeline", Static).update(
            render.day_timeline_renderable(self._day_data, width=w,
                                           now=now if show_now else None, live=live))

    def action_refresh_panels(self) -> None:
        self._day_data = render.collect_day(self.date)   # refetch the day's events (network on gcal)
        self._render_timeline()
        self.query_one("#left", Static).update(render.expression_renderable(self.date))
        self.query_one("#right", Static).update(render.day_bars_renderable(self.date))
        self._render_status()

    def _render_status(self) -> None:
        now = dt.datetime.now()
        parts = [f"🕐 {now:%H:%M:%S}"]
        if self.rec_start:
            s = int((now - self.rec_start).total_seconds())
            parts.append(f"▶ [{self.rec_bucket}] {self.rec_summary} · 已 {s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}")
        else:
            parts.append("⏸ 未在记录")
        parts.append("[s]开始/切换 [e]停止 [r]刷新 [q]退出")
        if self.flash:
            parts.append(self.flash)
        self.query_one("#status", Static).update("　".join(parts))

    # ---- recorder ----

    def action_start_rec(self) -> None:
        """Reveal + focus the input to name the activity you're starting now."""
        rec = self.query_one("#rec", Input)
        rec.can_focus = True
        rec.add_class("armed")
        rec.value = ""
        self.set_focus(rec)

    def _disarm(self) -> None:
        rec = self.query_one("#rec", Input)
        rec.value = ""
        rec.remove_class("armed")
        rec.can_focus = False
        self.set_focus(None)

    def action_stop_rec(self) -> None:
        if not self.rec_start:
            self.flash = "（当前没有在记录）"
            self._render_status()
            return
        self._log_segment(dt.datetime.now())
        self.flash = f"⏹ 已记录 [{self.rec_bucket}] {self.rec_summary}"
        self.rec_start = None
        self.action_refresh_panels()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "rec":
            return
        text = event.value.strip()
        self._disarm()
        if text:
            self._begin(text)

    def on_key(self, event) -> None:
        if event.key == "escape" and self.query_one("#rec", Input).has_focus:
            self._disarm()
            event.stop()

    def _begin(self, text: str) -> None:
        """Start (or switch to) an activity now. Leading valid-bucket token wins; else auto-classify by keyword."""
        head, _, rest = text.partition(" ")
        if head in BUCKETS:                              # explicit override, e.g. "life 磕盐"
            bucket, summary, auto = head, rest.strip() or head, False
        else:
            summary, bucket, auto = text, classify.guess_bucket(text), True
        now = dt.datetime.now()
        if self.rec_start:                               # switching → close out the previous segment first
            self._log_segment(now)
        self.rec_start, self.rec_bucket, self.rec_summary = now, bucket, summary
        self.flash = f"▶ 开始 [{bucket}{'·自动' if auto else ''}] {summary}"
        self.action_refresh_panels()

    def _log_segment(self, end: dt.datetime) -> None:
        """Write the running segment [rec_start, end] to today's ②Actual (same path as `timeplanner log`)."""
        start = self.rec_start
        if (end - start).total_seconds() < 60:           # avoid a zero-length event on a quick switch
            end = start + dt.timedelta(minutes=1)
        try:
            backend.log_actual(dt.date.today(), start.strftime("%H:%M"), end.strftime("%H:%M"),
                               self.rec_bucket, self.rec_summary)
        except Exception as e:                           # noqa: BLE001 — a write hiccup must not crash the TUI
            self.flash = f"⚠️ 记录失败：{e}"

    def action_quit(self) -> None:
        if self.rec_start:                               # don't lose an in-progress segment on exit
            self._log_segment(dt.datetime.now())
        self.exit()


def run(date: dt.date) -> None:
    Dashboard(date).run()

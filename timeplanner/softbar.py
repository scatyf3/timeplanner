"""Floating softbar (`timeplanner softbar`) —— a thin always-on-top bar for soft time recording.

    ┌──────────────────────────────────────────────────────────────────────────────────────┐
    │ 回来日 │ 计划 main 写 method · 剩 42min │ ▶ fun 玩 0h23 │ [在干嘛…] main side life health fun ■ │
    └──────────────────────────────────────────────────────────────────────────────────────┘

One click on a bucket ends the running segment, writes it to ②Actual and starts the next one
(core/running.py — same path as `timeplanner log`, so local and gcal backends both work).
Soft prompts, never blocks:
- work → fun: "先收尾 12 分钟？" (好 = a life wind-down segment, then a nudge to switch to fun)
- main/side on a rest day / re-entry morning / after an event: a note, click again to go ahead

Closing the bar does not stop the recording: the running segment lives in data/running.json,
so reopening the bar (or `timeplanner rec stop`) picks it up. Drag to move; position is remembered.
Right-click: collapse to a small pill (or double-click the bar), turn always-on-top off/on — both remembered.
Works on macOS and Windows (PySide6, `pip install -e '.[softbar]'`).
"""

from __future__ import annotations

import datetime as dt
import sys

from PySide6.QtCore import QPoint, QSettings, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QFont, QPalette
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QLayout, QLineEdit, QMenu,
                               QPushButton, QWidget)

from .core import recovery, running
from .core.gcal import BUCKET_HEX

BUCKET_ORDER = ["main", "side", "life", "health", "fun"]
BUCKET_LABEL = {"main": "main", "side": "side", "life": "生活", "health": "健康", "fun": "玩"}
CONFIRM_WINDOW_S = 6
FLASH_S = 6
SLOW_REFRESH_MS = 120_000          # plan + day mode (may hit GCal): every 2 min
BG = "rgba(28, 30, 34, 225)"
INK, QUIET = "#f2f2f2", "#a9adb3"


def _set(lab: QLabel, text: str) -> None:
    """setText + pin the minimum width to the text, so the layout never squeezes it into a cut-off."""
    lab.setText(text)
    lab.ensurePolished()   # stylesheet font weight/padding applied before measuring
    lab.setMinimumWidth(lab.fontMetrics().horizontalAdvance(text) + 16)


def _chip(text: str, color: str = QUIET) -> QLabel:
    lab = QLabel(text)
    lab.setStyleSheet(f"color:{color}; padding:0 6px;")
    return lab


class SoftBar(QWidget):
    def __init__(self) -> None:
        self._settings = QSettings("timeplanner", "softbar")
        self._on_top = self._settings.value("on_top", True, type=bool)
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
        if self._on_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        super().__init__(None, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("TimePlanner softbar")
        self._drag: QPoint | None = None
        self._pending: tuple[str, dt.datetime] | None = None   # (bucket, when) awaiting a second click
        f = QFont()
        f.setFamilies(["PingFang SC", "Microsoft YaHei UI", "Microsoft YaHei", "Noto Sans CJK SC", "sans-serif"])
        f.setPointSize(12)
        self.setFont(f)   # a CJK-capable primary font keeps text metrics = what's drawn (no clipped labels)

        frame = QFrame(self)
        frame.setObjectName("bar")
        frame.setStyleSheet(f"#bar {{ background:{BG}; border-radius:10px; }}"
                            f"QLabel {{ color:{INK}; }}"
                            "QLineEdit { background:rgba(255,255,255,0.08); color:#f2f2f2; border:none;"
                            " border-radius:6px; padding:3px 8px; min-width:120px; }")
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)   # the bar always fits its contents
        outer.addWidget(frame)
        row = QHBoxLayout(frame)
        row.setContentsMargins(10, 5, 8, 5)
        row.setSpacing(6)

        self.mode = _chip("")
        self.plan = _chip("", QUIET)
        self.rec = _chip("", INK)
        self.msg = _chip("", "#f6bf26")
        self.summary = QLineEdit()
        self.summary.setPlaceholderText("在干嘛（可选）")
        self.summary.returnPressed.connect(self._enter)

        self.buttons: dict[str, QPushButton] = {}
        self._seps = [self._sep(), self._sep()]
        for w in (self.mode, self._seps[0], self.plan, self._seps[1], self.rec, self.msg):
            row.addWidget(w)
        row.addStretch(1)
        row.addWidget(self.summary)
        for b in BUCKET_ORDER:
            btn = QPushButton(BUCKET_LABEL[b])
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _=False, b=b: self.choose(b))
            self.buttons[b] = btn
            row.addWidget(btn)
        self.stop_btn = QPushButton("■")
        self.stop_btn.setToolTip("停止记录")
        self.stop_btn.clicked.connect(self.stop)
        row.addWidget(self.stop_btn)

        # the wind-down question replaces the bucket row's message area while it's asked
        self.yes_btn = QPushButton("好，先收尾")
        self.no_btn = QPushButton("直接玩")
        self.yes_btn.clicked.connect(self._wind_down_yes)
        self.no_btn.clicked.connect(self._wind_down_no)
        for w in (self.no_btn, self.yes_btn):   # inserted at the same index → ends up yes, no
            w.hide()
            row.insertWidget(row.indexOf(self.msg) + 1, w)

        self._style_buttons()
        self._tick = QTimer(self, interval=1000, timeout=self._render_running)
        self._slow = QTimer(self, interval=SLOW_REFRESH_MS, timeout=self.refresh)
        self._nudge = QTimer(self, singleShot=True, timeout=self._nudge_fun)
        self._clear = QTimer(self, singleShot=True, timeout=lambda: (_set(self.msg, ""), self.adjustSize()))
        self._tick.start()
        self._slow.start()
        self.refresh()
        self._set_collapsed(self._settings.value("collapsed", False, type=bool))
        self._place()

    # ---- layout / look ----

    @staticmethod
    def _sep() -> QLabel:
        return _chip("·", QUIET)

    def _style_buttons(self, highlight: str | None = None) -> None:
        r = running.current()
        for b, btn in self.buttons.items():
            hexc = BUCKET_HEX.get(b, "#888")
            on = r is not None and r.bucket == b
            glow = b == highlight
            btn.setStyleSheet(
                f"QPushButton {{ color:{'#111' if on else INK}; background:{hexc if on else 'transparent'};"
                f" border:{'2px' if glow else '1px'} solid {hexc}; border-radius:6px; padding:3px 10px; }}"
                f"QPushButton:hover {{ background:{hexc}; color:#111; }}")
        self.stop_btn.setStyleSheet(f"QPushButton {{ color:{INK}; background:transparent; border:1px solid {QUIET};"
                                    " border-radius:6px; padding:3px 8px; }")
        for w in (self.yes_btn, self.no_btn):
            w.setStyleSheet("QPushButton { color:#111; background:#f6bf26; border:none; border-radius:6px;"
                            " padding:3px 8px; }")

    def _place(self) -> None:
        self.adjustSize()
        pos = self._settings.value("pos")
        if isinstance(pos, QPoint):
            self.move(pos)
            return
        scr = QApplication.primaryScreen().availableGeometry()
        self.move(scr.x() + (scr.width() - self.width()) // 2, scr.y() + 8)

    # ---- rendering ----

    def refresh(self) -> None:
        """Slow parts: day mode + plan block (may read GCal)."""
        try:
            mode = recovery.day_mode(dt.date.today())
            color = {recovery.REST: "#33b679", recovery.REENTRY: "#f6bf26",
                     recovery.POST_EVENT: "#f6bf26", recovery.EVENING_OFF: "#f6bf26"}.get(mode.kind, QUIET)
            self.mode.setStyleSheet(f"color:{color}; font-weight:600; padding:0 6px;")
            _set(self.mode, mode.label)
            self.mode.setToolTip(mode.reason + ("\n" + "\n".join(mode.rules) if mode.rules else ""))
        except Exception as e:  # noqa: BLE001 — the bar must stay up whatever the data sources do
            _set(self.mode, "?")
            self.mode.setToolTip(str(e))
        now = running._now()
        cur, nxt = running.plan_now(now=now)
        if cur:
            _set(self.plan, f"计划 {cur.bucket or 'ext'} {cur.summary} · 剩 {running._fmt(cur.end - now)}")
        elif nxt:
            _set(self.plan, f"下一块 {nxt.start:%H:%M} {nxt.bucket or 'ext'} {nxt.summary}")
        else:
            _set(self.plan, "没有后续计划块")
        self._render_running()

    def _render_running(self) -> None:
        r = running.current()
        if r:
            what = "" if r.summary == running.DEFAULT_SUMMARY.get(r.bucket) else f" {r.summary}"
            self.rec.setStyleSheet(f"color:{BUCKET_HEX.get(r.bucket, INK)}; font-weight:600; padding:0 6px;")
            _set(self.rec, f"▶ {BUCKET_LABEL.get(r.bucket, r.bucket)}{what} {running._fmt(r.elapsed())}")
        else:
            self.rec.setStyleSheet(f"color:{QUIET}; padding:0 6px;")
            _set(self.rec, "⏸ 未在记录")

    def flash(self, text: str) -> None:
        _set(self.msg, text)
        self._clear.start(FLASH_S * 1000)
        self.adjustSize()

    # ---- actions ----

    def _enter(self) -> None:
        """Enter in the summary box: start with the bucket guessed from the words (TUI-style)."""
        from .core import classify
        text = self.summary.text().strip()
        if text:
            self.choose(classify.guess_bucket(text))

    def choose(self, bucket: str) -> None:
        note = running.work_on_off_day(bucket)
        if note:
            p = self._pending
            if not (p and p[0] == bucket and (dt.datetime.now() - p[1]).total_seconds() < CONFIRM_WINDOW_S):
                self._pending = (bucket, dt.datetime.now())
                self.flash(f"{note}（再点一次 {BUCKET_LABEL[bucket]}）")
                return
        self._pending = None
        if running.wants_wind_down(bucket):
            self._ask_wind_down()
            return
        self._start(bucket, self.summary.text())

    def _start(self, bucket: str, summary: str = "") -> None:
        try:
            r, written = running.start(bucket, summary)
        except Exception as e:  # noqa: BLE001 — e.g. GCal offline: say so, keep the bar alive
            self.flash(f"记录失败：{e}")
            return
        self.summary.clear()
        done = f"已记 {written[-1].summary} · " if written else ""
        self.flash(f"{done}开始 {BUCKET_LABEL[bucket]}")
        self._style_buttons()
        self._render_running()
        self.adjustSize()

    def stop(self) -> None:
        try:
            written = running.stop()
        except Exception as e:  # noqa: BLE001
            self.flash(f"记录失败：{e}")
            return
        self._nudge.stop()
        self.flash(f"已记 {written[-1].summary}" if written else "当前没有在记录")
        self._style_buttons()
        self._render_running()

    def _ask_wind_down(self) -> None:
        _set(self.msg, "先收尾 12 分钟？写 next action + 收拾桌面")
        self._clear.stop()
        self.yes_btn.show()
        self.no_btn.show()
        self.adjustSize()

    def _hide_question(self) -> None:
        self.yes_btn.hide()
        self.no_btn.hide()
        _set(self.msg, "")
        self.adjustSize()

    def _wind_down_yes(self) -> None:
        self._hide_question()
        self._start("life", running.WIND_DOWN_SUMMARY)
        self._nudge.start(int(running.WIND_DOWN.total_seconds() * 1000))

    def _wind_down_no(self) -> None:
        self._hide_question()
        self._start("fun", self.summary.text())

    def _nudge_fun(self) -> None:
        r = running.current()
        if r and r.summary == running.WIND_DOWN_SUMMARY:
            self.flash("收尾完了，点「玩」开玩")
            self._style_buttons(highlight="fun")
            QApplication.beep()

    # ---- drag + menu ----

    def mousePressEvent(self, e) -> None:  # noqa: N802 — Qt API
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        if self._drag is not None and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseDoubleClickEvent(self, e) -> None:  # noqa: N802 — double-click the bar: collapse / expand
        self._set_collapsed(not self._collapsed)

    def _set_collapsed(self, on: bool) -> None:
        """Collapsed = only the day mode + what's being recorded; the plan, input and buttons hide."""
        self._collapsed = on
        for w in (*self._seps, self.plan, self.summary, *self.buttons.values(), self.stop_btn):
            w.setVisible(not on)
        self._settings.setValue("collapsed", on)
        self.adjustSize()

    def _toggle_on_top(self) -> None:
        self._on_top = not self._on_top
        self._settings.setValue("on_top", self._on_top)
        pos = self.pos()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, self._on_top)
        self.show()          # changing window flags hides the window; show it again where it was
        self.move(pos)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if self._drag is not None:
            self._settings.setValue("pos", self.pos())
        self._drag = None

    def contextMenuEvent(self, e) -> None:  # noqa: N802
        menu = QMenu(self)
        items = (("展开" if self._collapsed else "收起成小条（双击条也行）", lambda: self._set_collapsed(not self._collapsed)),
                 ("取消置顶" if self._on_top else "置顶", self._toggle_on_top),
                 ("刷新", self.refresh), ("回到屏幕顶部", self._reset_pos), ("退出（不停止记录）", self.close))
        for label, fn in items:
            act = QAction(label, menu)
            act.triggered.connect(fn)
            menu.addAction(act)
        menu.exec(e.globalPos())

    def _reset_pos(self) -> None:
        self._settings.remove("pos")
        self._place()


def _hide_dock_icon() -> None:
    """macOS: run as an accessory app (no Dock icon, no app menu), like a menu-bar utility. No pyobjc needed."""
    if sys.platform != "darwin":
        return
    try:
        import ctypes
        import ctypes.util
        objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
        objc.objc_getClass.restype = ctypes.c_void_p
        objc.sel_registerName.restype = ctypes.c_void_p
        send = objc.objc_msgSend
        send.restype = ctypes.c_void_p
        send.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        nsapp = send(objc.objc_getClass(b"NSApplication"), objc.sel_registerName(b"sharedApplication"))
        send.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long]
        send(nsapp, objc.sel_registerName(b"setActivationPolicy:"), 1)   # NSApplicationActivationPolicyAccessory
    except Exception:  # noqa: BLE001 — cosmetic; a Dock icon is not worth crashing over
        pass


def run() -> int:
    from PySide6.QtCore import QLockFile

    from .config import config

    config.data_dir.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(config.data_dir / "softbar.lock"))
    if not lock.tryLock(200):   # login autostart + a manual open → still one bar
        print("浮动条已经在跑了。")
        return 0
    app = QApplication.instance() or QApplication(sys.argv)
    _hide_dock_icon()
    app.setQuitOnLastWindowClosed(True)
    pal = app.palette()
    pal.setColor(QPalette.ColorRole.ToolTipBase, QColor("#222"))
    app.setPalette(pal)
    bar = SoftBar()
    bar.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(run())

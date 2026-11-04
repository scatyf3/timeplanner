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
Right-click: collapse to a small pill (or double-click the bar), tuck it into the menu bar icon, turn
always-on-top off/on — all remembered. The menu bar dot (Windows: tray) shows the bucket being recorded and
its menu switches buckets / stops / brings the bar back.
Auto-stop (core/running.autostop_reason): a main/side/fun segment ends by itself when the computer sleeps
(ended at the last tick before the gap), sits idle 20 min (ended when input stopped), or the bar was gone
(ended at its last heartbeat). Life/health keep running — you're away from the computer for those.
Works on macOS and Windows (PySide6, `pip install -e '.[softbar]'`).
"""

from __future__ import annotations

import datetime as dt
import sys

from PySide6.QtCore import QPoint, QSettings, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QLayout, QLineEdit, QMenu,
                               QPushButton, QSystemTrayIcon, QWidget)

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
        flags = Qt.WindowType.FramelessWindowHint
        if sys.platform != "darwin":
            # Windows: Tool keeps it off the taskbar. macOS: NOT Tool — a Tool window is an NSPanel that hides
            # whenever another app is frontmost (that's how the bar "disappeared"); the Dock icon is hidden instead.
            flags |= Qt.WindowType.Tool
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
        self._last_tick = running._now()
        self._ticks = 0
        self._tick = QTimer(self, interval=1000, timeout=self._on_tick)
        self._slow = QTimer(self, interval=SLOW_REFRESH_MS, timeout=self.refresh)
        self._nudge = QTimer(self, singleShot=True, timeout=self._nudge_fun)
        self._clear = QTimer(self, singleShot=True, timeout=lambda: (_set(self.msg, ""), self.adjustSize()))
        self._tick.start()
        self._slow.start()
        recovered = running.recover_on_start()      # e.g. the Mac was shut down mid-segment
        running.heartbeat()
        self.refresh()
        self._set_collapsed(self._settings.value("collapsed", False, type=bool))
        if recovered and recovered[0]:
            QTimer.singleShot(1500, lambda: self._announce_stop(recovered[0], recovered[1]))
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
        # a remembered spot on a monitor that's gone (or off the edge) → back to the top of the main screen
        if isinstance(pos, QPoint) and any(s.availableGeometry().contains(pos) for s in QApplication.screens()):
            self.move(pos)
            return
        scr = QApplication.primaryScreen().availableGeometry()
        self.move(scr.x() + (scr.width() - self.width()) // 2, scr.y() + 8)

    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        _mac_pin(self, self._on_top)

    def bring_back(self) -> None:
        """Make the bar visible again on-screen (a second `timeplanner softbar` / app open calls this)."""
        self._settings.setValue("tucked", False)
        self._place()
        self.show()
        self.raise_()
        _mac_pin(self, self._on_top)

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

    def _on_tick(self) -> None:
        """Every second: redraw the timer; every 30 s check idle; every minute heartbeat. Auto-stop on sleep/idle."""
        now = running._now()
        self._ticks += 1
        idle_s = None
        if self._ticks % 30 == 0:
            from .core import idle
            idle_s = idle.idle_seconds()
        hit = running.autostop_reason(running.current(), now, self._last_tick, idle_s)
        self._last_tick = now
        if hit:
            end, why = hit
            try:
                written = running.stop(end)
            except Exception as e:  # noqa: BLE001 — e.g. GCal offline after wake
                self.flash(f"自动结束失败：{e}")
                written = []
            if written:
                self._announce_stop(written, why)
        if self._ticks % 60 == 0:
            running.heartbeat(now)
        self._render_running()

    def _announce_stop(self, written, why: str) -> None:
        e = written[-1]
        text = f"{why}，已在 {e.end:%H:%M} 自动结束「{e.summary}」"
        self.flash(text)
        self._style_buttons()
        if getattr(self, "tray", None):
            self.tray.showMessage("TimePlanner", text, QSystemTrayIcon.MessageIcon.Information, 8000)

    def _render_running(self) -> None:
        r = running.current()
        if r:
            what = "" if r.summary == running.DEFAULT_SUMMARY.get(r.bucket) else f" {r.summary}"
            self.rec.setStyleSheet(f"color:{BUCKET_HEX.get(r.bucket, INK)}; font-weight:600; padding:0 6px;")
            _set(self.rec, f"▶ {BUCKET_LABEL.get(r.bucket, r.bucket)}{what} {running._fmt(r.elapsed())}")
        else:
            self.rec.setStyleSheet(f"color:{QUIET}; padding:0 6px;")
            _set(self.rec, "⏸ 未在记录")
        self._update_tray()

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
                if not self.isVisible():
                    self.bring_back()
                self.flash(f"{note}（再点一次 {BUCKET_LABEL[bucket]}）")
                return
        self._pending = None
        if running.wants_wind_down(bucket):
            if not self.isVisible():
                self.bring_back()
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
        _mac_pin(self, self._on_top)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if self._drag is not None:
            self._settings.setValue("pos", self.pos())
        self._drag = None

    def contextMenuEvent(self, e) -> None:  # noqa: N802
        menu = QMenu(self)
        items = (("展开" if self._collapsed else "收起成小条（双击条也行）", lambda: self._set_collapsed(not self._collapsed)),
                 ("取消置顶" if self._on_top else "置顶", self._toggle_on_top),
                 *((("收进菜单栏", self.tuck),) if getattr(self, "tray", None) else ()),
                 ("刷新", self.refresh), ("回到屏幕顶部", self._reset_pos), ("退出（不停止记录）", QApplication.quit))
        for label, fn in items:
            act = QAction(label, menu)
            act.triggered.connect(fn)
            menu.addAction(act)
        menu.exec(e.globalPos())

    # ---- menu bar / tray icon ----

    def attach_tray(self) -> None:
        """A dot in the macOS menu bar (Windows: system tray) colored by what's being recorded. Its menu switches
        buckets, stops, and shows/hides the bar, so the bar can live tucked away in it."""
        self.tray = QSystemTrayIcon(self)
        self._tray_menu = QMenu()
        self._tray_menu.aboutToShow.connect(self._fill_tray_menu)
        self.tray.setContextMenu(self._tray_menu)
        self.tray.activated.connect(self._tray_clicked)
        self._tray_key = None
        self._update_tray()
        self.tray.show()

    def _fill_tray_menu(self) -> None:
        m = self._tray_menu
        m.clear()
        r = running.current()
        head = m.addAction(running.status_text())
        head.setEnabled(False)
        m.addSeparator()
        for b in BUCKET_ORDER:
            act = m.addAction(("● " if r and r.bucket == b else "   ") + BUCKET_LABEL[b])
            act.triggered.connect(lambda _=False, b=b: self.choose(b))
        m.addAction("■ 停止记录").triggered.connect(self.stop)
        m.addSeparator()
        if self.isVisible():
            m.addAction("收进菜单栏").triggered.connect(self.tuck)
        else:
            m.addAction("显示浮动条").triggered.connect(self.untuck)
        m.addAction("退出（不停止记录）").triggered.connect(QApplication.quit)

    def _tray_clicked(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger and sys.platform != "darwin":
            self.untuck() if not self.isVisible() else self.tuck()   # Windows: left-click toggles the bar

    def _update_tray(self) -> None:
        if not getattr(self, "tray", None):
            return
        r = running.current()
        key = r.bucket if r else None
        if key != self._tray_key:                       # repaint the dot only when the bucket changes
            self._tray_key = key
            pm = QPixmap(36, 36)
            pm.fill(Qt.GlobalColor.transparent)
            p = QPainter(pm)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(QColor(QUIET) if not r else Qt.PenStyle.NoPen)
            p.setBrush(QColor(BUCKET_HEX.get(key, "#888")) if r else Qt.BrushStyle.NoBrush)
            p.drawEllipse(6, 6, 24, 24)
            p.end()
            self.tray.setIcon(QIcon(pm))
        self.tray.setToolTip(running.status_text())

    def tuck(self) -> None:
        """Hide the bar into the menu bar icon (remembered)."""
        if getattr(self, "tray", None):
            self.hide()
            self._settings.setValue("tucked", True)

    def untuck(self) -> None:
        self._settings.setValue("tucked", False)
        self.bring_back()

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


def _mac_pin(w: QWidget, on_top: bool) -> None:
    """macOS: never hide when another app is frontmost, show on every Space and over full-screen apps,
    and sit at the status-bar level when on top. Plain Qt can't say these; done via the ObjC runtime."""
    if sys.platform != "darwin":
        return
    try:
        import ctypes
        import ctypes.util
        objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
        objc.sel_registerName.restype = ctypes.c_void_p
        sel = objc.sel_registerName
        send = objc.objc_msgSend
        send.restype = ctypes.c_void_p
        send.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        win = send(ctypes.c_void_p(int(w.winId())), sel(b"window"))     # winId() is the NSView
        if not win:
            return
        send.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_bool]
        send(win, sel(b"setHidesOnDeactivate:"), False)
        send.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
        # CanJoinAllSpaces | Stationary | FullScreenAuxiliary
        send(win, sel(b"setCollectionBehavior:"), (1 << 0) | (1 << 4) | (1 << 8))
        send.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long]
        send(win, sel(b"setLevel:"), 25 if on_top else 0)              # NSStatusWindowLevel / normal
    except Exception:  # noqa: BLE001 — best effort; the bar still works with Qt's own on-top flag
        pass


_SERVER = "timeplanner-softbar"


def run() -> int:
    from PySide6.QtCore import QLockFile
    from PySide6.QtNetwork import QLocalServer, QLocalSocket

    from .config import config

    config.data_dir.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(config.data_dir / "softbar.lock"))
    if not lock.tryLock(200):   # already running → ask that bar to come back on screen, then leave
        sock = QLocalSocket()
        sock.connectToServer(_SERVER)
        if sock.waitForConnected(1000):
            sock.write(b"show")
            sock.waitForBytesWritten(1000)
            print("浮动条已经在跑了，已经把它调回屏幕上。")
        else:
            print("浮动条已经在跑了（没能叫醒它：右键菜单里「回到屏幕顶部」，或重启它）。")
        return 0
    app = QApplication.instance() or QApplication(sys.argv)
    _hide_dock_icon()
    app.setQuitOnLastWindowClosed(False)        # the bar may be tucked into the menu bar; quit via 退出
    pal = app.palette()
    pal.setColor(QPalette.ColorRole.ToolTipBase, QColor("#222"))
    app.setPalette(pal)
    bar = SoftBar()
    has_tray = QSystemTrayIcon.isSystemTrayAvailable()
    if has_tray:
        bar.attach_tray()
    if not (has_tray and bar._settings.value("tucked", False, type=bool)):
        bar.show()
    QLocalServer.removeServer(_SERVER)          # a stale socket from a crash would block listen()
    server = QLocalServer()
    server.newConnection.connect(lambda: (server.nextPendingConnection(), bar.bring_back()))
    server.listen(_SERVER)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(run())

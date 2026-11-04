"""Seconds since the last keyboard/mouse input on this machine, or None when it can't be read.

macOS: IOHIDSystem's HIDIdleTime (via `ioreg`, no extra deps). Windows: GetLastInputInfo. Elsewhere: None.
"""

from __future__ import annotations

import re
import subprocess
import sys

_HID = re.compile(r'"HIDIdleTime"\s*=\s*(\d+)')


def idle_seconds() -> float | None:
    try:
        if sys.platform == "darwin":
            out = subprocess.run(["ioreg", "-c", "IOHIDSystem", "-d", "4"], capture_output=True,
                                 text=True, timeout=3).stdout
            m = _HID.search(out)
            return int(m.group(1)) / 1e9 if m else None
        if sys.platform.startswith("win"):
            import ctypes

            class LASTINPUTINFO(ctypes.Structure):
                _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

            info = LASTINPUTINFO()
            info.cbSize = ctypes.sizeof(info)
            if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
                return None
            ctypes.windll.kernel32.GetTickCount64.restype = ctypes.c_uint64
            now32 = ctypes.windll.kernel32.GetTickCount64() & 0xFFFFFFFF   # dwTime is a 32-bit tick count
            return ((now32 - info.dwTime) % 2**32) / 1000.0
    except Exception:  # noqa: BLE001 — idle detection is best effort
        return None
    return None

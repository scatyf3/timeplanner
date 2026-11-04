"""Install the softbar as an app + login item (`timeplanner softbar --install` / `--uninstall`).

macOS:
- ~/Applications/TimePlanner Softbar.app — a thin wrapper (Spotlight / Launchpad can open it), LSUIElement so
  it has no Dock icon; its executable just execs this venv's Python on `timeplanner.softbar`.
- ~/Library/LaunchAgents/com.timeplanner.softbar.plist — starts it at login and restarts it if it crashes
  (KeepAlive on unsuccessful exit only, so 右键→退出 really quits). Log: ~/Library/Logs/timeplanner-softbar.log
Windows:
- a hidden-window launcher in the Startup folder (pythonw, no console).

The bar itself refuses a second instance (lock file), so login autostart + a manual open never give two bars.
Everything is plain files; --uninstall removes exactly what --install wrote.
"""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path

from .config import REPO_ROOT

LABEL = "com.timeplanner.softbar"
APP_NAME = "TimePlanner Softbar"


def _python(gui: bool = False) -> str:
    exe = Path(sys.executable)
    if gui and os.name == "nt":                      # pythonw = no console window
        w = exe.with_name("pythonw.exe")
        if w.is_file():
            return str(w)
    return str(exe)


# ---- macOS ----

def _mac_paths(home: Path) -> tuple[Path, Path, Path]:
    app = home / "Applications" / f"{APP_NAME}.app"
    agent = home / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    log = home / "Library" / "Logs" / "timeplanner-softbar.log"
    return app, agent, log


def _write_mac(home: Path) -> list[Path]:
    app, agent, log = _mac_paths(home)
    macos = app / "Contents" / "MacOS"
    macos.mkdir(parents=True, exist_ok=True)
    exe = macos / "softbar"
    exe.write_text(
        "#!/bin/bash\n"
        f'cd "{REPO_ROOT}"\n'
        f'exec "{_python()}" -m timeplanner.softbar "$@"\n', encoding="utf-8")
    exe.chmod(0o755)
    with open(app / "Contents" / "Info.plist", "wb") as f:
        plistlib.dump({
            "CFBundleName": APP_NAME, "CFBundleDisplayName": APP_NAME,
            "CFBundleIdentifier": LABEL, "CFBundleExecutable": "softbar",
            "CFBundlePackageType": "APPL", "CFBundleVersion": "0.1.0",
            "LSUIElement": True,                     # no Dock icon, no menu bar takeover
        }, f)

    agent.parent.mkdir(parents=True, exist_ok=True)
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(agent, "wb") as f:
        plistlib.dump({
            "Label": LABEL,
            "ProgramArguments": [str(exe)],
            "RunAtLoad": True,
            "KeepAlive": {"SuccessfulExit": False},  # restart after a crash, not after 退出
            "ProcessType": "Interactive",
            "StandardOutPath": str(log), "StandardErrorPath": str(log),
        }, f)
    return [app, agent]


def _launchctl(*args: str) -> None:
    try:
        subprocess.run(["launchctl", *args], check=False, capture_output=True)
    except FileNotFoundError:
        pass


# ---- Windows ----

def _win_startup(home: Path) -> Path:
    appdata = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
    return appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / f"{APP_NAME}.vbs"


def _write_win(home: Path) -> list[Path]:
    vbs = _win_startup(home)
    vbs.parent.mkdir(parents=True, exist_ok=True)
    vbs.write_text(
        'Set sh = CreateObject("WScript.Shell")\r\n'
        f'sh.CurrentDirectory = "{REPO_ROOT}"\r\n'
        f'sh.Run """{_python(gui=True)}"" -m timeplanner.softbar", 0, False\r\n', encoding="utf-8")
    return [vbs]


# ---- entry points ----

def install(platform: str | None = None, home: Path | None = None, start: bool = True) -> str:
    platform = platform or sys.platform
    home = home or Path.home()
    if platform == "darwin":
        app, agent = _write_mac(home)
        if start:
            _launchctl("unload", str(agent))
            _launchctl("load", "-w", str(agent))
        return (f"✅ 已装好：\n  app：{app}（Spotlight 搜「{APP_NAME}」也能开）\n"
                f"  登录自启：{agent}\n  日志：{_mac_paths(home)[2]}\n"
                + ("现在已经在跑了；" if start else "") + "右键浮动条 →「退出」会真退出，下次登录再自动起来。\n"
                "第一次启动 macOS 可能问能否访问「文稿」文件夹（仓库在那里），选允许。")
    if platform.startswith("win"):
        (vbs,) = _write_win(home)
        if start:
            subprocess.Popen([_python(gui=True), "-m", "timeplanner.softbar"], cwd=str(REPO_ROOT))
        return f"✅ 已装好：登录自启 {vbs}" + ("\n现在已经在跑了。" if start else "")
    return "（这个平台还不支持自动安装；直接跑 `timeplanner softbar` 即可。）"


def uninstall(platform: str | None = None, home: Path | None = None) -> str:
    import shutil
    platform = platform or sys.platform
    home = home or Path.home()
    removed: list[Path] = []
    if platform == "darwin":
        app, agent, _ = _mac_paths(home)
        if agent.exists():
            _launchctl("unload", "-w", str(agent))
            agent.unlink()
            removed.append(agent)
        if app.exists():
            shutil.rmtree(app)
            removed.append(app)
    elif platform.startswith("win"):
        vbs = _win_startup(home)
        if vbs.exists():
            vbs.unlink()
            removed.append(vbs)
    if not removed:
        return "（没有找到装过的 softbar app / 自启项。）"
    return "🗑️ 已移除：\n" + "\n".join(f"  {p}" for p in removed) + "\n正在跑的浮动条右键 →「退出」即可关掉。"

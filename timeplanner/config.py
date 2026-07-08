"""集中配置。所有路径/密钥从 .env 读，带合理默认。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

try:
    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env")  # 锚定仓库的 .env，从任何目录跑都能读到
except ImportError:  # dotenv 是软依赖，缺了也能跑（只是不自动读 .env）
    pass


def _get(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _repo_path(s: str) -> Path:
    """相对路径锚定到仓库根，绝对路径原样。用于凭据等，保证换目录跑不失效。"""
    p = Path(s).expanduser()
    return p if p.is_absolute() else REPO_ROOT / p


@dataclass
class Config:
    vault: Path = field(default_factory=lambda: Path(_get("TIMEPLANNER_VAULT")).expanduser())
    daily_glob: str = field(default_factory=lambda: _get(
        "TIMEPLANNER_DAILY_GLOB", "0. PeriodicNotes/{year}/Daily/{month}"))

    aw_host: str = field(default_factory=lambda: _get("AW_HOST", "http://localhost:5600"))

    lat: float = field(default_factory=lambda: float(_get("TIMEPLANNER_LAT", "40.7291")))
    lon: float = field(default_factory=lambda: float(_get("TIMEPLANNER_LON", "-73.9965")))
    timezone: str = field(default_factory=lambda: _get("TIMEPLANNER_TIMEZONE", "America/New_York"))

    gcal_credentials: str = field(
        default_factory=lambda: str(_repo_path(_get("GCAL_CREDENTIALS", "credentials.json"))))
    gcal_token: str = field(
        default_factory=lambda: str(_repo_path(_get("GCAL_TOKEN", "token.json"))))
    gcal_plan_id: str = field(default_factory=lambda: _get("GCAL_PLAN_ID"))
    gcal_actual_id: str = field(default_factory=lambda: _get("GCAL_ACTUAL_ID"))

    # 存储后端：local（本地 data/*.json）| gcal（真日历）
    backend: str = field(default_factory=lambda: _get("TIMEPLANNER_BACKEND", "local"))

    tg_bot_token: str = field(default_factory=lambda: _get("TG_BOT_TOKEN"))
    tg_chat_id: str = field(default_factory=lambda: _get("TG_CHAT_ID"))

    # 原则库
    principles_path: Path = field(default_factory=lambda: REPO_ROOT / "principles.md")

    # planner 记忆缓存目录（仓库内，gitignore，绝不碰库）
    cache_dir: Path = field(default_factory=lambda: REPO_ROOT / ".cache")
    # 本地 timeline 数据（Plan/Actual，gitignore，是你的真实日程）
    data_dir: Path = field(default_factory=lambda: REPO_ROOT / "data")

    def vault_ok(self) -> bool:
        return self.vault.is_dir()


config = Config()

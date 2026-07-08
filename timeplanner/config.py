"""集中配置。所有路径/密钥从 .env 读，带合理默认。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # dotenv 是软依赖，缺了也能跑（只是不自动读 .env）
    pass

REPO_ROOT = Path(__file__).resolve().parent.parent


def _get(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


@dataclass
class Config:
    vault: Path = field(default_factory=lambda: Path(_get("TIMEPLANNER_VAULT")).expanduser())
    daily_glob: str = field(default_factory=lambda: _get(
        "TIMEPLANNER_DAILY_GLOB", "0. PeriodicNotes/{year}/Daily/{month}"))

    aw_host: str = field(default_factory=lambda: _get("AW_HOST", "http://localhost:5600"))

    lat: float = field(default_factory=lambda: float(_get("TIMEPLANNER_LAT", "40.7291")))
    lon: float = field(default_factory=lambda: float(_get("TIMEPLANNER_LON", "-73.9965")))
    timezone: str = field(default_factory=lambda: _get("TIMEPLANNER_TIMEZONE", "America/New_York"))

    gcal_credentials: str = field(default_factory=lambda: _get("GCAL_CREDENTIALS", "credentials.json"))
    gcal_token: str = field(default_factory=lambda: _get("GCAL_TOKEN", "token.json"))
    gcal_plan_id: str = field(default_factory=lambda: _get("GCAL_PLAN_ID"))
    gcal_actual_id: str = field(default_factory=lambda: _get("GCAL_ACTUAL_ID"))

    tg_bot_token: str = field(default_factory=lambda: _get("TG_BOT_TOKEN"))
    tg_chat_id: str = field(default_factory=lambda: _get("TG_CHAT_ID"))

    # 原则库
    principles_path: Path = field(default_factory=lambda: REPO_ROOT / "principles.md")

    def vault_ok(self) -> bool:
        return self.vault.is_dir()


config = Config()

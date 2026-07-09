"""Centralized config. All paths/keys read from .env, with sensible defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

try:
    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env")  # anchor to the repo's .env so it's found from any directory
except ImportError:  # dotenv is a soft dependency; runs without it (just won't auto-read .env)
    pass


def _get(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _repo_path(s: str) -> Path:
    """Relative paths anchor to the repo root, absolute paths as-is. For credentials etc., so it doesn't break when run from another directory."""
    p = Path(s).expanduser()
    return p if p.is_absolute() else REPO_ROOT / p


@dataclass
class Config:
    vault: Path = field(default_factory=lambda: Path(_get("TIMEPLANNER_VAULT")).expanduser())
    daily_glob: str = field(default_factory=lambda: _get(
        "TIMEPLANNER_DAILY_GLOB", "0. PeriodicNotes/{year}/Daily/{month}"))

    aw_host: str = field(default_factory=lambda: _get("AW_HOST", "http://localhost:5600"))
    # cross-machine AW: a Syncthing-shared folder of per-host daily observation snapshots
    # (<host>-<date>.json). Empty = single-machine, feature off. See core/aw_sync.py.
    aw_sync_dir: Path | None = field(default_factory=lambda: (
        Path(_get("TIMEPLANNER_AW_SYNC_DIR")).expanduser() if _get("TIMEPLANNER_AW_SYNC_DIR") else None))

    lat: float = field(default_factory=lambda: float(_get("TIMEPLANNER_LAT", "40.7291")))
    lon: float = field(default_factory=lambda: float(_get("TIMEPLANNER_LON", "-73.9965")))
    timezone: str = field(default_factory=lambda: _get("TIMEPLANNER_TIMEZONE", "America/New_York"))

    gcal_credentials: str = field(
        default_factory=lambda: str(_repo_path(_get("GCAL_CREDENTIALS", "credentials.json"))))
    gcal_token: str = field(
        default_factory=lambda: str(_repo_path(_get("GCAL_TOKEN", "token.json"))))
    gcal_plan_id: str = field(default_factory=lambda: _get("GCAL_PLAN_ID"))
    gcal_actual_id: str = field(default_factory=lambda: _get("GCAL_ACTUAL_ID"))

    # storage backend: local (local data/*.json) | gcal (real calendar)
    backend: str = field(default_factory=lambda: _get("TIMEPLANNER_BACKEND", "local"))

    tg_bot_token: str = field(default_factory=lambda: _get("TG_BOT_TOKEN"))
    tg_chat_id: str = field(default_factory=lambda: _get("TG_CHAT_ID"))

    # Cubox read API (key from https://<domain>/my/settings/extensions)
    cubox_key: str = field(default_factory=lambda: _get("TIMEPLANNER_CUBOX_KEY"))
    cubox_domain: str = field(default_factory=lambda: _get("TIMEPLANNER_CUBOX_DOMAIN", "cubox.cc"))
    # folders synced locally as agent search/reflect material (comma-separated names)
    cubox_folders: list[str] = field(default_factory=lambda: [
        s.strip() for s in _get("TIMEPLANNER_CUBOX_FOLDERS", "科学学习,科学工作,health,心理").split(",")
        if s.strip()])

    # principles library
    principles_path: Path = field(default_factory=lambda: REPO_ROOT / "principles.md")

    # planner memory cache directory (inside repo, gitignored, never touches the vault)
    cache_dir: Path = field(default_factory=lambda: REPO_ROOT / ".cache")
    # local timeline data (Plan/Actual, gitignored, your real schedule)
    data_dir: Path = field(default_factory=lambda: REPO_ROOT / "data")

    def vault_ok(self) -> bool:
        return self.vault.is_dir()


config = Config()

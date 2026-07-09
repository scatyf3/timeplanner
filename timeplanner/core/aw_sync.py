"""Cross-machine ActivityWatch via derived daily snapshots (a lightweight alternative to aw-sync).

Each machine exports its day's local AW observation to a JSON snapshot in a shared folder
(Syncthing / WebDAV / anything that mirrors a directory); the collector reads every machine's
snapshots and merges them with its own live AW into one Observed. Conflict-free by construction:
snapshots are named `<host>-<date>.json`, each host owns its files, and merging is just addition.

The shared folder is `config.aw_sync_dir` (env `TIMEPLANNER_AW_SYNC_DIR`); unset = single-machine,
everything below no-ops and the observed line is just the local aw-server as before.

Snapshot schema (v1):
  {"host": str, "date": "YYYY-MM-DD", "active_minutes": float,
   "focus_blocks": [["<iso start>", "<iso end>"], ...],
   "top_apps": [["app", minutes], ...], "exported_at": "<iso>"}

Machines without timeplanner (e.g. a macOS laptop) can produce the same schema with the
standalone `scripts/aw_export.py`.
"""

from __future__ import annotations

import datetime as dt
import json
import socket
from collections import defaultdict

from ..config import config
from . import activitywatch
from .activitywatch import FocusBlock, Observed

SCHEMA = 1


def local_host() -> str:
    return socket.gethostname()


def _snap_path(host: str, date: dt.date):
    return config.aw_sync_dir / f"{host}-{date:%Y-%m-%d}.json"


def export_day(date: dt.date | None = None) -> "object | None":
    """Write this machine's local AW observation for `date` as a snapshot. No-op (None) when the
    sync dir isn't configured or AW has no data for the day."""
    date = date or dt.date.today()
    if not config.aw_sync_dir:
        return None
    obs = activitywatch.observe(date)
    if not obs.available:
        return None
    snap = {
        "schema": SCHEMA,
        "host": local_host(),
        "date": f"{date:%Y-%m-%d}",
        "active_minutes": round(obs.active_minutes, 3),
        "focus_blocks": [[b.start.isoformat(), b.end.isoformat()] for b in obs.focus_blocks],
        "top_apps": [[a, round(m, 3)] for a, m in obs.top_apps],
        "exported_at": dt.datetime.now().astimezone().isoformat(),
    }
    config.aw_sync_dir.mkdir(parents=True, exist_ok=True)
    p = _snap_path(local_host(), date)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)                                        # atomic; Syncthing then mirrors it
    return p


def load_snapshots(date: dt.date) -> list[dict]:
    """Every host's snapshot for `date` found in the shared folder (empty if none / not configured)."""
    if not config.aw_sync_dir or not config.aw_sync_dir.is_dir():
        return []
    out: list[dict] = []
    for p in config.aw_sync_dir.glob(f"*-{date:%Y-%m-%d}.json"):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            continue
    return out


def merged_observe(date: dt.date | None = None) -> Observed:
    """Local live AW for `date` merged with every *other* host's snapshot into one Observed.

    Local data comes from the live aw-server (always fresh); other hosts come from snapshots.
    A host's own snapshot is skipped here so live data isn't double-counted.
    """
    date = date or dt.date.today()
    local = activitywatch.observe(date)
    host = local_host()

    blocks: list[FocusBlock] = list(local.focus_blocks)
    active = local.active_minutes if local.available else 0.0
    app_min: dict[str, float] = defaultdict(float)
    for a, m in local.top_apps:
        app_min[a] += m
    hosts: list[str] = [host] if local.available else []

    for snap in load_snapshots(date):
        if snap.get("host") == host:                     # local host already covered by live data
            continue
        hosts.append(snap.get("host", "?"))
        active += float(snap.get("active_minutes", 0))
        for s, e in snap.get("focus_blocks", []):
            try:
                blocks.append(FocusBlock(dt.datetime.fromisoformat(s), dt.datetime.fromisoformat(e)))
            except ValueError:
                continue
        for a, m in snap.get("top_apps", []):
            app_min[a] += float(m)

    blocks.sort(key=lambda b: b.start)
    top_apps = sorted(app_min.items(), key=lambda x: -x[1])[:8]
    note = ""
    if len(hosts) > 1:
        note = f"（合并 {len(hosts)} 台：{', '.join(hosts)}）"
    elif not local.available and not hosts:
        note = local.note                                # AW down and no snapshots → surface the reason
    return Observed(date=date, active_minutes=active, focus_blocks=blocks,
                    top_apps=top_apps, available=bool(hosts) or local.available, note=note)


def summary(date: dt.date | None = None) -> str:
    """Observed summary over the merged multi-host view (drop-in for activitywatch.summary)."""
    return activitywatch.format_observed(merged_observe(date or dt.date.today()))

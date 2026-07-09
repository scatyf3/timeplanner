#!/usr/bin/env python3
"""Standalone ActivityWatch daily-snapshot exporter — zero deps (stdlib only).

Runs on any machine with ActivityWatch, WITHOUT installing timeplanner. It queries the
local aw-server, derives that day's observation, and writes a `<host>-<date>.json`
snapshot into a shared folder (Syncthing/WebDAV). timeplanner's collector merges every
machine's snapshots into the ③ Observed line.

The snapshot schema is identical to timeplanner/core/aw_sync.py (keep them in sync).

Usage:
    AW_SYNC_DIR=~/ActivityWatchSync python3 aw_export.py            # today + yesterday
    python3 aw_export.py --sync-dir ~/ActivityWatchSync --date 2026-07-08
    python3 aw_export.py --sync-dir ~/ActivityWatchSync --aw-host http://localhost:5600
    python3 aw_export.py --sync-dir ~/ActivityWatchSync --host my-macbook

Pass --host (or set AW_HOST_NAME) wherever socket.gethostname() isn't stable — on DHCP-derived
names like `10-20-89-245.dynapool…` it changes with the IP, stranding a stale snapshot under the
old name that the collector would then add on top of this machine's real data.

Put it on a schedule (every ~5-10 min) via launchd (macOS) / cron / Task Scheduler.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import socket
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

SCHEMA = 1
MERGE_GAP_S = 300        # join not-afk spans less than this apart into one focus block
MIN_BLOCK_MIN = 25       # a focus block counts only if >= this long
TIMEOUT = 5


def slug(host: str) -> str:
    """Filename-safe host token. Must match timeplanner/core/aw_sync.py:slug()."""
    return re.sub(r"[^A-Za-z0-9_.]+", "-", host).strip("-.") or "unknown"


def _get(host: str, path: str):
    with urllib.request.urlopen(f"{host}{path}", timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


def _day_range(date: dt.date) -> tuple[str, str]:
    start = dt.datetime.combine(date, dt.time.min).astimezone()
    end = start + dt.timedelta(days=1)
    return start.isoformat(), end.isoformat()


def _events(host: str, bucket_id: str, start: str, end: str) -> list[dict]:
    q = urllib.parse.urlencode({"start": start, "end": end, "limit": 10000})
    return _get(host, f"/api/0/buckets/{bucket_id}/events?{q}")


def _pick_bucket(host, buckets, btype, start, end):
    best_id, best = "", []
    for bid, meta in buckets.items():
        if meta.get("type") != btype:
            continue
        try:
            ev = _events(host, bid, start, end)
        except Exception:
            continue
        if len(ev) > len(best):
            best_id, best = bid, ev
    return best_id, best


def _merge_notafk(afk_events):
    spans = []
    for e in afk_events:
        if e.get("data", {}).get("status") != "not-afk":
            continue
        ts = dt.datetime.fromisoformat(e["timestamp"]).astimezone()   # AW ts are UTC → local
        spans.append((ts, ts + dt.timedelta(seconds=float(e.get("duration", 0)))))
    spans.sort()
    active_s = sum((b - a).total_seconds() for a, b in spans)
    blocks = []
    for a, b in spans:
        if blocks and (a - blocks[-1][1]).total_seconds() <= MERGE_GAP_S:
            blocks[-1][1] = max(blocks[-1][1], b)
        else:
            blocks.append([a, b])
    blocks = [(a, b) for a, b in blocks if (b - a).total_seconds() / 60 >= MIN_BLOCK_MIN]
    return active_s / 60, blocks


def export_day(sync_dir: Path, aw_host: str, date: dt.date, host: str) -> Path | None:
    start, end = _day_range(date)
    try:
        buckets = _get(aw_host, "/api/0/buckets/")
    except Exception as e:
        print(f"  {date}: aw-server unreachable ({e})")
        return None

    _, afk = _pick_bucket(aw_host, buckets, "afkstatus", start, end)
    active_min, blocks = _merge_notafk(afk)

    _, win = _pick_bucket(aw_host, buckets, "currentwindow", start, end)
    app_min = defaultdict(float)
    for e in win:
        app_min[e.get("data", {}).get("app", "?")] += float(e.get("duration", 0)) / 60
    top_apps = sorted(app_min.items(), key=lambda x: -x[1])[:8]

    if not blocks and not top_apps:
        print(f"  {date}: no AW data, skip")
        return None

    snap = {
        "schema": SCHEMA,
        "host": host,
        "date": f"{date:%Y-%m-%d}",
        "active_minutes": round(active_min, 3),
        "focus_blocks": [[a.isoformat(), b.isoformat()] for a, b in blocks],
        "top_apps": [[a, round(m, 3)] for a, m in top_apps],
        "exported_at": dt.datetime.now().astimezone().isoformat(),
    }
    sync_dir.mkdir(parents=True, exist_ok=True)
    p = sync_dir / f"{host}-{date:%Y-%m-%d}.json"
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)
    print(f"  {date}: exported {p.name} (active {active_min:.0f}min, {len(blocks)} blocks)")
    return p


def main():
    ap = argparse.ArgumentParser(description="Export local ActivityWatch daily snapshot(s).")
    ap.add_argument("--sync-dir", default=os.environ.get("AW_SYNC_DIR") or os.environ.get("TIMEPLANNER_AW_SYNC_DIR"),
                    help="shared snapshot folder (or set AW_SYNC_DIR)")
    ap.add_argument("--aw-host", default=os.environ.get("AW_HOST", "http://localhost:5600"))
    ap.add_argument("--host", default=os.environ.get("AW_HOST_NAME") or os.environ.get("TIMEPLANNER_AW_HOST_NAME"),
                    help="stable name for this machine (default: socket.gethostname(), "
                         "which is unstable on DHCP-derived hostnames)")
    ap.add_argument("--date", help="YYYY-MM-DD (default: today + yesterday)")
    args = ap.parse_args()
    if not args.sync_dir:
        ap.error("no sync dir: pass --sync-dir or set AW_SYNC_DIR")

    sync_dir = Path(args.sync_dir).expanduser()
    host = slug(args.host or socket.gethostname())
    dates = [dt.date.fromisoformat(args.date)] if args.date else \
        [dt.date.today(), dt.date.today() - dt.timedelta(days=1)]
    print(f"aw-export → {sync_dir}  (host={host})")
    for d in dates:
        export_day(sync_dir, args.aw_host, d, host)


if __name__ == "__main__":
    main()

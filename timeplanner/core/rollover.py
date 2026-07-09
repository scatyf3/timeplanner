"""Day-rollover hook: once the clock crosses into a new day, flush each past day's
local timeline (①Plan + ②Actual) into that day's Obsidian daily note, then drop it
from the local cache. Wired into every CLI command (see cli.main); stays silent
unless it actually flushes something, and is never allowed to crash the command
that triggered it.

The vault is otherwise a read-only data source; this is the one sanctioned
write-back (README todo #1). It only ever creates or replaces a single clearly
marked section (`## ⏱ 时间线（timeplanner 回填）`) — your own content is never
touched — and it deletes from the local cache only *after* the note write
succeeds, so a failed write never loses data.

"Past" means strictly before today, decided purely from what's still in the local
cache; no separate state file. On the gcal backend the Plan/Actual live in Google,
not local files, so this naturally no-ops.
"""

from __future__ import annotations

import datetime as dt

from ..config import config
from . import activitywatch, notes, timeline

SECTION_TITLE = "⏱ 时间线（timeplanner 回填）"

SLOT_MIN = 30                     # timeline row granularity (mirrors render.py)
# one timeline item = (start, end, label)
_Item = tuple[dt.datetime, dt.datetime, str]


def _past_dates(today: dt.date) -> list[dt.date]:
    """Every date strictly before `today` that still has local Plan or Actual events."""
    dates: set[dt.date] = set()
    for which in (timeline.PLAN, timeline.ACTUAL):
        for e in timeline.all_events(which):
            if e.start.date() < today:
                dates.add(e.start.date())
    return sorted(dates)


def _short(s: str, n: int = 16) -> str:
    s = s.strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def _esc(s: str) -> str:
    return s.replace("|", "\\|")


def _floor_slot(t: dt.datetime) -> dt.datetime:
    base = t.replace(minute=0, second=0, microsecond=0)
    steps = int((t - base).total_seconds() // (SLOT_MIN * 60))
    return base + dt.timedelta(minutes=SLOT_MIN * steps)


def _ceil_slot(t: dt.datetime) -> dt.datetime:
    f = _floor_slot(t)
    return f if f == t else f + dt.timedelta(minutes=SLOT_MIN)


def _cell(slot_start: dt.datetime, slot_end: dt.datetime, items: list[_Item]) -> str:
    """A block bar `█` for a slot an item covers (with its label at the head slot), else empty."""
    for start, end, label in items:
        if start < slot_end and end > slot_start:          # item overlaps this slot
            head = slot_start <= start < slot_end
            return f"█ {_esc(_short(label))}" if head else "█"
    return ""


def _section_body(date: dt.date) -> str | None:
    """Render a day's ①Plan / ②Actual (and ③AW when available) as parallel columns sharing one
    time axis — a Markdown table. Returns None if the day has nothing to write."""
    cols: list[tuple[str, list[_Item]]] = [
        ("① Plan", [(e.start, e.end, e.summary) for e in timeline.list_events(date, timeline.PLAN)]),
        ("② Actual", [(e.start, e.end, e.summary) for e in timeline.list_events(date, timeline.ACTUAL)]),
    ]
    try:                                                   # ③ AW is a bonus column, only if it has data
        obs = activitywatch.observe(date)
        if obs.available and obs.focus_blocks:
            cols.append(("③ AW观测", [(b.start, b.end, "在机") for b in obs.focus_blocks]))
    except Exception:                                      # noqa: BLE001 — AW being down must not block the flush
        pass

    spans = [it for _, items in cols for it in items]
    if not spans:
        return None
    lo = _floor_slot(min(s for s, *_ in spans))
    hi = _ceil_slot(max(e for _, e, *_ in spans))

    header = "| 时间 | " + " | ".join(name for name, _ in cols) + " |"
    sep = "|" + "---|" * (len(cols) + 1)
    rows = [header, sep]
    cur = lo
    while cur < hi:
        nxt = cur + dt.timedelta(minutes=SLOT_MIN)
        cells = [_cell(cur, nxt, items) for _, items in cols]
        if any(cells):                                       # collapse fully-idle slots; timestamp every shown row
            rows.append("| " + " | ".join([f"{cur:%H:%M}", *cells]) + " |")
        cur = nxt
    return "\n".join(rows)


def _upsert_section(text: str, title: str, body: str) -> str:
    """Insert or replace a `## {title}` section in a markdown doc, leaving everything else intact."""
    head = f"## {title}"
    lines = text.splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.strip() == head), None)
    block = [head, "", body]
    if start is None:                                   # append a fresh section
        base = text.rstrip("\n")
        return (base + "\n\n" if base else "") + "\n".join(block) + "\n"
    end = len(lines)                                    # replace from heading to next top-level heading / EOF
    for j in range(start + 1, len(lines)):
        if lines[j].startswith("## ") or lines[j].startswith("# "):
            end = j
            break
    new = lines[:start] + block + [""] + lines[end:]
    return "\n".join(new).rstrip("\n") + "\n"


def _write_note(date: dt.date, body: str) -> None:
    path = notes.daily_path(date)
    if path.is_file():
        text = path.read_text(encoding="utf-8")
    else:                                               # no note yet → create it (dirs included) so the flush isn't lost
        path.parent.mkdir(parents=True, exist_ok=True)
        text = ""
    path.write_text(_upsert_section(text, SECTION_TITLE, body), encoding="utf-8")


def run(today: dt.date | None = None) -> list[dt.date]:
    """Flush every past day's local timeline into Obsidian, then purge it from the cache.

    Returns the dates flushed. No-op (returns []) when the vault isn't configured, or
    when nothing in the local cache predates today.
    """
    today = today or dt.date.today()
    if not config.vault_ok():
        return []
    flushed: list[dt.date] = []
    for date in _past_dates(today):
        body = _section_body(date)
        if body is None:
            continue
        _write_note(date, body)                         # write first…
        timeline.drop_date(date, timeline.PLAN)         # …purge only after a successful write
        timeline.drop_date(date, timeline.ACTUAL)
        flushed.append(date)
    return flushed

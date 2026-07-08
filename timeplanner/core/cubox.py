"""Sync Cubox folders (articles + highlights + full text) into a local corpus, and search it.

Read-only. The API key comes from https://<domain>/my/settings/extensions and is set
in .env as TIMEPLANNER_CUBOX_KEY. Endpoints (reverse-engineered from the official
Obsidian sync plugin, all under /c/api/third-party):
  - group/list               → your folders (id + name; nesting via parent_id)
  - card/filter  (POST)      → cards, filterable by group_filters=[<folder id>…], cursor-paged
  - card/content?id=<id>     → one card's full article text (plain text)

Note: group_filters expects folder **ids**, not names — a name is silently ignored
(returns unfiltered). So a folder name is resolved to its id via group/list first.

Only two consumers: `sync` (CLI `cubox --sync`, periodic extraction) and `search`
(the agent's cubox_search tool during plan/reflect).
"""

from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import asdict, dataclass, field

import requests

from ..config import config

TIMEOUT = 15
PAGE = 50


class CuboxNotConfigured(RuntimeError):
    pass


@dataclass
class Folder:
    id: str
    name: str
    nested_name: str = ""
    parent_id: str | None = None


@dataclass
class Highlight:
    text: str
    note: str = ""


@dataclass
class Card:
    id: str
    title: str
    url: str = ""
    cubox_url: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    highlights: list[Highlight] = field(default_factory=list)
    type: str = ""
    create_time: str = ""
    update_time: str = ""


def is_configured() -> bool:
    return bool(config.cubox_key)


def _request(path: str, method: str = "GET", body: dict | None = None) -> dict:
    if not is_configured():
        raise CuboxNotConfigured(
            "未配置 Cubox API key：在 .env 里设 TIMEPLANNER_CUBOX_KEY"
            f"（key 在 https://{config.cubox_domain}/my/settings/extensions）。")
    resp = requests.request(
        method, f"https://{config.cubox_domain}{path}",
        headers={"Authorization": f"Bearer {config.cubox_key}", "Content-Type": "application/json"},
        json=body, timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def list_folders() -> list[Folder]:
    data = _request("/c/api/third-party/group/list").get("data") or []
    return [Folder(id=g.get("id", ""), name=g.get("name", ""),
                   nested_name=g.get("nested_name", ""), parent_id=g.get("parent_id"))
            for g in data]


def _resolve_folder(name_or_id: str) -> str | None:
    """A folder name (or nested_name) → its id. If given an id already, return it. None if not found."""
    folders = list_folders()
    if any(f.id == name_or_id for f in folders):
        return name_or_id
    for f in folders:
        if name_or_id in (f.name, f.nested_name):
            return f.id
    return None


def _to_card(c: dict) -> Card:
    hls = []
    for h in c.get("highlights") or []:
        if isinstance(h, dict):
            hls.append(Highlight(text=(h.get("text") or "").strip(), note=(h.get("note") or "").strip()))
        elif isinstance(h, str):
            hls.append(Highlight(text=h.strip()))
    return Card(
        id=str(c.get("id", "")),
        title=c.get("title") or c.get("article_title") or "(无标题)",
        url=c.get("url", ""),
        cubox_url=c.get("cubox_url", ""),
        description=c.get("description", ""),
        tags=list(c.get("tags") or []),
        highlights=hls,
        type=c.get("type", ""),
        create_time=c.get("create_time", ""),
        update_time=c.get("update_time", ""),
    )


def _folder_cards(folder: str) -> list[Card]:
    """All cards in a folder (name or id), cursor-paged through every page."""
    group_id = _resolve_folder(folder)
    if group_id is None:
        raise ValueError(f"Cubox 里没有这个文件夹：{folder}（用 list_folders 看可用的）")

    out: list[Card] = []
    last_id, last_upd = None, None
    while True:
        body: dict = {"limit": PAGE, "group_filters": [group_id]}
        if last_id and last_upd:
            body["last_card_id"], body["last_card_update_time"] = last_id, last_upd
        page = _request("/c/api/third-party/card/filter", method="POST", body=body).get("data") or []
        out += [_to_card(c) for c in page]
        if len(page) < PAGE:
            break
        last_id, last_upd = page[-1].get("id"), page[-1].get("update_time")
        if not (last_id and last_upd):
            break  # no cursor to advance → stop rather than loop forever
    return out


def _card_content(card_id: str) -> str:
    """One card's full article text (a separate call per card; the API returns plain text)."""
    data = _request(f"/c/api/third-party/card/content?id={card_id}").get("data")
    if isinstance(data, str):
        return data
    if isinstance(data, dict):                       # defensive: some cards may wrap it
        return data.get("content") or data.get("text") or ""
    return ""


# ---- local corpus (periodic sync → agent search / reflect material) ----

def _store_path():
    return config.data_dir / "cubox.json"


def sync(folders: list[str] | None = None, full: bool = True) -> dict:
    """Pull the configured folders' cards into a local JSON corpus. Full refresh.

    full=True also fetches each card's article text (one extra API call per card).
    Returns stats {folders, cards, highlights, chars}. Store is data/cubox.json (gitignored).
    """
    folders = folders or config.cubox_folders
    by_id: dict[str, dict] = {}
    per_folder: dict[str, int] = {}
    for name in folders:
        cards = _folder_cards(name)
        per_folder[name] = len(cards)
        for c in cards:
            if c.id in by_id:            # dedupe by id; first folder wins
                continue
            row = asdict(c)              # Card + nested Highlight dataclasses → dict
            row["folder"] = name
            if full:
                try:
                    row["content"] = _card_content(c.id)
                except requests.RequestException:
                    row["content"] = ""  # one flaky card shouldn't abort the whole sync
            by_id[c.id] = row
    cards = sorted(by_id.values(), key=lambda r: r.get("update_time", ""), reverse=True)
    store = {
        "synced_at": dt.datetime.now().astimezone().isoformat(),
        "folders": folders,
        "full": full,
        "cards": cards,
    }
    config.data_dir.mkdir(parents=True, exist_ok=True)
    p = _store_path()
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)
    return {"folders": per_folder, "cards": len(cards),
            "highlights": sum(len(r.get("highlights") or []) for r in cards),
            "chars": sum(len(r.get("content") or "") for r in cards)}


def _load_store() -> dict:
    p = _store_path()
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def local_cards() -> list[dict]:
    return _load_store().get("cards", [])


def _content_snippets(content: str, terms: list[str], k: int = 2, width: int = 180) -> list[str]:
    """Sentence-ish fragments of the article body that contain a query term."""
    out = []
    for part in re.split(r"[\n。！？!?]", content):
        pl = part.lower()
        if any(t in pl for t in terms):
            s = part.strip()
            if s:
                out.append(s[:width])
            if len(out) >= k:
                break
    return out


def search(query: str, limit: int = 8) -> list[dict]:
    """Keyword search over the local corpus (title + description + highlights + full body).

    Returns hits [{title, url, folder, score, snippets}]; snippets favor your highlights/notes,
    then fall back to matching body fragments.
    """
    terms = [t for t in query.lower().split() if t]
    if not terms:
        return []
    hits = []
    for c in local_cards():
        hay_title = (c.get("title") or "").lower()
        hay_desc = (c.get("description") or "").lower()
        content = c.get("content") or ""
        content_low = content.lower()
        highlights = c.get("highlights") or []
        score, snippets = 0, []
        for t in terms:
            score += 3 * hay_title.count(t) + hay_desc.count(t) + content_low.count(t)
        for h in highlights:                       # your highlights/notes rank highest
            text, note = (h.get("text") or ""), (h.get("note") or "")
            n = sum((text + " " + note).lower().count(t) for t in terms)
            if n:
                score += 2 * n
                snippets.append("✏️ " + text.strip()[:200] + (f"　📝 {note.strip()}" if note.strip() else ""))
        if content:                                # then body fragments
            snippets += _content_snippets(content, terms)
        if score:
            hits.append({"title": c.get("title", ""), "url": c.get("url", ""),
                         "folder": c.get("folder", ""), "score": score, "snippets": snippets[:4]})
    hits.sort(key=lambda h: -h["score"])
    return hits[:limit]


def search_summary(query: str, limit: int = 8) -> str:
    """Human/agent-readable search result over the local Cubox corpus."""
    store = _load_store()
    if not store:
        return "（本地还没有 Cubox 语料 —— 先跑 `timeplanner cubox --sync`）"
    hits = search(query, limit)
    if not hits:
        return f"（本地 Cubox 语料里没搜到「{query}」相关内容）"
    lines = [f"# 🔎 Cubox 命中「{query}」（{len(hits)} 篇，语料同步于 {store.get('synced_at','?')[:16]}）"]
    for h in hits:
        lines.append(f"\n## {h['title']}  ⟨{h['folder']}⟩\n{h['url']}")
        for s in h["snippets"]:
            lines.append(f"> {s}")
    return "\n".join(lines)

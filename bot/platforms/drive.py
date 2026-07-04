"""Google Drive crawler for public (link-shared) folders.

Two backends:
  1. Official Drive API v3 (used when GOOGLE_API_KEY is set) — robust, paginated.
  2. Page-scrape fallback (no key needed) — parses the listing Google embeds in
     the folder page source (window['_DRIVE_ivd']). Caveat: the embed only
     contains the first ~50 items of a folder, so very large flat folders may
     be partially indexed. Set GOOGLE_API_KEY for complete coverage.
"""
from __future__ import annotations

import asyncio
import html as htmllib
import json
import logging
import re
from dataclasses import dataclass

import aiohttp

log = logging.getLogger(__name__)

FOLDER_MIME = "application/vnd.google-apps.folder"
SHORTCUT_MIME = "application/vnd.google-apps.shortcut"
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

# Heuristic category classification from the file's name + folder path.
_RE_PYQ = re.compile(r"pyq|mid\s*sem|end\s*sem|quiz|exam|paper|sessional|test|answer\s*key", re.I)
_RE_BOOK = re.compile(r"textbook|book|edition|z-?lib", re.I)
_RE_NOTES = re.compile(r"notes|slides?|module|lecture|chap|\.ppt", re.I)


@dataclass(frozen=True)
class DriveFile:
    file_id: str
    name: str
    path: str        # human-readable folder path from the source root
    mime: str

    @property
    def url(self) -> str:
        return f"https://drive.google.com/file/d/{self.file_id}/view"

    @property
    def category(self) -> str:
        hay = f"{self.path}/{self.name}"
        if _RE_PYQ.search(hay):
            return "pyq"
        if _RE_BOOK.search(hay):
            return "textbook"
        if _RE_NOTES.search(hay):
            return "notes"
        return "other"


def extract_folder_id(url_or_id: str) -> str | None:
    """Accepts a bare id or any drive.google.com folder URL."""
    m = re.search(r"folders/([-\w]{20,})", url_or_id)
    if m:
        return m.group(1)
    if re.fullmatch(r"[-\w]{20,}", url_or_id.strip()):
        return url_or_id.strip()
    return None


# ── backend 1: official API (key only, works for link-shared folders) ──

async def _list_via_api(
    session: aiohttp.ClientSession, folder_id: str, api_key: str
) -> list[dict]:
    items: list[dict] = []
    page_token = None
    while True:
        params = {
            "q": f"'{folder_id}' in parents and trashed = false",
            "fields": "nextPageToken, files(id, name, mimeType)",
            "pageSize": "1000",
            "key": api_key,
        }
        if page_token:
            params["pageToken"] = page_token
        async with session.get(
            "https://www.googleapis.com/drive/v3/files",
            params=params,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as r:
            r.raise_for_status()
            data = await r.json()
        for f in data.get("files", []):
            items.append({"id": f["id"], "name": f["name"], "mime": f["mimeType"]})
        page_token = data.get("nextPageToken")
        if not page_token:
            return items


# ── backend 2: page scrape (no key) ────────────────────────

async def _fetch_folder_page(session: aiohttp.ClientSession, folder_id: str) -> tuple[str, list[dict]]:
    url = f"https://drive.google.com/drive/folders/{folder_id}"
    async with session.get(url, headers=_UA, timeout=aiohttp.ClientTimeout(total=30)) as r:
        r.raise_for_status()
        page = await r.text(errors="replace")

    title = "Drive folder"
    tm = re.search(r"<title>(.*?)</title>", page)
    if tm:
        title = htmllib.unescape(tm.group(1))
        title = re.sub(r"\s*[-‐-―]\s*Google Drive\s*$", "", title).strip()

    m = re.search(r"window\['_DRIVE_ivd'\] = '(.+?)';", page, re.DOTALL)
    if not m:
        return title, []
    raw = m.group(1).replace("\\/", "/")
    decoded = raw.encode("utf-8").decode("unicode_escape")
    try:
        arr = json.loads(decoded)
    except json.JSONDecodeError:
        log.warning("Drive scrape: could not parse listing for %s", folder_id)
        return title, []

    items: list[dict] = []
    for it in arr[0] or []:
        try:
            items.append({"id": it[0], "name": it[2], "mime": it[3]})
        except (IndexError, TypeError):
            continue
    return title, items


async def get_folder_title(session: aiohttp.ClientSession, folder_id: str) -> str | None:
    """Cheap reachability check; returns the folder's name or None."""
    try:
        title, _ = await _fetch_folder_page(session, folder_id)
        return title
    except Exception:  # noqa: BLE001
        return None


# ── crawler ────────────────────────────────────────────────

async def crawl(
    session: aiohttp.ClientSession,
    root_id: str,
    api_key: str | None = None,
    max_folders: int = 500,
    delay: float = 0.25,
    root_title: str | None = None,
) -> tuple[str, list[DriveFile]]:
    """Breadth-first walk of a public folder tree. Returns (root_title, files).

    `root_title` overrides the folder's own name as the first path segment,
    so a renamed source keeps its display name across re-crawls.
    """
    scraped_title, root_items = await _fetch_folder_page(session, root_id)
    root_title = root_title or scraped_title
    if api_key:
        try:
            root_items = await _list_via_api(session, root_id, api_key)
        except Exception as e:  # noqa: BLE001 — key may lack Drive access; scrape still works
            log.warning("Drive API listing failed (%s); using page-scrape backend.", e)
            api_key = None

    files: list[DriveFile] = []
    queue: list[tuple[str, str, list[dict] | None]] = [(root_id, root_title, root_items)]
    visited: set[str] = set()

    while queue and len(visited) < max_folders:
        folder_id, path, preloaded = queue.pop(0)
        if folder_id in visited:
            continue
        visited.add(folder_id)

        if preloaded is None:
            try:
                if api_key:
                    items = await _list_via_api(session, folder_id, api_key)
                else:
                    _, items = await _fetch_folder_page(session, folder_id)
            except Exception as e:  # noqa: BLE001
                log.warning("Drive crawl: failed folder %s (%s)", path, e)
                continue
            await asyncio.sleep(delay)
        else:
            items = preloaded

        for it in items:
            if it["mime"] == FOLDER_MIME:
                queue.append((it["id"], f"{path}/{it['name']}", None))
            elif it["mime"] != SHORTCUT_MIME:
                files.append(DriveFile(file_id=it["id"], name=it["name"], path=path, mime=it["mime"]))

    if queue:
        log.warning("Drive crawl of %s hit the %d-folder cap; index may be partial.", root_title, max_folders)
    return root_title, files

"""NITC official website scraper — notice boards.

The notice pages are server-rendered static HTML. Each notice is:
    <div class="xc-c-name"><a target="_blank" href="...pdf">
        <p class="c-name">TITLE ...</p>
so a regex parse is stable enough. If the site redesigns, fetch_notices
raises nothing and simply returns [] (callers treat that as "no change").
"""
from __future__ import annotations

import hashlib
import html as htmllib
import re
from dataclasses import dataclass

import aiohttp

_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

# board key -> (display label, page url)
BOARDS: dict[str, tuple[str, str]] = {
    "academic": ("Academic", "https://nitc.ac.in/noticeboard/academic"),
    "general": ("General", "https://nitc.ac.in/noticeboard/general-notices"),
}

_ITEM_RE = re.compile(
    r'class="xc-c-name"><a target="_blank" href="([^"]+)"\s*>\s*<p class="c-name">(.*?)</p>',
    re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class Notice:
    board: str      # key into BOARDS
    title: str
    url: str

    @property
    def key(self) -> str:
        raw = f"{self.board}|{self.url}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]

    @property
    def board_label(self) -> str:
        return BOARDS.get(self.board, (self.board, ""))[0]


def _clean_title(raw: str) -> str:
    return htmllib.unescape(_WS_RE.sub(" ", _TAG_RE.sub("", raw))).strip()


async def fetch_notices(session: aiohttp.ClientSession, board: str) -> list[Notice]:
    """Notices for one board, newest first (site order). [] on parse mismatch."""
    _, url = BOARDS[board]
    async with session.get(url, headers=_UA, timeout=aiohttp.ClientTimeout(total=30)) as r:
        r.raise_for_status()
        page = await r.text(errors="replace")

    out: list[Notice] = []
    seen: set[str] = set()
    for href, raw_title in _ITEM_RE.findall(page):
        title = _clean_title(raw_title)
        if not title or href in seen:
            continue
        seen.add(href)
        out.append(Notice(board=board, title=title[:300], url=href))
    return out

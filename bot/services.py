"""Contest-fetching service: pulls from clist.by (preferred) with a
Codeforces-only fallback, de-duplicates, and sorts by start time.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import aiohttp

from .config import config
from .platforms import codeforces, clist
from .platforms.base import Contest

log = logging.getLogger(__name__)


async def fetch_all_contests(session: aiohttp.ClientSession) -> list[Contest]:
    contests: list[Contest] = []
    sources_ok = False

    if config.has_clist:
        try:
            contests = await clist.fetch_contests(session, config.clist_username, config.clist_api_key)
            sources_ok = True
        except Exception as e:  # noqa: BLE001 — degrade gracefully to fallback
            log.warning("clist.by fetch failed (%s); falling back to Codeforces only.", e)

    if not sources_ok:
        try:
            contests = await codeforces.fetch_contests(session)
        except Exception as e:  # noqa: BLE001
            log.error("Codeforces fallback also failed: %s", e)
            return []

    # Keep only genuinely upcoming, de-dup by key, sort.
    now = datetime.now(timezone.utc)
    seen: set[str] = set()
    upcoming: list[Contest] = []
    for c in contests:
        if c.start <= now or c.key in seen:
            continue
        seen.add(c.key)
        upcoming.append(c)
    upcoming.sort(key=lambda c: c.start)
    return upcoming

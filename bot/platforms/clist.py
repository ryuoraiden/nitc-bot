"""clist.by adapter — aggregated contest schedules across many judges.

API docs: https://clist.by/api/v4/doc/
Auth is via query params: username + api_key.
"""
from __future__ import annotations

from datetime import datetime, timezone

import aiohttp

from .base import Contest

API = "https://clist.by/api/v4/contest/"

# Resource host -> friendly platform label. Extend as you add platforms.
RESOURCES = {
    "codeforces.com": "Codeforces",
    "leetcode.com": "LeetCode",
    "codechef.com": "CodeChef",
    "atcoder.jp": "AtCoder",
}


def _parse_dt(s: str) -> datetime:
    # clist returns ISO like "2026-07-05T14:35:00" (UTC, no tz suffix).
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


async def fetch_contests(
    session: aiohttp.ClientSession, username: str, api_key: str, resources: list[str] | None = None
) -> list[Contest]:
    # clist v4 does an exact match on `resource`, so a comma-joined list matches
    # nothing. Instead we fetch all upcoming contests and filter client-side to
    # the platforms we care about.
    allow = set(resources) if resources else set(RESOURCES.keys())
    params = {
        "username": username,
        "api_key": api_key,
        "upcoming": "true",
        "order_by": "start",
        "limit": "300",
    }
    async with session.get(API, params=params, timeout=aiohttp.ClientTimeout(total=25)) as r:
        if r.status == 401:
            raise RuntimeError("clist.by auth failed — check CLIST_USERNAME / CLIST_API_KEY.")
        r.raise_for_status()
        data = await r.json()

    out: list[Contest] = []
    for c in data.get("objects", []):
        host = c.get("host", "")
        if host not in allow:
            continue
        out.append(
            Contest(
                platform=host,
                name=c["event"],
                url=c["href"],
                start=_parse_dt(c["start"]),
                duration_seconds=int(c.get("duration", 0)),
            )
        )
    return out

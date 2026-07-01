"""Codeforces adapter — uses the official public API (no key required).

Docs: https://codeforces.com/apiHelp
"""
from __future__ import annotations

from datetime import datetime, timezone

import aiohttp

from .base import Contest, PlatformUser

API = "https://codeforces.com/api"
PLATFORM = "codeforces.com"


async def _get(session: aiohttp.ClientSession, method: str, **params) -> object:
    async with session.get(f"{API}/{method}", params=params, timeout=aiohttp.ClientTimeout(total=20)) as r:
        data = await r.json()
    if data.get("status") != "OK":
        raise RuntimeError(f"Codeforces API error: {data.get('comment', 'unknown')}")
    return data["result"]


async def fetch_contests(session: aiohttp.ClientSession) -> list[Contest]:
    """Upcoming (phase == BEFORE) regular contests."""
    result = await _get(session, "contest.list", gym="false")
    out: list[Contest] = []
    for c in result:
        if c.get("phase") != "BEFORE" or "startTimeSeconds" not in c:
            continue
        start = datetime.fromtimestamp(c["startTimeSeconds"], tz=timezone.utc)
        out.append(
            Contest(
                platform=PLATFORM,
                name=c["name"],
                url=f"https://codeforces.com/contests/{c['id']}",
                start=start,
                duration_seconds=int(c.get("durationSeconds", 0)),
            )
        )
    return out


def _to_user(u: dict) -> PlatformUser:
    handle = u["handle"]
    name = " ".join(filter(None, [u.get("firstName"), u.get("lastName")])) or handle
    return PlatformUser(
        platform=PLATFORM,
        handle=handle,
        display=name,
        rating=u.get("rating"),
        rank=u.get("rank"),
        extra=f"Max rating: {u['maxRating']}" if u.get("maxRating") else None,
        profile_url=f"https://codeforces.com/profile/{handle}",
        score=u.get("rating"),
        score_label="Rating",
    )


async def get_user(session: aiohttp.ClientSession, handle: str) -> PlatformUser:
    result = await _get(session, "user.info", handles=handle, checkHistoricHandles="false")
    return _to_user(result[0])


async def get_users(session: aiohttp.ClientSession, handles: list[str]) -> list[PlatformUser]:
    """Batch lookup — Codeforces accepts up to ~10k handles separated by ';'."""
    if not handles:
        return []
    result = await _get(
        session, "user.info", handles=";".join(handles), checkHistoricHandles="false"
    )
    return [_to_user(u) for u in result]


async def verify_token(session: aiohttp.ClientSession, handle: str, token: str) -> bool:
    """User proves ownership by setting `token` as their First name in CF settings."""
    try:
        result = await _get(session, "user.info", handles=handle, checkHistoricHandles="false")
    except RuntimeError:
        return False
    return (result[0].get("firstName") or "").strip() == token

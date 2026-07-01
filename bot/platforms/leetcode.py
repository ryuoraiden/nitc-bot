"""LeetCode adapter — unofficial public GraphQL endpoint.

There is no official API. This uses the same GraphQL endpoint the website uses,
so it can break if LeetCode changes their schema. Handle failures gracefully.
"""
from __future__ import annotations

import aiohttp

from .base import PlatformUser

GRAPHQL = "https://leetcode.com/graphql"
PLATFORM = "leetcode.com"

_PROFILE_QUERY = """
query userPublicProfile($username: String!) {
  matchedUser(username: $username) {
    username
    profile { realName aboutMe ranking }
    submitStatsGlobal { acSubmissionNum { difficulty count } }
  }
}
"""

_HEADERS = {
    "Content-Type": "application/json",
    "Referer": "https://leetcode.com",
    "User-Agent": "Mozilla/5.0 (compatible; cp-contest-bot/1.0)",
}


async def _fetch_profile(session: aiohttp.ClientSession, handle: str) -> dict | None:
    payload = {"query": _PROFILE_QUERY, "variables": {"username": handle}}
    async with session.post(
        GRAPHQL, json=payload, headers=_HEADERS, timeout=aiohttp.ClientTimeout(total=20)
    ) as r:
        if r.status != 200:
            raise RuntimeError(f"LeetCode returned HTTP {r.status}")
        data = await r.json()
    return (data.get("data") or {}).get("matchedUser")


async def get_user(session: aiohttp.ClientSession, handle: str) -> PlatformUser:
    user = await _fetch_profile(session, handle)
    if not user:
        raise RuntimeError(f"LeetCode user '{handle}' not found.")
    profile = user.get("profile") or {}
    solved = 0
    for row in (user.get("submitStatsGlobal") or {}).get("acSubmissionNum", []):
        if row.get("difficulty") == "All":
            solved = row.get("count", 0)
    return PlatformUser(
        platform=PLATFORM,
        handle=user["username"],
        display=profile.get("realName") or user["username"],
        rating=None,
        rank=f"#{profile['ranking']}" if profile.get("ranking") else None,
        extra=f"Solved: {solved}",
        profile_url=f"https://leetcode.com/u/{user['username']}/",
        score=solved,
        score_label="Solved",
    )


async def verify_token(session: aiohttp.ClientSession, handle: str, token: str) -> bool:
    """User proves ownership by putting `token` in their profile 'Summary' (aboutMe)."""
    try:
        user = await _fetch_profile(session, handle)
    except RuntimeError:
        return False
    if not user:
        return False
    about = ((user.get("profile") or {}).get("aboutMe") or "")
    return token in about

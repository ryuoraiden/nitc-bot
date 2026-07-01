"""Registry that maps a platform choice to its lookup/verify functions.

Each entry provides:
  get_user(session, handle)      -> PlatformUser
  verify(session, handle, token) -> bool
  instructions(token)            -> str   (how the user proves ownership)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

import aiohttp

from . import codeforces, leetcode
from .base import PlatformUser


@dataclass(frozen=True)
class PlatformAdapter:
    key: str
    label: str
    get_user: Callable[[aiohttp.ClientSession, str], Awaitable[PlatformUser]]
    verify: Callable[[aiohttp.ClientSession, str, str], Awaitable[bool]]
    instructions: Callable[[str], str]


REGISTRY: dict[str, PlatformAdapter] = {
    "codeforces": PlatformAdapter(
        key="codeforces",
        label="Codeforces",
        get_user=codeforces.get_user,
        verify=codeforces.verify_token,
        instructions=lambda token: (
            "Open **Codeforces → Settings → Social** and set your **First name** to:\n"
            f"```{token}```\n"
            "Save, then run `/verify codeforces`. You can change your name back afterwards."
        ),
    ),
    "leetcode": PlatformAdapter(
        key="leetcode",
        label="LeetCode",
        get_user=leetcode.get_user,
        verify=leetcode.verify_token,
        instructions=lambda token: (
            "Open **LeetCode → Edit Profile → Summary** and paste this token anywhere in it:\n"
            f"```{token}```\n"
            "Save, then run `/verify leetcode`. You can remove it afterwards."
        ),
    ),
}

# Platforms usable in slash-command choices.
PLATFORM_CHOICES = [(a.label, a.key) for a in REGISTRY.values()]

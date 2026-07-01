"""Shared data models for platform adapters."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class Contest:
    platform: str          # e.g. "codeforces.com"
    name: str
    url: str
    start: datetime        # timezone-aware, UTC
    duration_seconds: int

    @property
    def end(self) -> datetime:
        from datetime import timedelta

        return self.start + timedelta(seconds=self.duration_seconds)

    @property
    def key(self) -> str:
        """Stable identifier used for reminder de-duplication."""
        raw = f"{self.platform}|{self.name}|{int(self.start.timestamp())}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]

    @property
    def is_upcoming(self) -> bool:
        return self.start > datetime.now(timezone.utc)

    def duration_human(self) -> str:
        mins = self.duration_seconds // 60
        h, m = divmod(mins, 60)
        if h and m:
            return f"{h}h {m}m"
        if h:
            return f"{h}h"
        return f"{m}m"


@dataclass(frozen=True)
class PlatformUser:
    platform: str
    handle: str
    display: str
    rating: int | None = None
    rank: str | None = None
    extra: str | None = None   # freeform line, e.g. "Solved: 512"
    profile_url: str | None = None
    # Natural ranking metric for leaderboards (e.g. rating on CF, #solved on LeetCode).
    score: int | None = None
    score_label: str | None = None

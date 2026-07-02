"""Central configuration, loaded from environment / .env file."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _lead_minutes() -> list[int]:
    raw = os.getenv("REMINDER_LEAD_MINUTES", "1440,60")
    out: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            out.append(int(part))
    return sorted(set(out), reverse=True) or [1440, 60]


@dataclass(frozen=True)
class Config:
    discord_token: str = field(default_factory=lambda: os.getenv("DISCORD_TOKEN", ""))
    owner_id: int | None = field(default_factory=lambda: int(os.getenv("OWNER_ID")) if os.getenv("OWNER_ID") else None)
    # Optional: a single server (guild) ID for instant slash-command sync during dev.
    # Global sync (when unset) can take up to ~1 hour to appear in Discord.
    guild_id: int | None = field(default_factory=lambda: int(os.getenv("GUILD_ID")) if os.getenv("GUILD_ID") else None)
    clist_username: str = field(default_factory=lambda: os.getenv("CLIST_USERNAME", ""))
    clist_api_key: str = field(default_factory=lambda: os.getenv("CLIST_API_KEY", ""))
    # Optional: Google API key for complete Drive indexing (scrape fallback caps
    # each folder listing at ~50 items).
    google_api_key: str = field(default_factory=lambda: os.getenv("GOOGLE_API_KEY", ""))
    reminder_lead_minutes: list[int] = field(default_factory=_lead_minutes)
    refresh_interval_minutes: int = field(default_factory=lambda: _int("REFRESH_INTERVAL_MINUTES", 30))
    db_path: str = field(default_factory=lambda: os.getenv("DB_PATH", "data/bot.db"))

    @property
    def has_clist(self) -> bool:
        return bool(self.clist_username and self.clist_api_key)

    def validate(self) -> list[str]:
        """Return a list of human-readable problems with the config."""
        problems: list[str] = []
        if not self.discord_token:
            problems.append("DISCORD_TOKEN is missing.")
        if not self.has_clist:
            problems.append(
                "CLIST_USERNAME / CLIST_API_KEY missing — contest fetching will fall "
                "back to the Codeforces-only source."
            )
        return problems


config = Config()

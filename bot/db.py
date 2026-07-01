"""SQLite persistence layer (async, via aiosqlite).

Tables
------
guilds          one row per Discord server: which channel gets reminders.
links           discord_id <-> platform handle mappings (verified or pending).
reminders_sent  de-dup ledger so a (contest, lead-time) reminder fires once.
"""
from __future__ import annotations

import os
from pathlib import Path

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS guilds (
    guild_id           INTEGER PRIMARY KEY,
    reminder_channel   INTEGER,
    mention_role       INTEGER
);

CREATE TABLE IF NOT EXISTS links (
    discord_id   INTEGER NOT NULL,
    platform     TEXT    NOT NULL,
    handle       TEXT    NOT NULL,
    verified     INTEGER NOT NULL DEFAULT 0,
    token        TEXT,
    created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (discord_id, platform)
);

CREATE TABLE IF NOT EXISTS reminders_sent (
    contest_key  TEXT    NOT NULL,
    lead_minutes INTEGER NOT NULL,
    sent_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (contest_key, lead_minutes)
);
"""


class Database:
    def __init__(self, path: str):
        self.path = path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        Path(os.path.dirname(self.path) or ".").mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        return self._conn

    # ── guilds ────────────────────────────────────────────
    async def set_reminder_channel(self, guild_id: int, channel_id: int) -> None:
        await self.conn.execute(
            "INSERT INTO guilds (guild_id, reminder_channel) VALUES (?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET reminder_channel = excluded.reminder_channel",
            (guild_id, channel_id),
        )
        await self.conn.commit()

    async def set_mention_role(self, guild_id: int, role_id: int | None) -> None:
        await self.conn.execute(
            "INSERT INTO guilds (guild_id, mention_role) VALUES (?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET mention_role = excluded.mention_role",
            (guild_id, role_id),
        )
        await self.conn.commit()

    async def all_reminder_targets(self) -> list[aiosqlite.Row]:
        """Guilds that have a reminder channel configured."""
        cur = await self.conn.execute(
            "SELECT guild_id, reminder_channel, mention_role FROM guilds "
            "WHERE reminder_channel IS NOT NULL"
        )
        return await cur.fetchall()

    # ── links ─────────────────────────────────────────────
    async def upsert_link(
        self, discord_id: int, platform: str, handle: str, verified: bool, token: str | None
    ) -> None:
        await self.conn.execute(
            "INSERT INTO links (discord_id, platform, handle, verified, token) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(discord_id, platform) DO UPDATE SET "
            "  handle = excluded.handle, verified = excluded.verified, token = excluded.token",
            (discord_id, platform, handle, int(verified), token),
        )
        await self.conn.commit()

    async def get_link(self, discord_id: int, platform: str) -> aiosqlite.Row | None:
        cur = await self.conn.execute(
            "SELECT * FROM links WHERE discord_id = ? AND platform = ?",
            (discord_id, platform),
        )
        return await cur.fetchone()

    async def get_links(self, discord_id: int) -> list[aiosqlite.Row]:
        cur = await self.conn.execute(
            "SELECT * FROM links WHERE discord_id = ? ORDER BY platform", (discord_id,)
        )
        return await cur.fetchall()

    async def get_verified_by_platform(self, platform: str) -> list[aiosqlite.Row]:
        cur = await self.conn.execute(
            "SELECT discord_id, handle FROM links WHERE platform = ? AND verified = 1",
            (platform,),
        )
        return await cur.fetchall()

    async def delete_link(self, discord_id: int, platform: str) -> int:
        cur = await self.conn.execute(
            "DELETE FROM links WHERE discord_id = ? AND platform = ?", (discord_id, platform)
        )
        await self.conn.commit()
        return cur.rowcount

    # ── reminders ─────────────────────────────────────────
    async def was_sent(self, contest_key: str, lead_minutes: int) -> bool:
        cur = await self.conn.execute(
            "SELECT 1 FROM reminders_sent WHERE contest_key = ? AND lead_minutes = ?",
            (contest_key, lead_minutes),
        )
        return await cur.fetchone() is not None

    async def mark_sent(self, contest_key: str, lead_minutes: int) -> None:
        await self.conn.execute(
            "INSERT OR IGNORE INTO reminders_sent (contest_key, lead_minutes) VALUES (?, ?)",
            (contest_key, lead_minutes),
        )
        await self.conn.commit()

    async def prune_reminders(self, older_than_days: int = 7) -> None:
        await self.conn.execute(
            "DELETE FROM reminders_sent WHERE sent_at < datetime('now', ?)",
            (f"-{older_than_days} days",),
        )
        await self.conn.commit()

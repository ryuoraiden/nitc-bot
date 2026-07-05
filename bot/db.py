"""SQLite persistence layer (async, via aiosqlite).

Tables
------
guilds          one row per Discord server: which channel gets reminders.
links           discord_id <-> platform handle mappings (verified or pending).
reminders_sent  de-dup ledger so a (contest, lead-time) reminder fires once.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS guilds (
    guild_id           INTEGER PRIMARY KEY,
    reminder_channel   INTEGER,
    mention_role       INTEGER,
    notices_channel    INTEGER,
    welcome_channel    INTEGER,
    goodbye_channel    INTEGER
);

CREATE TABLE IF NOT EXISTS notices_seen (
    notice_key  TEXT PRIMARY KEY,
    board       TEXT NOT NULL,
    title       TEXT NOT NULL,
    url         TEXT NOT NULL,
    first_seen  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS reaction_roles (
    message_id  INTEGER NOT NULL,
    emoji       TEXT    NOT NULL,
    guild_id    INTEGER NOT NULL,
    role_id     INTEGER NOT NULL,
    PRIMARY KEY (message_id, emoji)
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

CREATE TABLE IF NOT EXISTS drive_sources (
    folder_id   TEXT PRIMARY KEY,
    label       TEXT NOT NULL,
    added_by    INTEGER,
    added_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS drive_files (
    file_id     TEXT PRIMARY KEY,
    source_id   TEXT NOT NULL,
    name        TEXT NOT NULL,
    path        TEXT NOT NULL,
    mime        TEXT,
    category    TEXT NOT NULL DEFAULT 'other'
);

CREATE VIRTUAL TABLE IF NOT EXISTS drive_fts USING fts5(
    file_id UNINDEXED, name, path, category UNINDEXED
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
        # Lightweight migrations for columns added after a table already exists
        # (CREATE TABLE IF NOT EXISTS won't touch existing tables).
        for ddl in (
            "ALTER TABLE guilds ADD COLUMN notices_channel INTEGER",
            "ALTER TABLE guilds ADD COLUMN welcome_channel INTEGER",
            "ALTER TABLE guilds ADD COLUMN goodbye_channel INTEGER",
        ):
            try:
                await self._conn.execute(ddl)
            except aiosqlite.OperationalError:
                pass  # column already exists
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

    async def set_notices_channel(self, guild_id: int, channel_id: int) -> None:
        await self.conn.execute(
            "INSERT INTO guilds (guild_id, notices_channel) VALUES (?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET notices_channel = excluded.notices_channel",
            (guild_id, channel_id),
        )
        await self.conn.commit()

    async def all_notice_targets(self) -> list[aiosqlite.Row]:
        cur = await self.conn.execute(
            "SELECT guild_id, notices_channel FROM guilds WHERE notices_channel IS NOT NULL"
        )
        return await cur.fetchall()

    async def set_guild_channel(self, guild_id: int, column: str, channel_id: int | None) -> None:
        """Set one of the guilds table's channel columns (column name is code-controlled)."""
        assert column in {"welcome_channel", "goodbye_channel"}
        await self.conn.execute(
            f"INSERT INTO guilds (guild_id, {column}) VALUES (?, ?) "
            f"ON CONFLICT(guild_id) DO UPDATE SET {column} = excluded.{column}",
            (guild_id, channel_id),
        )
        await self.conn.commit()

    async def get_guild(self, guild_id: int) -> aiosqlite.Row | None:
        cur = await self.conn.execute("SELECT * FROM guilds WHERE guild_id = ?", (guild_id,))
        return await cur.fetchone()

    # ── website notices ───────────────────────────────────
    async def notice_seen(self, notice_key: str) -> bool:
        cur = await self.conn.execute(
            "SELECT 1 FROM notices_seen WHERE notice_key = ?", (notice_key,)
        )
        return await cur.fetchone() is not None

    async def mark_notice_seen(self, notice_key: str, board: str, title: str, url: str) -> None:
        await self.conn.execute(
            "INSERT OR IGNORE INTO notices_seen (notice_key, board, title, url) VALUES (?, ?, ?, ?)",
            (notice_key, board, title, url),
        )
        await self.conn.commit()

    async def notices_seen_count(self, board: str) -> int:
        cur = await self.conn.execute(
            "SELECT COUNT(*) AS n FROM notices_seen WHERE board = ?", (board,)
        )
        row = await cur.fetchone()
        return row["n"]

    # ── reaction roles ────────────────────────────────────
    async def add_reaction_role(
        self, message_id: int, emoji: str, guild_id: int, role_id: int
    ) -> None:
        await self.conn.execute(
            "INSERT OR REPLACE INTO reaction_roles (message_id, emoji, guild_id, role_id) "
            "VALUES (?, ?, ?, ?)",
            (message_id, emoji, guild_id, role_id),
        )
        await self.conn.commit()

    async def get_reaction_role(self, message_id: int, emoji: str) -> int | None:
        cur = await self.conn.execute(
            "SELECT role_id FROM reaction_roles WHERE message_id = ? AND emoji = ?",
            (message_id, emoji),
        )
        row = await cur.fetchone()
        return row["role_id"] if row else None

    async def message_has_reaction_roles(self, message_id: int) -> bool:
        cur = await self.conn.execute(
            "SELECT 1 FROM reaction_roles WHERE message_id = ? LIMIT 1", (message_id,)
        )
        return await cur.fetchone() is not None

    async def clear_reaction_roles(self, message_id: int) -> int:
        cur = await self.conn.execute(
            "DELETE FROM reaction_roles WHERE message_id = ?", (message_id,)
        )
        await self.conn.commit()
        return cur.rowcount

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

    # ── study materials (Drive index) ─────────────────────
    async def add_drive_source(self, folder_id: str, label: str, added_by: int | None) -> None:
        await self.conn.execute(
            "INSERT INTO drive_sources (folder_id, label, added_by) VALUES (?, ?, ?) "
            "ON CONFLICT(folder_id) DO UPDATE SET label = excluded.label",
            (folder_id, label, added_by),
        )
        await self.conn.commit()

    async def list_drive_sources(self) -> list[aiosqlite.Row]:
        cur = await self.conn.execute(
            "SELECT s.*, (SELECT COUNT(*) FROM drive_files f WHERE f.source_id = s.folder_id) AS n_files "
            "FROM drive_sources s ORDER BY s.added_at"
        )
        return await cur.fetchall()

    async def _rebuild_fts(self) -> None:
        """Rebuild the FTS mirror (cheap at this scale, keeps it trivially in sync)."""
        await self.conn.execute("DELETE FROM drive_fts")
        await self.conn.execute(
            "INSERT INTO drive_fts (file_id, name, path, category) "
            "SELECT file_id, name, path, category FROM drive_files"
        )

    async def replace_drive_files(self, source_id: str, files: list[tuple]) -> None:
        """files: (file_id, name, path, mime, category) tuples for one source."""
        await self.conn.execute("DELETE FROM drive_files WHERE source_id = ?", (source_id,))
        await self.conn.executemany(
            "INSERT OR REPLACE INTO drive_files (file_id, source_id, name, path, mime, category) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [(f[0], source_id, f[1], f[2], f[3], f[4]) for f in files],
        )
        await self._rebuild_fts()
        await self.conn.commit()

    async def rename_drive_source(self, folder_id: str, new_label: str) -> None:
        """Rename a source and rewrite its files' path prefix to match."""
        await self.conn.execute(
            "UPDATE drive_sources SET label = ? WHERE folder_id = ?", (new_label, folder_id)
        )
        cur = await self.conn.execute(
            "SELECT path FROM drive_files WHERE source_id = ? LIMIT 1", (folder_id,)
        )
        row = await cur.fetchone()
        if row:
            old_root = row["path"].split("/")[0]
            await self.conn.execute(
                "UPDATE drive_files SET path = ? || substr(path, ?) "
                "WHERE source_id = ? AND (path = ? OR path LIKE ?)",
                (new_label, len(old_root) + 1, folder_id, old_root, f"{old_root}/%"),
            )
            await self._rebuild_fts()
        await self.conn.commit()

    async def remove_drive_source(self, folder_id: str) -> int:
        await self.conn.execute("DELETE FROM drive_files WHERE source_id = ?", (folder_id,))
        cur = await self.conn.execute(
            "DELETE FROM drive_sources WHERE folder_id = ?", (folder_id,)
        )
        await self._rebuild_fts()
        await self.conn.commit()
        return cur.rowcount

    async def drive_file_count(self) -> int:
        cur = await self.conn.execute("SELECT COUNT(*) AS n FROM drive_files")
        row = await cur.fetchone()
        return row["n"]

    async def search_drive(
        self, query: str, category: str | None = None, limit: int = 8
    ) -> list[aiosqlite.Row]:
        """FTS search over file name + folder path. AND first, OR fallback."""
        tokens = re.findall(r"\w+", query)
        if not tokens:
            return []
        for joiner in (" ", " OR "):
            match = joiner.join(f"{t}*" for t in tokens)
            sql = (
                "SELECT f.* FROM drive_fts "
                "JOIN drive_files f ON f.file_id = drive_fts.file_id "
                "WHERE drive_fts MATCH ? "
            )
            params: list = [match]
            if category:
                sql += "AND f.category = ? "
                params.append(category)
            sql += "ORDER BY bm25(drive_fts) LIMIT ?"
            params.append(limit)
            try:
                cur = await self.conn.execute(sql, params)
                rows = await cur.fetchall()
            except aiosqlite.OperationalError:
                rows = []
            if rows:
                return rows
        return []

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

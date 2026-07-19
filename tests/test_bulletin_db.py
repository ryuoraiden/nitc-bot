from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from bot.db import Database


class BulletinDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "bot.db")
        self.db = Database(self.db_path)
        await self.db.connect()

    async def asyncTearDown(self):
        await self.db.close()
        self.temp_dir.cleanup()

    async def test_filter_and_priority_order(self):
        await self.db.mark_notice_seen("w", "academic", "Workshop on robotics", "https://x/w")
        await self.db.mark_notice_seen(
            "p", "general", "Placement internship applications close today", "https://x/p"
        )
        rows = await self.db.bulletin_notices(limit=10)
        self.assertEqual([row["notice_key"] for row in rows], ["p", "w"])
        placement_rows = await self.db.bulletin_notices(category="placement", limit=10)
        self.assertEqual([row["notice_key"] for row in placement_rows], ["p"])
        urgent_rows = await self.db.bulletin_notices(urgent_only=True, limit=10)
        self.assertEqual([row["notice_key"] for row in urgent_rows], ["p"])

    async def test_digest_enable_seeds_history_then_returns_new_notices(self):
        await self.db.mark_notice_seen("old", "general", "Scholarship circular", "https://x/old")
        await self.db.set_notices_channel(42, 99, "daily_digest")
        self.assertEqual(await self.db.pending_digest_notices(42), [])

        await self.db.mark_notice_seen(
            "new", "academic", "Workshop registration closes tomorrow", "https://x/new"
        )
        rows = await self.db.pending_digest_notices(42)
        self.assertEqual([row["notice_key"] for row in rows], ["new"])
        await self.db.mark_digest_delivered(42, ["new"])
        self.assertEqual(await self.db.pending_digest_notices(42), [])

    async def test_unclassified_notices_still_reach_the_digest(self):
        await self.db.set_notices_channel(42, 99, "daily_digest")
        await self.db.mark_notice_seen(
            "plain", "general", "Water supply disruption in campus", "https://x/plain"
        )
        rows = await self.db.pending_digest_notices(42)
        self.assertEqual([row["notice_key"] for row in rows], ["plain"])
        self.assertIsNone(rows[0]["tags"])

    async def test_delivery_modes_select_correct_targets(self):
        await self.db.set_notices_channel(1, 101, "immediate")
        await self.db.set_notices_channel(2, 102, "daily_digest")
        await self.db.set_notices_channel(3, 103, "both")
        immediate = {row["guild_id"] for row in await self.db.all_notice_targets()}
        digest = {row["guild_id"] for row in await self.db.all_digest_targets()}
        self.assertEqual(immediate, {1, 3})
        self.assertEqual(digest, {2, 3})


class LegacyMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_current_notice_schema_is_backfilled(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = str(Path(temp_dir) / "legacy.db")
            conn = sqlite3.connect(path)
            conn.executescript(
                """
                CREATE TABLE guilds (
                    guild_id INTEGER PRIMARY KEY,
                    reminder_channel INTEGER,
                    mention_role INTEGER,
                    notices_channel INTEGER,
                    welcome_channel INTEGER,
                    goodbye_channel INTEGER
                );
                CREATE TABLE notices_seen (
                    notice_key TEXT PRIMARY KEY,
                    board TEXT NOT NULL,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL,
                    first_seen TEXT NOT NULL DEFAULT (datetime('now'))
                );
                INSERT INTO notices_seen (notice_key, board, title, url)
                VALUES ('legacy', 'academic', 'Internship deadline', 'https://x/legacy');
                INSERT INTO guilds (guild_id, notices_channel) VALUES (7, 77);
                """
            )
            conn.close()

            db = Database(path)
            await db.connect()
            rows = await db.bulletin_notices(limit=10)
            self.assertEqual(rows[0]["notice_key"], "legacy")
            self.assertEqual(set(rows[0]["tags"].split(",")), {"deadline", "placement"})
            self.assertEqual(rows[0]["classification_version"], 1)
            targets = await db.all_notice_targets()
            self.assertEqual(
                [(row["guild_id"], row["notices_channel"]) for row in targets],
                [(7, 77)],
            )
            await db.close()

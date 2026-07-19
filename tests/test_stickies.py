from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bot.cogs.stickies import should_repost
from bot.db import Database


class StickyThresholdTests(unittest.TestCase):
    def test_either_enabled_threshold_reposts(self):
        self.assertTrue(should_repost(5, 2, 5, 15))
        self.assertTrue(should_repost(1, 15, 5, 15))
        self.assertFalse(should_repost(4, 14.9, 5, 15))

    def test_zero_disables_a_threshold(self):
        self.assertFalse(should_repost(999, 14, 0, 15))
        self.assertTrue(should_repost(1, 15, 0, 15))


class StickyDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Database(str(Path(self.temp_dir.name) / "bot.db"))
        await self.db.connect()

    async def asyncTearDown(self):
        await self.db.close()
        self.temp_dir.cleanup()

    async def test_all_sticky_channel_ids(self):
        self.assertEqual(await self.db.all_sticky_channel_ids(), set())
        await self.db.upsert_sticky(
            guild_id=1, channel_id=2, content="hi", style="plain", image_url=None,
            every_messages=5, after_seconds=15, last_message_id=3,
            last_posted_at=100.0, created_by=4,
        )
        self.assertEqual(await self.db.all_sticky_channel_ids(), {2})
        await self.db.delete_sticky(2)
        self.assertEqual(await self.db.all_sticky_channel_ids(), set())

    async def test_sticky_lifecycle_and_persistence(self):
        await self.db.upsert_sticky(
            guild_id=1,
            channel_id=2,
            content="Read the rules",
            style="embed",
            image_url="https://example.com/rules.png",
            every_messages=5,
            after_seconds=15,
            last_message_id=3,
            last_posted_at=100.0,
            created_by=4,
        )
        row = await self.db.get_sticky(2)
        self.assertEqual(row["content"], "Read the rules")
        self.assertEqual(row["active"], 1)

        await self.db.set_sticky_message_count(2, 4)
        await self.db.mark_sticky_posted(2, 9, 200.0)
        row = await self.db.get_sticky(2)
        self.assertEqual((row["message_count"], row["last_message_id"]), (0, 9))

        await self.db.set_sticky_active(2, False)
        self.assertEqual((await self.db.get_sticky(2))["active"], 0)
        self.assertEqual(await self.db.delete_sticky(2), 1)
        self.assertIsNone(await self.db.get_sticky(2))

    async def test_list_is_scoped_to_guild(self):
        for guild_id, channel_id in ((1, 10), (2, 20)):
            await self.db.upsert_sticky(
                guild_id=guild_id,
                channel_id=channel_id,
                content="sticky",
                style="plain",
                image_url=None,
                every_messages=5,
                after_seconds=15,
                last_message_id=channel_id + 1,
                last_posted_at=100.0,
                created_by=4,
            )
        self.assertEqual([row["channel_id"] for row in await self.db.list_stickies(1)], [10])

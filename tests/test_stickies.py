from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from bot.cogs.stickies import Stickies, expand_line_breaks, should_repost
from bot.db import Database


class _FakeSent:
    def __init__(self, message_id: int):
        self.id = message_id


class _FakePartial:
    def __init__(self, channel, message_id: int):
        self.channel, self.id = channel, message_id

    async def delete(self):
        self.channel.deleted.append(self.id)


class _FakeChannel:
    """Minimal stand-in for a text channel: records sends and deletes."""

    def __init__(self, channel_id: int = 2):
        self.id = channel_id
        self.sent: list[dict] = []
        self.deleted: list[int] = []
        self._next_id = 1000

    async def send(self, content=None, *, embed=None, allowed_mentions=None):
        self._next_id += 1
        self.sent.append({"content": content, "embed": embed, "id": self._next_id})
        return _FakeSent(self._next_id)

    def get_partial_message(self, message_id: int):
        return _FakePartial(self, message_id)


class _FakeMessage:
    def __init__(self, channel, message_id: int, *, bot: bool = False, webhook: bool = False):
        self.id = message_id
        self.channel = channel
        self.guild = SimpleNamespace(id=1)
        self.author = SimpleNamespace(bot=bot)
        self.webhook_id = 77 if webhook else None


class StickyThresholdTests(unittest.TestCase):
    def test_literal_newline_escape_becomes_line_break(self):
        self.assertEqual(expand_line_breaks(r"First line\nSecond line"), "First line\nSecond line")

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


class StickyListenerTests(unittest.IsolatedAsyncioTestCase):
    """Drives on_message directly: bot/webhook messages count, our sticky doesn't."""

    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Database(str(Path(self.temp_dir.name) / "bot.db"))
        await self.db.connect()
        self.channel = _FakeChannel()
        self.cog = Stickies(SimpleNamespace(db=self.db, user=SimpleNamespace(id=999)))
        self.cog._sticky_channels = {self.channel.id}
        await self.db.upsert_sticky(
            guild_id=1,
            channel_id=self.channel.id,
            content="Read the rules",
            style="plain",
            image_url=None,
            every_messages=2,
            after_seconds=0,
            last_message_id=500,
            last_posted_at=100.0,
            created_by=4,
        )

    async def asyncTearDown(self):
        await self.db.close()
        self.temp_dir.cleanup()

    async def _count(self) -> int:
        return (await self.db.get_sticky(self.channel.id))["message_count"]

    async def test_bot_and_webhook_messages_count_and_trigger_repost(self):
        await self.cog.on_message(_FakeMessage(self.channel, 1, bot=True))
        self.assertEqual(await self._count(), 1)
        self.assertEqual(self.channel.sent, [])

        # A slash-command reply (bot author + webhook id) tips it over the threshold.
        await self.cog.on_message(_FakeMessage(self.channel, 2, bot=True, webhook=True))
        self.assertEqual(len(self.channel.sent), 1)
        row = await self.db.get_sticky(self.channel.id)
        self.assertEqual(row["message_count"], 0)
        self.assertEqual(row["last_message_id"], self.channel.sent[0]["id"])
        self.assertEqual(self.channel.deleted, [500])

    async def test_own_sticky_is_ignored_so_it_cannot_loop(self):
        row = await self.db.get_sticky(self.channel.id)
        await self.cog.on_message(_FakeMessage(self.channel, row["last_message_id"], bot=True))
        self.assertEqual(await self._count(), 0)
        self.assertEqual(self.channel.sent, [])

    async def test_paused_sticky_ignores_everything(self):
        await self.db.set_sticky_active(self.channel.id, False)
        for i in range(5):
            await self.cog.on_message(_FakeMessage(self.channel, 10 + i))
        self.assertEqual(self.channel.sent, [])
        self.assertEqual(await self._count(), 0)

    async def test_channel_without_a_sticky_is_untouched(self):
        other = _FakeChannel(channel_id=404)
        await self.cog.on_message(_FakeMessage(other, 1))
        self.assertEqual(other.sent, [])

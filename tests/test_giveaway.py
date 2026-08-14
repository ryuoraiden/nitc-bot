from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bot.db import Database
from bot.giveaway import draw_winners, parse_duration


class DurationParsingTests(unittest.TestCase):
    def test_single_and_compound_units(self):
        self.assertEqual(parse_duration("1d"), timedelta(days=1))
        self.assertEqual(parse_duration("2h30m"), timedelta(hours=2, minutes=30))
        self.assertEqual(parse_duration("1d 6h"), timedelta(days=1, hours=6))
        self.assertEqual(parse_duration("45S"), timedelta(seconds=45))

    def test_rejects_junk_and_out_of_range(self):
        for bad in ("", "soon", "1w", "5h banana", "9s", "61d"):
            with self.subTest(bad=bad):
                self.assertIsNone(parse_duration(bad))


class DrawWinnersTests(unittest.TestCase):
    def test_winners_are_unique_and_capped_by_pool(self):
        winners = draw_winners({1: 1, 2: 1, 3: 1}, 5)
        self.assertEqual(sorted(winners), [1, 2, 3])

    def test_excluded_and_zero_weight_entrants_never_win(self):
        winners = draw_winners({1: 5, 2: 1, 3: 0}, 3, exclude={1})
        self.assertEqual(winners, [2])

    def test_weighting_favours_bonus_entries(self):
        wins = 0
        for _ in range(400):
            if draw_winners({1: 50, 2: 1}, 1) == [1]:
                wins += 1
        self.assertGreater(wins, 320)  # ~98% expected, generous margin


class GiveawayDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Database(str(Path(self.temp_dir.name) / "bot.db"))
        await self.db.connect()
        self.ends_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()

    async def asyncTearDown(self):
        await self.db.close()
        self.temp_dir.cleanup()

    async def _create(self, prize: str = "T-shirt", **overrides) -> int:
        kwargs = dict(
            prize=prize, description=None, host_id=1, guild_id=10, channel_id=20,
            winners_count=1, required_roles=[], role_logic="all", bonus_role_id=None,
            bonus_entries=0, image_name=None, created_by=1, ends_at=self.ends_at,
        )
        kwargs.update(overrides)
        return await self.db.create_giveaway(**kwargs)

    async def test_create_roundtrip_and_message_lookup(self):
        gid = await self._create(required_roles=[7, 8], role_logic="any")
        await self.db.set_giveaway_message(gid, 999)
        g = await self.db.giveaway_by_message(999)
        self.assertEqual(g["id"], gid)
        self.assertEqual(g["required_roles"], "[7, 8]")
        self.assertEqual(g["role_logic"], "any")
        self.assertFalse(g["ended"])

    async def test_entry_is_idempotent_and_removable(self):
        gid = await self._create()
        await self.db.add_entry(gid, 100, 1)
        await self.db.add_entry(gid, 100, 3)  # re-entry updates the weight
        rows = await self.db.giveaway_entries(gid)
        self.assertEqual([(r["user_id"], r["entries"]) for r in rows], [(100, 3)])
        self.assertTrue(await self.db.has_entry(gid, 100))
        self.assertTrue(await self.db.remove_entry(gid, 100))
        self.assertFalse(await self.db.has_entry(gid, 100))
        self.assertEqual(await self.db.count_giveaway_entrants(gid), 0)

    async def test_active_and_unfinished_exclude_settled_giveaways(self):
        live = await self._create("live")
        await self.db.set_giveaway_message(live, 1)
        done = await self._create("done")
        await self.db.set_giveaway_message(done, 2)
        killed = await self._create("killed")
        await self.db.set_giveaway_message(killed, 3)
        await self.db.end_giveaway(done, [42])
        await self.db.cancel_giveaway(killed)

        self.assertEqual([g["id"] for g in await self.db.active_giveaways(10)], [live])
        self.assertEqual([g["id"] for g in await self.db.unfinished_giveaways()], [live])
        self.assertEqual((await self.db.latest_active_giveaway(10))["id"], live)
        self.assertEqual((await self.db.latest_giveaway(10))["id"], killed)
        self.assertEqual((await self.db.get_giveaway(done))["winners_json"], "[42]")

    async def test_unfinished_skips_giveaways_that_never_posted(self):
        await self._create("unposted")
        self.assertEqual(await self.db.unfinished_giveaways(), [])

    async def test_reroll_appends_to_the_winner_list(self):
        gid = await self._create()
        await self.db.end_giveaway(gid, [1])
        await self.db.set_giveaway_winners(gid, [1, 2])
        self.assertEqual((await self.db.get_giveaway(gid))["winners_json"], "[1, 2]")


if __name__ == "__main__":
    unittest.main()

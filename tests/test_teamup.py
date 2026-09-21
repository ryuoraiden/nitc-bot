from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from bot.cogs.teamup import (
    ALL_TAG_NAMES,
    FORUM_TAG_LIMIT,
    MAX_TAGS,
    build_card,
    build_guide,
    card_view,
    format_links,
    normalize_skills,
    pick_tags,
)
from bot.db import Database


def _post(**overrides) -> dict:
    post = {
        "id": 1,
        "guild_id": 10,
        "author_id": 20,
        "kind": "members",
        "hackathon": "Smart India Hackathon",
        "skills": "Frontend,ML/AI",
        "slots": "2 of 4",
        "about": "Building a campus navigation app",
        "links": "https://github.com/example",
        "thread_id": 30,
        "message_id": 40,
        "status": "open",
        "created_at": "2026-09-21 10:00:00",
    }
    post.update(overrides)
    return post


class SkillTests(unittest.TestCase):
    def test_aliases_canonicalize_and_unknowns_survive(self):
        self.assertEqual(
            normalize_skills("react, ML , figma, rust"),
            ["Frontend", "ML/AI", "Design", "rust"],
        )

    def test_dedupes_across_aliases(self):
        self.assertEqual(normalize_skills("frontend; React | web"), ["Frontend"])

    def test_empty_and_caps(self):
        self.assertEqual(normalize_skills(""), [])
        self.assertEqual(normalize_skills(None), [])
        self.assertEqual(len(normalize_skills(",".join(f"s{i}" for i in range(20)))), 10)

    def test_tag_names_fit_discord_limits(self):
        self.assertLessEqual(len(ALL_TAG_NAMES), FORUM_TAG_LIMIT)
        self.assertTrue(all(len(n) <= 20 for n in ALL_TAG_NAMES))


class TagTests(unittest.TestCase):
    def setUp(self):
        self.tags = [SimpleNamespace(name=n) for n in ALL_TAG_NAMES]

    def test_kind_first_then_skills_capped_at_five(self):
        picked = pick_tags(self.tags, "members", ["Frontend", "Backend", "Design", "Data", "Mobile"])
        self.assertEqual(len(picked), MAX_TAGS)
        self.assertEqual(picked[0].name, "Looking for members")

    def test_ignores_skills_without_a_tag(self):
        picked = pick_tags(self.tags, "team", ["rust", "Frontend"])
        self.assertEqual([t.name for t in picked], ["Looking for team", "Frontend"])

    def test_closed_goes_first(self):
        picked = pick_tags(self.tags, "team", [], closed=True)
        self.assertEqual(picked[0].name, "Closed")


class CardTests(unittest.TestCase):
    def test_open_card_fields_and_footer(self):
        embed = build_card(_post(), handles=["Codeforces: `x`"], interested=3)
        self.assertIn("Smart India Hackathon", embed.title)
        names = [f.name for f in embed.fields]
        self.assertEqual(names, ["Skills needed", "Team size", "Posted by", "Links"])
        self.assertIn("Codeforces", embed.fields[2].value)
        self.assertIn("3 interested", embed.footer.text)
        self.assertLessEqual(len(embed), 6000)

    def test_team_kind_labels_and_closed_state(self):
        embed = build_card(_post(kind="team", status="closed", links=None, slots=None))
        self.assertEqual([f.name for f in embed.fields], ["My skills", "Preferred team size", "Posted by"])
        self.assertIn("Team full", embed.footer.text)

    def test_view_disables_buttons_when_closed(self):
        view = card_view(7, open_=False)
        self.assertTrue(all(item.item.disabled for item in view.children))
        self.assertEqual({item.item.custom_id for item in view.children}, {"lft:int:7", "lft:close:7"})


class TeamUpDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Database(str(Path(self.temp_dir.name) / "bot.db"))
        await self.db.connect()

    async def asyncTearDown(self):
        await self.db.close()
        self.temp_dir.cleanup()

    async def _post(self, author: int = 1) -> int:
        return await self.db.create_lft_post(
            guild_id=10, author_id=author, kind="members", hackathon="SIH",
            skills="Frontend", slots=None, about=None, links=None,
        )

    async def test_config_round_trip(self):
        await self.db.set_teamup_config(10, 100, 200, 300)
        row = await self.db.get_guild(10)
        self.assertEqual((row["teamup_forum"], row["teamup_connect"], row["teamup_role"]), (100, 200, 300))

    async def test_interest_dedup_and_count(self):
        pid = await self._post()
        self.assertTrue(await self.db.add_lft_interest(pid, 5))
        self.assertFalse(await self.db.add_lft_interest(pid, 5))
        await self.db.set_lft_interest_thread(pid, 5, 999)
        self.assertEqual((await self.db.get_lft_interest(pid, 5))["thread_id"], 999)
        self.assertEqual(await self.db.count_lft_interests(pid), 1)

    async def test_open_count_close_and_expiry(self):
        a = await self._post()
        await self._post()
        self.assertEqual(await self.db.count_open_lft_posts(10, 1), 2)
        self.assertTrue(await self.db.close_lft_post(a))
        self.assertFalse(await self.db.close_lft_post(a))
        self.assertEqual(await self.db.count_open_lft_posts(10, 1), 1)
        self.assertEqual(await self.db.stale_lft_posts(30), [])
        self.assertEqual(len(await self.db.stale_lft_posts(0)), 1)

    async def test_ping_cooldown(self):
        pid = await self._post()
        self.assertFalse(await self.db.lft_pinged_recently(10, 1))
        await self.db.set_lft_post_message(pid, 30, 40, pinged=True)
        self.assertTrue(await self.db.lft_pinged_recently(10, 1))
        self.assertFalse(await self.db.lft_pinged_recently(10, 2))

    async def test_delete_cleans_up_interests(self):
        pid = await self._post()
        await self.db.add_lft_interest(pid, 5)
        await self.db.delete_lft_post(pid)
        self.assertIsNone(await self.db.get_lft_post(pid))
        self.assertEqual(await self.db.count_lft_interests(pid), 0)


class GuideTests(unittest.TestCase):
    def test_guide_with_everything(self):
        embed = build_guide("<#1>", "<#2>", "<@&3>")
        text = " ".join(f.value for f in embed.fields)
        for needle in ("/lft", "<#1>", "<#2>", "<@&3>", "Team full", "I'm interested"):
            self.assertIn(needle, text)
        self.assertLessEqual(len(embed), 6000)

    def test_guide_without_optional_parts(self):
        embed = build_guide("<#1>", None, None)
        names = [f.name for f in embed.fields]
        self.assertNotIn("🔔 Get notified", names)
        self.assertNotIn(" in None", " ".join(f.value for f in embed.fields))


class LinkTests(unittest.TestCase):
    def test_line_breaks_are_kept(self):
        self.assertEqual(format_links("https://a.com\n\nhttps://b.com\n"), "https://a.com\nhttps://b.com")

    def test_literal_backslash_n_like_stick(self):
        self.assertEqual(format_links(r"https://a.com\nhttps://b.com"), "https://a.com\nhttps://b.com")

    def test_urls_on_one_line_are_split(self):
        self.assertEqual(
            format_links("https://a.com, https://b.com www.c.com"),
            "https://a.com\nhttps://b.com\nwww.c.com",
        )

    def test_labelled_lines_left_alone(self):
        text = "GitHub: https://a.com Portfolio: https://b.com"
        self.assertEqual(format_links(text), text)

    def test_empty(self):
        self.assertIsNone(format_links(""))
        self.assertIsNone(format_links("   \n  "))

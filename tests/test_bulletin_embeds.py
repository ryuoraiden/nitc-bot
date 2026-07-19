from __future__ import annotations

import unittest

from bot.cogs.notices import _bulletin_embed, _chunk_lines, _digest_embed, _safe_title


def _row(index: int, tags: str = "deadline,placement") -> dict:
    return {
        "notice_key": str(index),
        "title": f"Internship application deadline {index} " + "details " * 30,
        "url": f"https://example.test/{index}.pdf",
        "board": "academic",
        "first_seen": "2026-07-19 02:30:00",
        "tags": tags,
    }


class BulletinEmbedTests(unittest.TestCase):
    def test_bulletin_description_stays_within_discord_limit(self):
        embed = _bulletin_embed([_row(i) for i in range(15)], None, False)
        self.assertLessEqual(len(embed.description), 4096)
        self.assertLessEqual(len(embed), 6000)

    def test_digest_fields_stay_within_discord_limits(self):
        rows = [_row(i, ("deadline", "workshop", "placement", "admin")[i % 4]) for i in range(15)]
        embed = _digest_embed(rows, has_more=True)
        self.assertLessEqual(len(embed.fields), 25)
        self.assertTrue(all(len(field.value) <= 1024 for field in embed.fields))
        self.assertLessEqual(len(embed), 6000)
        self.assertIn("/bulletin", embed.footer.text)

    def test_digest_puts_untagged_rows_under_other(self):
        rows = [_row(0, "deadline"), _row(1, ""), _row(2, "stale_category")]
        embed = _digest_embed(rows)
        other_fields = [f for f in embed.fields if f.name.startswith("📌 Other")]
        self.assertEqual(len(other_fields), 1)
        self.assertIn("https://example.test/1.pdf", other_fields[0].value)
        self.assertIn("https://example.test/2.pdf", other_fields[0].value)

    def test_chunk_lines_truncates_single_overlong_line(self):
        chunks = _chunk_lines(["x" * 2000, "short"], max_length=1000)
        self.assertTrue(all(len(chunk) <= 1000 for chunk in chunks))

    def test_safe_title_escapes_link_breaking_brackets(self):
        self.assertNotIn("]", _safe_title("Result [Phase 2] published").replace("\\]", ""))

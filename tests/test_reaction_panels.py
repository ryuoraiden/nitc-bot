from __future__ import annotations

import unittest

from bot.cogs.reaction_roles import _iter_entries, parse_message_ref
from bot.reaction_panels import PANELS


class MessageRefTests(unittest.TestCase):
    def test_parses_message_link(self):
        self.assertEqual(
            parse_message_ref("https://discord.com/channels/111/222/333"),
            (222, 333),
        )

    def test_parses_bare_id_without_channel(self):
        self.assertEqual(parse_message_ref("1521871106871398551"), (None, 1521871106871398551))

    def test_rejects_junk(self):
        for bad in ("not a link", "12", "abc", ""):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    parse_message_ref(bad)


class PanelDefinitionTests(unittest.TestCase):
    def test_select_panels_stay_under_discord_option_cap(self):
        for name, spec in PANELS.items():
            if spec.get("style") == "select":
                with self.subTest(panel=name):
                    self.assertLessEqual(len(list(_iter_entries(spec))), 25)

    def test_no_duplicate_roles_within_a_panel(self):
        for name, spec in PANELS.items():
            queries = [q for _, q, _ in _iter_entries(spec)]
            with self.subTest(panel=name):
                self.assertEqual(len(queries), len(set(queries)))

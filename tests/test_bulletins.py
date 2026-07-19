from __future__ import annotations

import unittest

from bot.bulletins import classify_notice, normalize_title


class BulletinClassifierTests(unittest.TestCase):
    def test_each_supported_category(self):
        cases = {
            "deadline": "Last date for fee payment extended to Friday",
            "workshop": "Workshop on practical machine learning",
            "placement": "Campus recruitment drive for final-year students",
            "admin": "Circular regarding hostel allotment",
        }
        for expected, title in cases.items():
            with self.subTest(expected=expected):
                self.assertIn(expected, classify_notice(title).tags)

    def test_multi_tag_deadline_is_urgent(self):
        result = classify_notice("Internship applications close on 30 July")
        self.assertEqual(result.tags, ("deadline", "placement"))
        self.assertTrue(result.urgent)

    def test_matching_is_case_and_punctuation_tolerant(self):
        result = classify_notice("PRE-PLACEMENT TALK — registration closes tomorrow")
        self.assertEqual(result.tags, ("deadline", "placement"))

    def test_unrelated_title_is_not_forced_into_admin(self):
        self.assertEqual(classify_notice("Research article published by faculty").tags, ())

    def test_word_boundaries_avoid_substring_matches(self):
        self.assertNotIn("workshop", classify_notice("Seminarist alumni gathering").tags)

    def test_normalization_collapses_separators(self):
        self.assertEqual(
            normalize_title("  Course—Registration / Notice "),
            "course registration notice",
        )

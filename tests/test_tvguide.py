import unittest
from datetime import date

from parsers.tvplus import EUROSPORT_CHANNELS, TARGET_CHANNELS
from parsers.tvguide_cached import SOURCE, infer_direct_event


class TVGuideTests(unittest.TestCase):
    def event(self, title: str, *, channel: str = "Setanta Sports 1") -> dict:
        return {
            "source": "tvplus",
            "source_url": "https://example.test/channel",
            "channel": channel,
            "date": "2026-09-12",
            "time": "19:00",
            "sport": "",
            "tournament": "",
            "title": title,
            "raw_title": title,
            "is_live": False,
            "estimated_broadcast_end_date": "2026-09-12",
            "estimated_broadcast_end": "21:00",
            "end_estimation_method": "provider_epg",
            "end_confidence": "high",
        }

    def test_inventory_contains_all_14_epg_channels(self):
        names = {item.name for item in TARGET_CHANNELS + EUROSPORT_CHANNELS}
        self.assertEqual(len(names), 14)
        self.assertIn("Eurosport", names)
        self.assertIn("Eurosport 2", names)
        self.assertIn("KHL HD", names)
        self.assertIn("Setanta Sports KZ", names)
        self.assertIn("МАТЧ! Планета", names)

    def test_specific_current_match_becomes_direct_candidate(self):
        parsed = infer_direct_event(
            self.event('Фонбет Чемпионат КХЛ. ХК "Сочи" - СКА', channel="KHL HD"),
            target_date=date(2026, 9, 12),
            seen=set(),
        )
        self.assertTrue(parsed["is_live"])
        self.assertEqual(parsed["source"], SOURCE)
        self.assertEqual(parsed["live_evidence_method"], "provider_scheduled_sport_event")

    def test_historical_year_is_not_promoted(self):
        parsed = infer_direct_event(
            self.event("Турнир Bushido Fighting Championship 2020"),
            target_date=date(2026, 9, 12),
            seen=set(),
        )
        self.assertFalse(parsed["is_live"])

    def test_repeat_occurrence_is_not_promoted_twice(self):
        seen = set()
        first = infer_direct_event(
            self.event("Футбол. Челси - Астон Вилла"),
            target_date=date(2026, 9, 12),
            seen=seen,
        )
        second = infer_direct_event(
            self.event("Футбол. Челси - Астон Вилла"),
            target_date=date(2026, 9, 12),
            seen=seen,
        )
        self.assertTrue(first["is_live"])
        self.assertFalse(second["is_live"])


if __name__ == "__main__":
    unittest.main()

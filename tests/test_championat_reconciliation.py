import unittest
from datetime import date
from unittest.mock import patch

from parsers.tvguide_cached import RECONCILE_LOOKAHEAD_HOURS, infer_direct_event
from verifiers.championat_occurrence import verify_championat_occurrence


def base_event(**overrides):
    event = {
        "source": "tvguide",
        "source_url": "https://tv.telecom.kz/channels/example/program",
        "channel": "Setanta Sports 1",
        "date": "2026-09-13",
        "time": "02:00",
        "timezone": "Asia/Almaty",
        "sport": "MMA",
        "tournament": "Noche UFC",
        "title": "Силва vs. Дельгадо",
        "raw_title": "Noche UFC. Силва vs. Дельгадо",
        "is_live": False,
        "is_live_broadcast": False,
        "live_state": "unknown",
        "live_evidence_method": "provider_epg_sport_candidate",
        "estimated_broadcast_end_date": "2026-09-13",
        "estimated_broadcast_end": "06:00",
        "is_sport_event": True,
    }
    event.update(overrides)
    return event


class ChampionatOccurrenceTests(unittest.TestCase):
    @patch("verifiers.championat_occurrence._openserp_search_any")
    def test_championat_matching_event_and_time_confirms_live(self, search):
        search.return_value = ([{
            "title": "Силва - Дельгадо, 13 сентября 2026",
            "snippet": "13 сентября 2026, воскресенье. 00:00 МСК. Noche UFC. Силва – Дельгадо. Не начался",
            "url": "https://www.championat.com/boxing/_ufc/match/12345/",
        }], {})
        result = verify_championat_occurrence(base_event())
        self.assertEqual(result["state"], "confirmed_direct")
        self.assertEqual(result["verification_source"], "championat.com")
        self.assertEqual(result["difference_minutes"], 0)

    @patch("verifiers.championat_occurrence._openserp_search_any")
    def test_championat_previous_day_event_rejects_replay(self, search):
        search.return_value = ([{
            "title": "Барыс - Амур, 12 сентября 2026",
            "snippet": "12 сентября 2026, суббота. 14:30 МСК. Барыс – Амур. Окончен",
            "url": "https://www.championat.com/hockey/_superleague/match/67890/",
        }], {})
        event = base_event(
            channel="KHL HD",
            sport="Хоккей",
            tournament="Фонбет Чемпионат КХЛ",
            title="Барыс – Амур",
            raw_title="Фонбет Чемпионат КХЛ. Барыс – Амур",
            time="05:45",
        )
        result = verify_championat_occurrence(event)
        self.assertEqual(result["state"], "mismatch")
        self.assertEqual(result["verification_source"], "championat.com")
        self.assertGreater(abs(result["difference_minutes"]), 30)

    @patch("verifiers.championat_occurrence.verify_broadcast_occurrence")
    @patch("verifiers.championat_occurrence._openserp_search_any")
    def test_generic_media_consensus_cannot_publish_tvguide_live(self, search, fallback):
        search.return_value = ([], {})
        fallback.return_value = {
            "state": "confirmed_direct",
            "difference_minutes": 0,
            "external_time_kz": "2026-09-13T02:00:00+05:00",
            "sources": [{"source_name": "sports.ru", "url": "https://sports.ru/example"}],
        }
        result = verify_championat_occurrence(base_event())
        self.assertEqual(result["state"], "unknown")

    @patch("verifiers.championat_occurrence.verify_broadcast_occurrence")
    @patch("verifiers.championat_occurrence._openserp_search_any")
    def test_official_source_can_fallback_when_championat_has_no_event(self, search, fallback):
        search.return_value = ([], {})
        fallback.return_value = {
            "state": "confirmed_direct",
            "difference_minutes": 0,
            "external_time_kz": "2026-09-13T02:00:00+05:00",
            "sources": [{"source_name": "ufc.com", "url": "https://www.ufc.com/event/test"}],
        }
        result = verify_championat_occurrence(base_event())
        self.assertEqual(result["state"], "confirmed_direct")
        self.assertEqual(result["verification_source"], "official_fallback")


class TvGuidePolicyTests(unittest.TestCase):
    def test_provider_live_label_is_not_enough_without_external_reconciliation(self):
        raw = base_event(
            source="tvplus",
            is_live=True,
            is_live_broadcast=True,
            live_state="live",
            live_evidence_method="provider_live_text",
        )
        result = infer_direct_event(raw, target_date=date(2026, 9, 13), seen=set())
        self.assertTrue(result["provider_claimed_live"])
        self.assertFalse(result["is_live_broadcast"])
        self.assertFalse(result["is_live"])
        self.assertTrue(result["is_sport_event"])
        self.assertEqual(result["reconciliation_state"], "unverified")

    def test_reconciliation_window_covers_full_epg_week(self):
        self.assertGreaterEqual(RECONCILE_LOOKAHEAD_HOURS, 7 * 24)


if __name__ == "__main__":
    unittest.main()

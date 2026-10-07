import unittest

from services.event_api_validation import _build_discrepancy, _match_score


class EventApiValidationTests(unittest.TestCase):
    def test_conservative_match(self):
        candidate = {
            "title": "Арсенал - Ливерпуль",
            "start_at": "2026-10-02T22:00:00+05:00",
        }
        external = {
            "home": "Arsenal",
            "away": "Liverpool",
            "start_at": "2026-10-02T17:10:00+00:00",
        }
        self.assertGreaterEqual(_match_score(candidate, external), 0.78)

    def test_time_difference_over_30_minutes_rejected(self):
        candidate = {
            "title": "Арсенал - Ливерпуль",
            "start_at": "2026-10-02T22:00:00+05:00",
        }
        external = {
            "home": "Arsenal",
            "away": "Liverpool",
            "start_at": "2026-10-02T18:00:00+00:00",
        }
        self.assertEqual(_match_score(candidate, external), 0.0)

    def test_mismatch_returns_human_apply_patch(self):
        candidate = {
            "title": "Арсенал - Ливерпуль",
            "channel": "SETANTA SPORTS 1",
            "date": "2026-10-02",
            "time": "22:00",
            "start_at": "2026-10-02T22:00:00+05:00",
            "source_record_id": "a" * 24,
        }
        external = {
            "provider": "espn_public",
            "id": "42",
            "home": "Arsenal",
            "away": "Liverpool",
            "start_at": "2026-10-02T17:30:00+00:00",
            "status": "pre",
            "status_detail": "Scheduled",
            "league": "eng.1",
            "source_url": "https://www.espn.com/example",
        }
        item = _build_discrepancy(candidate, external, 1.0)
        self.assertEqual(item["kind"], "time_mismatch")
        self.assertEqual(item["patch"]["date"], "2026-10-02")
        self.assertEqual(item["patch"]["time"], "22:30")
        self.assertEqual(item["source_name"], "ESPN")

    def test_cancelled_event_can_be_removed_only_after_editor_applies(self):
        candidate = {
            "title": "Арсенал - Ливерпуль",
            "channel": "SETANTA SPORTS 1",
            "date": "2026-10-02",
            "time": "22:00",
            "start_at": "2026-10-02T22:00:00+05:00",
            "source_record_id": "b" * 24,
        }
        external = {
            "provider": "thesportsdb",
            "id": "77",
            "home": "Arsenal",
            "away": "Liverpool",
            "start_at": "2026-10-02T17:00:00+00:00",
            "status": "Cancelled",
            "status_detail": "",
            "league": "Premier League",
            "source_url": "https://www.thesportsdb.com/event/77",
        }
        item = _build_discrepancy(candidate, external, 1.0)
        self.assertEqual(item["kind"], "event_cancelled")
        self.assertEqual(item["patch"], {"cancelled": "true"})
        self.assertEqual(item["level"], "critical")


if __name__ == "__main__":
    unittest.main()

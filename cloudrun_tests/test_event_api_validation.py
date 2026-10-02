import unittest

from services.event_api_validation import _match_score


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


if __name__ == "__main__":
    unittest.main()

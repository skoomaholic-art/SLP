import unittest

import parsers.sportplus_cached as sportplus_cached
from services.schedule_change_guard import systemic_one_day_shift


class SportPlusGuideStabilityTests(unittest.TestCase):
    def setUp(self):
        sportplus_cached._cache_html = None
        sportplus_cached._candidate_html = None
        sportplus_cached._candidate_signature = None
        sportplus_cached._candidate_seen = 0

    def test_dom_attribute_change_does_not_create_new_schedule_revision(self):
        first = '<html><div class="blink"><a title="LIVE">Матч</a></div></html>'
        second = '<html><div><a title="">Матч</a></div></html>'
        self.assertEqual(
            sportplus_cached._canonical_schedule_signature(first),
            sportplus_cached._canonical_schedule_signature(second),
        )

    def test_changed_visible_schedule_requires_two_consecutive_observations(self):
        stable = '<html><div>ПН 14.09</div><div>21:55</div><div>Матч A</div></html>'
        changed = '<html><div>ПН 14.09</div><div>21:55</div><div>Матч B</div></html>'
        sportplus_cached._cache_html = stable

        selected, promoted = sportplus_cached._select_stable_html(changed)
        self.assertFalse(promoted)
        self.assertEqual(selected, stable)

        selected, promoted = sportplus_cached._select_stable_html(changed)
        self.assertTrue(promoted)
        self.assertEqual(selected, changed)

    def test_different_transient_candidate_resets_confirmation(self):
        stable = '<html><div>Матч A</div></html>'
        candidate_one = '<html><div>Матч B</div></html>'
        candidate_two = '<html><div>Матч C</div></html>'
        sportplus_cached._cache_html = stable

        first, promoted = sportplus_cached._select_stable_html(candidate_one)
        self.assertFalse(promoted)
        self.assertEqual(first, stable)

        second, promoted = sportplus_cached._select_stable_html(candidate_two)
        self.assertFalse(promoted)
        self.assertEqual(second, stable)
        self.assertEqual(sportplus_cached._candidate_seen, 1)


class ScheduleChangeGuardTests(unittest.TestCase):
    @staticmethod
    def shift(title: str, old_day: str, new_day: str) -> dict:
        return {
            "type": "updated",
            "title": title,
            "changes": [
                {
                    "field": "time",
                    "channel": "Sport+ Qazaqstan",
                    "old": f"2026-09-{old_day}T21:55:00+05:00",
                    "new": f"2026-09-{new_day}T21:55:00+05:00",
                },
                {
                    "field": "end",
                    "channel": "Sport+ Qazaqstan",
                    "old": f"2026-09-{old_day}T23:55:00+05:00",
                    "new": f"2026-09-{new_day}T23:55:00+05:00",
                },
            ],
        }

    def test_mass_exact_one_day_shift_is_classified_as_systemic_remap(self):
        changes = [
            self.shift("A", "15", "14"),
            self.shift("B", "16", "15"),
            self.shift("C", "18", "17"),
        ]
        result = systemic_one_day_shift(changes)
        self.assertIsNotNone(result)
        self.assertEqual(result["channel"], "Sport+ Qazaqstan")
        self.assertEqual(result["delta_minutes"], -1440)
        self.assertEqual(result["event_count"], 3)

    def test_real_clock_change_is_not_suppressed(self):
        changes = [
            self.shift("A", "15", "14"),
            self.shift("B", "16", "15"),
            self.shift("C", "18", "17"),
        ]
        changes[1]["changes"][0]["new"] = "2026-09-15T22:10:00+05:00"
        self.assertIsNone(systemic_one_day_shift(changes))


if __name__ == "__main__":
    unittest.main()

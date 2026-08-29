import unittest
from unittest.mock import patch

from verifiers import web_search


class OpenSerpSpeedTests(unittest.TestCase):

    def test_primary_engines_are_fast_pair(self):
        self.assertEqual(
            web_search.PRIMARY_SEARCH_ENGINES,
            ("bing", "duckduckgo"),
        )

    def test_baidu_is_fallback_only(self):
        self.assertEqual(
            web_search.FALLBACK_SEARCH_ENGINES,
            ("baidu",),
        )
        blocked = {
            "google",
            "yandex",
            "ecosia",
        }
        self.assertTrue(
            blocked.isdisjoint(
                web_search.PRIMARY_SEARCH_ENGINES
            )
        )

    def test_time_status_has_no_accuracy_icons(self):
        labels = [
            web_search.time_status(5)["label"],
            web_search.time_status(15)["label"],
            web_search.time_status(25)["label"],
        ]

        self.assertEqual(
            labels,
            [
                "Время совпадает",
                "Событие требуется проверить",
                "Есть расхождение по времени",
            ],
        )

        for label in labels:
            self.assertNotIn("✅", label)
            self.assertNotIn("🟡", label)
            self.assertNotIn("🔴", label)

    def test_no_second_search_when_event_pages_found(self):
        event = {
            "date": "2026-08-29",
            "time": "18:00",
            "sport": "Футбол",
            "tournament": "Тест",
            "title": "Команда A - Команда B",
            "channel": "Qazsport",
        }

        with patch.object(
            web_search,
            "openserp_search",
            return_value=(
                [{"url": "https://example.com/event"}],
                {},
            ),
        ) as search_mock, patch.object(
            web_search,
            "dedupe_results",
            side_effect=lambda results: results,
        ), patch.object(
            web_search,
            "collect_candidates",
            return_value=([], 1),
        ):
            result = web_search.verify_event(event)

        self.assertEqual(search_mock.call_count, 1)
        self.assertFalse(result["found"])
        self.assertEqual(
            result["matching_results_count"],
            1,
        )

    def test_baidu_runs_only_when_primary_found_nothing(self):
        event = {
            "date": "2026-08-29",
            "time": "18:00",
            "sport": "Футбол",
            "tournament": "Тест",
            "title": "Команда A - Команда B",
            "channel": "Qazsport",
        }

        with patch.object(
            web_search,
            "openserp_search",
            side_effect=[([], {}), ([], {})],
        ) as search_mock, patch.object(
            web_search,
            "dedupe_results",
            side_effect=lambda results: results,
        ), patch.object(
            web_search,
            "collect_candidates",
            side_effect=[([], 0), ([], 0)],
        ):
            web_search.verify_event(event)

        self.assertEqual(search_mock.call_count, 2)

        first_call = search_mock.call_args_list[0]
        second_call = search_mock.call_args_list[1]

        self.assertEqual(
            first_call.kwargs["engines"],
            web_search.PRIMARY_SEARCH_ENGINES,
        )
        self.assertEqual(
            second_call.kwargs["engines"],
            web_search.FALLBACK_SEARCH_ENGINES,
        )

    def test_external_time_over_30_minutes_is_rejected(self):
        self.assertTrue(
            web_search.is_plausible_external_time_difference(30)
        )
        self.assertTrue(
            web_search.is_plausible_external_time_difference(-30)
        )
        self.assertFalse(
            web_search.is_plausible_external_time_difference(31)
        )
        self.assertFalse(
            web_search.is_plausible_external_time_difference(-31)
        )


if __name__ == "__main__":
    unittest.main()

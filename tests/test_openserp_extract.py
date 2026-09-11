import unittest
from unittest.mock import patch

from verifiers import web_search


class OpenSerpExtractTests(unittest.TestCase):

    def test_event_kind_match_draw_generic(self):
        match_event = {
            "title": "Андерлехт - Кайрат",
            "tournament": "Лига Европы УЕФА",
        }
        draw_event = {
            "title": "Жеребьёвка общего этапа",
            "tournament": "Лига конференций УЕФА",
        }
        generic_event = {
            "title": "Grand Slam",
            "tournament": "Grand Slam",
            "sport": "Дзюдо",
        }

        self.assertEqual(
            web_search.event_kind(match_event),
            "MATCH",
        )
        self.assertEqual(
            web_search.event_kind(draw_event),
            "DRAW",
        )
        self.assertEqual(
            web_search.event_kind(generic_event),
            "GENERIC",
        )

    def test_kairat_aliases_cover_kazakh_russian_english(self):
        aliases = set()

        for value in (
            "Қайрат",
            "Кайрат",
        ):
            aliases.update(
                web_search.participant_aliases(value)
            )

        self.assertIn("кайрат", aliases)
        self.assertIn("kairat", aliases)

    def test_match_works_with_english_names(self):
        event = {
            "title": "Андерлехт - Кайрат",
            "tournament": "Лига Европы УЕФА",
        }
        result = {
            "title": "Anderlecht vs Kairat",
            "snippet": "UEFA Europa League playoff",
        }

        self.assertTrue(
            web_search.matches_event(result, event)
        )

    def test_draw_requires_correct_tournament(self):
        event = {
            "title": "Жеребьёвка общего этапа",
            "tournament": "Лига конференций УЕФА",
        }

        wrong = {
            "title": "UEFA Champions League draw",
            "snippet": "League phase draw",
        }
        correct = {
            "title": "UEFA Conference League draw",
            "snippet": "League phase draw",
        }

        self.assertFalse(
            web_search.matches_event(wrong, event)
        )
        self.assertTrue(
            web_search.matches_event(correct, event)
        )

    def test_generic_event_requires_sport_and_exact_date_context(self):
        event = {
            "date": "2026-09-11",
            "time": "20:00",
            "title": "Grand Slam",
            "tournament": "Grand Slam",
            "sport": "Дзюдо",
        }
        correct = {
            "title": "Judo Grand Slam",
            "snippet": "11 September 2026 start time 20:00",
        }
        wrong_sport = {
            "title": "Tennis Grand Slam",
            "snippet": "11 September 2026 start time 20:00",
        }
        wrong_date = {
            "title": "Judo Grand Slam",
            "snippet": "12 September 2026 start time 20:00",
        }
        self.assertTrue(web_search.matches_event(correct, event))
        self.assertFalse(web_search.matches_event(wrong_sport, event))
        self.assertFalse(web_search.matches_event(wrong_date, event))

    def test_first_party_broadcast_source_is_not_independent(self):
        event = {
            "source_url": "https://qazsporttv.kz/ru/program",
            "title": "Кайрат - Астана",
            "tournament": "КПЛ",
            "sport": "Футбол",
        }
        own = {
            "url": "https://qazsporttv.kz/ru/program/2026-09-11",
            "title": "Кайрат - Астана",
        }
        independent = {
            "url": "https://uefa.com/example",
            "title": "Кайрат - Астана",
        }
        self.assertTrue(web_search.is_first_party_result(own, event))
        self.assertFalse(web_search.is_first_party_result(independent, event))

    def test_total_search_outage_is_reported_as_technical_error(self):
        event = {
            "date": "2026-09-11",
            "time": "20:00",
            "title": "Кайрат - Астана",
            "tournament": "КПЛ",
            "sport": "Футбол",
            "source_url": "https://qazsporttv.kz/ru/program",
        }
        with patch.object(
            web_search,
            "openserp_search",
            side_effect=RuntimeError("offline"),
        ) as search_mock:
            result = web_search.verify_event(event)
        self.assertFalse(result["found"])
        self.assertEqual(result["verification_error"], "search_unavailable")
        self.assertEqual(search_mock.call_count, 1)

    def test_page_context_beats_broadcast_nearest_time(self):
        event = {
            "date": "2026-08-29",
            "time": "23:20",
            "title": "Андерлехт - Кайрат",
            "tournament": "Лига Европы УЕФА",
            "sport": "Футбол",
        }
        result = {
            "url": "https://example.kz/match",
            "content": (
                "TV schedule: 23:20. "
                "Other programmes are listed here. "
                "Anderlecht vs Kairat kickoff time: 23:30."
            ),
        }

        best = web_search.best_time_for_result(
            result,
            event,
        )

        self.assertIsNotNone(best)
        self.assertEqual(
            best["dt_kz"].strftime("%H:%M"),
            "23:30",
        )

    def test_sportsdb_api_candidate_normalizes_cyrillic_match_and_utc(self):
        import json

        payload = {
            "event": [{
                "idEvent": "2527760",
                "strEvent": "Beşiktaş vs Erzurumspor",
                "dateEvent": "2026-09-11",
                "strTime": "17:00:00",
                "strLeague": "Turkish Super Lig",
                "strSport": "Soccer",
                "strStatus": "NS",
            }]
        }

        class Response:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def read(self):
                return json.dumps(payload).encode("utf-8")

        event = {
            "date": "2026-09-11",
            "time": "21:55",
            "title": "БЕШИКТАШ – ЭРЗУРУМСПОР",
            "sport": "Футбол",
        }
        with patch.object(web_search.urllib.request, "urlopen", return_value=Response()):
            candidate = web_search.sportsdb_candidate(event)

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate["dt_kz"].strftime("%H:%M"), "22:00")
        self.assertEqual(candidate["weight"], 60)
        self.assertEqual(candidate["engine"], "direct_api")

    def test_batch_payload_uses_native_openserp_endpoint(self):
        response = [
            {
                "page_content": "example",
                "metadata": {
                    "source": "https://example.com"
                },
            }
        ]

        with patch.object(
            web_search,
            "ensure_openserp",
        ), patch.object(
            web_search,
            "_post_json",
            return_value=response,
        ) as post_mock:
            result = web_search.openserp_extract_batch(
                ["https://example.com"]
            )

        self.assertEqual(result, response)

        endpoint = post_mock.call_args.args[0]
        payload = post_mock.call_args.args[1]

        self.assertTrue(
            endpoint.endswith("/extract/batch")
        )
        self.assertEqual(
            payload,
            {
                "urls": ["https://example.com"],
                "mode": "fast",
                "clean": False,
            },
        )


if __name__ == "__main__":
    unittest.main()

import unittest
from unittest.mock import patch

from bot.verification import build_verification_text
from verifiers import web_search


EVENT = {
    "date": "2026-09-12",
    "time": "04:55",
    "estimated_broadcast_end_date": "2026-09-12",
    "estimated_broadcast_end": "07:50",
    "title": "Братья Дурымановы",
    "raw_event_title": "Братья Дурымановы",
    "raw_title": "MMA. Series Classic. Братья Дурымановы",
    "sport": "MMA",
    "tournament": "Series Classic",
    "channel": "MMA-TV.COM",
    "source": "tvguide",
}


class OpenSerpRuntimeTests(unittest.TestCase):
    def test_remote_base_url_is_used(self):
        with patch.object(web_search, "OPENSERP_BASE_URL", "http://openserp.railway.internal:7000"):
            self.assertEqual(
                web_search._openserp_endpoint("/mega/search"),
                "http://openserp.railway.internal:7000/mega/search",
            )

    def test_search_outage_is_not_reported_as_no_results(self):
        with patch.object(web_search, "openserp_search", side_effect=RuntimeError("offline")):
            result = web_search.verify_event(EVENT)
        self.assertTrue(result["verification_unavailable"])
        self.assertEqual(result["source_count"], 0)
        text = build_verification_text(EVENT, result)
        self.assertIn("Внешняя проверка сейчас недоступна", text)


if __name__ == "__main__":
    unittest.main()

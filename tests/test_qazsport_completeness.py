import unittest

from parsers.qazsport_complete import apply_page_live_markers, extract_page_live_times


class QazsportCompletenessTests(unittest.TestCase):
    def test_page_level_live_tokens_are_extracted(self):
        page_text = (
            "11:10 Волейбол LIVE 11:25 Азия чемпионаты 3 орын үшін матч "
            "15:25 Басқа матч LIVE 17:55 Футбол Ұлытау – Алта LIVE 20:00 Студия"
        )
        self.assertEqual(
            extract_page_live_times(page_text),
            {"11:25", "17:55", "20:00"},
        )

    def test_live_marker_outside_anchor_promotes_matching_event(self):
        events = [
            {
                "date": "2026-09-13",
                "time": "11:25",
                "raw_title": "Азия чемпионаты (Ерлер). 3 орын үшін матч Волейбол",
                "title": "Азия чемпионаты (Ерлер). 3 орын үшін матч Волейбол",
                "sport": "",
                "tournament": "",
                "is_live": False,
                "is_live_broadcast": False,
            },
            {
                "date": "2026-09-13",
                "time": "17:55",
                "raw_title": "Футбол. Қазақстан Премьер-лигасы Ұлытау – Алта",
                "title": "Қазақстан Премьер-лигасы Ұлытау – Алта",
                "sport": "",
                "tournament": "",
                "is_live": False,
                "is_live_broadcast": False,
            },
        ]
        result = apply_page_live_markers(
            events,
            "LIVE 11:25 Азия чемпионаты LIVE 17:55 Футбол Ұлытау – Алта",
        )
        self.assertTrue(result[0]["is_live_broadcast"])
        self.assertTrue(result[1]["is_live_broadcast"])
        self.assertEqual(result[0]["live_evidence_method"], "qazsport_page_live_text")
        self.assertEqual(result[1]["live_evidence_method"], "qazsport_page_live_text")


if __name__ == "__main__":
    unittest.main()

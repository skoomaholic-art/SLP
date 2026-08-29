import unittest

from models import SportEvent
from services.event_contract import (
    REQUIRED_EVENT_FIELDS,
    build_sport_event,
    validate_sport_event,
)


class EventContractTests(unittest.TestCase):

    def test_build_sport_event_creates_universal_contract(self):
        event = build_sport_event(
            {
                "date": "2026-08-29",
                "time": "20:00",
                "channel": "Qazsport",
                "sport": "Футбол",
                "tournament": "QJ League",
                "title": "Тараз - Тобол",
                "raw_title": "Футбол. QJ League Тараз - Тобол",
                "is_live": True,
                "estimated_broadcast_end_date": "2026-08-29",
                "estimated_broadcast_end": "22:00",
                "end_estimation_method": "next_program",
                "end_confidence": "high",
            },
            source="qazsport",
            source_url="https://qazsporttv.kz/ru/program/2026-08-29",
        )

        self.assertIsInstance(event, dict)
        self.assertEqual(event["source"], "qazsport")
        self.assertEqual(event["channel"], "Qazsport")
        self.assertEqual(event["title"], "Тараз - Тобол")

        for field in REQUIRED_EVENT_FIELDS:
            self.assertIn(field, event)

    def test_contract_preserves_adapter_specific_fields(self):
        event = build_sport_event(
            {
                "date": "2026-08-29",
                "time": "20:00",
                "channel": "Qazsport",
                "sport": "Футбол",
                "tournament": "QJ League",
                "title": "Тараз - Тобол",
                "raw_title": "Футбол. QJ League Тараз - Тобол",
                "is_live": True,
                "schedule_offset": 1200,
            },
            source="qazsport",
            source_url="https://qazsporttv.kz/ru/program/2026-08-29",
        )

        self.assertEqual(event["schedule_offset"], 1200)
        self.assertIsNone(event["estimated_broadcast_end_date"])
        self.assertIsNone(event["estimated_broadcast_end"])
        self.assertEqual(event["end_confidence"], "unknown")

    def test_title_falls_back_to_raw_title(self):
        event = build_sport_event(
            {
                "date": "2026-08-29",
                "time": "09:00",
                "channel": "Future Channel",
                "raw_title": "Some sports event",
                "is_live": False,
            },
            source="future_source",
            source_url="https://example.com/schedule",
        )

        self.assertEqual(event["title"], "Some sports event")
        self.assertEqual(event["sport"], "")
        self.assertEqual(event["tournament"], "")

    def test_invalid_date_is_rejected(self):
        event = {
            "source": "qazsport",
            "source_url": "https://qazsporttv.kz/ru/program",
            "channel": "Qazsport",
            "date": "29-08-2026",
            "time": "20:00",
            "sport": "Футбол",
            "tournament": "QJ League",
            "title": "Тараз - Тобол",
            "raw_title": "Тараз - Тобол",
            "is_live": True,
            "estimated_broadcast_end_date": None,
            "estimated_broadcast_end": None,
            "end_estimation_method": None,
            "end_confidence": "unknown",
        }

        with self.assertRaises(ValueError):
            validate_sport_event(event)

    def test_model_is_typed_dict_compatible_at_runtime(self):
        event: SportEvent = build_sport_event(
            {
                "date": "2026-08-29",
                "time": "20:00",
                "channel": "Qazsport",
                "raw_title": "Матч",
                "is_live": True,
            },
            source="qazsport",
            source_url="https://qazsporttv.kz/ru/program",
        )

        self.assertEqual(event["title"], "Матч")


if __name__ == "__main__":
    unittest.main()

import unittest
from datetime import datetime

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
                "is_live_broadcast": True,
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
        self.assertTrue(event["is_live_broadcast"])
        self.assertEqual(event["timezone"], "Asia/Almaty")
        self.assertIsNotNone(datetime.fromisoformat(event["start_time"]).utcoffset())
        self.assertIsNotNone(datetime.fromisoformat(event["end_time"]).utcoffset())

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
                "is_live_broadcast": True,
                "schedule_offset": 1200,
            },
            source="qazsport",
            source_url="https://qazsporttv.kz/ru/program/2026-08-29",
        )

        self.assertEqual(event["schedule_offset"], 1200)
        self.assertIsNone(event["estimated_broadcast_end_date"])
        self.assertIsNone(event["estimated_broadcast_end"])
        self.assertEqual(event["end_estimation_method"], "fallback_duration")
        self.assertEqual(event["end_confidence"], "low")

    def test_legacy_is_live_is_migrated_without_flag_loss(self):
        event = build_sport_event(
            {
                "date": "2026-08-29",
                "time": "09:00",
                "channel": "Legacy Channel",
                "raw_title": "LIVE Some sports event",
                "is_live": True,
            },
            source="legacy_source",
            source_url="https://example.com/schedule",
        )
        self.assertTrue(event["is_live_broadcast"])
        self.assertTrue(event["is_live"])

    def test_title_falls_back_to_raw_title(self):
        event = build_sport_event(
            {
                "date": "2026-08-29",
                "time": "09:00",
                "channel": "Future Channel",
                "raw_title": "Some sports event",
                "is_live_broadcast": False,
            },
            source="future_source",
            source_url="https://example.com/schedule",
        )
        self.assertEqual(event["title"], "Some sports event")
        self.assertEqual(event["sport"], "")
        self.assertEqual(event["tournament"], "")

    def test_naive_explicit_start_time_is_rejected(self):
        with self.assertRaises(ValueError):
            build_sport_event(
                {
                    "date": "2026-08-29",
                    "time": "09:00",
                    "start_time": "2026-08-29T09:00:00",
                    "channel": "Bad Channel",
                    "raw_title": "Event",
                    "is_live_broadcast": False,
                },
                source="bad",
                source_url="https://example.com",
            )

    def test_model_is_typed_dict_compatible_at_runtime(self):
        event: SportEvent = build_sport_event(
            {
                "date": "2026-08-29",
                "time": "20:00",
                "channel": "Qazsport",
                "raw_title": "Матч",
                "is_live_broadcast": True,
            },
            source="qazsport",
            source_url="https://qazsporttv.kz/ru/program",
        )
        self.assertEqual(event["title"], "Матч")


if __name__ == "__main__":
    unittest.main()

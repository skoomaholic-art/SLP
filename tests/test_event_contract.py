import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from models import SportEvent
from services.event_contract import (
    REQUIRED_EVENT_FIELDS,
    build_sport_event,
    validate_sport_event,
)


KZ = ZoneInfo("Asia/Almaty")


class EventContractTests(unittest.TestCase):
    def build_event(self, **overrides):
        data = {
            "date": "2026-09-11",
            "time": "20:00",
            "channel": "Qazsport",
            "sport": "Футбол",
            "tournament": "QJ League",
            "title": "Тараз - Тобол",
            "raw_title": "LIVE Футбол. QJ League Тараз - Тобол",
            "is_live_broadcast": True,
            "estimated_broadcast_end_date": "2026-09-11",
            "estimated_broadcast_end": "22:00",
            "end_estimation_method": "next_program",
            "end_confidence": "high",
        }
        data.update(overrides)
        return build_sport_event(
            data,
            source="qazsport",
            source_url="https://qazsporttv.kz/ru/program/2026-09-11",
        )

    def test_build_sport_event_creates_aware_contract(self):
        event = self.build_event()
        self.assertIsInstance(event, dict)
        self.assertEqual(event["source"], "qazsport")
        self.assertEqual(event["channel"], "Qazsport")
        self.assertEqual(event["normalized_title"], "Тараз - Тобол")
        self.assertEqual(event["timezone"], "Asia/Almaty")
        self.assertEqual(event["start_time"].utcoffset().total_seconds(), 5 * 3600)
        self.assertEqual(event["end_time"].utcoffset().total_seconds(), 5 * 3600)
        self.assertTrue(event["is_live_broadcast"])
        self.assertTrue(event["event_key"])
        self.assertIsNotNone(event["updated_at"].tzinfo)

        for field in REQUIRED_EVENT_FIELDS:
            self.assertIn(field, event)

    def test_legacy_is_live_is_migrated_but_not_reinterpreted(self):
        event = self.build_event(is_live_broadcast=None, is_live=True)
        # Explicit None is false by design; old adapters should omit the new
        # field entirely while migrating.
        self.assertFalse(event["is_live_broadcast"])

        legacy = {
            "date": "2026-09-11",
            "time": "20:00",
            "channel": "Qazsport",
            "raw_title": "LIVE Матч",
            "is_live": True,
        }
        event = build_sport_event(
            legacy,
            source="qazsport",
            source_url="https://qazsporttv.kz/ru/program",
        )
        self.assertTrue(event["is_live_broadcast"])
        self.assertTrue(event["is_live"])

    def test_event_key_is_stable_for_same_broadcast(self):
        first = self.build_event()
        second = self.build_event()
        self.assertEqual(first["event_key"], second["event_key"])

    def test_adapter_specific_fields_are_preserved(self):
        event = self.build_event(schedule_offset=1200)
        self.assertEqual(event["schedule_offset"], 1200)

    def test_midnight_legacy_end_rolls_to_next_day(self):
        event = self.build_event(
            date="2026-09-11",
            time="23:20",
            estimated_broadcast_end_date="2026-09-11",
            estimated_broadcast_end="01:30",
        )
        self.assertEqual(event["end_time"].isoformat(), "2026-09-12T01:30:00+05:00")
        self.assertEqual(event["estimated_broadcast_end_date"], "2026-09-12")

    def test_explicit_naive_datetime_is_rejected(self):
        with self.assertRaises(ValueError):
            self.build_event(
                start_time=datetime(2026, 9, 11, 20, 0),
                end_time=datetime(2026, 9, 11, 22, 0, tzinfo=KZ),
            )

    def test_explicit_end_before_start_is_rejected(self):
        with self.assertRaises(ValueError):
            self.build_event(
                start_time=datetime(2026, 9, 11, 20, 0, tzinfo=KZ),
                end_time=datetime(2026, 9, 11, 19, 0, tzinfo=KZ),
            )

    def test_validate_rejects_wrong_timezone_name(self):
        event: SportEvent = self.build_event()
        event["timezone"] = "UTC"
        with self.assertRaises(ValueError):
            validate_sport_event(event)


if __name__ == "__main__":
    unittest.main()

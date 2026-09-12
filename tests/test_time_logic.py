import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from services.time_logic import (
    get_event_status,
    get_scheduled_datetimes,
    get_time_window_text,
)

KZ = ZoneInfo("Asia/Almaty")


class TimeLogicTests(unittest.TestCase):
    def test_same_day_schedule_is_timezone_aware(self):
        event = {
            "date": "2026-08-29",
            "time": "10:55",
            "estimated_broadcast_end_date": "2026-08-29",
            "estimated_broadcast_end": "13:00",
            "sport": "Волейбол",
        }

        start, end = get_scheduled_datetimes(event)

        self.assertEqual(start.strftime("%Y-%m-%d %H:%M"), "2026-08-29 10:55")
        self.assertEqual(end.strftime("%Y-%m-%d %H:%M"), "2026-08-29 13:00")
        self.assertIsNotNone(start.utcoffset())
        self.assertIsNotNone(end.utcoffset())
        self.assertEqual(get_time_window_text(event), "10:55–13:00")

    def test_midnight_rollover_when_end_date_is_wrong(self):
        event = {
            "date": "2026-08-29",
            "time": "23:20",
            "estimated_broadcast_end_date": "2026-08-29",
            "estimated_broadcast_end": "01:30",
            "sport": "Футбол",
        }
        _, end = get_scheduled_datetimes(event)
        self.assertEqual(end.strftime("%Y-%m-%d %H:%M"), "2026-08-30 01:30")
        self.assertEqual(
            get_time_window_text(event),
            "29 августа, 23:20–30 августа, 01:30",
        )

    def test_last_event_gets_fallback_end(self):
        event = {
            "date": "2026-08-29",
            "time": "23:20",
            "estimated_broadcast_end_date": None,
            "estimated_broadcast_end": None,
            "sport": "Футбол",
        }
        start, end = get_scheduled_datetimes(event)
        self.assertEqual(int((end - start).total_seconds() / 60), 150)
        self.assertEqual(end.strftime("%Y-%m-%d %H:%M"), "2026-08-30 01:50")

    def test_upcoming_status(self):
        event = {
            "date": "2026-08-29",
            "time": "18:00",
            "estimated_broadcast_end_date": "2026-08-29",
            "estimated_broadcast_end": "20:00",
            "is_live_broadcast": True,
        }
        now = datetime(2026, 8, 29, 17, 59, tzinfo=KZ)
        self.assertEqual(get_event_status(event, now), "upcoming")

    def test_live_now_requires_source_live_flag(self):
        event = {
            "date": "2026-08-29",
            "time": "18:00",
            "estimated_broadcast_end_date": "2026-08-29",
            "estimated_broadcast_end": "20:00",
            "is_live_broadcast": True,
        }
        now = datetime(2026, 8, 29, 19, 0, tzinfo=KZ)
        self.assertEqual(get_event_status(event, now), "live_now")

    def test_ordinary_epg_in_current_window_is_not_live(self):
        event = {
            "date": "2026-08-29",
            "time": "18:00",
            "estimated_broadcast_end_date": "2026-08-29",
            "estimated_broadcast_end": "20:00",
            "is_live_broadcast": False,
        }
        now = datetime(2026, 8, 29, 19, 0, tzinfo=KZ)
        self.assertEqual(get_event_status(event, now), "current")

    def test_finished_status(self):
        event = {
            "date": "2026-08-29",
            "time": "18:00",
            "estimated_broadcast_end_date": "2026-08-29",
            "estimated_broadcast_end": "20:00",
            "is_live_broadcast": True,
        }
        now = datetime(2026, 8, 29, 20, 0, tzinfo=KZ)
        self.assertEqual(get_event_status(event, now), "finished")

    def test_external_time_does_not_affect_status(self):
        event = {
            "date": "2026-08-29",
            "time": "18:00",
            "estimated_broadcast_end_date": "2026-08-29",
            "estimated_broadcast_end": "20:00",
            "is_live_broadcast": True,
            "external_date_kz": "2026-08-29",
            "external_time_kz": "23:00",
        }
        now = datetime(2026, 8, 29, 19, 0, tzinfo=KZ)
        self.assertEqual(get_event_status(event, now), "live_now")


if __name__ == "__main__":
    unittest.main()

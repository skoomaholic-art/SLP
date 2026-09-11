import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from services.time_logic import (
    get_event_status,
    get_scheduled_datetimes,
    get_time_window_text,
)

KZ = ZoneInfo("Asia/Almaty")


def event(**overrides):
    value = {
        "date": "2026-09-11",
        "time": "18:00",
        "estimated_broadcast_end_date": "2026-09-11",
        "estimated_broadcast_end": "20:00",
        "sport": "Футбол",
        "is_live_broadcast": True,
    }
    value.update(overrides)
    return value


class TimeLogicTests(unittest.TestCase):
    def test_same_day_schedule(self):
        start, end = get_scheduled_datetimes(event())
        self.assertEqual(start.isoformat(), "2026-09-11T18:00:00+05:00")
        self.assertEqual(end.isoformat(), "2026-09-11T20:00:00+05:00")
        self.assertEqual(get_time_window_text(event()), "18:00–20:00")

    def test_midnight_rollover_when_legacy_end_date_is_wrong(self):
        item = event(
            time="23:20",
            estimated_broadcast_end="01:30",
        )
        _, end = get_scheduled_datetimes(item)
        self.assertEqual(end.isoformat(), "2026-09-12T01:30:00+05:00")
        self.assertEqual(
            get_time_window_text(item),
            "11 сентября, 23:20–12 сентября, 01:30",
        )

    def test_last_event_gets_bounded_sport_fallback(self):
        item = event(
            time="23:20",
            estimated_broadcast_end_date=None,
            estimated_broadcast_end=None,
        )
        start, end = get_scheduled_datetimes(item)
        self.assertEqual(int((end - start).total_seconds() / 60), 150)
        self.assertEqual(end.isoformat(), "2026-09-12T01:50:00+05:00")

    def test_future_source_live_is_upcoming_not_live_now(self):
        now = datetime(2026, 9, 11, 17, 59, tzinfo=KZ)
        self.assertEqual(get_event_status(event(), now), "upcoming")

    def test_source_live_inside_window_is_live_now(self):
        now = datetime(2026, 9, 11, 19, 0, tzinfo=KZ)
        self.assertEqual(get_event_status(event(), now), "live_now")

    def test_source_live_after_end_is_finished(self):
        now = datetime(2026, 9, 11, 20, 0, tzinfo=KZ)
        self.assertEqual(get_event_status(event(), now), "finished")

    def test_regular_epg_inside_window_is_on_air_not_live_now(self):
        now = datetime(2026, 9, 11, 19, 0, tzinfo=KZ)
        self.assertEqual(
            get_event_status(event(is_live_broadcast=False), now),
            "on_air",
        )

    def test_naive_now_is_rejected(self):
        with self.assertRaises(ValueError):
            get_event_status(event(), datetime(2026, 9, 11, 19, 0))

    def test_external_time_does_not_affect_status(self):
        item = event(
            external_date_kz="2026-09-11",
            external_time_kz="23:00",
        )
        now = datetime(2026, 9, 11, 19, 0, tzinfo=KZ)
        self.assertEqual(get_event_status(item, now), "live_now")


if __name__ == "__main__":
    unittest.main()

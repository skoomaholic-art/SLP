import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from services.event_status import (
    diagnose_live_candidate,
    get_event_end,
    get_event_status,
    is_live_now,
)

KZ = ZoneInfo("Asia/Almaty")


def event(start_hour=14, end_hour=16, live=True):
    return {
        "channel": "Qazsport",
        "title": "Тестовый матч",
        "sport": "Футбол",
        "start_time": datetime(2026, 9, 11, start_hour, 0, tzinfo=KZ),
        "end_time": datetime(2026, 9, 11, end_hour, 0, tzinfo=KZ),
        "is_live_broadcast": live,
    }


class EventStatusTests(unittest.TestCase):
    def test_live_candidate_is_included_inside_window(self):
        item = event()
        now = datetime(2026, 9, 11, 14, 36, tzinfo=KZ)
        diagnostic = diagnose_live_candidate(item, now)
        self.assertTrue(diagnostic["included"])
        self.assertEqual(diagnostic["reason"], "live_now")
        self.assertEqual(diagnostic["status"], "live_now")
        self.assertTrue(is_live_now(item, now))

    def test_future_candidate_reason_is_explicit(self):
        item = event(start_hour=18, end_hour=20)
        now = datetime(2026, 9, 11, 14, 0, tzinfo=KZ)
        diagnostic = diagnose_live_candidate(item, now)
        self.assertFalse(diagnostic["included"])
        self.assertEqual(diagnostic["reason"], "starts_in_future")
        self.assertEqual(get_event_status(item, now), "upcoming")

    def test_finished_candidate_reason_is_explicit(self):
        item = event(start_hour=10, end_hour=12)
        now = datetime(2026, 9, 11, 14, 0, tzinfo=KZ)
        diagnostic = diagnose_live_candidate(item, now)
        self.assertFalse(diagnostic["included"])
        self.assertEqual(diagnostic["reason"], "event_finished")

    def test_regular_program_is_never_live_now(self):
        item = event(live=False)
        now = datetime(2026, 9, 11, 15, 0, tzinfo=KZ)
        diagnostic = diagnose_live_candidate(item, now)
        self.assertFalse(diagnostic["included"])
        self.assertEqual(diagnostic["reason"], "source_not_live")
        self.assertEqual(get_event_status(item, now), "on_air")

    def test_next_same_channel_program_can_define_end(self):
        item = event()
        item["end_time"] = None
        next_item = {
            "channel": "Qazsport",
            "start_time": datetime(2026, 9, 11, 15, 30, tzinfo=KZ),
        }
        self.assertEqual(
            get_event_end(item, next_item).isoformat(),
            "2026-09-11T15:30:00+05:00",
        )

    def test_fallback_does_not_keep_event_live_forever(self):
        item = event()
        item["end_time"] = None
        item["sport"] = "Неизвестный спорт"
        now = datetime(2026, 9, 11, 17, 0, tzinfo=KZ)
        self.assertEqual(get_event_status(item, now), "finished")

    def test_invalid_timezone_is_visible_in_diagnostic(self):
        item = event()
        item["start_time"] = datetime(2026, 9, 11, 14, 0)
        now = datetime(2026, 9, 11, 14, 36, tzinfo=KZ)
        diagnostic = diagnose_live_candidate(item, now)
        self.assertFalse(diagnostic["included"])
        self.assertEqual(diagnostic["reason"], "invalid_timezone")


if __name__ == "__main__":
    unittest.main()

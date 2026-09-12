import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from services.time_logic import get_event_status


KZ = ZoneInfo("Asia/Almaty")


class LiveRegressionTests(unittest.TestCase):
    def test_qazsport_khl_broadcast_is_live_inside_window(self):
        event = {
            "date": "2026-09-12",
            "time": "16:25",
            "estimated_broadcast_end_date": "2026-09-12",
            "estimated_broadcast_end": "18:55",
            "sport": "Хоккей",
        }
        now = datetime(2026, 9, 12, 18, 0, tzinfo=KZ)
        self.assertEqual(get_event_status(event, now=now), "live")

    def test_sportplus_aca_broadcast_is_live_until_midnight(self):
        event = {
            "date": "2026-09-12",
            "time": "17:00",
            "estimated_broadcast_end_date": "2026-09-13",
            "estimated_broadcast_end": "00:00",
            "sport": "MMA",
        }
        now = datetime(2026, 9, 12, 18, 0, tzinfo=KZ)
        self.assertEqual(get_event_status(event, now=now), "live")

    def test_start_is_inclusive_and_end_is_exclusive(self):
        event = {
            "date": "2026-09-12",
            "time": "17:00",
            "estimated_broadcast_end_date": "2026-09-12",
            "estimated_broadcast_end": "19:00",
            "sport": "MMA",
        }
        start = datetime(2026, 9, 12, 17, 0, tzinfo=KZ)
        end = datetime(2026, 9, 12, 19, 0, tzinfo=KZ)
        self.assertEqual(get_event_status(event, now=start), "live")
        self.assertEqual(get_event_status(event, now=end), "finished")


if __name__ == "__main__":
    unittest.main()

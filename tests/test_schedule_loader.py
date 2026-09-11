import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

from services.schedule_loader import calculate_actual_horizon, nearest_monday

KZ = ZoneInfo("Asia/Almaty")


class ScheduleLoaderTests(unittest.TestCase):
    def test_friday_minimum_horizon_is_nearest_monday(self):
        self.assertEqual(
            nearest_monday(date(2026, 9, 11)),
            date(2026, 9, 14),
        )

    def test_monday_includes_current_monday(self):
        self.assertEqual(
            nearest_monday(date(2026, 9, 14)),
            date(2026, 9, 14),
        )

    def test_horizon_extends_to_furthest_published_live(self):
        events = [
            {
                "start_time": datetime(2026, 9, 15, 18, 0, tzinfo=KZ),
                "is_live_broadcast": True,
            },
            {
                "start_time": datetime(2026, 9, 16, 20, 0, tzinfo=KZ),
                "is_live_broadcast": True,
            },
            {
                "start_time": datetime(2026, 9, 18, 20, 0, tzinfo=KZ),
                "is_live_broadcast": False,
            },
        ]
        self.assertEqual(
            calculate_actual_horizon(
                events,
                today=date(2026, 9, 11),
                minimum_horizon=date(2026, 9, 14),
            ),
            date(2026, 9, 16),
        )

    def test_non_live_epg_does_not_extend_horizon(self):
        events = [
            {
                "start_time": datetime(2026, 9, 20, 20, 0, tzinfo=KZ),
                "is_live_broadcast": False,
            }
        ]
        self.assertEqual(
            calculate_actual_horizon(
                events,
                today=date(2026, 9, 11),
                minimum_horizon=date(2026, 9, 14),
            ),
            date(2026, 9, 14),
        )


if __name__ == "__main__":
    unittest.main()

import logging
import unittest
from datetime import datetime
from io import StringIO
from zoneinfo import ZoneInfo

from services.event_status import (
    evaluate_live_candidate,
    filter_live_now,
    get_event_status,
    get_scheduled_datetimes,
)

KZ = ZoneInfo("Asia/Almaty")


def event(
    *,
    time: str,
    end: str | None = None,
    live: bool = True,
    title: str = "Матч",
    sport: str = "Футбол",
) -> dict:
    return {
        "date": "2026-09-12",
        "time": time,
        "channel": "Qazsport",
        "title": title,
        "raw_title": title,
        "sport": sport,
        "is_live_broadcast": live,
        "estimated_broadcast_end_date": "2026-09-12" if end else None,
        "estimated_broadcast_end": end,
    }


class EventStatusTests(unittest.TestCase):
    def test_future_live_broadcast_is_not_live_now(self):
        candidate = event(time="18:55", end="21:00")
        now = datetime(2026, 9, 12, 14, 0, tzinfo=KZ)
        decision = evaluate_live_candidate(candidate, now)
        self.assertFalse(decision.included)
        self.assertEqual(decision.status, "upcoming")
        self.assertEqual(decision.reason, "starts_in_future")

    def test_current_source_live_is_included(self):
        candidate = event(time="14:00", end="16:00")
        now = datetime(2026, 9, 12, 14, 36, tzinfo=KZ)
        decision = evaluate_live_candidate(candidate, now)
        self.assertTrue(decision.included)
        self.assertEqual(decision.status, "live_now")
        self.assertEqual(get_event_status(candidate, now), "live_now")

    def test_finished_live_is_excluded(self):
        candidate = event(time="10:00", end="12:00")
        now = datetime(2026, 9, 12, 14, 36, tzinfo=KZ)
        decision = evaluate_live_candidate(candidate, now)
        self.assertFalse(decision.included)
        self.assertEqual(decision.reason, "event_finished")

    def test_current_epg_without_live_marker_is_not_live_now(self):
        candidate = event(time="14:00", end="16:00", live=False)
        now = datetime(2026, 9, 12, 14, 36, tzinfo=KZ)
        self.assertEqual(get_event_status(candidate, now), "current")
        self.assertEqual(filter_live_now([candidate], now), [])

    def test_next_same_channel_program_is_used_when_end_missing(self):
        candidate = event(time="14:00", end=None)
        next_program = event(time="15:30", end="16:00", live=False, title="Новости")
        start, end = get_scheduled_datetimes(candidate, next_event=next_program)
        self.assertEqual(start.strftime("%H:%M"), "14:00")
        self.assertEqual(end.strftime("%H:%M"), "15:30")

    def test_missing_end_uses_sport_fallback_not_infinite_live(self):
        candidate = event(time="14:00", end=None, sport="Футбол")
        now_live = datetime(2026, 9, 12, 15, 0, tzinfo=KZ)
        now_late = datetime(2026, 9, 12, 20, 0, tzinfo=KZ)
        self.assertEqual(get_event_status(candidate, now_live), "live_now")
        self.assertEqual(get_event_status(candidate, now_late), "finished")

    def test_absurd_next_program_gap_is_clamped_for_live(self):
        candidate = event(time="10:00", end="23:00")
        _, end = get_scheduled_datetimes(candidate)
        self.assertEqual(end.strftime("%H:%M"), "16:00")

    def test_filter_logs_reason_for_each_source_live_candidate(self):
        stream = StringIO()
        logger = logging.getLogger("test.slp.live")
        logger.handlers = []
        handler = logging.StreamHandler(stream)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False

        now = datetime(2026, 9, 12, 14, 36, tzinfo=KZ)
        events = [
            event(time="10:00", end="12:00", title="Finished"),
            event(time="14:00", end="16:00", title="Current"),
            event(time="18:00", end="20:00", title="Future"),
        ]
        result = filter_live_now(events, now, logger=logger)
        text = stream.getvalue()

        self.assertEqual([item["title"] for item in result], ["Current"])
        self.assertIn("reason=event_finished", text)
        self.assertIn("reason=live_now", text)
        self.assertIn("reason=starts_in_future", text)
        self.assertIn("included=True", text)


if __name__ == "__main__":
    unittest.main()

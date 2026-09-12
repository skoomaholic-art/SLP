import unittest

from bot.formatters import build_schedule_messages, compact_event_line, display_title


def make_event(channel="Qazsport"):
    return {
        "source": "qazsport",
        "source_url": "https://example.test",
        "channel": channel,
        "date": "2099-09-12",
        "time": "18:00",
        "sport": "Футбол",
        "tournament": "QJ League",
        "title": "Тараз – Тобол",
        "is_live": True,
        "raw_title": "Футбол. QJ League Тараз – Тобол",
        "estimated_broadcast_end_date": "2099-09-12",
        "estimated_broadcast_end": "20:00",
        "end_estimation_method": "next_program",
        "end_confidence": "high",
    }


class ScheduleCompactViewTests(unittest.TestCase):
    def test_match_title_stays_compact(self):
        self.assertEqual(display_title(make_event()), "Тараз – Тобол")

    def test_line_contains_time_title_and_channel(self):
        line = compact_event_line(make_event())
        self.assertIn("18:00–20:00", line)
        self.assertIn("Тараз – Тобол", line)
        self.assertIn("Qazsport", line)

    def test_schedule_has_current_status_legend(self):
        text = "\n".join(build_schedule_messages([make_event()]))
        self.assertIn("🔴 LIVE · 🟡 SOON · ⚪ OVER", text)
        self.assertNotIn("Step 71.2", text)
        self.assertNotIn("Parser v1 RC", text)


if __name__ == "__main__":
    unittest.main()

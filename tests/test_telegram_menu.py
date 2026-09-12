import unittest

from bot.formatters import build_schedule_messages
from bot.handlers import (
    TELEGRAM_SCHEDULE_EVENT_LIMIT,
    _schedule_view_events,
    _send_messages,
)
from bot.keyboards import MAIN_KEYBOARD


class FakeMessage:
    def __init__(self):
        self.sent = []

    async def answer(self, text, **kwargs):
        self.sent.append((text, kwargs.get("reply_markup")))


class FakeScheduleService:
    def __init__(self, events):
        self.events = events

    def get_events(self):
        return list(self.events)


class TelegramMenuTests(unittest.IsolatedAsyncioTestCase):
    async def test_result_menu_is_attached_to_last_chunk(self):
        message = FakeMessage()
        await _send_messages(message, ["part 1", "part 2"], reply_markup=MAIN_KEYBOARD)
        self.assertEqual(message.sent[0], ("part 1", None))
        self.assertEqual(message.sent[1], ("part 2", MAIN_KEYBOARD))

    async def test_schedule_view_is_bounded_before_sending(self):
        events = [{"index": index} for index in range(TELEGRAM_SCHEDULE_EVENT_LIMIT + 25)]
        shown, total = _schedule_view_events(FakeScheduleService(events))
        self.assertEqual(total, len(events))
        self.assertEqual(len(shown), TELEGRAM_SCHEDULE_EVENT_LIMIT)
        self.assertEqual(shown, events[:TELEGRAM_SCHEDULE_EVENT_LIMIT])

    async def test_schedule_header_explains_truncation(self):
        event = {
            "source": "qazsport",
            "channel": "Qazsport",
            "date": "2099-09-12",
            "time": "18:00",
            "title": "Тараз – Тобол",
            "sport": "Футбол",
            "tournament": "QJ League",
            "estimated_broadcast_end_date": "2099-09-12",
            "estimated_broadcast_end": "20:00",
        }
        text = "\n".join(build_schedule_messages([event], total_count=100))
        self.assertIn("событий: 100", text)
        self.assertIn("показано: 1", text)
        self.assertIn("Полный горизонт расписания доступен через XLSX", text)


if __name__ == "__main__":
    unittest.main()

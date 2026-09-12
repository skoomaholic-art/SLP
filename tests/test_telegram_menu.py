import unittest

from bot.handlers import _send_messages
from bot.keyboards import MAIN_KEYBOARD


class FakeMessage:
    def __init__(self):
        self.sent = []

    async def answer(self, text, **kwargs):
        self.sent.append((text, kwargs.get("reply_markup")))


class TelegramMenuTests(unittest.IsolatedAsyncioTestCase):
    async def test_result_menu_is_attached_to_last_chunk(self):
        message = FakeMessage()
        await _send_messages(message, ["part 1", "part 2"], reply_markup=MAIN_KEYBOARD)
        self.assertEqual(message.sent[0], ("part 1", None))
        self.assertEqual(message.sent[1], ("part 2", MAIN_KEYBOARD))


if __name__ == "__main__":
    unittest.main()

import ast
import unittest
from pathlib import Path


MAIN = Path(__file__).resolve().parents[1] / "main.py"


class TelegramNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = MAIN.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def function_source(self, name):
        node = next(
            item for item in self.tree.body
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
            and item.name == name
        )
        return ast.get_source_segment(self.source, node) or ""

    def test_main_menu_has_primary_actions(self):
        source = self.function_source("build_main_keyboard")
        for callback in ("schedule", "live", "export_schedule", "status"):
            self.assertIn(f'callback_data="{callback}"', source)
        self.assertIn("_notification_button(chat_id)", source)
        self.assertIn('callback_data="notifications"', self.function_source("_notification_button"))

    def test_schedule_result_keeps_navigation_at_bottom(self):
        source = self.function_source("build_schedule_keyboard")
        for callback in ("schedule", "live", "export_schedule", "status", "menu"):
            self.assertIn(f'callback_data="{callback}"', source)
        self.assertIn('callback_data=f"schedule_ch:{channel}"', source)

    def test_channel_schedule_keeps_navigation_at_bottom(self):
        source = self.function_source("build_channel_schedule_keyboard")
        self.assertIn('callback_data="schedule"', source)
        self.assertIn('callback_data=f"schedule_ch:{channel}"', source)
        self.assertIn('callback_data="live"', source)
        self.assertIn('callback_data="menu"', source)

    def test_live_filter_requires_official_evidence(self):
        source = self.function_source("get_confirmed_live_events")
        self.assertIn("is_confirmed_direct_event(event)", source)
        self.assertIn('get_event_status(event) == "live"', source)

    def test_live_result_keeps_navigation_at_bottom(self):
        source = self.function_source("build_live_keyboard")
        for callback in ("details_live", "live", "schedule", "export_schedule", "menu"):
            self.assertIn(f'callback_data="{callback}"', source)

    def test_details_put_keyboard_on_last_message(self):
        source = self.function_source("show_details")
        self.assertIn("reply_markup=keyboard if len(messages) == 1 else None", source)
        self.assertIn("index == len(messages) - 1", source)


    def test_background_refresh_ignores_identical_message_error(self):
        source = self.function_source("background_verify_and_refresh")
        self.assertIn("except TelegramBadRequest as error", source)
        self.assertIn("message is not modified", source)

    def test_menu_callback_creates_fresh_bottom_menu(self):
        source = self.function_source("menu_callback")
        self.assertIn("build_main_keyboard(callback.message.chat.id)", source)


if __name__ == "__main__":
    unittest.main()

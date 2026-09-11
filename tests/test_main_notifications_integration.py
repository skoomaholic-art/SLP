import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN_PATH = ROOT / "main.py"
GITIGNORE_PATH = ROOT / ".gitignore"


class MainNotificationsIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = MAIN_PATH.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_main_imports_schedule_watch_service(self):
        imported = set()
        for node in ast.walk(self.tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module == "services.schedule_watch"
            ):
                imported.update(alias.name for alias in node.names)

        self.assertTrue(
            {
                "format_schedule_change",
                "stable_event_identity",
                "build_schedule_snapshot",
                "diff_schedule_snapshots",
                "get_previous_snapshot",
                "get_subscribers",
                "is_subscribed",
                "set_subscription",
                "update_snapshot",
            }.issubset(imported)
        )

    def test_notification_button_and_callback_exist(self):
        self.assertIn('callback_data="notifications"', self.source)
        async_functions = {
            node.name
            for node in self.tree.body
            if isinstance(node, ast.AsyncFunctionDef)
        }
        self.assertIn("notifications_callback", async_functions)

    def test_watcher_forces_real_source_refresh(self):
        function = next(
            node
            for node in self.tree.body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "check_schedule_changes_once"
        )
        calls = [
            node
            for node in ast.walk(function)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "load_schedule_events"
        ]
        self.assertEqual(len(calls), 1)
        keywords = {item.arg: item.value for item in calls[0].keywords}
        self.assertTrue(isinstance(keywords["force_refresh"], ast.Constant))
        self.assertTrue(keywords["force_refresh"].value)
        self.assertTrue(isinstance(keywords["return_errors"], ast.Constant))
        self.assertTrue(keywords["return_errors"].value)

    def test_source_error_prevents_snapshot_diff(self):
        function_source = ast.get_source_segment(
            self.source,
            next(
                node
                for node in self.tree.body
                if isinstance(node, ast.AsyncFunctionDef)
                and node.name == "check_schedule_changes_once"
            ),
        )
        self.assertIn("if source_errors", function_source)
        self.assertIn("return []", function_source)

    def test_main_starts_background_notification_loop(self):
        main_function = next(
            node
            for node in self.tree.body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "main"
        )
        self.assertTrue(
            any(
                isinstance(node, ast.Name)
                and node.id == "notification_watch_loop"
                for node in ast.walk(main_function)
            )
        )

    def test_runtime_files_are_ignored_by_git(self):
        gitignore = GITIGNORE_PATH.read_text(encoding="utf-8")
        self.assertIn("slp_state.json", gitignore)
        self.assertIn("logs.txt", gitignore)
        self.assertIn("*.zip", gitignore)

    def test_details_always_explain_confidence_when_time_missing(self):
        self.assertIn(
            '📊 Уровень доверия: Не рассчитывается',
            self.source,
        )
        self.assertIn(
            'расхождение больше 30 минут',
            self.source,
        )

    def test_details_show_live_evidence(self):
        self.assertIn('📡 Основание LIVE:', self.source)
        self.assertIn(
            'LIVE-пометка в EPG провайдера',
            self.source,
        )

    def test_schedule_lines_show_channel_without_opening_details(self):
        self.assertIn('f"{get_schedule_display_title(event)} | {channel}"', self.source)
        self.assertIn('event.get("channel") or "Канал не указан"', self.source)

    def test_schedule_has_attention_badge_for_suspicious_accuracy(self):
        self.assertIn('attention = "⚠️" if accuracy.get("needs_attention") else ""', self.source)
        self.assertIn('⚠️ проверить время', self.source)
        self.assertIn('verification.get("time_rejected", False)', self.source)

    def test_schedule_builds_self_explanatory_non_match_titles(self):
        self.assertIn('def get_schedule_display_title(event):', self.source)
        self.assertIn('for part in (sport, tournament, title):', self.source)
        self.assertIn('text.replace("Grand slam", "Grand Slam")', self.source)

    def test_relevant_results_label_is_clear(self):
        self.assertIn('🎯 Релевантных результатов:', self.source)
        self.assertNotIn('🎯 По событию:', self.source)


if __name__ == "__main__":
    unittest.main()

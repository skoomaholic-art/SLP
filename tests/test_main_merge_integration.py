import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_PATH = ROOT / "bot_app.py"


class MainMergeIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = APP_PATH.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_bot_app_imports_merge_service(self):
        imported = set()
        for node in ast.walk(self.tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module == "services.schedule_merge"
            ):
                imported.update(alias.name for alias in node.names)
        self.assertTrue(
            {
                "group_simulcasts",
                "merge_source_schedules",
                "unique_channels",
            }.issubset(imported)
        )

    def test_load_schedule_uses_merge_source_schedules(self):
        load_function = next(
            node
            for node in self.tree.body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "load_schedule_events"
        )
        calls = {
            node.func.id
            for node in ast.walk(load_function)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
        }
        self.assertIn("merge_source_schedules", calls)

    def test_telegram_views_use_simulcast_groups(self):
        functions = {
            node.name: node
            for node in self.tree.body
            if isinstance(node, ast.FunctionDef)
        }
        for name in ("build_schedule_text", "build_live_text"):
            calls = {
                node.func.id
                for node in ast.walk(functions[name])
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
            }
            self.assertIn("group_simulcasts", calls)

    def test_entrypoint_is_thin(self):
        main_source = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn("from bot_app import main", main_source)
        self.assertNotIn("@dp.callback_query", main_source)

    def test_channel_count_is_not_hardcoded(self):
        self.assertNotIn("· 2 канала", self.source)
        self.assertIn("unique_channels(events)", self.source)


if __name__ == "__main__":
    unittest.main()

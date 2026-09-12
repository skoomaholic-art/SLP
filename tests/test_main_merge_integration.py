import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN_PATH = ROOT / "main.py"


class MainMergeIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = MAIN_PATH.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_main_imports_schedule_view_helpers(self):
        imported = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.ImportFrom) and node.module == "services.schedule_merge":
                imported.update(alias.name for alias in node.names)
        self.assertTrue({"group_simulcasts", "unique_channels"}.issubset(imported))

    def test_main_uses_shared_parser_orchestrator(self):
        self.assertIn("from agents.orchestrator import ParserOrchestrator", self.source)
        self.assertIn("orchestrator = ParserOrchestrator()", self.source)
        load_function = next(
            node
            for node in self.tree.body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "load_schedule_events"
        )
        function_source = ast.get_source_segment(self.source, load_function) or ""
        self.assertIn("await orchestrator.refresh()", function_source)
        self.assertNotIn("get_qazsport_schedule", function_source)
        self.assertNotIn("get_sportplus_schedule", function_source)

    def test_live_callback_uses_explicit_live_now_filter(self):
        callback = next(
            node
            for node in self.tree.body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "live_callback"
        )
        callback_source = ast.get_source_segment(self.source, callback) or ""
        self.assertIn("filter_live_now", callback_source)
        self.assertNotIn('get_event_status(event) == "live"', callback_source)

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

    def test_channel_count_is_not_hardcoded(self):
        self.assertNotIn("· 2 канала", self.source)
        self.assertIn("unique_channels(events)", self.source)

    def test_main_prints_the_database_path_used_by_telegram(self):
        self.assertIn('SLP SQLite: {orchestrator.database.path}', self.source)


if __name__ == "__main__":
    unittest.main()

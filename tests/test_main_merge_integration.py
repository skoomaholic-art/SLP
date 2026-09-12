import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN_PATH = ROOT / "main.py"
SERVICE_PATH = ROOT / "services" / "schedule_service.py"


class ProductionEntrypointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.main_source = MAIN_PATH.read_text(encoding="utf-8")
        cls.main_tree = ast.parse(cls.main_source)
        cls.service_source = SERVICE_PATH.read_text(encoding="utf-8")

    def test_main_is_thin_entrypoint(self):
        self.assertLess(len(self.main_source.splitlines()), 80)
        self.assertIn("ParserOrchestrator", self.main_source)
        self.assertIn("ScheduleService", self.main_source)
        self.assertIn("scheduler_loop", self.main_source)
        self.assertNotIn("get_qazsport_schedule", self.main_source)
        self.assertNotIn("get_sportplus_schedule", self.main_source)

    def test_bot_reads_database_backed_service(self):
        self.assertIn("load_active_source_snapshot", self.service_source)
        self.assertIn("merge_source_schedules", self.service_source)
        self.assertIn("get_event_status", self.service_source)

    def test_only_one_main_coroutine(self):
        names = [
            node.name
            for node in self.main_tree.body
            if isinstance(node, ast.AsyncFunctionDef)
        ]
        self.assertEqual(names, ["main"])


if __name__ == "__main__":
    unittest.main()

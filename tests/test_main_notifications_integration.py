import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HANDLERS_PATH = ROOT / "bot" / "handlers.py"
SCHEDULER_PATH = ROOT / "scheduler" / "jobs.py"
GITIGNORE_PATH = ROOT / ".gitignore"


class RuntimeIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.handlers = HANDLERS_PATH.read_text(encoding="utf-8")
        cls.scheduler = SCHEDULER_PATH.read_text(encoding="utf-8")

    def test_notifications_are_background_driven(self):
        self.assertIn("scheduler_loop", self.scheduler)
        self.assertIn("schedule_service.refresh()", self.scheduler)
        self.assertIn("diff_schedule_snapshots", self.scheduler)
        self.assertIn("get_subscribers", self.scheduler)

    def test_bot_has_real_export(self):
        self.assertIn("build_schedule_xlsx", self.handlers)
        self.assertIn("answer_document", self.handlers)
        self.assertNotIn("подключим после стабилизации", self.handlers)

    def test_admin_diagnostics_exist(self):
        self.assertIn('Command("refresh")', self.handlers)
        self.assertIn('Command("errors")', self.handlers)
        self.assertIn("build_health_text", self.handlers)

    def test_runtime_files_are_ignored(self):
        gitignore = GITIGNORE_PATH.read_text(encoding="utf-8")
        self.assertIn("slp_state.json", gitignore)
        self.assertIn("*.db", gitignore)


if __name__ == "__main__":
    unittest.main()

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN_PATH = ROOT / "main.py"


class StartupRefreshTests(unittest.TestCase):
    def test_initial_refresh_precedes_polling(self):
        source = MAIN_PATH.read_text(encoding="utf-8")
        self.assertLess(
            source.index("await schedule_service.refresh()"),
            source.index("await dispatcher.start_polling("),
        )

    def test_main_still_has_single_entry_coroutine(self):
        tree = ast.parse(MAIN_PATH.read_text(encoding="utf-8"))
        names = [
            node.name
            for node in tree.body
            if isinstance(node, ast.AsyncFunctionDef)
        ]
        self.assertEqual(names, ["main"])


if __name__ == "__main__":
    unittest.main()

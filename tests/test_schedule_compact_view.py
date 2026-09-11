import ast
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MAIN_PATH = PROJECT_ROOT / "main.py"


def function_source(name: str) -> str:
    source = MAIN_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(source, node) or ""
    raise AssertionError(f"Function {name!r} not found")


class ScheduleCompactViewTests(unittest.TestCase):
    def test_compact_event_line_has_no_accuracy_text_row(self):
        source = function_source("compact_event_line")
        self.assertNotIn("accuracy = get_accuracy(event)", source)
        self.assertNotIn("accuracy['text']", source)
        self.assertIn("get_schedule_status_badge(event)", source)
        self.assertIn("| {channel}", source)

    def test_simulcast_block_has_no_accuracy_text_row(self):
        source = function_source("compact_simulcast_block")
        self.assertNotIn("accuracy = get_accuracy(event)", source)
        self.assertNotIn("accuracy['text']", source)
        self.assertIn("get_schedule_status_badge(event)", source)
        self.assertIn("| {channel}", source)

    def test_attention_legend_remains_in_schedule(self):
        source = MAIN_PATH.read_text(encoding="utf-8")
        self.assertIn("⚠️ проверить время", source)
        self.assertIn('callback_data="menu"', source)


if __name__ == "__main__":
    unittest.main()

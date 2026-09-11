import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from services.event_contract import build_sport_event
from services.event_store import (
    get_store_diagnostics,
    load_events,
    save_complete_snapshot,
    upsert_events,
)

KZ = ZoneInfo("Asia/Almaty")


def build(title: str, hour: int, *, live: bool = True) -> dict:
    return build_sport_event(
        {
            "channel": "Qazsport",
            "raw_title": f"RAW {title}",
            "normalized_title": title,
            "title": title,
            "sport": "Футбол",
            "tournament": "Тест",
            "start_time": datetime(2026, 9, 11, hour, 0, tzinfo=KZ),
            "end_time": datetime(2026, 9, 11, hour + 2, 0, tzinfo=KZ),
            "is_live_broadcast": live,
            "end_estimation_method": "source",
            "end_confidence": "high",
        },
        source="qazsport",
        source_url="https://qazsporttv.kz/ru/program/2026-09-11",
    )


class EventStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "events.sqlite3"

    def tearDown(self):
        self.temp.cleanup()

    def test_complete_snapshot_preserves_required_fields(self):
        original = build("Матч A", 14)
        save_complete_snapshot([original], self.db_path)
        events = load_events(path=self.db_path)
        self.assertEqual(len(events), 1)
        stored = events[0]
        self.assertEqual(stored["event_key"], original["event_key"])
        self.assertEqual(stored["source"], "qazsport")
        self.assertEqual(stored["source_url"], original["source_url"])
        self.assertEqual(stored["channel"], "Qazsport")
        self.assertEqual(stored["raw_title"], "RAW Матч A")
        self.assertEqual(stored["normalized_title"], "Матч A")
        self.assertEqual(stored["timezone"], "Asia/Almaty")
        self.assertEqual(stored["start_time"].isoformat(), "2026-09-11T14:00:00+05:00")
        self.assertEqual(stored["end_time"].isoformat(), "2026-09-11T16:00:00+05:00")
        self.assertTrue(stored["is_live_broadcast"])
        self.assertIsNotNone(stored["updated_at"].tzinfo)

    def test_latest_complete_snapshot_is_the_active_view(self):
        first = build("Матч A", 14)
        second = build("Матч B", 18)
        save_complete_snapshot([first], self.db_path)
        save_complete_snapshot([second], self.db_path)
        events = load_events(path=self.db_path)
        self.assertEqual([item["title"] for item in events], ["Матч B"])

        diagnostics = get_store_diagnostics(self.db_path)
        self.assertEqual(diagnostics["total"], 1)
        self.assertEqual(diagnostics["history_total"], 2)
        self.assertEqual(diagnostics["live_marked"], 1)
        self.assertTrue(diagnostics["snapshot_refreshed_at"])

    def test_partial_upsert_does_not_replace_last_good_snapshot(self):
        first = build("Последний хороший", 14)
        partial = build("Частичный refresh", 18)
        save_complete_snapshot([first], self.db_path)
        upsert_events([partial], self.db_path)
        events = load_events(path=self.db_path)
        self.assertEqual([item["title"] for item in events], ["Последний хороший"])

    def test_live_only_query_uses_source_live_flag(self):
        live = build("LIVE", 14, live=True)
        regular = build("Обычная передача", 18, live=False)
        save_complete_snapshot([live, regular], self.db_path)
        events = load_events(live_broadcasts_only=True, path=self.db_path)
        self.assertEqual([item["title"] for item in events], ["LIVE"])


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from agents.orchestrator import ParserOrchestrator
from agents.qa_agent import ParserQAAgent
from agents.source_agent import SourceHealthAgent
from services.event_contract import build_sport_event
from services.source_horizon import HorizonDiscovery
from storage.database import SLPDatabase


KZ = ZoneInfo("Asia/Almaty")


def make_event(source: str, event_date: date, index: int = 0) -> dict:
    hour = 13 + index
    channel = "Qazsport" if source == "qazsport" else "Sport+ Qazaqstan"
    return build_sport_event(
        {
            "channel": channel,
            "date": event_date.isoformat(),
            "time": f"{hour:02d}:00",
            "sport": "Футбол",
            "tournament": "Test League",
            "title": f"Команда {index} – Соперник {index}",
            "is_live_broadcast": True,
            "raw_title": f"LIVE Команда {index} – Соперник {index}",
            "estimated_broadcast_end_date": None,
            "estimated_broadcast_end": None,
            "end_estimation_method": None,
            "end_confidence": "unknown",
        },
        source=source,
        source_url=f"https://example.test/{source}",
    )


class DatabaseAndAgentTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = SLPDatabase(Path(self.temp_dir.name) / "test.db")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_source_agent_blocks_zero_after_good_baseline(self):
        self.db.record_parser_run(
            run_id="baseline",
            source="qazsport",
            scope_date="2026-09-12",
            status="ok",
            event_count=12,
            previous_count=None,
        )
        assessment = SourceHealthAgent(self.db).assess(
            source="qazsport",
            scope_date="2026-09-12",
            current_count=0,
        )
        self.assertEqual(assessment.status, "blocked")
        self.assertFalse(assessment.publish_allowed)

    def test_qa_rejects_wrong_source(self):
        event = make_event("sportplus", date(2026, 9, 12))
        result = ParserQAAgent().validate_batch(
            [event],
            expected_source="qazsport",
        )
        self.assertFalse(result.ok)
        self.assertTrue(any("expected='qazsport'" in item for item in result.errors))

    def test_database_restores_active_snapshot(self):
        events = [make_event("qazsport", date(2026, 9, 12), index) for index in range(3)]
        self.db.upsert_source_snapshot(
            run_id="run-1",
            source="qazsport",
            scope_date="2026-09-12",
            events=events,
        )
        restored = self.db.load_active_source_snapshot("qazsport", "2026-09-12")
        self.assertEqual(len(restored), 3)
        self.assertEqual(restored[0]["source"], "qazsport")
        self.assertTrue(restored[0]["is_live_broadcast"])

    def test_database_schema_separates_live_flag_and_dedup_key(self):
        with sqlite3.connect(self.db.path) as connection:
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(events)").fetchall()
            }
        self.assertIn("is_live_broadcast", columns)
        self.assertIn("dedup_key", columns)
        self.assertIn("start_at", columns)
        self.assertIn("end_at", columns)
        self.assertIn("timezone", columns)
        self.assertIn("last_seen_at", columns)

    def test_database_query_returns_only_source_live_when_requested(self):
        live = make_event("qazsport", date(2026, 9, 12), 0)
        ordinary = make_event("qazsport", date(2026, 9, 12), 1)
        ordinary["is_live_broadcast"] = False
        ordinary["is_live"] = False
        self.db.upsert_source_snapshot(
            run_id="run-1",
            source="qazsport",
            scope_date="2026-09-12",
            events=[live, ordinary],
        )
        result = self.db.load_active_events(
            start_date="2026-09-12",
            end_date="2026-09-12",
            live_broadcast_only=True,
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["title"], live["title"])


class OrchestratorFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = SLPDatabase(Path(self.temp_dir.name) / "test.db")
        self.collapse_qazsport_today = False
        self.today = date(2026, 9, 12)
        self.furthest = date(2026, 9, 16)

        async def qazsport_loader(target_date: date) -> list[dict]:
            if target_date == self.today and self.collapse_qazsport_today:
                return []
            return [make_event("qazsport", target_date, index) for index in range(5)]

        async def sportplus_loader(target_date: date) -> list[dict]:
            return [make_event("sportplus", target_date, index) for index in range(5)]

        async def date_discoverer(today: date) -> HorizonDiscovery:
            return HorizonDiscovery(
                dates_by_source={
                    "qazsport": {today, self.furthest},
                    "sportplus": {today, self.furthest},
                },
                warnings=[],
            )

        self.orchestrator = ParserOrchestrator(
            database=self.db,
            loaders={
                "qazsport": qazsport_loader,
                "sportplus": sportplus_loader,
            },
            date_discoverer=date_discoverer,
        )

    async def asyncTearDown(self):
        self.temp_dir.cleanup()

    async def test_collapsed_source_uses_last_good_snapshot(self):
        now = datetime(2026, 9, 12, 12, 0, tzinfo=KZ)

        first = await self.orchestrator.refresh(now=now)
        self.assertFalse(first.source_errors)
        self.assertTrue(any(event["source"] == "qazsport" for event in first.events))

        self.collapse_qazsport_today = True
        second = await self.orchestrator.refresh(now=now)

        today_qaz = [
            item
            for item in second.source_runs
            if item.source == "qazsport" and item.scope_date == self.today.isoformat()
        ][0]
        self.assertEqual(today_qaz.status, "blocked")
        self.assertTrue(today_qaz.used_fallback)
        self.assertEqual(len(today_qaz.events), 5)
        self.assertTrue(any("Qazsport" in error for error in second.source_errors))

    async def test_horizon_extends_past_nearest_monday_to_furthest_live(self):
        now = datetime(2026, 9, 12, 12, 0, tzinfo=KZ)
        result = await self.orchestrator.refresh(now=now)
        self.assertEqual(result.minimum_horizon, "2026-09-14")
        self.assertEqual(result.actual_horizon, "2026-09-16")
        self.assertTrue(any(event["date"] == "2026-09-16" for event in result.events))


if __name__ == "__main__":
    unittest.main()

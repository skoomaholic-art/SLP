from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from agents.orchestrator import ParserOrchestrator
from agents.qa_agent import ParserQAAgent
from agents.source_agent import SourceHealthAgent
from storage.database import SLPDatabase


KZ = ZoneInfo("Asia/Almaty")


def make_event(source: str, event_date: date, index: int = 0) -> dict:
    hour = 13 + index
    return {
        "source": source,
        "source_url": f"https://example.test/{source}",
        "channel": "Qazsport" if source == "qazsport" else "Sport+ Qazaqstan",
        "date": event_date.isoformat(),
        "time": f"{hour:02d}:00",
        "sport": "Футбол",
        "tournament": "Test League",
        "title": f"Команда {index} – Соперник {index}",
        "is_live": True,
        "raw_title": f"LIVE Команда {index} – Соперник {index}",
        "estimated_broadcast_end_date": None,
        "estimated_broadcast_end": None,
        "end_estimation_method": None,
        "end_confidence": "unknown",
    }


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


class OrchestratorFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = SLPDatabase(Path(self.temp_dir.name) / "test.db")
        self.collapse_qazsport_today = False
        self.today = date(2026, 9, 12)

        async def qazsport_loader(target_date: date) -> list[dict]:
            if target_date == self.today and self.collapse_qazsport_today:
                return []
            return [make_event("qazsport", target_date, index) for index in range(5)]

        async def sportplus_loader(target_date: date) -> list[dict]:
            return [make_event("sportplus", target_date, index) for index in range(5)]

        self.orchestrator = ParserOrchestrator(
            database=self.db,
            loaders={
                "qazsport": qazsport_loader,
                "sportplus": sportplus_loader,
            },
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


if __name__ == "__main__":
    unittest.main()

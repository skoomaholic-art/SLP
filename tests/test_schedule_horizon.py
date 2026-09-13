from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from agents.orchestrator import ParserOrchestrator, source_lookahead_days
from services.schedule_service import ScheduleService
from storage.database import SLPDatabase


KZ = ZoneInfo("Asia/Almaty")


def make_event(source: str, event_date: date, hour: int, title: str) -> dict:
    return {
        "source": source,
        "source_url": f"https://example.test/{source}",
        "channel": "Qazsport" if source == "qazsport" else "Sport+ Qazaqstan",
        "date": event_date.isoformat(),
        "time": f"{hour:02d}:00",
        "sport": "Футбол",
        "tournament": "Test League",
        "title": title,
        "is_live": True,
        "raw_title": f"LIVE {title}",
        "estimated_broadcast_end_date": event_date.isoformat(),
        "estimated_broadcast_end": f"{(hour + 2) % 24:02d}:00",
        "end_estimation_method": "next_program",
        "end_confidence": "high",
    }


class ScheduleHorizonTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = SLPDatabase(Path(self.temp_dir.name) / "test.db")
        self.service = ScheduleService(ParserOrchestrator(database=self.db))

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_database_read_includes_future_scope(self):
        future = date(2026, 9, 14)
        event = make_event("sportplus", future, 18, "Future Match")
        self.db.upsert_source_snapshot(
            run_id="future",
            source="sportplus",
            scope_date=future.isoformat(),
            events=[event],
        )
        events = self.service.get_events(
            now=datetime(2026, 9, 12, 12, 0, tzinfo=KZ)
        )
        self.assertEqual([item["title"] for item in events], ["Future Match"])

    def test_database_read_can_select_one_calendar_day(self):
        first = date(2026, 9, 13)
        second = date(2026, 9, 14)
        self.db.upsert_source_snapshot(
            run_id="daily",
            source="sportplus",
            scope_date=first.isoformat(),
            events=[make_event("sportplus", first, 18, "First Day")],
        )
        self.db.upsert_source_snapshot(
            run_id="daily",
            source="sportplus",
            scope_date=second.isoformat(),
            events=[make_event("sportplus", second, 19, "Second Day")],
        )

        selected = self.service.get_events(
            now=datetime(2026, 9, 13, 12, 0, tzinfo=KZ),
            target_date=second,
        )
        self.assertEqual([item["title"] for item in selected], ["Second Day"])

    def test_schedule_dates_are_consecutive_until_last_known_event(self):
        first = date(2026, 9, 13)
        last = date(2026, 9, 15)
        self.db.upsert_source_snapshot(
            run_id="daily",
            source="sportplus",
            scope_date=last.isoformat(),
            events=[make_event("sportplus", last, 19, "Last Day")],
        )
        self.assertEqual(
            self.service.get_schedule_dates(
                now=datetime(2026, 9, 13, 12, 0, tzinfo=KZ)
            ),
            [date(2026, 9, 13), date(2026, 9, 14), date(2026, 9, 15)],
        )

    def test_qazsport_horizon_reaches_nearest_monday_from_saturday(self):
        saturday = date(2026, 9, 12)
        self.assertGreaterEqual(source_lookahead_days("qazsport", saturday), 2)

    def test_sportplus_horizon_uses_wide_guide(self):
        self.assertEqual(
            source_lookahead_days("sportplus", date(2026, 9, 12)),
            14,
        )


class SourceIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_current_provider_failure_keeps_other_provider_events(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = SLPDatabase(Path(temp_dir) / "test.db")
            today = date(2026, 9, 12)

            async def qazsport_loader(target_date: date) -> list[dict]:
                if target_date == today:
                    raise RuntimeError("qazsport unavailable")
                return []

            async def sportplus_loader(target_date: date) -> list[dict]:
                if target_date == today:
                    return [make_event("sportplus", target_date, 17, "Healthy Match")]
                return []

            orchestrator = ParserOrchestrator(
                database=db,
                loaders={
                    "qazsport": qazsport_loader,
                    "sportplus": sportplus_loader,
                },
            )
            result = await orchestrator.refresh(
                now=datetime(2026, 9, 12, 18, 0, tzinfo=KZ)
            )

            self.assertTrue(any("Qazsport" in item for item in result.source_errors))
            self.assertTrue(
                any(event["source"] == "sportplus" for event in result.events)
            )


if __name__ == "__main__":
    unittest.main()

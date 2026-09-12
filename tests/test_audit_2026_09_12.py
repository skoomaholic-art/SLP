import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from agents.orchestrator import ParserOrchestrator
from parsers.tvguide_cached import infer_direct_event, normalize_provider_timezone
from services.live_evidence import event_is_live_broadcast, event_is_schedule_candidate
from services.schedule_service import ScheduleService
from services.time_logic import get_event_status
from storage.database import SLPDatabase


KZ = ZoneInfo("Asia/Almaty")
AUDIT_NOW = datetime(2026, 9, 12, 22, 28, tzinfo=KZ)


def epg_event(start: str, end_date: str, end: str, title: str) -> dict:
    return {
        "source": "tvplus",
        "source_url": "https://example.test",
        "channel": "Setanta Sports 1",
        "date": "2026-09-12",
        "time": start,
        "timezone": "Asia/Almaty",
        "sport": "Футбол",
        "tournament": "",
        "title": title,
        "raw_title": title,
        "is_live": False,
        "is_live_broadcast": False,
        "live_state": "unknown",
        "live_evidence_method": "none",
        "estimated_broadcast_end_date": end_date,
        "estimated_broadcast_end": end,
        "end_estimation_method": "provider_epg",
        "end_confidence": "high",
    }


class AuditTimezoneRegressionTests(unittest.TestCase):
    def test_confirmed_audit_examples_recalculate_after_utc_to_kz(self):
        cases = (
            ("16:30", "2026-09-12", "19:00", "Тоттенхэм - Эвертон", "live", "21:30"),
            ("16:25", "2026-09-12", "18:30", "Бразилия - Чили", "live", "21:25"),
            ("21:00", "2026-09-13", "01:00", "Noche UFC", "upcoming", "02:00"),
            ("22:00", "2026-09-13", "00:55", "Viju Snooker Cup финал", "upcoming", "03:00"),
        )
        for start, end_date, end, title, expected_status, expected_local_start in cases:
            with self.subTest(title=title):
                normalized = normalize_provider_timezone(
                    epg_event(start, end_date, end, title)
                )
                self.assertEqual(normalized["time"], expected_local_start)
                self.assertEqual(
                    get_event_status(normalized, now=AUDIT_NOW),
                    expected_status,
                )

    def test_epg_presence_alone_never_means_direct(self):
        titles = (
            "Уимблдон 2026, четвертьфинал",
            "Formula 1, Гран-при Нидерландов, гонка",
            "Превью к этапу WRC 2026",
            "FIA WEC 6 часов Трассы Америк Review",
        )
        for title in titles:
            with self.subTest(title=title):
                parsed = infer_direct_event(
                    epg_event("16:00", "2026-09-12", "18:00", title),
                    target_date=date(2026, 9, 12),
                    seen=set(),
                )
                self.assertFalse(event_is_live_broadcast(parsed))

    def test_qazsport_unmarked_sport_program_remains_visible_in_schedule(self):
        event = {
            "source": "qazsport",
            "channel": "Qazsport",
            "raw_title": "Азия чемпионаты (Ерлер). 3 орын үшін матч Волейбол",
            "title": "Азия чемпионаты (Ерлер). 3 орын үшін матч Волейбол",
            "sport": "",
            "tournament": "",
            "is_live": False,
        }
        self.assertTrue(event_is_schedule_candidate(event))
        self.assertFalse(event_is_live_broadcast(event))


class AuditScheduleSeparationTests(unittest.TestCase):
    def test_schedule_can_include_sport_candidate_without_calling_it_direct(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = SLPDatabase(Path(temp_dir) / "audit.db")
            orchestrator = ParserOrchestrator(
                database=db,
                loaders={"tvguide": lambda _: None},
            )
            service = ScheduleService(orchestrator)
            event = epg_event("16:30", "2026-09-12", "19:00", "Тоттенхэм - Эвертон")
            event = normalize_provider_timezone(event)
            event = infer_direct_event(
                event,
                target_date=date.fromisoformat(event["date"]),
                seen=set(),
            )
            db.upsert_source_snapshot(
                run_id="audit",
                source="tvguide",
                scope_date=event["date"],
                events=[event],
            )
            rows = service.get_events(now=AUDIT_NOW)
            self.assertEqual(len(rows), 1)
            self.assertFalse(event_is_live_broadcast(rows[0]))
            self.assertEqual(service.get_live_events(now=AUDIT_NOW), [])


if __name__ == "__main__":
    unittest.main()

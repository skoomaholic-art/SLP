import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from agents.orchestrator import ParserOrchestrator
from parsers.tvguide_cached import apply_reconciliation_result, infer_direct_event
from services.schedule_service import ScheduleService
from storage.database import SLPDatabase
from verifiers.broadcast_occurrence import verify_broadcast_occurrence

KZ = ZoneInfo("Asia/Almaty")


def base_event(**overrides):
    event = {
        "source": "tvguide",
        "source_url": "https://example.test",
        "channel": "Setanta Sports 1",
        "date": "2026-09-13",
        "time": "02:00",
        "timezone": "Asia/Almaty",
        "sport": "MMA",
        "tournament": "Noche UFC",
        "title": "Силва vs. Дельгадо - Main Card",
        "raw_title": "Noche UFC: Силва vs. Дельгадо - Main Card",
        "is_live": False,
        "is_live_broadcast": False,
        "live_state": "unknown",
        "live_evidence_method": "provider_epg_sport_candidate",
        "estimated_broadcast_end_date": "2026-09-13",
        "estimated_broadcast_end": "06:00",
        "end_estimation_method": "provider_epg",
        "end_confidence": "high",
        "is_sport_event": True,
    }
    event.update(overrides)
    return event


class BroadcastOccurrenceTests(unittest.TestCase):
    @patch("verifiers.broadcast_occurrence.openserp_search")
    def test_noche_ufc_previous_us_date_converts_to_kz_broadcast(self, search):
        search.return_value = ([{
            "title": "Noche UFC: Silva vs Delgado",
            "snippet": "Sat, Sep 12 2026 / 5:00 PM EDT. Main Card.",
            "url": "https://www.ufc.com/event/ufc-fight-night-september-12-2026",
        }], {})
        result = verify_broadcast_occurrence(base_event())
        self.assertEqual(result["state"], "confirmed_direct")
        self.assertEqual(result["difference_minutes"], 0)

    @patch("verifiers.broadcast_occurrence.openserp_search")
    def test_search_engine_group_falls_back_after_datacenter_block(self, search):
        result_row = {
            "title": "Noche UFC: Silva vs Delgado",
            "snippet": "Sat, Sep 12 2026 / 5:00 PM EDT. Main Card.",
            "url": "https://www.ufc.com/event/ufc-fight-night-september-12-2026",
        }
        search.side_effect = [RuntimeError("engine group blocked"), ([result_row], {})]
        result = verify_broadcast_occurrence(base_event())
        self.assertEqual(result["state"], "confirmed_direct")
        self.assertGreaterEqual(search.call_count, 2)

    @patch("verifiers.broadcast_occurrence.openserp_search")
    def test_khl_replay_is_rejected_when_real_match_was_previous_day(self, search):
        search.return_value = ([{
            "title": "Барыс - Амур 12 сентября 2026",
            "snippet": "Матч начнется 12 сентября 2026 в 14:30 МСК",
            "url": "https://www.sports.ru/hockey/match/barys-amur/",
        }], {})
        event = base_event(
            channel="KHL Prime",
            sport="Хоккей",
            tournament="Фонбет Чемпионат КХЛ",
            title='"Барыс" - "Амур"',
            raw_title='Фонбет Чемпионат КХЛ. "Барыс" - "Амур"',
            time="02:50",
            estimated_broadcast_end="05:00",
        )
        result = verify_broadcast_occurrence(event)
        self.assertEqual(result["state"], "mismatch")
        self.assertGreater(abs(result["difference_minutes"]), 30)


class TvguideReplayTests(unittest.TestCase):
    def test_second_same_epg_program_on_channel_is_replay_not_candidate(self):
        seen = set()
        raw = base_event(source="tvplus", is_sport_event=False)
        first = infer_direct_event(raw, target_date=date(2026, 9, 13), seen=seen)
        second = infer_direct_event(raw, target_date=date(2026, 9, 13), seen=seen)
        self.assertTrue(first["is_sport_event"])
        self.assertFalse(second["is_sport_event"])
        self.assertEqual(second["reconciliation_state"], "replay")
        self.assertEqual(second["live_evidence_method"], "provider_repeat_epg")

    def test_unknown_external_result_fails_closed(self):
        event = apply_reconciliation_result(base_event(), {"state": "unknown", "sources": []})
        self.assertFalse(event["is_live_broadcast"])
        self.assertFalse(event["is_sport_event"])

    def test_confirmed_championat_result_promotes_direct(self):
        event = apply_reconciliation_result(
            base_event(),
            {
                "state": "confirmed_direct",
                "verification_source": "championat.com",
                "difference_minutes": 0,
                "external_time_kz": "2026-09-13T02:00:00+05:00",
                "sources": [{"source_name": "championat.com"}],
            },
        )
        self.assertTrue(event["is_live_broadcast"])
        self.assertTrue(event["is_sport_event"])
        self.assertEqual(event["live_evidence_method"], "championat_schedule_match")
        self.assertEqual(event["reconciliation_verification_source"], "championat.com")


class PublicScheduleTests(unittest.TestCase):
    def test_public_schedule_excludes_unconfirmed_epg(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = SLPDatabase(Path(temp_dir) / "slp.db")
            orchestrator = ParserOrchestrator(database=db, loaders={"tvguide": lambda _: None})
            service = ScheduleService(orchestrator)
            event = base_event(time="03:00", estimated_broadcast_end="05:00")
            db.upsert_source_snapshot(
                run_id="test",
                source="tvguide",
                scope_date="2026-09-13",
                events=[event],
            )
            rows = service.get_events(now=datetime(2026, 9, 13, 0, 30, tzinfo=KZ))
            self.assertEqual(rows, [])

    def test_public_schedule_keeps_confirmed_direct(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = SLPDatabase(Path(temp_dir) / "slp.db")
            orchestrator = ParserOrchestrator(database=db, loaders={"tvguide": lambda _: None})
            service = ScheduleService(orchestrator)
            event = base_event(
                time="03:00",
                estimated_broadcast_end="05:00",
                is_live=True,
                is_live_broadcast=True,
                live_state="live",
                live_evidence_method="championat_schedule_match",
            )
            db.upsert_source_snapshot(
                run_id="test",
                source="tvguide",
                scope_date="2026-09-13",
                events=[event],
            )
            rows = service.get_events(now=datetime(2026, 9, 13, 0, 30, tzinfo=KZ))
            self.assertEqual(len(rows), 1)


if __name__ == "__main__":
    unittest.main()

"""Schedule self-check: pure rules, no network and no database writes."""
from datetime import datetime
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from services.schedule_anomalies import (
    find_schedule_anomalies, summarize_anomalies,
)
from services.time_logic import KZ_TIMEZONE

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=KZ_TIMEZONE)


def entry(title, start, end, *, channel="SETANTA SPORTS 1", sport="Футбол",
          tournament="Премьер-лига", end_known=True, record="rec"):
    return {
        "id": "id-" + record, "title": title, "sport": sport,
        "tournament": tournament, "channel": channel, "end_known": end_known,
        "source_record_id": record, "source": "email_epg_test",
        "start_at": f"2026-{start}:00+05:00", "end_at": f"2026-{end}:00+05:00",
        "broadcasts": [{
            "channel": channel, "source_record_id": record, "active": True,
            "source": "email_epg_test",
            "start_at": f"2026-{start}:00+05:00",
            "end_at": f"2026-{end}:00+05:00",
        }],
    }


def kinds(events, now=NOW):
    return [item["kind"] for item in find_schedule_anomalies(events, now=now)]


class ScheduleAnomalyTests(unittest.TestCase):
    def test_normal_schedule_is_clean(self):
        events = [
            entry("Арсенал - Челси", "10-08T20:00", "10-08T22:15", record="a"),
            entry("Ливерпуль - Эвертон", "10-08T22:30", "10-09T00:45",
                  record="b"),
            entry("Арсенал - Челси", "10-08T20:00", "10-08T22:15",
                  channel="QAZSPORT HD", record="c"),
        ]
        self.assertEqual(kinds(events), [])

    def test_twenty_hour_basketball_broadcast_is_an_error(self):
        events = [entry(
            "Барселона - Црвена Звезда", "10-08T02:30", "10-08T22:30",
            sport="Баскетбол", tournament="Евролига",
        )]
        found = find_schedule_anomalies(events, now=NOW)
        self.assertEqual([item["kind"] for item in found],
                         ["duration_too_long"])
        self.assertEqual(found[0]["level"], "error")
        self.assertEqual(found[0]["details"]["duration_minutes"], 20 * 60)
        self.assertIn("20 ч", found[0]["message"])

    def test_long_format_sports_may_run_most_of_a_day_part(self):
        events = [entry(
            "ATP 500. Алматы. 1/4 финала", "10-08T12:00", "10-08T19:30",
            sport="Теннис", tournament="ATP 500",
        )]
        self.assertEqual(kinds(events), [])

    def test_estimated_end_is_never_judged(self):
        events = [entry(
            "Барселона - Реал", "10-08T02:30", "10-08T22:30", end_known=False,
        )]
        self.assertEqual(kinds(events), [])

    def test_too_short_known_broadcast(self):
        events = [entry("Арсенал - Челси", "10-08T20:00", "10-08T20:10")]
        self.assertEqual(kinds(events), ["duration_too_short"])

    def test_two_different_broadcasts_collide_on_one_channel(self):
        events = [
            entry("Арсенал - Челси", "10-08T20:00", "10-08T22:15", record="a"),
            entry("Бавария - Боруссия Д", "10-08T21:00", "10-08T23:00",
                  tournament="Бундеслига", record="b"),
        ]
        found = find_schedule_anomalies(events, now=NOW)
        self.assertEqual([item["kind"] for item in found], ["channel_overlap"])
        self.assertEqual(found[0]["title"], "Бавария - Боруссия Д")
        self.assertEqual(found[0]["details"]["other_storage_id"], "a")

    def test_back_to_back_broadcasts_do_not_collide(self):
        events = [
            entry("Арсенал - Челси", "10-08T20:00", "10-08T22:00", record="a"),
            entry("Бавария - Боруссия Д", "10-08T22:00", "10-09T00:00",
                  record="b"),
        ]
        self.assertEqual(kinds(events), [])

    def test_same_slot_from_two_sources_is_a_soft_duplicate(self):
        events = [
            entry("Арсенал - Челси", "10-08T19:55", "10-08T22:15", record="a"),
            entry("Арсенал - Челси. 8-й тур", "10-08T20:00", "10-08T22:15",
                  record="b"),
        ]
        found = find_schedule_anomalies(events, now=NOW)
        self.assertEqual([item["kind"] for item in found], ["duplicate_slot"])
        self.assertEqual(found[0]["level"], "attention")

    def test_same_fixture_at_two_times_on_different_channels(self):
        events = [
            entry("Украина - Швеция", "10-08T20:00", "10-08T22:00", record="a",
                  tournament="Отбор ЧМ"),
            entry("Украина - Швеция", "10-08T23:45", "10-09T01:45",
                  channel="QAZSPORT HD", record="b", tournament="Отбор ЧМ"),
        ]
        found = find_schedule_anomalies(events, now=NOW)
        self.assertEqual([item["kind"] for item in found],
                         ["same_fixture_different_time"])
        self.assertEqual(found[0]["details"]["gap_minutes"], 225)

    def test_studio_shows_without_two_sides_are_not_fixture_duplicates(self):
        events = [
            entry("Формула-1. Гран-при Сингапура", "10-08T15:00",
                  "10-08T17:00", sport="Автоспорт", record="a"),
            entry("Формула-1. Гран-при Сингапура", "10-08T19:00",
                  "10-08T21:00", sport="Автоспорт", channel="QAZSPORT HD",
                  record="b"),
        ]
        self.assertEqual(kinds(events), [])

    def test_test_label_but_not_test_match(self):
        self.assertEqual(kinds([entry(
            "Украина - Швеция тест", "10-08T20:00", "10-08T22:00",
        )]), ["test_label"])
        self.assertEqual(kinds([entry(
            "Англия - Австралия. Тест-матч", "10-08T20:00", "10-08T22:00",
            sport="Регби",
        )]), [])
        self.assertEqual(kinds([entry(
            "Тестостерон - Атлант", "10-08T20:00", "10-08T22:00",
        )]), [])

    def test_impossible_tournament_year(self):
        found = find_schedule_anomalies([entry(
            "Украина - Швеция", "10-08T20:00", "10-08T22:00",
            tournament="ЧМ-2032",
        )], now=NOW)
        self.assertEqual([item["kind"] for item in found], ["impossible_year"])
        self.assertEqual(found[0]["details"]["year"], 2032)
        # Qualifiers for a tournament two years away and seasons are normal.
        self.assertEqual(kinds([entry(
            "Казахстан - Бельгия", "10-08T20:00", "10-08T22:00",
            tournament="Отбор Евро-2028",
        )]), [])
        self.assertEqual(kinds([entry(
            "Барыс - Авангард", "10-08T20:00", "10-08T22:00",
            sport="Хоккей", tournament="КХЛ 2026/27",
        )]), [])

    def test_day_and_month_swap_is_suspected_only_far_from_today(self):
        # 7 April entered as 04.07 shows up as 4 July, months from today.
        april_as_july = datetime(2026, 4, 6, 12, 0, tzinfo=KZ_TIMEZONE)
        events = [entry("Астана - Кайрат", "07-04T19:00", "07-04T21:00")]
        found = find_schedule_anomalies(events, now=april_as_july)
        self.assertEqual([item["kind"] for item in found],
                         ["date_swap_suspect"])
        self.assertEqual(found[0]["details"]["swapped_date"], "2026-04-07")
        # The same row seen in early July is simply this week's schedule.
        self.assertEqual(kinds(
            events, now=datetime(2026, 7, 2, 12, 0, tzinfo=KZ_TIMEZONE)
        ), [])
        # A far-away date whose swap is not near today is left alone.
        self.assertEqual(kinds(
            [entry("Астана - Кайрат", "12-05T19:00", "12-05T21:00")],
            now=april_as_july,
        ), [])

    def test_findings_are_sorted_by_severity_and_summarized(self):
        events = [
            entry("Арсенал - Челси тест", "10-08T20:00", "10-08T22:00",
                  record="a"),
            entry("Барселона - Реал", "10-09T02:30", "10-09T22:30",
                  channel="QAZSPORT HD", record="b"),
        ]
        found = find_schedule_anomalies(events, now=NOW)
        self.assertEqual([item["level"] for item in found],
                         ["error", "warning"])
        summary = summarize_anomalies(found)
        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["levels"]["error"], 1)
        self.assertEqual(summary["kinds"]["test_label"], 1)
        self.assertEqual(len({item["key"] for item in found}), 2)

    def test_inactive_broadcasts_and_bad_rows_are_ignored(self):
        broken = entry("Арсенал - Челси", "10-08T02:30", "10-08T22:30")
        broken["broadcasts"][0]["active"] = False
        self.assertEqual(kinds([broken, {"title": "x"}, None]), [])


class ScheduleAnomalyEndpointTests(unittest.TestCase):
    def test_endpoint_reads_real_rows_and_does_not_modify_them(self):
        import cloudrun_web as web
        from storage.database import SLPDatabase

        with tempfile.TemporaryDirectory() as tmp:
            database = SLPDatabase(Path(tmp) / "slp.db")
            today = datetime.now(KZ_TIMEZONE).date().isoformat()
            payload = {
                "source": "qazsport", "source_url": "https://example.test/",
                "channel": "QAZSPORT HD", "date": today, "time": "02:30",
                "timezone": "Asia/Almaty", "sport": "Баскетбол",
                "tournament": "Евролига",
                "title": "Барселона - Црвена Звезда",
                "raw_title": "Барселона - Црвена Звезда",
                "is_live": True, "is_live_broadcast": True,
                "estimated_broadcast_end_date": today,
                "estimated_broadcast_end": "22:30",
                "end_estimation_method": "explicit", "end_confidence": "high",
            }
            database.upsert_source_snapshot(
                run_id="anomaly-test", source="qazsport", scope_date=today,
                events=[payload],
            )
            before = self._dump(database)
            request = SimpleNamespace(
                app=SimpleNamespace(state=SimpleNamespace(database=database)),
            )
            original = web.current_user
            web.current_user = lambda _request: {"username": "t"}
            try:
                result = web.anomalies(request)
                with self.assertRaises(web.HTTPException):
                    web.anomalies(request, start="2026-13-01")
            finally:
                web.current_user = original
            self.assertEqual(result["checked_events"], 1)
            self.assertEqual(result["summary"]["levels"]["error"], 1)
            self.assertEqual(result["anomalies"][0]["kind"],
                             "duration_too_long")
            self.assertEqual(result["timezone"], "Asia/Almaty")
            json.dumps(result)
            self.assertEqual(self._dump(database), before)

    @staticmethod
    def _dump(database):
        with database._connect() as conn:
            return [tuple(row) for row in conn.execute(
                "SELECT storage_id,payload_json,active FROM events "
                "ORDER BY storage_id"
            )]


if __name__ == "__main__":
    unittest.main()

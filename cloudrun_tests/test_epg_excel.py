"""Focused regression for the two actual supplier XLSX layouts. No Gmail or network."""
from datetime import date
from io import BytesIO
from pathlib import Path
import tempfile
import unittest

from openpyxl import Workbook

from services.epg_excel import (
    InvalidEPG, import_parsed_epg, imported_epg_status, parse_epg_xlsx,
)
from storage.database import SLPDatabase
from cloudrun_web import event_rows


def workbook_bytes(rows):
    workbook = Workbook()
    ws = workbook.active
    for col, row, value in rows:
        ws.cell(row, col, value)
    result = BytesIO()
    workbook.save(result)
    workbook.close()
    return result.getvalue()


class EPGExcelTests(unittest.TestCase):
    def test_setanta1_one_sheet_live_only_and_epg_end(self):
        data = workbook_bytes([
            (2, 4, "29 сентября"), (1, 5, "AST"),
            (1, 6, 0.50), (2, 6, "LIVE. Футбол. АПЛ, 6 тур, Команда А - Команда Б"),
            (1, 7, 0.60), (2, 7, "Футбол. Повтор матча"),
            (1, 8, 0.70), (2, 8, "LIVE. Студийная программа"),
        ])
        result = parse_epg_xlsx(data,
            "EPG Setanta Sports 1 Kazakhstan 29.09.26 - 05.10.26_MEDIA.xlsx",
            today=date(2026, 9, 29))
        self.assertEqual(result.channel, "SETANTA SPORTS 1")
        self.assertEqual(len(result.events), 1)
        event = result.events[0]
        self.assertEqual((event["date"], event["time"]), ("2026-09-29", "12:00"))
        self.assertEqual(event["title"], "Команда А - Команда Б")
        self.assertEqual(event["sport"], "Футбол")
        self.assertEqual(event["tournament"], "АПЛ, 6 тур")
        self.assertEqual(event["end_estimation_method"], "next_program")
        self.assertEqual(event["estimated_broadcast_end"], "14:24")

    def test_qsport_multisheet_duration_and_midnight(self):
        workbook = Workbook()
        first = workbook.active
        first["C3"] = "30 сентября"
        first["B4"] = "AST"
        first["B5"] = 0.99
        first["C5"] = "LIVE. Футбол. Лига, 4 тур, Команда А – Команда Б"
        first["D5"] = 0.10
        first["B6"] = 1.09
        first["C6"] = "Обзор матчей"
        later = workbook.create_sheet()
        later["C3"] = "1 октября"
        later["B4"] = "AST"
        later["B5"] = 0.50
        later["C5"] = "LIVE. Фигурное катание. Финал, женщины"
        later["D5"] = 0.08
        data = BytesIO()
        workbook.save(data)
        workbook.close()
        result = parse_epg_xlsx(data.getvalue(),
            "EPG QSport Arena 28.09.26 - 04.10.26_MEDIA.xlsx",
            today=date(2026, 9, 29))
        self.assertEqual(result.channel, "Q ARENA")
        self.assertEqual(len(result.events), 2)
        self.assertEqual(result.events[0]["date"], "2026-09-30")
        self.assertEqual(result.events[0]["time"], "23:46")
        self.assertEqual(result.events[0]["estimated_broadcast_end_date"], "2026-10-01")
        self.assertEqual(result.events[0]["estimated_broadcast_end"], "02:10")
        self.assertEqual(result.events[1]["date"], "2026-10-01")

    def test_missing_live_is_not_invented_and_hash_idempotent(self):
        data = workbook_bytes([
            (3, 3, "1 октября"), (2, 4, "AST"),
            (2, 5, 0.50), (3, 5, "Футбол. Старый повтор"),
        ])
        result = parse_epg_xlsx(data, "EPG SETANTA QAZAQSTAN 29.09.26 - 05.10.26_MEDIA.xlsx",
                                today=date(2026, 9, 29))
        self.assertEqual(result.channel, "SETANTA SPORTS KZ")
        self.assertEqual(result.events, ())
        with tempfile.TemporaryDirectory() as folder:
            db = SLPDatabase(Path(folder) / "db.sqlite")
            self.assertEqual(import_parsed_epg(db, result)["status"], "imported")
            self.assertEqual(import_parsed_epg(db, result)["status"], "already_imported")
            statuses = imported_epg_status(db, first=date(2026, 10, 1),
                                           last=date(2026, 10, 1))
            kz = next(x for x in statuses if x["channel"] == "SETANTA SPORTS KZ")
            self.assertEqual(kz["status"], "ready")
            self.assertEqual(kz["live_events"], 0)
            self.assertEqual(db.active_event_count(), 0)

    def test_midnight_event_not_lost_when_next_day_header_absent(self):
        data = workbook_bytes([
            (3, 3, "29 сентября"), (2, 4, "AST"),
            (2, 5, 1.05), (3, 5, "LIVE. Футбол. Лига, 5 тур, Команда А - Команда Б"),
            (2, 6, 1.13), (3, 6, "Обзор футбола"),
        ])
        parsed = parse_epg_xlsx(
            data, "EPG QSport Arena 28.09.26 - 04.10.26_MEDIA.xlsx",
            today=date(2026, 9, 29),
        )
        self.assertEqual(parsed.events[0]["date"], "2026-09-30")
        self.assertIn("2026-09-30", parsed.scope_dates)
        with tempfile.TemporaryDirectory() as folder:
            db = SLPDatabase(Path(folder) / "db.sqlite")
            import_parsed_epg(db, parsed)
            self.assertEqual(len(db.load_active_source_snapshot(
                "email_epg_qarena", "2026-09-30"
            )), 1)

    def test_unknown_channel_rejected(self):
        with self.assertRaises(InvalidEPG):
            parse_epg_xlsx(b"not a spreadsheet", "mystery.xlsx")

    def test_simulcast_only_different_channels_matching_event(self):
        with tempfile.TemporaryDirectory() as folder:
            db = SLPDatabase(Path(folder) / "db.sqlite")
            def row(channel, tm, tournament="Лига", name="Команда А - Команда Б"):
                return {
                    "source": "unit", "date": "2026-10-03", "time": tm,
                    "channel": channel, "sport": "Футбол", "tournament": tournament,
                    "title": name, "raw_title": name,
                    "is_live_broadcast": True, "is_sport_event": True,
                }
            db.upsert_source_snapshot(
                run_id="t", source="x", scope_date="2026-10-03",
                events=[
                    row("EUROSPORT 1", "18:00"),
                    row("EUROSPORT 2", "18:10"),
                    row("EUROSPORT 1", "19:00"),
                    row("EUROSPORT 2", "20:00", "Кубок"),
                ],
            )
            events = event_rows(db, "2026-10-03", "2026-10-03")
            self.assertEqual(len(events), 3)
            first = events[0]
            self.assertEqual(first["channels"], ["EUROSPORT 1", "EUROSPORT 2"])
            self.assertEqual(len(first["broadcasts"]), 2)
            self.assertEqual(events[1]["channels"], ["EUROSPORT 1"])
            self.assertEqual(events[2]["channels"], ["EUROSPORT 2"])


if __name__ == "__main__":
    unittest.main()

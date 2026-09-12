import unittest
from io import BytesIO

from openpyxl import load_workbook

from services.export_xlsx import build_schedule_xlsx


class ExportXlsxTests(unittest.TestCase):
    def test_export_contains_schedule_and_summary(self):
        event = {
            "source": "qazsport",
            "source_url": "https://example.test/qazsport",
            "channel": "Qazsport",
            "date": "2099-09-12",
            "time": "18:00",
            "sport": "Футбол",
            "tournament": "QJ League",
            "title": "Тараз – Тобол",
            "is_live": True,
            "raw_title": "LIVE Футбол. QJ League Тараз – Тобол",
            "estimated_broadcast_end_date": "2099-09-12",
            "estimated_broadcast_end": "20:00",
            "end_estimation_method": "next_program",
            "end_confidence": "high",
        }

        payload = build_schedule_xlsx([event])
        self.assertTrue(payload.startswith(b"PK"))

        workbook = load_workbook(BytesIO(payload))
        self.assertEqual(workbook.sheetnames, ["Расписание", "Сводка"])
        sheet = workbook["Расписание"]
        self.assertEqual(sheet["G2"].value, "Тараз – Тобол")
        self.assertEqual(sheet["H2"].value, "Qazsport")
        self.assertEqual(sheet["I2"].value, "qazsport")


if __name__ == "__main__":
    unittest.main()

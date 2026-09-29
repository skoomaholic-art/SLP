"""25-column template integrity checks without contacting external services."""
from datetime import datetime
from io import BytesIO
import unittest

from openpyxl import Workbook, load_workbook

from services.editorial_export import HEADERS, InvalidTemplate, build_working_xlsx


class EditorialExportTests(unittest.TestCase):
    def _source(self):
        workbook = Workbook()
        sheet = workbook.active
        for col, header in enumerate(HEADERS, 1):
            sheet.cell(1, col).value = header
        source = [
            60000, "24.09", "23:45", "Футбол", "Лига наций УЕФА",
            "Португалия - Уэльс", "Freedom Media 8592",
            datetime(2026, 9, 24, 23, 35), datetime(2026, 9, 25, 1, 45),
            "APPROVED_LIVE_RU", "APPROVED_LIVE_KZ",
            "APPROVED_SOON_RU", "APPROVED_SOON_KZ",
            "", "", "", "Футбол Лига наций УЕФА",
            "APPROVED_ARCH_RU", "APPROVED_ARCH_KZ",
            "ПОРТУГАЛИЯ", "ПОРТУГАЛИЯ", "УЭЛЬС", "УЭЛЬС",
            "Футбол. Лига наций УЕФА", "Футбол. УЕФА Ұлттар лигасы",
        ]
        for col, value in enumerate(source, 1):
            sheet.cell(2, col).value = value
        return workbook

    def _event(self, date, time, title, tournament="Ла Лига"):
        return {
            "date": date, "time": time, "title": title,
            "sport": "Футбол", "tournament": tournament,
            "channel": "SETANTA SPORTS 1",
            "start_at": date + "T" + time + ":00+05:00",
            "platform_start_at": (
                datetime.fromisoformat(date + "T" + time + ":00") -
                __import__("datetime").timedelta(minutes=10)
            ).isoformat() + "+05:00",
            "end_at": (
                datetime.fromisoformat(date + "T" + time + ":00") +
                __import__("datetime").timedelta(minutes=150)
            ).isoformat() + "+05:00",
        }

    def test_preserve_existing_row_and_sort_new_matches(self):
        workbook = self._source()
        previous = self._event("2026-09-24", "23:45",
                               "Португалия - Уэльс", "Лига наций УЕФА")
        early = self._event("2026-09-23", "15:00", "Команда А - Команда Б")
        future = self._event("2026-09-25", "12:00", "Команда В - Команда Г")
        result = build_working_xlsx(workbook, [future, previous, early])
        sheet = result.active
        self.assertEqual(sheet.max_row, 4)
        self.assertEqual(sheet["F2"].value, "Команда А - Команда Б")
        self.assertEqual(sheet["F3"].value, "Португалия - Уэльс")
        self.assertEqual(sheet["F4"].value, "Команда В - Команда Г")
        self.assertEqual(sheet["J3"].value, "APPROVED_LIVE_RU")
        self.assertEqual(sheet["Y3"].value, "Футбол. УЕФА Ұлттар лигасы")
        self.assertEqual(sheet["U2"].value, "")
        self.assertEqual(sheet["Y4"].value, "")
        self.assertEqual(
            [sheet.cell(i, 1).value for i in range(2, 5)],
            [60000, 59990, 59980],
        )
        self.assertEqual(sheet["H2"].value, datetime(2026, 9, 23, 14, 50))
        output = BytesIO()
        result.save(output)
        reloaded = load_workbook(BytesIO(output.getvalue()))
        self.assertEqual(reloaded.active["F3"].value, "Португалия - Уэльс")
        reloaded.close()
        result.close()

    def test_wrong_header_fails_without_losing_data(self):
        workbook = self._source()
        workbook.active["T1"] = "WRONG TEAM 1"
        with self.assertRaises(InvalidTemplate):
            build_working_xlsx(workbook, [])
        self.assertEqual(workbook.active["F2"].value, "Португалия - Уэльс")
        workbook.close()


if __name__ == "__main__":
    unittest.main()

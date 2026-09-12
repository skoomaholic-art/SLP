import unittest
from datetime import date

from parsers.sportplus_epg import get_published_dates_from_html
from services.source_horizon import extract_qazsport_published_dates, nearest_monday


class SourceHorizonTests(unittest.TestCase):
    def test_nearest_monday_from_saturday(self):
        self.assertEqual(
            nearest_monday(date(2026, 9, 12)),
            date(2026, 9, 14),
        )

    def test_nearest_monday_is_inclusive(self):
        self.assertEqual(
            nearest_monday(date(2026, 9, 14)),
            date(2026, 9, 14),
        )

    def test_qazsport_dates_are_read_from_program_links(self):
        html = """
        <a href="/ru/program/2026-09-12">12</a>
        <a href="/ru/program/2026-09-13">13</a>
        <a href="https://qazsporttv.kz/ru/program/2026-09-16?lang=ru">16</a>
        """
        dates = extract_qazsport_published_dates(
            html,
            today=date(2026, 9, 12),
        )
        self.assertIn(date(2026, 9, 12), dates)
        self.assertIn(date(2026, 9, 13), dates)
        self.assertIn(date(2026, 9, 16), dates)

    def test_sportplus_header_dates_extend_past_monday(self):
        html = """
        <html><body>
          <div>Суббота 12.09 СБ</div>
          <div>Воскресенье 13.09 ВС</div>
          <div>Понедельник 14.09 ПН</div>
          <div>Среда 16.09 СР</div>
          <div>07:00</div><div>Программа</div>
        </body></html>
        """
        dates = get_published_dates_from_html(
            html,
            reference_date=date(2026, 9, 12),
        )
        self.assertEqual(dates[-1], date(2026, 9, 16))


if __name__ == "__main__":
    unittest.main()

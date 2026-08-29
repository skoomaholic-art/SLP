import unittest
from datetime import date

from parsers.sportplus import (
    BASE_URL,
    _extract_headers,
    parse_sportplus_html,
    parse_sportplus_title,
    strip_live_marker,
)


SAMPLE_HTML = """
<html>
  <body>
    <div>Пятница 28.08 ПТ</div>
    <div>Суббота 29.08 СБ</div>

    <div>07:00</div><div>ҚР Әнұраны</div>
    <div>16:55</div><div>ФУТБОЛ. КПЛ. 24-Й ТУР. ЕРТІС – АҚТӨБЕ. ПРЯМАЯ ТРАНСЛЯЦИЯ ИЗ ПАВЛОДАРА</div>
    <div>19:00</div><div>МИР ММА (ТЕЛЕЖУРНАЛ)</div>
    <div>22:00</div><div>ММА. OPEN FC 68. ПРЯМАЯ ТРАНСЛЯЦИЯ ИЗ САНКТ-ПЕТЕРБУРГА, РОССИЯ</div>
    <div>02:10</div><div>ҚР Әнұраны</div>

    <div>07:00</div><div>ҚР Әнұраны</div>
    <div>09:55</div><div>ФУТБОЛ. QJ LEAGUE. 16-Й ТУР. ҚАЙРАТ 2010 – ТОБЫЛ 2010. ТІКЕЛЕЙ ЭФИР</div>
    <div>12:00</div><div>НА ПУЛЬСЕ СПОРТА</div>
    <div>18:00</div><div>ФУТБОЛ ПЛЮС. ПРЯМОЙ ЭФИР</div>
    <div>19:00</div><div>ҚР Әнұраны</div>
  </body>
</html>
"""


class SportPlusParserTests(unittest.TestCase):
    def test_live_marker_is_removed(self):
        self.assertEqual(
            strip_live_marker(
                "ФУТБОЛ. КПЛ. ИРТЫШ – АКТОБЕ. ПРЯМАЯ ТРАНСЛЯЦИЯ ИЗ ПАВЛОДАРА"
            ),
            "ФУТБОЛ. КПЛ. ИРТЫШ – АКТОБЕ",
        )

    def test_title_is_structured(self):
        parsed = parse_sportplus_title(
            "ФУТБОЛ. КПЛ. 24-Й ТУР. ЕРТІС – АҚТӨБЕ. ПРЯМАЯ ТРАНСЛЯЦИЯ ИЗ ПАВЛОДАРА"
        )

        self.assertEqual(parsed["sport"], "Футбол")
        self.assertEqual(parsed["tournament"], "КПЛ. 24-Й ТУР")
        self.assertEqual(parsed["title"], "Иртыш – Актобе")

    def test_selected_day_returns_only_sport_live_events(self):
        events = parse_sportplus_html(
            SAMPLE_HTML,
            target_date="2026-08-29",
            reference_date=date(2026, 8, 29),
        )

        self.assertEqual(len(events), 1)
        event = events[0]

        self.assertEqual(event["source"], "sportplus")
        self.assertEqual(event["channel"], "Sport+ Qazaqstan")
        self.assertEqual(event["date"], "2026-08-29")
        self.assertEqual(event["time"], "09:55")
        self.assertEqual(event["sport"], "Футбол")
        self.assertEqual(event["title"], "Кайрат 2010 – ТОБЫЛ 2010")
        self.assertTrue(event["is_live"])

    def test_next_program_sets_broadcast_end(self):
        events = parse_sportplus_html(
            SAMPLE_HTML,
            target_date="2026-08-28",
            reference_date=date(2026, 8, 29),
        )

        first = events[0]
        self.assertEqual(first["time"], "16:55")
        self.assertEqual(first["estimated_broadcast_end"], "19:00")
        self.assertEqual(first["end_estimation_method"], "next_program")

    def test_midnight_program_belongs_to_next_calendar_date(self):
        events = parse_sportplus_html(
            SAMPLE_HTML,
            target_date="2026-08-28",
            reference_date=date(2026, 8, 29),
        )

        second = events[1]
        self.assertEqual(second["time"], "22:00")
        self.assertEqual(second["estimated_broadcast_end_date"], "2026-08-29")
        self.assertEqual(second["estimated_broadcast_end"], "02:10")

    def test_kazakh_weekday_headers_are_supported(self):
        strings = [
            "ДБ 24.08",
            "СС 25.08",
            "СР 26.08",
            "Бейсенбі 27.08",
            "ЖМ 28.08",
            "СБ 29.08",
            "ЖБ 30.08",
            "07:00",
            "ҚР Әнұраны",
        ]

        dates, last_index = _extract_headers(
            strings,
            date(2026, 8, 29),
        )

        self.assertEqual(last_index, 6)
        self.assertEqual(dates[0].isoformat(), "2026-08-24")
        self.assertEqual(dates[-1].isoformat(), "2026-08-30")

    def test_current_ru_url_does_not_use_www_redirect(self):
        self.assertEqual(
            BASE_URL,
            "https://sportplustv.kz/ru/tvguide",
        )

    def test_media_basket_is_recognized_as_basketball(self):
        parsed = parse_sportplus_title(
            "MEDIA BASKET ALMATY. ПОЛУФИНАЛЫ. ПРЯМАЯ ТРАНСЛЯЦИЯ ИЗ АЛМАТЫ"
        )
        self.assertEqual(parsed["sport"], "Баскетбол")


if __name__ == "__main__":
    unittest.main()

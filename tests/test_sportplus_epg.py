import unittest
from datetime import date

from parsers.sportplus_epg import parse_sportplus_epg_html


SAMPLE_HTML = """
<html><body>
  <div>Суббота 12.09 СБ</div>
  <div>Воскресенье 13.09 ВС</div>

  <div>07:00</div><div>ҚР Әнұраны</div>
  <div>12:00</div><div>НА ПУЛЬСЕ СПОРТА</div>
  <div>14:50</div><div>БАСКЕТБОЛ. КУБОК АЗИИ. КАЗАХСТАН – ЯПОНИЯ. ПРЯМАЯ ТРАНСЛЯЦИЯ</div>
  <div>16:25</div><div>ХОККЕЙНЫЙ ОБЗОР</div>
  <div>18:00</div><div>ФУТБОЛ ПЛЮС. ПРЯМОЙ ЭФИР</div>
  <div>23:30</div><div>ҚР Әнұраны</div>

  <div>07:00</div><div>ҚР Әнұраны</div>
  <div>11:00</div><div>ФУТБОЛ. ТЕСТ. ПРЯМАЯ ТРАНСЛЯЦИЯ</div>
  <div>13:00</div><div>НОВОСТИ</div>
</body></html>
"""


class SportPlusEpgTests(unittest.TestCase):
    def test_adapter_keeps_ordinary_epg_programmes(self):
        events = parse_sportplus_epg_html(
            SAMPLE_HTML,
            target_date="2026-09-12",
            reference_date=date(2026, 9, 12),
        )
        titles = [event["raw_title"] for event in events]
        self.assertIn("НА ПУЛЬСЕ СПОРТА", titles)
        ordinary = next(event for event in events if event["raw_title"] == "НА ПУЛЬСЕ СПОРТА")
        self.assertFalse(ordinary["is_live_broadcast"])

    def test_explicit_direct_sport_is_live_broadcast(self):
        events = parse_sportplus_epg_html(
            SAMPLE_HTML,
            target_date="2026-09-12",
            reference_date=date(2026, 9, 12),
        )
        live = next(event for event in events if event["time"] == "14:50")
        self.assertTrue(live["is_live_broadcast"])
        self.assertEqual(live["sport"], "Баскетбол")
        self.assertEqual(live["estimated_broadcast_end"], "16:25")

    def test_direct_studio_without_recognized_sport_is_not_invented_live(self):
        events = parse_sportplus_epg_html(
            SAMPLE_HTML,
            target_date="2026-09-12",
            reference_date=date(2026, 9, 12),
        )
        studio = next(event for event in events if event["time"] == "18:00")
        self.assertFalse(studio["is_live_broadcast"])

    def test_event_contract_contains_aware_times(self):
        events = parse_sportplus_epg_html(
            SAMPLE_HTML,
            target_date="2026-09-13",
            reference_date=date(2026, 9, 12),
        )
        live = next(event for event in events if event["time"] == "11:00")
        self.assertIn("+05:00", live["start_time"])
        self.assertIn("+05:00", live["end_time"])
        self.assertEqual(live["timezone"], "Asia/Almaty")


if __name__ == "__main__":
    unittest.main()

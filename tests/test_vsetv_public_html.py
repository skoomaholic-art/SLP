"""Regression coverage for the public VseTV HTML structure seen on 30 Sep 2026."""
from datetime import date
import unittest

from parsers.vsetv_live import parse_vsetv_week_html
from services.vsetv_sources import normalize_live_record


PAGE_HEAD = """
<select name="timezone">
  <option value="11" selected>Казахстан: Актюбинское время - UTC+5</option>
  <option value="14">Россия: Московское время - MSK</option>
</select>
<select name="selected_hours1"><option selected>5</option></select>
<div class="weekdaytitle">Вторник, 29 сентября</div>
"""
LIVE = '<img src="pic/ico_live.gif">Гандбол. Чемпионат России. Финал. Прямая трансляция.'
HIDDEN_TIME = '<div class="time">19:<img src="/pic/sj.gif"><img src="/pic/sj.gif"></div>'
MIDNIGHT = '<div class="time"><img src="/pic/n1.gif"><img src="/pic/n1.gif">:<img src="/pic/n1.gif"><img src="/pic/n1.gif"></div>'


class VseTVPublicHTMLTests(unittest.TestCase):
    def test_obfuscated_time_and_kazakhstan_timezone(self):
        html = PAGE_HEAD + HIDDEN_TIME + '<div class="prname2">' + LIVE + '</div>'
        rows = parse_vsetv_week_html(
            html, channel="МАТЧ! ПЛАНЕТА",
            anchor_date=date(2026, 9, 30), require_timezone=True,
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["date"], rows[0]["time"]),
                         ("2026-09-29", "19:55"))
        result = normalize_live_record(rows[0], channel="МАТЧ! ПЛАНЕТА")
        self.assertIsNotNone(result)
        self.assertEqual((result["date"], result["time"]),
                         ("2026-09-29", "19:55"))
        self.assertEqual(result["source_timezone"], "Asia/Almaty")
        self.assertEqual(result["time_normalization"], "already_kz_utc5")

    def test_early_morning_belongs_to_next_day(self):
        html = PAGE_HEAD + MIDNIGHT + '<div class="prname2">' + LIVE + '</div>'
        rows = parse_vsetv_week_html(
            html, channel="МАТЧ! ПЛАНЕТА",
            anchor_date=date(2026, 9, 30), require_timezone=True,
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["date"], rows[0]["time"]),
                         ("2026-09-30", "00:00"))

    def test_moscow_selected_gets_only_one_conversion(self):
        html = PAGE_HEAD.replace(
            'value="11" selected', 'value="11"'
        ).replace('value="14"', 'value="14" selected')
        html += HIDDEN_TIME + '<div class="prname2">' + LIVE + '</div>'
        rows = parse_vsetv_week_html(
            html, channel="МАТЧ! ПЛАНЕТА",
            anchor_date=date(2026, 9, 30), require_timezone=True,
        )
        result = normalize_live_record(rows[0], channel="МАТЧ! ПЛАНЕТА")
        self.assertEqual((result["date"], result["time"]),
                         ("2026-09-29", "21:55"))
        self.assertEqual(result["source_timezone"], "Europe/Moscow")

    def test_unknown_time_image_does_not_guess(self):
        html = PAGE_HEAD + '<div class="time">19:<img src="/pic/other.gif">5</div>'
        html += '<div class="prname2">' + LIVE + '</div>'
        rows = parse_vsetv_week_html(
            html, channel="МАТЧ! ПЛАНЕТА",
            anchor_date=date(2026, 9, 30), require_timezone=True,
        )
        self.assertEqual(rows, [])

    def test_missing_timezone_not_assumed_for_fetched_pages(self):
        html = HIDDEN_TIME + '<div class="weekdaytitle">Вторник, 29 сентября</div>'
        html += HIDDEN_TIME + '<div class="prname2">' + LIVE + '</div>'
        rows = parse_vsetv_week_html(
            html, channel="МАТЧ! ПЛАНЕТА",
            anchor_date=date(2026, 9, 30), require_timezone=True,
        )
        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()

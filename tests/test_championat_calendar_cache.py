import unittest
from datetime import date

from parsers.championat import parse_match_center_html
from verifiers.championat_calendar import match_championat_calendar


HTML = """
<html><body>
<div>13 сентября 2026</div>
<div class="match-card">
  <div>14:00</div>
  <a href="/hockey/_superleague/tournament/7092/match/999001/">Авангард – Металлург Мг</a>
  <span>Не начался</span>
</div>
<div class="match-card">
  <div>17:00</div>
  <a href="/football/_other/tournament/6976/match/999002/">Кайрат – Тобол</a>
  <span>Не начался</span>
</div>
</body></html>
"""


def epg(**overrides):
    value = {
        "channel": "KHL HD",
        "date": "2026-09-13",
        "time": "15:55",
        "sport": "Хоккей",
        "tournament": "Фонбет Чемпионат КХЛ",
        "title": "Авангард – Металлург Мг",
        "raw_title": "Фонбет Чемпионат КХЛ. Авангард – Металлург Мг",
    }
    value.update(overrides)
    return value


class ChampionatCalendarParserTests(unittest.TestCase):
    def test_match_center_msk_time_is_converted_to_almaty(self):
        rows = parse_match_center_html(HTML, date(2026, 9, 13))
        self.assertEqual(len(rows), 2)
        hockey = next(row for row in rows if row["sport"] == "Хоккей")
        self.assertEqual(hockey["time"], "16:00")
        self.assertEqual(hockey["timezone"], "Asia/Almaty")
        self.assertEqual(hockey["source_timezone"], "Europe/Moscow")

    def test_local_match_confirms_tv_preshow(self):
        rows = parse_match_center_html(HTML, date(2026, 9, 13))
        result = match_championat_calendar(epg(), rows)
        self.assertEqual(result["state"], "confirmed_direct")
        self.assertEqual(result["difference_minutes"], 5)
        self.assertEqual(result["verification_source"], "championat_calendar")

    def test_local_match_rejects_delayed_replay(self):
        rows = parse_match_center_html(HTML, date(2026, 9, 13))
        result = match_championat_calendar(
            epg(time="23:30"),
            rows,
        )
        self.assertEqual(result["state"], "mismatch")
        self.assertLess(result["difference_minutes"], -10)

    def test_missing_championat_event_stays_unknown(self):
        rows = parse_match_center_html(HTML, date(2026, 9, 13))
        result = match_championat_calendar(
            epg(title="Локальный турнир неизвестной лиги", raw_title="Локальный турнир неизвестной лиги"),
            rows,
        )
        self.assertEqual(result["state"], "unknown")


if __name__ == "__main__":
    unittest.main()

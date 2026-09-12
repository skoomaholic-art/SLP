import unittest
from datetime import date

from parsers.championat import parse_match_center_html, parse_match_center_text
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

READER_TEXT = """
Футбол
Альфа-Банк Российская Премьер-лига. 8-й тур
* 18:30 Динамо М–Оренбург Не начался
* 20:45 ЦСКА – Рубин Не начался
Хоккей
OLIMPBET МХЛ — регулярный чемпионат
* 17:00 СКА-1946–Тайфун 2 : 0 Перерыв
Автоспорт
WRC 2026. Ралли Чили
* 21:08 Ралли Чили. Спецучасток 10 Не началось
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

    def test_reader_accepts_compact_en_dash_without_splitting_hyphenated_team(self):
        rows = parse_match_center_text(READER_TEXT, date(2026, 9, 13))
        titles = {row["title"] for row in rows}
        self.assertIn("Динамо М–Оренбург", titles)
        self.assertIn("ЦСКА – Рубин", titles)
        self.assertIn("СКА-1946–Тайфун", titles)

    def test_reader_keeps_non_head_to_head_motorsport_stage(self):
        rows = parse_match_center_text(READER_TEXT, date(2026, 9, 13))
        event = next(row for row in rows if "Спецучасток 10" in row["title"])
        self.assertEqual(event["sport"], "Автоспорт")
        self.assertEqual(event["tournament"], "WRC 2026. Ралли Чили")

    def test_reader_time_is_converted_from_moscow_to_almaty(self):
        rows = parse_match_center_text(READER_TEXT, date(2026, 9, 13))
        football = next(row for row in rows if row["title"] == "Динамо М–Оренбург")
        self.assertEqual(football["time"], "20:30")
        self.assertEqual(football["timezone"], "Asia/Almaty")

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
            epg(
                title="Локальный турнир неизвестной лиги",
                raw_title="Локальный турнир неизвестной лиги",
            ),
            rows,
        )
        self.assertEqual(result["state"], "unknown")


if __name__ == "__main__":
    unittest.main()

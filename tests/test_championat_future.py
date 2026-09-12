import unittest
from datetime import date

from parsers.championat_future import (
    extract_tournament_links,
    parse_tournament_calendar_text,
    select_tournament_links,
)


SECTION_TEXT = """
Россия
[Альфа-Банк Российская Премьер-лига](https://www.championat.com/football/_russiapl/tournament/7096/)
[Фонбет Кубок России](https://www.championat.com/football/_russiacup/tournament/7094/)
Англия
[Англия — Премьер-лига](https://www.championat.com/football/_england/tournament/7101/)
"""

CALENDAR_TEXT = """
|  | Тур | Дата, время |  | Счет |
|  | 9 | 16.09.2026 18:30 | Тур 9 16.09.2026 18:30 [Спартак М](https://example.test/a) – [Факел](https://example.test/b) | – : – |
|  | 9 | 17.09.2026 20:45 | Тур 9 17.09.2026 20:45 Динамо Мх–ЦСКА | – : – |
|  | 10 | 09.10.2026 19:30 | Тур 10 09.10.2026 19:30 Ростов – Акрон | – : – |
"""


class ChampionatFutureCalendarTests(unittest.TestCase):
    def test_extracts_tournament_links_from_reader_markdown(self):
        links = extract_tournament_links(SECTION_TEXT, "/stat/football/")
        self.assertEqual(len(links), 3)
        self.assertEqual(links[0]["sport"], "Футбол")
        self.assertTrue(links[0]["url"].endswith("/tournament/7096/"))

    def test_selects_only_tournaments_relevant_to_epg_hints(self):
        links = extract_tournament_links(SECTION_TEXT, "/stat/football/")
        selected = select_tournament_links(
            links,
            ["Российская Премьер-лига", "England Premier League"],
        )
        names = {item["name"] for item in selected}
        self.assertIn("Альфа-Банк Российская Премьер-лига", names)
        self.assertNotIn("Фонбет Кубок России", names)

    def test_calendar_parser_keeps_only_requested_week(self):
        rows = parse_tournament_calendar_text(
            CALENDAR_TEXT,
            tournament="Альфа-Банк Российская Премьер-лига",
            sport="Футбол",
            source_url="https://www.championat.com/football/_russiapl/tournament/7096/calendar/",
            start_date=date(2026, 9, 13),
            end_date=date(2026, 9, 20),
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["title"], "Спартак М – Факел")
        self.assertEqual(rows[0]["date"], "2026-09-16")
        # Championat calendar is Moscow time; Almaty is +2h in September 2026.
        self.assertEqual(rows[0]["time"], "20:30")
        self.assertEqual(rows[1]["title"], "Динамо Мх–ЦСКА")
        self.assertEqual(rows[1]["time"], "22:45")


if __name__ == "__main__":
    unittest.main()

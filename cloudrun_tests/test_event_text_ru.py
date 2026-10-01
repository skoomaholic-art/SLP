import unittest

from services.event_text_ru import normalize_event_fields, strip_bookmakers


class EventTextRussianTests(unittest.TestCase):
    def test_kazakh_football_title_is_normalized_to_russian_structure(self):
        result = normalize_event_fields(
            title="ФУТБОЛ. УЕФА Ұлттар Лигасы. Туркия - Италия",
            sport="Футбол",
            tournament="",
        )
        self.assertEqual(result["sport"], "Футбол")
        self.assertEqual(result["tournament"], "Лига наций УЕФА")
        self.assertEqual(result["title"], "Турция - Италия")

    def test_asian_games_weightlifting_is_normalized(self):
        result = normalize_event_fields(
            title="XX ЖАЗҒЫ АЗИЯ ОЙЫНДАРЫ. Ауыр атлетика (Әйелдер 86 кг). Финал",
            sport="Ауыр атлетика",
            tournament="",
        )
        self.assertEqual(result["sport"], "Тяжёлая атлетика")
        self.assertEqual(result["tournament"], "XX Летние Азиатские игры")
        self.assertEqual(result["title"], "Женщины, 86 кг. Финал")

    def test_english_event_is_normalized_to_russian(self):
        result = normalize_event_fields(
            title="Football. Premier League. Arsenal - Chelsea",
            sport="",
            tournament="",
        )
        self.assertEqual(result["sport"], "Футбол")
        self.assertEqual(result["tournament"], "Премьер-лига")
        self.assertEqual(result["title"], "Арсенал - Челси")

    def test_bookmaker_words_are_removed_everywhere(self):
        result = normalize_event_fields(
            title="FONBET Arsenal - Chelsea BET",
            sport="Football",
            tournament="BETBOOM Premier League",
        )
        self.assertNotRegex(
            " ".join(result.values()).casefold(),
            r"fonbet|betboom|\bbet\b|фонбет|бетбум",
        )
        self.assertEqual(strip_bookmakers("ФОНБЕТ Чемпионат КХЛ"), "Чемпионат КХЛ")

    def test_real_betis_is_not_mistaken_for_standalone_bet_word(self):
        self.assertEqual(
            strip_bookmakers("Real Betis - Barcelona"),
            "Real Betis - Barcelona",
        )


if __name__ == "__main__":
    unittest.main()

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

    def test_qazsport_kazakh_disciplines_are_translated(self):
        result = normalize_event_fields(
            title="Садақ ату (Әйелдер). Командалық жарыс. Финал",
            sport="Садақ ату",
            tournament="XX Жазғы Азия Ойындары",
        )
        self.assertEqual(result["title"], "Женщины. Командные соревнования. Финал")
        self.assertEqual(result["tournament"], "XX Летние Азиатские игры")
        self.assertEqual(result["sport"], "Стрельба из лука")

        second = normalize_event_fields(
            title="Суға секіру (Айелде)",
            sport="Суға секіру",
            tournament="XX ЖАЗҒЫ АЗИЯ ОЙЫНДАРЫ",
        )
        self.assertEqual(second["title"], "Женщины")
        self.assertEqual(second["sport"], "Прыжки в воду")

    def test_sportplus_all_caps_becomes_readable(self):
        result = normalize_event_fields(
            title="СУПЕРЛИГА КАЗАХСТАНА. 7-Й ТУР. ОКЖЕТПЕС - АЯТ",
            sport="ФУТЗАЛ",
            tournament="",
        )
        self.assertEqual(
            result["title"],
            "Суперлига Казахстана. 7-й тур. Окжетпес - Аят",
        )

    def test_alash_pride_brand_stays_latin(self):
        result = normalize_event_fields(
            title="ALASH PRIDE 131",
            sport="MMA",
            tournament="",
        )
        self.assertEqual(result["title"], "ALASH PRIDE 131")
        self.assertEqual(result["sport"], "ММА")

    def test_qazsport_world_championship_and_qualification_translate_cleanly(self):
        result = normalize_event_fields(
            title="Әлем чемпионаты. Іріктеу",
            sport="Дзюдо",
            tournament="",
        )
        self.assertEqual(result["sport"], "Дзюдо")
        self.assertEqual(result["tournament"], "Чемпионат мира")
        self.assertEqual(result["title"], "Отборочный этап")

    def test_qazsport_wrestling_phrase_translates_cleanly(self):
        result = normalize_event_fields(
            title="Күрес (Еркін күрес, Әйелдер күресі). Іріктеу",
            sport="Күрес",
            tournament="XX Жазғы Азия Ойындары",
        )
        self.assertEqual(result["sport"], "Борьба")
        self.assertEqual(result["tournament"], "XX Летние Азиатские игры")
        self.assertEqual(
            result["title"],
            "Вольная борьба. Женщины. Отборочный этап",
        )

    def test_mixed_caps_team_names_are_normalized_without_translation(self):
        first = normalize_event_fields(
            title="ЖАС КЫРАН 2010 - Кайрат 2010",
            sport="Футбол",
            tournament="КДЖ лига. 20-Й ТУР",
        )
        self.assertEqual(first["title"], "Жас Кыран 2010 - Кайрат 2010")
        self.assertEqual(first["tournament"], "КДЖ лига. 20-й тур")

        second = normalize_event_fields(
            title="ТОБЫЛ 2009 - Актобе 2009",
            sport="Футбол",
            tournament="КДЖ лига. 20-Й ТУР",
        )
        self.assertEqual(second["title"], "Тобыл 2009 - Актобе 2009")
        self.assertEqual(second["tournament"], "КДЖ лига. 20-й тур")

    def test_nations_league_prefix_is_split_from_fixture(self):
        result = normalize_event_fields(
            title="Лига наций УЕФА Дания - Португалия",
            sport="Футбол",
            tournament="Лига наций УЕФА Дания - Португалия",
        )
        self.assertEqual(result["sport"], "Футбол")
        self.assertEqual(result["tournament"], "Лига наций УЕФА")
        self.assertEqual(result["title"], "Дания - Португалия")

    def test_aca_brand_stays_uppercase_latin(self):
        result = normalize_event_fields(
            title="ACA 208",
            sport="MMA",
            tournament="",
        )
        self.assertEqual(result["title"], "ACA 208")
        self.assertEqual(result["sport"], "ММА")

    def test_real_betis_is_not_mistaken_for_standalone_bet_word(self):
        self.assertEqual(
            strip_bookmakers("Real Betis - Barcelona"),
            "Real Betis - Barcelona",
        )


if __name__ == "__main__":
    unittest.main()

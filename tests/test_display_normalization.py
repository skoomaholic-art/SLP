import unittest

from bot.formatters import display_title, normalize_display_text


class DisplayNormalizationTests(unittest.TestCase):
    def test_uppercase_kazakhstan_clubs_are_normalized(self):
        self.assertEqual(
            normalize_display_text("ШАХТЕР 2010 – ОРДАБАСЫ 2010"),
            "Шахтёр 2010 – Ордабасы 2010",
        )
        self.assertEqual(
            normalize_display_text("Иртыш – ОҚЖЕТПЕС"),
            "Иртыш – Окжетпес",
        )
        self.assertEqual(
            normalize_display_text("Қайрат – ТОБЫЛ"),
            "Кайрат – Тобол",
        )

    def test_foreign_club_names_lose_shouting_caps(self):
        self.assertEqual(
            normalize_display_text("ГАЛАТАСАРАЙ – КОДЖАЭЛИСПОР"),
            "Галатасарай – Коджаэлиспор",
        )

    def test_common_kazakh_tournament_phrases_are_russian(self):
        self.assertEqual(
            normalize_display_text(
                "Футбол. УЕФА Чемпиондар Лигасы. Жалпы кезең. 1 тур"
            ),
            "Футбол. Лига чемпионов УЕФА. Общий этап. 1 тур",
        )

    def test_sports_acronyms_stay_uppercase(self):
        self.assertEqual(
            normalize_display_text(
                'Хоккей. Фонбет Чемпионат КХЛ. "СИБИРЬ" - "АВТОМОБИЛИСТ"'
            ),
            'Хоккей. Фонбет Чемпионат КХЛ. "Сибирь" – "Автомобилист"',
        )

    def test_grand_slam_is_russian_in_telegram(self):
        self.assertEqual(
            display_title({"title": "Grand Slam"}),
            "Большой шлем",
        )


if __name__ == "__main__":
    unittest.main()

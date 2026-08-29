import unittest

from parsers.qazsport import (
    normalize_russian_text,
    parse_qazsport_title,
)


class QazsportNormalizationTests(unittest.TestCase):

    def test_quarter_final_backslash_is_normalized(self):
        event = parse_qazsport_title(
            "Волейбол. Азия чемпионаты (Әйелдер) "
            "1\\4 финал Индонезия - Иран"
        )

        self.assertEqual(event["sport"], "Волейбол")
        self.assertEqual(
            event["tournament"],
            "Чемпионат Азии. Женщины",
        )
        self.assertEqual(
            event["title"],
            "1/4 финала Индонезия - Иран",
        )

    def test_half_final_backslash_is_normalized(self):
        self.assertEqual(
            normalize_russian_text("1\\2 финал"),
            "1/2 финала",
        )

    def test_kairat_is_normalized(self):
        event = parse_qazsport_title(
            "Футбол. УЕФА Еуропа Лигасы "
            "Плей-офф кезеңі "
            "Андерлехт (Бельгия) - Қайрат (Қазақстан)"
        )

        self.assertEqual(event["sport"], "Футбол")
        self.assertEqual(
            event["tournament"],
            "Лига Европы УЕФА Плей-офф",
        )
        self.assertEqual(
            event["title"],
            "Андерлехт (Бельгия) - Кайрат (Казахстан)",
        )

    def test_grand_slam_judo_without_sport_prefix(self):
        event = parse_qazsport_title(
            "Grand Slam Дзюдо"
        )

        self.assertEqual(event["sport"], "Дзюдо")
        self.assertEqual(event["tournament"], "Grand Slam")
        self.assertEqual(event["title"], "Grand Slam")

    def test_grand_slam_judo_with_sport_prefix(self):
        event = parse_qazsport_title(
            "Дзюдо. Grand Slam"
        )

        self.assertEqual(event["sport"], "Дзюдо")
        self.assertEqual(event["tournament"], "Grand Slam")
        self.assertEqual(event["title"], "Grand Slam")

    def test_conference_draw_stays_structured(self):
        event = parse_qazsport_title(
            "Футбол. УЕФА Конференциялар Лигасы "
            "жалпы кезеңнің жеребе тарту рәсімі"
        )

        self.assertEqual(event["sport"], "Футбол")
        self.assertEqual(
            event["tournament"],
            "Лига конференций УЕФА",
        )
        self.assertEqual(
            event["title"],
            "Жеребьёвка общего этапа",
        )


if __name__ == "__main__":
    unittest.main()
import unittest

from parsers.qazsport import (
    build_url,
    clean_title,
    extract_sport_and_remainder,
    normalize_russian_text,
    parse_qazsport_title,
    time_to_minutes,
)


class QazsportBaselineTests(unittest.TestCase):

    def test_build_url_for_date(self):
        self.assertEqual(
            build_url("2026-08-29"),
            "https://qazsporttv.kz/ru/program/2026-08-29",
        )

    def test_time_to_minutes(self):
        self.assertEqual(
            time_to_minutes("23:50"),
            23 * 60 + 50,
        )

    def test_clean_live_title(self):
        self.assertEqual(
            clean_title(
                "11:55 LIVE Волейбол. Матч",
                "11:55",
            ),
            "Волейбол. Матч",
        )

    def test_extract_sport(self):
        raw_sport, sport, remainder = (
            extract_sport_and_remainder(
                "Футбол. QJ League Тараз - Тобол"
            )
        )

        self.assertEqual(
            raw_sport,
            "Футбол",
        )
        self.assertEqual(
            sport,
            "Футбол",
        )
        self.assertEqual(
            remainder,
            "QJ League Тараз - Тобол",
        )

    def test_kazakh_tournament_normalization(self):
        self.assertEqual(
            normalize_russian_text(
                "Азия чемпионаты (Әйелдер)"
            ),
            "Чемпионат Азии. Женщины",
        )

    def test_uefa_conference_draw(self):
        event = parse_qazsport_title(
            "Футбол. "
            "УЕФА Конференциялар Лигасы "
            "жалпы кезеңнің жеребе тарту рәсімі"
        )

        self.assertEqual(
            event["sport"],
            "Футбол",
        )
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
import unittest

from services.schedule_merge import (
    deduplicate_broadcasts,
    group_simulcasts,
    merge_source_schedules,
    normalize_match_text,
    same_sporting_event,
    unique_channels,
)


def event(
    *,
    channel="Qazsport",
    date="2026-08-29",
    time="18:50",
    title="Кайрат - Андерлехт",
    sport="Футбол",
    tournament="Лига Европы УЕФА",
):
    return {
        "source": channel.casefold(),
        "source_url": "https://example.com",
        "channel": channel,
        "date": date,
        "time": time,
        "sport": sport,
        "tournament": tournament,
        "title": title,
        "is_live": True,
        "raw_title": title,
        "estimated_broadcast_end_date": date,
        "estimated_broadcast_end": "21:00",
        "end_estimation_method": "next_program",
        "end_confidence": "high",
    }


class ScheduleMergeTests(unittest.TestCase):
    def test_true_duplicate_broadcast_is_removed(self):
        first = event()
        duplicate = dict(first)

        result = deduplicate_broadcasts([first, duplicate])
        self.assertEqual(len(result), 1)

    def test_same_event_on_two_channels_is_preserved(self):
        first = event(channel="Qazsport", time="18:50")
        second = event(
            channel="Sport+ Qazaqstan",
            time="19:00",
        )

        result = merge_source_schedules([first], [second])
        self.assertEqual(len(result), 2)
        self.assertEqual(
            {item["channel"] for item in result},
            {"Qazsport", "Sport+ Qazaqstan"},
        )

    def test_same_event_on_two_channels_is_grouped_for_display(self):
        first = event(channel="Qazsport", time="18:50")
        second = event(
            channel="Sport+ Qazaqstan",
            time="19:00",
        )

        groups = group_simulcasts([first, second])
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]), 2)

    def test_draws_from_different_tournaments_are_not_grouped(self):
        first = event(
            channel="Qazsport",
            title="Жеребьёвка общего этапа",
            tournament="Лига чемпионов УЕФА",
        )
        second = event(
            channel="Sport+ Qazaqstan",
            time="19:00",
            title="Жеребьёвка общего этапа",
            tournament="Лига конференций УЕФА",
        )

        self.assertFalse(same_sporting_event(first, second))
        self.assertEqual(len(group_simulcasts([first, second])), 2)

    def test_same_channel_rebroadcast_is_not_simulcast(self):
        first = event(channel="Qazsport", time="18:50")
        second = event(channel="Qazsport", time="19:00")
        self.assertFalse(same_sporting_event(first, second))

    def test_kazakh_letters_are_normalized_only_for_matching(self):
        self.assertEqual(
            normalize_match_text("Қайрат"),
            normalize_match_text("Кайрат"),
        )

    def test_unique_channels(self):
        result = unique_channels(
            [
                event(channel="Qazsport"),
                event(channel="Sport+ Qazaqstan"),
                event(channel="Qazsport", time="20:00"),
            ]
        )
        self.assertEqual(
            result,
            ["Qazsport", "Sport+ Qazaqstan"],
        )

    def test_kairat_latin_and_cyrillic_are_grouped(self):
        first = event(
            channel="Qazsport",
            title="Кайрат - Андерлехт",
            time="23:50",
        )
        second = event(
            channel="Sport+ Qazaqstan",
            title="Kairat - Андерлехт",
            time="23:55",
        )

        self.assertTrue(same_sporting_event(first, second))
        self.assertEqual(len(group_simulcasts([first, second])), 1)

    def test_cross_midnight_same_event_is_grouped(self):
        first = event(
            channel="Qazsport",
            date="2026-08-29",
            time="23:55",
        )
        second = event(
            channel="Sport+ Qazaqstan",
            date="2026-08-30",
            time="00:05",
        )

        self.assertTrue(same_sporting_event(first, second))
        self.assertEqual(len(group_simulcasts([first, second])), 1)


if __name__ == "__main__":
    unittest.main()

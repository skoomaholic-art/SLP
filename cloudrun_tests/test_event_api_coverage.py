"""ESPN cross-check coverage: request planning only, no network."""
import asyncio
import unittest
from unittest.mock import patch

from services import event_api_validation as validation


def candidate(day, sport, title="Арсенал - Ливерпуль"):
    return {"date": day, "sport": sport, "title": title, "time": "22:00"}


class EspnCoverageTests(unittest.TestCase):
    def test_feed_list_covers_cups_and_has_no_duplicates(self):
        feeds = validation.ESPN_FEEDS
        self.assertEqual(len(feeds), len(set(feeds)))
        leagues = {league for _sport, league in feeds}
        for expected in ("eng.league_cup", "ger.dfb_pokal",
                         "uefa.europa.conf", "uefa.nations", "ksa.1"):
            self.assertIn(expected, leagues)
        # The slug used by sports-api for the Saudi league is rejected by ESPN.
        self.assertNotIn("sau.1", leagues)

    def test_only_feeds_of_scheduled_sports_are_requested(self):
        plan = validation._espn_sports_by_date([
            candidate("2026-10-08", "Футбол"),
            candidate("2026-10-09", "Баскетбол"),
            candidate("2026-10-09", "Теннис"),
        ])
        # The previous day is included for matches after midnight in Almaty.
        self.assertEqual(sorted(plan),
                         ["2026-10-07", "2026-10-08", "2026-10-09"])
        self.assertEqual(plan["2026-10-07"], {"soccer"})
        self.assertEqual(plan["2026-10-08"], {"soccer", "basketball", "other"})
        requests = validation._espn_requests(sorted(plan), plan)
        soccer = sum(1 for sport, _ in validation.ESPN_FEEDS if sport == "soccer")
        self.assertEqual(
            sum(1 for day, _s, _l in requests if day == "2026-10-07"), soccer
        )
        self.assertEqual(
            [league for day, _s, league in requests if day == "2026-10-09"],
            ["nba"],
        )

    def test_unknown_sport_asks_every_feed_and_tennis_asks_none(self):
        unknown = validation._espn_sports_by_date([candidate("2026-10-08", "")])
        self.assertEqual(
            len(validation._espn_requests(["2026-10-08"], unknown)),
            len(validation.ESPN_FEEDS),
        )
        tennis = validation._espn_sports_by_date(
            [candidate("2026-10-08", "Теннис")]
        )
        self.assertEqual(validation._espn_requests(sorted(tennis), tennis), [])

    def test_day_limit_bounds_the_number_of_requests(self):
        days = [f"2026-10-{day:02d}" for day in range(1, 21)]
        requests = validation._espn_requests(days, None)
        self.assertEqual(
            len(requests), validation.ESPN_MAX_DAYS * len(validation.ESPN_FEEDS)
        )

    def test_events_are_fetched_concurrently_and_deduplicated(self):
        calls = []
        running = peak = 0

        async def fake_get_json(_session, url, params=None):
            nonlocal running, peak
            calls.append((url, params["dates"]))
            running += 1
            peak = max(peak, running)
            await asyncio.sleep(0)
            running -= 1
            if "/eng.1/" not in url:
                return None  # a failing feed must not break the others
            return {"events": [{
                "id": "401", "date": "2026-10-08T19:00Z",
                "competitions": [{"competitors": [
                    {"homeAway": "home", "team": {"displayName": "Arsenal"}},
                    {"homeAway": "away", "team": {"displayName": "Liverpool"}},
                ]}],
                "status": {"type": {"state": "pre", "name": "STATUS_SCHEDULED"}},
            }]}

        plan = validation._espn_sports_by_date(
            [candidate("2026-10-08", "Футбол")]
        )
        with patch.object(validation, "_get_json", fake_get_json):
            events = asyncio.run(
                validation._espn_events(None, sorted(plan), plan)
            )
        soccer = sum(1 for sport, _ in validation.ESPN_FEEDS if sport == "soccer")
        self.assertEqual(len(calls), 2 * soccer)
        self.assertGreater(peak, 1)
        self.assertLessEqual(peak, validation.ESPN_CONCURRENCY)
        # The same fixture returned for two requested days is kept once.
        self.assertEqual(len(events), 1)
        self.assertEqual(
            (events[0]["home"], events[0]["away"], events[0]["league"]),
            ("Arsenal", "Liverpool", "eng.1"),
        )
        self.assertTrue(events[0]["source_url"].startswith("https://"))


if __name__ == "__main__":
    unittest.main()

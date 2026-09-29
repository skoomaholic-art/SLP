import asyncio
import unittest
from datetime import date
from unittest.mock import AsyncMock, patch

from parsers.championat import ChampionatCalendar
from parsers.tvplus import EUROSPORT_CHANNELS, TARGET_CHANNELS
from parsers.tvguide_cached import (
    SOURCE,
    _reconcile_external,
    infer_direct_event,
    normalize_provider_timezone,
)


class TVGuideTests(unittest.TestCase):
    def event(
        self,
        title: str,
        *,
        channel: str = "Setanta Sports 1",
        is_live: bool = False,
        method: str = "none",
        state: str = "unknown",
    ) -> dict:
        return {
            "source": "tvplus",
            "source_url": "https://example.test/channel",
            "channel": channel,
            "date": "2026-09-12",
            "time": "19:00",
            "timezone": "Asia/Almaty",
            "sport": "",
            "tournament": "",
            "title": title,
            "raw_title": title,
            "is_live": is_live,
            "is_live_broadcast": is_live,
            "live_state": state,
            "live_evidence_method": method,
            "estimated_broadcast_end_date": "2026-09-12",
            "estimated_broadcast_end": "21:00",
            "end_estimation_method": "provider_epg",
            "end_confidence": "high",
        }

    def test_inventory_contains_all_17_epg_channels(self):
        names = {item.name for item in TARGET_CHANNELS + EUROSPORT_CHANNELS}
        self.assertEqual(len(names), 17)
        self.assertIn("Eurosport", names)
        self.assertIn("Eurosport 2", names)
        self.assertIn("KHL HD", names)
        self.assertIn("Setanta Sports KZ", names)
        self.assertIn("МАТЧ! Планета", names)

    def test_epg_clock_is_converted_from_utc_to_almaty(self):
        event = self.event("Футбол. Тоттенхэм - Эвертон")
        event["date"] = "2026-09-12"
        event["time"] = "16:30"
        event["estimated_broadcast_end"] = "19:00"
        normalized = normalize_provider_timezone(event)
        self.assertEqual(normalized["time"], "21:30")
        self.assertEqual(normalized["estimated_broadcast_end_date"], "2026-09-13")
        self.assertEqual(normalized["estimated_broadcast_end"], "00:00")
        self.assertEqual(normalized["source_timezone"], "UTC")
        self.assertEqual(normalized["timezone"], "Asia/Almaty")

    def test_epg_sport_match_is_not_promoted_to_direct_without_evidence(self):
        parsed = infer_direct_event(
            self.event('Фонбет Чемпионат КХЛ. ХК "Сочи" - СКА', channel="KHL HD"),
            target_date=date(2026, 9, 12),
            seen=set(),
        )
        self.assertFalse(parsed["is_live"])
        self.assertFalse(parsed["is_live_broadcast"])
        self.assertTrue(parsed["is_sport_event"])
        self.assertEqual(parsed["source"], SOURCE)
        self.assertEqual(parsed["live_evidence_method"], "provider_epg_sport_candidate")

    def test_explicit_provider_live_text_requires_external_confirmation(self):
        parsed = infer_direct_event(
            self.event(
                "Футбол. Челси - Астон Вилла LIVE",
                is_live=True,
                method="provider_live_text",
                state="live",
            ),
            target_date=date(2026, 9, 12),
            seen=set(),
        )
        self.assertTrue(parsed["provider_claimed_live"])
        self.assertFalse(parsed["is_live"])
        self.assertFalse(parsed["is_live_broadcast"])
        self.assertTrue(parsed["is_sport_event"])
        self.assertEqual(parsed["reconciliation_state"], "unverified")
        self.assertEqual(parsed["live_evidence_method"], "provider_live_unverified")

    def test_historical_year_is_not_promoted(self):
        parsed = infer_direct_event(
            self.event("Турнир Bushido Fighting Championship 2020"),
            target_date=date(2026, 9, 12),
            seen=set(),
        )
        self.assertFalse(parsed["is_live"])
        self.assertFalse(parsed["is_sport_event"])

    def test_preview_and_review_are_not_sport_schedule_candidates(self):
        for title in (
            "Превью к этапу WRC 2026",
            "FIA WEC 6 часов Трассы Америк Review",
        ):
            with self.subTest(title=title):
                parsed = infer_direct_event(
                    self.event(title),
                    target_date=date(2026, 9, 12),
                    seen=set(),
                )
                self.assertFalse(parsed["is_live_broadcast"])
                self.assertFalse(parsed["is_sport_event"])

    def test_q_channel_does_not_use_official_fallback(self):
        event = infer_direct_event(
            self.event(
                "Футбол. Челси - Астон Вилла",
                channel="Q Football",
            ),
            target_date=date(2026, 9, 12),
            seen=set(),
        )
        calendar = ChampionatCalendar(events=[], errors=[], fetched_dates=[])

        async def run():
            with patch(
                "parsers.tvguide_cached.get_championat_calendar",
                new=AsyncMock(return_value=calendar),
            ), patch(
                "parsers.tvguide_cached.verify_official_fallback"
            ) as fallback:
                result = await _reconcile_external(
                    {date(2026, 9, 12): [event]}
                )
                fallback.assert_not_called()
                return result

        reconciled = asyncio.run(run())
        item = reconciled[date(2026, 9, 12)][0]
        self.assertEqual(item["reconciliation_state"], "pending_championat_match")
        self.assertFalse(item["is_live_broadcast"])
        self.assertFalse(item["is_live"])


if __name__ == "__main__":
    unittest.main()

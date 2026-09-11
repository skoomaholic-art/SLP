import unittest
from datetime import date

from parsers.tvplus import (
    EUROSPORT_CHANNELS,
    TARGET_CHANNELS,
    TVPlusChannel,
    _event_from_epg,
    _local_epg_datetime,
    _page_id,
    parse_tvplus_title,
)


class TVPlusParserTests(unittest.TestCase):
    def setUp(self):
        self.channel = TVPlusChannel(
            "Setanta Sports 1",
            "5f9984549e0766c2417d4076",
        )

    def event(self, title, description=""):
        return {
            "title": title,
            "eventDescriptionMedium": description,
            "scheduledFor": {
                "begin": "2026-09-11T19:00:00Z",
                "end": "2026-09-11T21:00:00Z",
            },
        }

    def test_target_channel_pack_contains_requested_provider_channels(self):
        names = {channel.name for channel in TARGET_CHANNELS}
        self.assertEqual(
            names,
            {
                "KHL HD", "KHL Prime", "MMA-TV.COM", "Q Arena",
                "Q Football", "Q League", "Setanta Sports 1",
                "Setanta Sports 2", "Setanta Sports KZ", "viju+ Sport",
                "БОКС ТВ", "МАТЧ! Планета",
            },
        )


    def test_eurosport_pack_is_configured_via_mobikino_provider(self):
        names = {channel.name for channel in EUROSPORT_CHANNELS}
        self.assertEqual(names, {"Eurosport", "Eurosport 2"})
        self.assertTrue(all("kcell.server-api" in channel.api_base for channel in EUROSPORT_CHANNELS))
        self.assertTrue(all(channel.source == "mobikino" for channel in EUROSPORT_CHANNELS))

    def test_z_suffix_is_provider_wall_clock_not_utc_conversion(self):
        value = _local_epg_datetime("2026-09-11T19:00:00Z")
        self.assertEqual(value.strftime("%Y-%m-%d %H:%M %z"), "2026-09-11 19:00 +0500")

    def test_page_id_matches_provider_format(self):
        self.assertEqual(_page_id(date(2026, 9, 11)), "2026-09-11t00d12h")

    def test_unmarked_program_is_not_promoted_to_live(self):
        parsed = _event_from_epg(
            self.channel,
            self.event("Бундеслига: Гамбург - Майнц"),
        )
        self.assertFalse(parsed["is_live"])
        self.assertEqual(parsed["live_state"], "unknown")
        self.assertEqual(parsed["live_evidence_method"], "none")

    def test_explicit_provider_live_marker_is_accepted(self):
        parsed = _event_from_epg(
            self.channel,
            self.event("Футбол. Челси - Лидс. Прямая трансляция"),
        )
        self.assertTrue(parsed["is_live"])
        self.assertEqual(parsed["live_state"], "live")
        self.assertEqual(parsed["live_evidence_method"], "provider_live_text")
        self.assertEqual(parsed["live_evidence_confidence"], "high")

    def test_replay_marker_overrides_live_marker(self):
        parsed = _event_from_epg(
            self.channel,
            self.event("Футбол. Челси - Лидс. LIVE. Повтор"),
        )
        self.assertFalse(parsed["is_live"])
        self.assertEqual(parsed["live_state"], "not_live")
        self.assertEqual(parsed["live_evidence_method"], "provider_replay_text")

    def test_channel_sport_hint_is_used(self):
        khl = TVPlusChannel("KHL HD", "id", "Хоккей")
        parsed = _event_from_epg(
            khl,
            self.event('Фонбет Чемпионат КХЛ. "Лада" - "Торпедо"'),
        )
        self.assertEqual(parsed["sport"], "Хоккей")

    def test_colon_match_is_structured(self):
        parsed = parse_tvplus_title("Бундеслига: Гамбург - Майнц")
        self.assertEqual(parsed["tournament"], "Бундеслига")
        self.assertEqual(parsed["title"], "Гамбург - Майнц")


if __name__ == "__main__":
    unittest.main()

"""No-network VseTV regression. The bot's existing Setanta IDs are unchanged."""
from datetime import date
import unittest

from parsers.vsetv_live import CHANNEL_IDS, parse_vsetv_week_html
from services.vsetv_sources import WEB_CHANNEL_IDS, normalize_live_record


class VseTVSourceTests(unittest.TestCase):
    def test_source_channels_are_not_conflated(self):
        self.assertEqual(WEB_CHANNEL_IDS["KHL PRIME"], 806)
        self.assertEqual(WEB_CHANNEL_IDS["KHL HD"], 1641)
        self.assertEqual(WEB_CHANNEL_IDS["EUROSPORT 1"], 535)
        self.assertEqual(WEB_CHANNEL_IDS["EUROSPORT 2"], 1082)
        self.assertEqual(WEB_CHANNEL_IDS["МАТЧ! ПЛАНЕТА"], 32)
        self.assertEqual(WEB_CHANNEL_IDS["viju+ Sport"], 332)
        self.assertEqual(CHANNEL_IDS["Setanta Sports 1"], 771)

    def test_one_specific_live_badge_and_moscow_midnight_rollover(self):
        raw = {
            "third_party_live_badge": True,
            "explicit_direct_text": True,
            "date": "2026-09-30", "time": "23:35",
            "title": "ФОНБЕТ Чемпионат КХЛ. Спартак - Барыс. Прямая трансляция",
        }
        event = normalize_live_record(raw, channel="KHL PRIME")
        self.assertEqual(event["date"], "2026-10-01")
        self.assertEqual(event["time"], "01:35")
        self.assertEqual(event["channel"], "KHL PRIME")
        self.assertEqual(event["sport"], "Хоккей")
        self.assertTrue(event["is_live_broadcast"])
        self.assertNotIn("ФОНБЕТ", event["title"])
        self.assertEqual(event["source_timezone"], "Europe/Moscow")
        self.assertEqual(event["time_normalization"], "msk_to_kz")

    def test_studio_and_unconfirmed_evidence_never_enter_schedule(self):
        base = {"date": "2026-10-01", "time": "18:50",
                "title": "Футбол. Студия Live. Прямая трансляция"}
        self.assertIsNone(normalize_live_record(
            {**base, "third_party_live_badge": True}, channel="EUROSPORT 1"
        ))
        self.assertIsNone(normalize_live_record(
            {**base, "third_party_live_badge": False}, channel="EUROSPORT 1"
        ))

    def test_parser_rejects_same_page_non_live_programme(self):
        html = """
        <div>Четверг, 1 октября</div>
        <div class="time">21:20</div>
        <div class="prname2"><img src="pic/ico_live.gif"
          height="11">ФОНБЕТ Чемпионат КХЛ. Спартак - Барыс. Прямая трансляция.</div>
        <div class="time">23:30</div>
        <div class="prname2">КХЛ. Подробно. Запись.</div>
        """
        rows = parse_vsetv_week_html(
            html, channel="KHL PRIME", anchor_date=date(2026, 10, 1),
        )
        self.assertEqual(len(rows), 1)
        sport = normalize_live_record(rows[0], channel="KHL PRIME")
        self.assertIsNotNone(sport)
        self.assertEqual(sport["time"], "23:20")


if __name__ == "__main__":
    unittest.main()

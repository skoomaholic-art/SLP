import unittest

from parsers.qazsport_complete import apply_page_live_markers, extract_row_live_times
from parsers.sportplus_cached import extract_sportplus_on_air_times
from parsers.tvguide_broadcast import apply_vsetv_evidence
from parsers.vsetv_live import parse_vsetv_live_html
from services.broadcast_evidence import add_broadcast_evidence


QAZSPORT_HTML = """
<a href="#" class="w-full program-item relative flex">
  <div class="w-18 flex-col">
    <div class="rounded-xl bg-live-10 text-live">LIVE</div>
    <div class="font-bold">15:25</div>
  </div>
  <div>Азия чемпионаты (Ерлер). Финал</div>
  <div>Волейбол. Жапония - Иран</div>
</a>
<a href="#" class="w-full program-item relative flex">
  <div class="w-18 flex-col">
    <div class="rounded-xl bg-live-10 text-live">LIVE</div>
    <div class="font-bold">07:00</div>
  </div>
  <div>Қазақстан Республикасының Мемлекеттік Әнұраны</div>
</a>
"""

SPORTPLUS_HTML = """
<ul class="programms_list">
  <li>
    <span>02:40</span>
    <div class="blink"><a href="https://sportplustv.kz/ru/live" title="LIVE"></a></div>
    <div>ҚР Әнұраны</div>
  </li>
</ul>
"""

VSETV_HTML = """
<div id="schedule_container">
  <div class="time">04:25</div>
  <div class="prname2">
    <img src="pic/ico_live.gif" width="18" height="11" align="absmiddle">
    &nbsp;Бейсбол. Мексиканская бейсбольная лига LMB. Ольмекас де Табаско - Перикос де Пуэбла. Прямая трансляция.
  </div>
  <div class="time">07:30</div>
  <div class="prname2">E:60 - Хроники профессионального спорта.</div>
</div>
"""


class BroadcastEvidenceTests(unittest.TestCase):
    def test_qazsport_row_live_badge_is_official_direct_for_sport(self):
        self.assertEqual(extract_row_live_times(QAZSPORT_HTML), {"07:00", "15:25"})
        events = [
            {
                "time": "15:25",
                "raw_title": "Волейбол. Азия чемпионаты (Ерлер). Финал. Жапония - Иран",
                "is_live": False,
                "is_live_broadcast": False,
            }
        ]
        updated, detected, matched = apply_page_live_markers(
            events,
            "15:25 Волейбол",
            page_html=QAZSPORT_HTML,
        )
        event = updated[0]
        self.assertIn("15:25", detected)
        self.assertIn("15:25", matched)
        self.assertTrue(event["is_live_broadcast"])
        self.assertTrue(event["is_sport_event"])
        self.assertEqual(event["live_evidence_method"], "official_live_badge")
        self.assertTrue(event["provider_claimed_live"])

    def test_qazsport_live_badge_does_not_promote_non_sport(self):
        events = [
            {
                "time": "07:00",
                "raw_title": "Қазақстан Республикасының Мемлекеттік Әнұраны",
                "is_live": False,
                "is_live_broadcast": False,
            }
        ]
        updated, _, _ = apply_page_live_markers(
            events,
            "07:00 Қазақстан Республикасының Мемлекеттік Әнұраны",
            page_html=QAZSPORT_HTML,
        )
        event = updated[0]
        self.assertFalse(event["is_live_broadcast"])
        self.assertFalse(event["is_sport_event"])
        self.assertTrue(event["provider_claimed_live"])

    def test_sportplus_blink_is_only_on_air_marker(self):
        self.assertEqual(extract_sportplus_on_air_times(SPORTPLUS_HTML), {"02:40"})
        item = add_broadcast_evidence(
            {"is_live": False, "is_live_broadcast": False},
            method="official_on_air_marker",
            source="sportplustv.kz",
            value="div.blink a[title=LIVE]",
            confidence="high",
            on_air_now=True,
        )
        self.assertTrue(item["provider_on_air_now"])
        self.assertFalse(item.get("provider_claimed_live", False))
        self.assertFalse(item["is_live_broadcast"])

    def test_vsetv_icon_is_third_party_claim_not_direct_by_itself(self):
        rows = parse_vsetv_live_html(
            VSETV_HTML,
            channel="Setanta Sports 1",
            target_date=__import__("datetime").date(2026, 9, 13),
            source_url="https://www.vsetv.com/example",
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["time"], "04:25")
        self.assertTrue(rows[0]["explicit_direct_text"])

        events = [
            {
                "channel": "Setanta Sports 1",
                "date": "2026-09-13",
                "time": "04:25",
                "sport": "Бейсбол",
                "tournament": "Мексиканская бейсбольная лига LMB",
                "title": "Ольмекас де Табаско - Перикос де Пуэбла",
                "raw_title": "Бейсбол. Мексиканская бейсбольная лига LMB. Ольмекас де Табаско - Перикос де Пуэбла",
                "is_live": False,
                "is_live_broadcast": False,
                "reconciliation_state": "unknown",
            }
        ]
        enriched = apply_vsetv_evidence(events, rows)[0]
        self.assertTrue(enriched["third_party_claimed_live"])
        self.assertFalse(enriched["is_live_broadcast"])
        self.assertEqual(enriched["reconciliation_state"], "third_party_live_unconfirmed")


if __name__ == "__main__":
    unittest.main()

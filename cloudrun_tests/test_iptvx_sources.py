from datetime import datetime
import unittest

from services.iptvx_sources import parse_iptvx_page, parse_iptvx_xml, page_url_for


class IptvxSourceTests(unittest.TestCase):
    def test_direct_page_parses_editor_supplied_channel_and_converts_msk_to_kz(self):
        html = """
        <main>
          <p><u>tvg-id="setanta-sports"</u></p>
          <h3>Пятница, 02 октября 2026 г.</h3>
          <section>
            <p>20:30 Футбол. АПЛ. Арсенал – Ливерпуль</p>
            <p>22:45 Футбол. АПЛ. Обзор тура</p>
            <p>23:30 Баскетбол. Евролига. Реал Мадрид – Барселона</p>
          </section>
        </main>
        """
        events, stats = parse_iptvx_page(
            html,
            channel="SETANTA SPORTS 1",
            page_id="setanta-sports",
            source_url="https://epg.iptvx.one/id/setanta-sports",
        )
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["channel"], "SETANTA SPORTS 1")
        self.assertEqual(events[0]["date"], "2026-10-02")
        self.assertEqual(events[0]["time"], "22:30")
        self.assertEqual(events[0]["estimated_broadcast_end"], "00:45")
        self.assertEqual(events[0]["estimated_broadcast_end_date"], "2026-10-03")
        self.assertEqual(events[1]["date"], "2026-10-03")
        self.assertEqual(events[1]["time"], "01:30")
        self.assertEqual(stats["programmes"], 3)
        self.assertEqual(stats["sport_candidates"], 2)
        self.assertEqual(stats["filtered"], 1)

    def test_direct_page_rejects_wrong_tvg_id(self):
        html = '<p><u>tvg-id="other"</u></p><h3>Пятница, 02 октября 2026 г.</h3>'
        with self.assertRaisesRegex(ValueError, "iptvx_page_id_mismatch"):
            parse_iptvx_page(
                html,
                channel="SETANTA SPORTS 1",
                page_id="setanta-sports",
            )

    def test_exact_editor_urls_are_registered(self):
        expected = {
            "SETANTA SPORTS 1": "setanta-sports",
            "SETANTA SPORTS 2": "setanta-sports-plus",
            "SETANTA SPORTS KZ": "setanta-kz",
            "QAZSPORT HD": "kazsport",
            "SPORT+ Qazaqstan": "sport-plus-kz",
            "Q FOOTBALL": "q-football-kz",
            "Q LEAGUE": "q-sport-ext",
            "Q ARENA": "qsport-kz",
            "EUROSPORT 1": "eurosport1",
            "EUROSPORT 2": "eurosport2",
            "viju+ Sport": "viasat-sport",
            "МАТЧ! ПЛАНЕТА": "match-planeta",
            "KHL HD": "kxl",
            "KHL PRIME": "kxl-hd",
        }
        for channel, page_id in expected.items():
            self.assertEqual(
                page_url_for(channel),
                "https://epg.iptvx.one/id/" + page_id,
            )

    def test_only_explicit_live_rows_are_emitted(self):
        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <tv>
          <programme start="20261002190000 +0300" stop="20261002210000 +0300"
                     channel="setanta-kz">
            <title>LIVE Футбол. АПЛ. Арсенал - Ливерпуль</title>
            <category>Футбол</category>
          </programme>
          <programme start="20261002210000 +0300" channel="setanta-kz">
            <title>Обзор матчей АПЛ</title>
            <category>Футбол</category>
          </programme>
        </tv>"""
        events, stats = parse_iptvx_xml(xml)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["channel"], "SETANTA SPORTS KZ")
        self.assertEqual(events[0]["date"], "2026-10-02")
        self.assertEqual(events[0]["time"], "21:00")
        self.assertEqual(events[0]["estimated_broadcast_end"], "23:00")
        self.assertEqual(stats["mapped"], 2)
        self.assertEqual(stats["live"], 1)
        self.assertEqual(stats["unconfirmed"], 1)

    def test_live_icon_is_accepted_as_provider_evidence(self):
        xml = """<tv>
          <programme start="20261002150000 +0300" channel="eurosport1">
            <title>Теннис. ATP 500. Полуфинал</title>
            <category>Теннис</category>
            <icon src="https://example.test/ico_live.gif"/>
          </programme>
        </tv>"""
        events, _ = parse_iptvx_xml(xml)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["channel"], "EUROSPORT 1")
        self.assertEqual(events[0]["time"], "17:00")


if __name__ == "__main__":
    unittest.main()

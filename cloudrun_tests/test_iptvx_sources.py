from datetime import datetime
import gzip
import unittest

from services.iptvx_sources import (
    page_url_for,
    parse_iptvx_page,
    parse_iptvx_xml,
)


class IptvxSourceTests(unittest.TestCase):
    def test_xmltv_accepts_whitelisted_sports_candidate_without_live_tag(self):
        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <tv>
          <programme start="20261002190000 +0300" stop="20261002210000 +0300"
                     channel="q-sport-ext">
            <title>Футбол. Премьер-лига. Астана - Кайрат</title>
            <category>Спорт</category>
          </programme>
          <programme start="20261002210000 +0300" stop="20261002220000 +0300"
                     channel="q-sport-ext">
            <title>Обзор тура</title>
            <category>Спорт</category>
          </programme>
        </tv>"""
        events, stats = parse_iptvx_xml(xml)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["channel"], "Q LEAGUE")
        self.assertEqual(events[0]["date"], "2026-10-02")
        self.assertEqual(events[0]["time"], "21:00")
        self.assertEqual(events[0]["live_state"], "candidate")
        self.assertEqual(
            events[0]["live_evidence_method"],
            "iptvx_sports_channel_candidate",
        )
        self.assertEqual(stats["channels"]["Q LEAGUE"]["programmes"], 2)
        self.assertEqual(stats["channels"]["Q LEAGUE"]["candidates"], 1)

    def test_gzip_extensionless_xmltv_is_decoded(self):
        xml = b"""<tv>
          <programme start="20261002150000 +0300" channel="eurosport1">
            <title>Теннис. ATP 500. Полуфинал</title>
            <category>Теннис</category>
          </programme>
        </tv>"""
        events, stats = parse_iptvx_xml(gzip.compress(xml))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["channel"], "EUROSPORT 1")
        self.assertEqual(stats["mapped"], 1)

    def test_xmltv_explicit_live_remains_high_confidence(self):
        xml = """<tv>
          <programme start="20261002150000 +0300" stop="20261002170000 +0300"
                     channel="eurosport1">
            <title>LIVE Теннис. ATP 500. Полуфинал</title>
            <category>Теннис</category>
          </programme>
        </tv>"""
        events, stats = parse_iptvx_xml(xml)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["channel"], "EUROSPORT 1")
        self.assertEqual(events[0]["time"], "17:00")
        self.assertEqual(events[0]["live_state"], "live")
        self.assertEqual(events[0]["live_confidence"], "high")
        self.assertEqual(stats["explicit_live"], 1)

    def test_xmltv_ignores_unapproved_channel(self):
        xml = """<tv>
          <programme start="20261002150000 +0300" channel="random-channel">
            <title>Футбол. Команда A - Команда B</title>
            <category>Спорт</category>
          </programme>
        </tv>"""
        events, stats = parse_iptvx_xml(xml)
        self.assertEqual(events, [])
        self.assertEqual(stats["mapped"], 0)

    def test_xmltv_source_url_is_human_checkable_channel_page(self):
        xml = """<tv>
          <programme start="20261002150000 +0300" channel="match-planeta">
            <title>Хоккей. Динамо - Спартак</title>
            <category>Спорт</category>
          </programme>
        </tv>"""
        events, _ = parse_iptvx_xml(xml)
        self.assertEqual(
            events[0]["source_url"],
            "https://epg.iptvx.one/id/match-planeta",
        )
        self.assertEqual(events[0]["provider_source"], "iptvx_xmltv")

    def test_page_fallback_parses_programme_rows(self):
        html = """
        <main>
          <p><u>tvg-id="setanta-sports"</u></p>
          <h3>Пятница, 02 октября 2026 г.</h3>
          <section>
            <p>20:30 Футбол. АПЛ. Арсенал - Ливерпуль</p>
            <p>22:45 Обзор тура</p>
            <p>23:30 Баскетбол. Евролига. Реал - Барселона</p>
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
        self.assertEqual(events[0]["time"], "22:30")
        self.assertEqual(events[1]["date"], "2026-10-03")
        self.assertEqual(events[1]["time"], "01:30")
        self.assertEqual(stats["programmes"], 3)

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


if __name__ == "__main__":
    unittest.main()

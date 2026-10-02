from datetime import datetime
import unittest

from services.iptvx_sources import parse_iptvx_xml


class IptvxSourceTests(unittest.TestCase):
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

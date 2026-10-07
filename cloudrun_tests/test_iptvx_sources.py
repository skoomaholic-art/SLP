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
        xml = """<tv>
          <programme start="20261002150000 +0300" channel="eurosport1">
            <title>Теннис. ATP 500. Полуфинал</title>
            <category>Теннис</category>
          </programme>
        </tv>""".encode("utf-8")
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
            # epg.iptvx.one/id/kxl is "КХЛ ТВ | КХЛ | KHL" (the SD/Prime
            # feed); kxl-hd is "КХЛ HD". Verified on the live pages 2026-10-05.
            "KHL PRIME": "kxl",
            "KHL HD": "kxl-hd",
        }
        for channel, page_id in expected.items():
            self.assertEqual(
                page_url_for(channel),
                "https://epg.iptvx.one/id/" + page_id,
            )


    # --- LIVE! icon handling (rows copied from the real pages, 2026-10-05) ---

    ICON = '<img src="https://epg.iptvx.one/live.png" title="LIVE!" alt="LIVE!">'

    def _setanta(self, body):
        return parse_iptvx_page(
            '<main><p>tvg-id="setanta-sports"</p>' + body + "</main>",
            channel="SETANTA SPORTS 1",
            page_id="setanta-sports",
        )

    def test_live_icon_marks_event_live_and_unmarked_repeat_is_dropped(self):
        events, stats = self._setanta(
            "<h3>Вторник, 29 сентября 2026 г.</h3>"
            "<p>12:00 Футбол. АПЛ. Брентфорд – Челси</p>"
            "<p>19:00 " + self.ICON + "Баскетбол. Евролига. Дубай – Барселона</p>"
            "<p>21:30 " + self.ICON + "Баскетбол. Евролига. Валенсия – Баскония</p>"
            "<h3>Суббота, 03 октября 2026 г.</h3>"
            "<p>00:00 Баскетбол. Евролига. Дубай – Барселона</p>"
            "<p>11:00 " + self.ICON + "Автоспорт. Формула-1. Квалификация</p>"
        )
        self.assertEqual(
            [(e["date"], e["time"]) for e in events],
            [("2026-09-29", "21:00"), ("2026-09-29", "23:30"),
             ("2026-10-03", "13:00")],
        )
        for event in events:
            self.assertEqual(event["live_state"], "live")
            self.assertEqual(event["live_confidence"], "high")
            self.assertEqual(event["live_evidence_method"], "iptvx_live_icon")
            self.assertNotIn("LIVE", event["raw_title"].upper())
        self.assertEqual(stats["live_marked"], 3)
        self.assertEqual(stats["unmarked_repeats"], 2)
        self.assertTrue(stats["live_marker_required"])

    def test_live_icon_is_read_from_br_separated_and_table_layouts(self):
        for body in (
            "<h3>Четверг, 01 октября 2026 г.</h3><div>"
            "17:00 Футбол. Германия. Боруссия Дортмунд – Падерборн 07<br>"
            "19:00 " + self.ICON + "Баскетбол. Евролига. Хапоэль – Реал Мадрид<br>"
            "</div>",
            "<h3>Четверг, 01 октября 2026 г.</h3><table>"
            "<tr><td>17:00</td><td>Футбол. Германия. Боруссия Дортмунд – Падерборн 07</td></tr>"
            "<tr><td>19:00</td><td>" + self.ICON + "<b>Баскетбол. Евролига. Хапоэль – Реал Мадрид</b></td></tr>"
            "</table>",
            "<h3>Четверг, 01 октября 2026 г.</h3><ul>"
            "<li><span>17:00</span> Футбол. Германия. Боруссия Дортмунд – Падерборн 07</li>"
            "<li><span>19:00</span> " + self.ICON + " Баскетбол. Евролига. Хапоэль – Реал Мадрид</li>"
            "</ul>",
        ):
            events, stats = self._setanta(body)
            self.assertEqual(stats["programmes"], 2, body)
            self.assertEqual(len(events), 1, body)
            self.assertEqual(events[0]["time"], "21:00")
            self.assertIn("Реал Мадрид", events[0]["raw_title"])
            self.assertEqual(events[0]["live_evidence_method"], "iptvx_live_icon")

    def test_description_paragraphs_and_live_studio_shows_are_not_events(self):
        events, stats = parse_iptvx_page(
            '<main><p>tvg-id="kxl"</p>'
            "<h3>Понедельник, 28 сентября 2026 г.</h3>"
            "<p>16:20 " + self.ICON + "КХЛ. Авангард – Металлург (Мг)</p>"
            "<p>19:00 " + self.ICON + "КХЛ. Динамо (Минск) – Лада</p>"
            "<p>22:00 " + self.ICON + "КХЛ. Подробно</p>"
            "<p>Вас ждет неожиданный взгляд на события. Вед: Александр Бойков</p>"
            "<p>22:25 КХЛ. Авангард – Металлург (Мг)</p>"
            "<h3>Пятница, 02 октября 2026 г.</h3>"
            "<p>11:00 " + self.ICON + "На связи</p>"
            "<p>18:50 " + self.ICON + "КХЛ. Нефтехимик -Барыс</p></main>",
            channel="KHL PRIME",
            page_id="kxl",
        )
        self.assertEqual(
            [e["raw_title"] for e in events],
            ["КХЛ. Авангард – Металлург (Мг)", "КХЛ. Динамо (Минск) – Лада",
             "КХЛ. Нефтехимик -Барыс"],
        )
        self.assertEqual(events[0]["channel"], "KHL PRIME")
        self.assertEqual(events[0]["time"], "18:20")
        self.assertEqual(stats["programmes"], 6)

    def test_page_without_any_live_icon_keeps_candidates(self):
        events, stats = self._setanta(
            "<h3>Пятница, 02 октября 2026 г.</h3>"
            "<p>20:30 Футбол. АПЛ. Арсенал - Ливерпуль</p>"
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["live_state"], "candidate")
        self.assertFalse(stats["live_marker_required"])
        self.assertEqual(stats["unmarked_repeats"], 0)

    def test_text_live_prefix_is_equivalent_to_icon(self):
        events, _ = self._setanta(
            "<h3>Пятница, 02 октября 2026 г.</h3>"
            "<p>20:45 LIVE! Баскетбол. Евролига. Фенербахче – Дубай</p>"
            "<p>23:00 Футбол. АПЛ. Ливерпуль – Фулхэм</p>"
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["raw_title"], "Баскетбол. Евролига. Фенербахче – Дубай")
        self.assertEqual(events[0]["live_evidence_method"], "iptvx_live_icon")

    def test_unrelated_images_are_not_live_markers(self):
        events, stats = self._setanta(
            "<h3>Пятница, 02 октября 2026 г.</h3>"
            '<p>20:30 <img src="/logo/delivery.png" alt="logo">Футбол. АПЛ. Арсенал - Ливерпуль</p>'
        )
        self.assertEqual(stats["live_marked"], 0)
        self.assertEqual(events[0]["live_state"], "candidate")


if __name__ == "__main__":
    unittest.main()

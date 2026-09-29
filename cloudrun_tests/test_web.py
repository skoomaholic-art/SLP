"""Focused web-only regression; does not contact external sites or send mail."""
import base64
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import cloudrun_web as web
from storage.database import SLPDatabase


class WebTests(unittest.TestCase):
    def test_session_and_named_avatar(self):
        original = web.SESSION_SECRET
        web.SESSION_SECRET = "a-private-signing-secret-at-least-32-bytes"
        old = os.environ.get("SPORT_WEB_USERS")
        try:
            pw = "new-long-private-password"
            salt = b"1234567890123456"
            hashed = "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(
                hashlib.scrypt(pw.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
            ).decode()
            os.environ["SPORT_WEB_USERS"] = json.dumps({
                "Дания": {"password_hash": hashed, "role": "editor", "avatar": "🇩🇰"}
            }, ensure_ascii=False)
            self.assertTrue(web._password_matches(pw, hashed))
            fingerprint = hashlib.sha256(hashed.encode()).hexdigest()
            request = SimpleNamespace(cookies={
                "sport_web_session": web._session("Дания", fingerprint)
            })
            self.assertEqual(web.current_user(request)["avatar"], "🇩🇰")
            self.assertFalse(web._password_matches("wrong", hashed))
        finally:
            web.SESSION_SECRET = original
            if old is None:
                os.environ.pop("SPORT_WEB_USERS", None)
            else:
                os.environ["SPORT_WEB_USERS"] = old

    def test_direct_only_dedup_barys_and_time(self):
        with tempfile.TemporaryDirectory() as folder:
            database = SLPDatabase(Path(folder) / "sports.db")
            def event(title, channel):
                return {
                    "source": "qazsport", "source_url": "https://example.test/program",
                    "date": "2026-09-30", "time": "12:00",
                    "channel": channel, "sport": "Футбол",
                    "tournament": "Лига", "title": title,
                    "raw_title": title, "is_live": True,
                    "is_live_broadcast": True,
                }
            database.upsert_source_snapshot(
                run_id="regression", source="qazsport", scope_date="2026-09-30",
                events=[event("Команда А - Команда Б", "QAZSPORT"),
                        event("Барыс - Спартак", "QAZSPORT")],
            )
            database.upsert_source_snapshot(
                run_id="regression", source="tvguide", scope_date="2026-09-30",
                events=[event("Команда А - Команда Б", "SETANTA 1")],
            )
            rows = web.event_rows(database, "2026-09-30", "2026-09-30")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["channels"], ["QAZSPORT HD", "SETANTA SPORTS 1"])
            self.assertEqual(rows[0]["platform_start_at"][11:16], "11:50")
            self.assertEqual(rows[0]["end_at"][11:16], "14:10")
            self.assertEqual(rows[0]["channel"], "QAZSPORT HD")

    def test_non_sport_and_betting_cleaner(self):
        self.assertEqual(web.clean("ФОНБЕТ Чемпионат КХЛ"), "Чемпионат КХЛ")
        self.assertEqual(web.channel_name("SETANTA 2"), "SETANTA SPORTS 2")


if __name__ == "__main__":
    unittest.main()

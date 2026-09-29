"""Focused web-only regression; does not contact external sites or send mail."""
import asyncio
import base64
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

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

    def test_supplied_profile_avatars_and_app_icon_assets(self):
        # The authenticated identity selects an application-owned image.
        self.assertEqual(web.profile_avatar("Skoomaholic"),
                         "/assets/skoomaholic.webp")
        self.assertEqual(web.profile_avatar("Дания"),
                         "/assets/daniya.webp")
        self.assertEqual(web.profile_avatar("Вадим"),
                         "/assets/vadim.webp")
        self.assertEqual(web.profile_avatar("USER4"), "")
        assets = Path(__file__).resolve().parents[1] / "cloudrun_ui" / "assets"
        expected = {
            "app-icon.png": b"\x89PNG",
            "skoomaholic.webp": b"RIFF",
            "daniya.webp": b"RIFF",
            "vadim.webp": b"RIFF",
        }
        hashes = []
        for name, prefix in expected.items():
            raw = (assets / name).read_bytes()
            self.assertTrue(raw.startswith(prefix), name)
            self.assertGreater(len(raw), 500, name)
            hashes.append(hashlib.sha256(raw).digest())
        self.assertEqual(len(hashes), len(set(hashes)))

    def test_ui_names_channels_without_swapping_images(self):
        import re
        index = (Path(__file__).resolve().parents[1] /
                 "cloudrun_ui" / "index.html").read_text()
        logos_js = (Path(__file__).resolve().parents[1] /
                    "cloudrun_ui" / "logos.js").read_text()
        self.assertIn('href="/assets/app-icon.png"', index)
        self.assertIn('function channelLogo(channel)', index)
        self.assertNotIn('className="logo-variant"', index)
        self.assertNotIn('channel==="KHL PRIME"?"PRIME":"HD"', index)
        logo_map = re.search(
            r'Object.freeze\((\{.*\})\);', logos_js, re.DOTALL)
        self.assertIsNotNone(logo_map)
        logos = json.loads(logo_map.group(1))
        self.assertNotEqual(logos["KHL PRIME"], logos["KHL HD"])
        self.assertNotEqual(logos["SETANTA SPORTS 1"], logos["SETANTA SPORTS 2"])
        self.assertNotEqual(logos["EUROSPORT 1"], logos["EUROSPORT 2"])

    def test_source_inventory_has_exactly_14_distinct_channels(self):
        with tempfile.TemporaryDirectory() as directory:
            database = SLPDatabase(Path(directory) / "sport.db")
            request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
                database=database, backup=None
            )))
            status = web._source_status(request)
            channels = [item["channel"] for item in (
                status["websites"] + status["excel"]
            )]
            self.assertEqual(len(channels), 14)
            self.assertEqual(len(set(channels)), 14)
            self.assertNotIn("TVGuide (проверенные LIVE)", channels)
            self.assertIn("KHL PRIME", channels)
            self.assertIn("KHL HD", channels)
            self.assertIn("EUROSPORT 1", channels)
            self.assertIn("EUROSPORT 2", channels)

    def test_scheduler_job_requires_persistence_and_private_oidc(self):
        request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(backup=None)),
            headers={},
        )
        with self.assertRaises(HTTPException) as failure:
            asyncio.run(web.scheduled_refresh(request))
        self.assertEqual(failure.exception.status_code, 503)

        request.app.state.backup = object()
        with patch.dict(os.environ, {
            "SPORT_SCHEDULER_SERVICE_ACCOUNT": "cron@example.test"
        }), patch.object(web, "PUBLIC_URL", "https://sport.example"):
            with self.assertRaises(HTTPException) as failure:
                asyncio.run(web.scheduled_refresh(request))
        self.assertEqual(failure.exception.status_code, 401)

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
            self.assertEqual(rows[0]["end_at"][11:16], "14:40")
            self.assertEqual(rows[0]["channel"], "QAZSPORT HD")

    def test_non_sport_and_betting_cleaner(self):
        self.assertEqual(web.clean("ФОНБЕТ Чемпионат КХЛ"), "Чемпионат КХЛ")
        self.assertEqual(web.channel_name("SETANTA 2"), "SETANTA SPORTS 2")


if __name__ == "__main__":
    unittest.main()

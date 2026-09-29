"""Offline Gmail and editorial regressions; never contact Gmail or send mail."""
import base64
from datetime import date
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet
from openpyxl import Workbook

from services import gmail_integration as gmail, editorial_store as edits
from storage.database import SLPDatabase
from cloudrun_web import event_rows


def sample_xlsx() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet["C3"] = "30 сентября"
    sheet["B4"] = "AST"
    sheet["B5"] = 0.50
    sheet["C5"] = "LIVE. Футбол. АПЛ, 6 тур, Команда А - Команда Б"
    sheet["B6"] = 0.57
    sheet["C6"] = "Повтор матча"
    stream = BytesIO()
    workbook.save(stream)
    workbook.close()
    return stream.getvalue()


class GmailOfflineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.db = SLPDatabase(Path(self.temporary.name) / "storage.db")
        gmail.init_gmail_schema(self.db)

    def test_oauth_requires_owner_identity_and_encrypts_refresh_token(self):
        secrets = {
            "SPORT_GMAIL_CLIENT_ID": "client-123",
            "SPORT_GMAIL_CLIENT_SECRET": "do-not-print",
            "SPORT_GMAIL_TOKEN_KEY": Fernet.generate_key().decode(),
            "SPORT_PUBLIC_URL": "https://sport.example",
        }
        with patch.dict("os.environ", secrets):
            link = gmail.start_oauth(self.db, username="Skoomaholic")
            self.assertIn("accounts.google.com", link)
            with self.db._connect() as conn:
                state_row = conn.execute(
                    "SELECT state_sha FROM gmail_oauth_states"
                ).fetchone()
            from urllib.parse import parse_qs, urlparse
            state = parse_qs(urlparse(link).query)["state"][0]
            self.assertNotEqual(state_row["state_sha"], state)
            with self.assertRaises(gmail.GmailTransportError):
                gmail.complete_oauth(
                    self.db, username="Дания", state=state, code="code-123"
                )
            # OAuth state is one-use even if a different account tries it.
            with self.assertRaises(gmail.GmailTransportError):
                gmail.complete_oauth(
                    self.db, username="Skoomaholic", state=state, code="code-123"
                )
            link = gmail.start_oauth(self.db, username="Skoomaholic")
            state = parse_qs(urlparse(link).query)["state"][0]
            with patch.object(gmail, "_json_http", return_value={
                "access_token": "fake-access", "refresh_token": "fake-private-token"
            }), patch.object(gmail, "_json_api", return_value={
                "emailAddress": gmail.OWNER_ACCOUNT
            }):
                gmail.complete_oauth(
                    self.db, username="Skoomaholic", state=state, code="code-123"
                )
            with self.db._connect() as conn:
                encrypted = conn.execute(
                    "SELECT refresh_token_cipher FROM gmail_oauth WHERE id=1"
                ).fetchone()[0]
            self.assertNotIn(b"fake-private-token", bytes(encrypted))
            self.assertTrue(gmail.status(self.db)["connected"])

    def test_sync_stages_file_and_change_letter_without_import(self):
        blob = sample_xlsx()
        msg1, msg2 = "abcde1234567", "abcde1234568"
        envelope = {
            "id": msg1, "internalDate": "1790812800000",
            "snippet": "EPG сетка Setanta",
            "payload": {
                "headers": [
                    {"name": "Subject", "value": "EPG Setanta Sports 2 Kazakhstan"},
                    {"name": "From", "value": "Поставщик <supplier@example.test>"},
                ],
                "parts": [{
                    "filename": "EPG Setanta Sports 2 Kazakhstan 29.09.26 - 05.10.26_MEDIA.xlsx",
                    "body": {"size": len(blob), "attachmentId": "attachment-1"},
                }],
            },
        }
        change_letter = {
            "id": msg2, "internalDate": "1790812800000",
            "snippet": "Изменение эфира SPORT PLUS",
            "payload": {
                "headers": [
                    {"name": "Subject", "value": "Изменения SPORT PLUS"},
                    {"name": "From", "value": "Поставщик <supplier@example.test>"},
                ],
            },
        }
        def fake_api(url, token, payload=None):
            if "messages?" in url:
                return {"messages": [{"id": msg1}, {"id": msg2}]}
            if msg1 in url and "/attachments/" in url:
                return {"data": base64.urlsafe_b64encode(blob).decode().rstrip("=")}
            if msg1 in url:
                return envelope
            if msg2 in url:
                return change_letter
            raise AssertionError(url)
        with patch.object(gmail, "_access_token", return_value="secret-access"):
            with patch.object(gmail, "_json_api", side_effect=fake_api):
                first = gmail.sync_inbox(self.db)
                second = gmail.sync_inbox(self.db)
        self.assertEqual(first["new_attachments"], 1)
        self.assertEqual(second["new_attachments"], 0)
        self.assertEqual(self.db.active_event_count(), 0)
        notices = gmail.list_notices(self.db)
        self.assertEqual(len(notices), 2)
        pending = next(x for x in notices if x["status"] == "pending")
        review = next(x for x in notices if x["status"] == "review")
        self.assertEqual(pending["detected_channel"], "SETANTA SPORTS 2")
        self.assertFalse(review["has_attachment"])
        result = gmail.approve_notice(self.db, pending["id"], username="Вадим")
        self.assertEqual(result["status"], "imported")
        self.assertGreater(self.db.active_event_count(), 0)
        self.assertEqual(gmail.approve_notice.__name__, "approve_notice")
        self.assertFalse(next(x for x in gmail.list_notices(self.db)
                              if x["id"] == pending["id"])["has_attachment"])

    def test_forwarded_mojibake_is_repaired_before_change_detection(self):
        broken = "РќР° 6 РѕРєС‚СЏР±СЂСЏ РґРѕР±Р°РІРёР»Рё РџСЂСЏРјРѕР№ СЌС„РёСЂ РўРµРЅРЅРёСЃР°"
        repaired = gmail._repair_forwarded_text(broken)
        self.assertIn("На 6 октября", repaired)
        self.assertIn("Прямой эфир Тенниса", repaired)

    def test_forwarded_generic_attachment_uses_body_context_and_workbook(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet["B1"] = "ПРОГРАММА ПЕРЕДАЧ ТЕЛЕКАНАЛА SETANTA SPORTS 1 KAZAKHSTAN"
        sheet["B4"] = "30 сентября"
        sheet["A5"] = "AST"
        sheet["A6"] = 0.5
        sheet["B6"] = "LIVE. Футбол. АПЛ, 6 тур, Команда А - Команда Б"
        stream = BytesIO(); workbook.save(stream); workbook.close()
        blob = stream.getvalue()
        msg = "abcde1234570"
        forwarded_text = (
            "От: Sabina Nazarova <sabina@silkwaymedia.com> "
            "Тема: актуальная сетка Setanta. Со вторника есть изменения."
        )
        body = base64.urlsafe_b64encode(forwarded_text.encode()).decode().rstrip("=")
        envelope = {
            "id": msg, "internalDate": "1790812800000",
            "snippet": "Пересылаю сетку",
            "payload": {
                "headers": [
                    {"name": "Subject", "value": "FW: актуальная сетка"},
                    {"name": "From", "value": "Alexandr <alexandr.petrossov@fmedia.kz>"},
                ],
                "parts": [
                    {"mimeType": "text/plain", "body": {"data": body}},
                    {
                        "filename": "сетка Канала.xlsx",
                        "body": {"size": len(blob), "attachmentId": "attachment-generic"},
                    },
                ],
            },
        }

        def fake_api(url, token, payload=None):
            if "messages?" in url:
                return {"messages": [{"id": msg}]}
            if "/attachments/" in url:
                return {"data": base64.urlsafe_b64encode(blob).decode().rstrip("=")}
            if msg in url:
                return envelope
            raise AssertionError(url)

        with patch.object(gmail, "_access_token", return_value="secret-access"),              patch.object(gmail, "_json_api", side_effect=fake_api):
            outcome = gmail.sync_inbox(self.db)

        self.assertEqual(outcome["new_attachments"], 1)
        notice = gmail.list_notices(self.db)[0]
        self.assertEqual(notice["detected_channel"], "SETANTA SPORTS 1")
        self.assertEqual(notice["filename"], "сетка Канала.xlsx")
        self.assertEqual(self.db.active_event_count(), 0)

    def test_test_mail_does_not_send_unless_explicitly_enabled(self):
        with patch.dict("os.environ", {"SPORT_GMAIL_ENABLE_TEST_SEND": "false"}):
            with patch.object(gmail, "_json_api") as post:
                with self.assertRaises(gmail.GmailNotConfigured):
                    gmail.test_request(
                        self.db, username="Дания", category="q",
                        destination="alexandr.petrossov@fmedia.kz"
                    )
                post.assert_not_called()


class HistoryTests(unittest.TestCase):
    def test_history_is_persistent_and_editor_overrides_do_not_destroy_source(self):
        with tempfile.TemporaryDirectory() as folder:
            db = SLPDatabase(Path(folder) / "events.db")
            event = {
                "source": "tvguide", "date": "2026-10-03", "time": "12:00",
                "sport": "Футбол", "tournament": "Лига",
                "title": "Команда А - Команда Б",
                "raw_title": "Команда А - Команда Б",
                "channel": "SETANTA SPORTS 2", "is_live": True,
                "is_live_broadcast": True,
            }
            db.upsert_source_snapshot(
                run_id="r1", source="tvguide", scope_date="2026-10-03",
                events=[event]
            )
            amended = {**event, "tournament": "Лига, 2-й тур"}
            db.upsert_source_snapshot(
                run_id="r2", source="tvguide", scope_date="2026-10-03",
                events=[amended]
            )
            storage_id = db.event_revisions(limit=3)[0]["storage_id"]
            self.assertEqual(
                [x["change_kind"] for x in db.event_revisions(storage_id)],
                ["source_changed", "added_to_source"]
            )
            edits.apply_edit(
                db, storage_id=storage_id, username="Дания",
                values={"title": "Команда А - Команда В",
                        "team1_kz": "А командасы", "time": "12:30"}
            )
            visible = event_rows(db, "2026-10-03", "2026-10-03")
            self.assertEqual(len(visible), 1)
            self.assertEqual(visible[0]["title"], "Команда А - Команда В")
            self.assertEqual(visible[0]["team1_kz"], "А командасы")
            self.assertEqual(visible[0]["time"], "12:30")
            self.assertEqual(len(edits.edit_history(db, storage_id=storage_id)), 1)
            db.upsert_source_snapshot(
                run_id="r3", source="tvguide", scope_date="2026-10-03",
                events=[]
            )
            history = db.event_revisions(storage_id)
            self.assertEqual(history[0]["change_kind"], "removed_from_source")
            self.assertEqual(len(history), 3)
            self.assertEqual(
                db._connect().execute(
                    "SELECT payload_json FROM events WHERE storage_id=?",
                    (storage_id,),
                ).fetchone()["payload_json"] is not None,
                True,
            )


if __name__ == "__main__":
    unittest.main()

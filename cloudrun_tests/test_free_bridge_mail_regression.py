"""Regression: forwarded official EPG and approved free-bridge formats."""
import base64
from datetime import date
import hashlib
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from openpyxl import Workbook

from services import free_apps_script as bridge
from services import gmail_integration as gmail
from storage.database import SLPDatabase


def example_sportplus():
    workbook = Workbook()
    sheet = workbook.active
    sheet["C1"] = '"SPORT PLUS QAZAQSTAN" АРНАСЫНЫҢ ТЕЛЕБАҒДАРЛАМАСЫ'
    sheet["C2"] = "СӘРСЕНБІ, 30 ҚЫРКҮЙЕК"
    sheet["B3"] = .5
    sheet["C3"] = "ММА. ALASH PRIDE 131. ПРЯМАЯ ТРАНСЛЯЦИЯ"
    sheet["D3"] = .125
    buffer = BytesIO()
    workbook.save(buffer)
    workbook.close()
    return buffer.getvalue()


class GmailForwardedSupplierTests(unittest.TestCase):
    def test_plain_russian_forwarded_sender(self):
        body = ("Пересланное письмо\nОтправитель: kozhayeva@sportpluskz.tv"
                " EXTERNAL EMAIL\nТема: SPORT PLUS")
        self.assertEqual(
            gmail._original_sender(body, "alexandr.petrossov@fmedia.kz"),
            "kozhayeva@sportpluskz.tv",
        )

    def test_approved_official_layout_auto_imports_next_attachment(self):
        raw = example_sportplus()
        digest = hashlib.sha256(raw).hexdigest()
        supplier = "kozhayeva@sportpluskz.tv"
        meta = {
            "filename": "сетка Канала.xlsx",
            "sender": supplier,
            "subject": "SPORT PLUS QAZAQSTAN расписание",
            "received": "2026-09-29T10:00:00+05:00",
            "sha256": digest,
        }
        first = dict(meta, id="a" * 32, messageId="supplier-message-1")
        second = dict(meta, id="b" * 32, messageId="supplier-message-2",
                      received="2026-09-30T10:00:00+05:00")
        files = [first]
        class FakeScript:
            def manifest(self, offset):
                self_offset = offset
                self_files = files[self_offset:self_offset + 25]
                return {"ok": True, "files": self_files,
                        "next": None, "lastScan": "2026-09-30T10:01:00Z"}
            def file(self, identifier):
                return {"ok": True, "data": base64.b64encode(raw).decode(),
                        "sha256": digest}
        with tempfile.TemporaryDirectory() as folder:
            database = SLPDatabase(Path(folder) / "slp.db")
            with patch.object(bridge, "ScriptClient", return_value=FakeScript()):
                initial = bridge.sync_inbox(database)
                self.assertEqual(initial["new_attachments"], 1)
                self.assertEqual(initial["auto_imported"], 0)
                with database._connect() as conn:
                    pending = conn.execute(
                        "SELECT id,status FROM gmail_notices WHERE message_id=?",
                        ("supplier-message-1",),
                    ).fetchone()
                self.assertEqual(pending["status"], "pending")
                gmail.approve_notice(database, pending["id"], username="editor")
                files.append(second)
                later = bridge.sync_inbox(database)
                self.assertEqual(later["new_attachments"], 1)
                self.assertEqual(later["auto_imported"], 1)
                with database._connect() as conn:
                    imported = conn.execute(
                        "SELECT status,channel_detection_method "
                        "FROM gmail_notices WHERE message_id=?",
                        ("supplier-message-2",),
                    ).fetchone()
                self.assertEqual(imported["status"], "imported")
                self.assertEqual(imported["channel_detection_method"],
                                 "confirmed_format")
            self.assertTrue(database.load_active_source_snapshot(
                "email_epg_sportplus", date(2026, 9, 30).isoformat()
            ))


if __name__ == "__main__":
    unittest.main()

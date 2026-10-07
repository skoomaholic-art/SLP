"""Collection applies supplier spreadsheets over open-source rows.

No network and no mail are used: the bridge is replaced by a fake that serves
a workbook built in the test.
"""
import asyncio
import base64
import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import cloudrun_web as web
from cloudrun_tests.test_free_bridge_mail_regression import example_sportplus
from services import free_apps_script as bridge
from services import gmail_integration as gmail
from storage.database import SLPDatabase

SOURCE = "email_epg_sportplus"
DAY = "2026-09-30"


class StoredSupplierFilesTests(unittest.TestCase):
    def setUp(self):
        self.raw = example_sportplus()
        digest = hashlib.sha256(self.raw).hexdigest()
        meta = {
            "filename": "сетка Канала.xlsx",
            "sender": "supplier@sportplus.example",
            "subject": "SPORT PLUS QAZAQSTAN расписание",
            "sha256": digest,
        }
        self.first = dict(meta, id="a" * 32, messageId="m-1",
                          received="2026-09-29T10:00:00+05:00")
        self.second = dict(meta, id="b" * 32, messageId="m-2",
                           received="2026-09-30T10:00:00+05:00")
        self.files = [self.first]
        files, raw = self.files, self.raw

        class FakeScript:
            def manifest(self, offset):
                return {"ok": True, "files": files[offset:offset + 25],
                        "next": None, "total": len(files),
                        "lastScan": "2026-09-30T10:01:00Z"}

            def file(self, identifier):
                return {"ok": True, "data": base64.b64encode(raw).decode(),
                        "sha256": digest}

        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.database = SLPDatabase(Path(self.folder.name) / "slp.db")
        patcher = patch.object(bridge, "ScriptClient", return_value=FakeScript())
        patcher.start()
        self.addCleanup(patcher.stop)

    def _notice(self, message_id):
        with self.database._connect() as conn:
            return dict(conn.execute(
                "SELECT id,status,reason FROM gmail_notices WHERE message_id=?",
                (message_id,),
            ).fetchone())

    def _scan_review_only(self):
        return bridge.sync_inbox(self.database, allow_auto_import=False)

    def test_first_file_of_an_official_layout_waits_for_a_person(self):
        self._scan_review_only()
        outcome = gmail.auto_apply_pending(self.database)
        self.assertEqual(
            (outcome["applied"], outcome["left_for_review"]), (0, 1)
        )
        notice = self._notice("m-1")
        self.assertEqual(notice["status"], "pending")
        self.assertIn("первое подтверждение", notice["reason"])
        self.assertFalse(
            self.database.load_active_source_snapshot(SOURCE, DAY)
        )

    def test_stored_file_in_a_confirmed_layout_is_applied_on_collect(self):
        self._scan_review_only()
        gmail.approve_notice(
            self.database, self._notice("m-1")["id"], username="editor"
        )
        # The background scan stores the next file without importing it.
        self.files.append(self.second)
        scanned = self._scan_review_only()
        self.assertEqual(scanned["auto_imported"], 0)
        self.assertEqual(self._notice("m-2")["status"], "pending")

        outcome = gmail.auto_apply_pending(self.database)
        self.assertEqual(
            (outcome["applied"], outcome["left_for_review"]), (1, 0)
        )
        self.assertEqual(outcome["channels"], ["SPORT+ Qazaqstan"])
        self.assertEqual(self._notice("m-2")["status"], "imported")
        self.assertTrue(
            self.database.load_active_source_snapshot(SOURCE, DAY)
        )
        # Nothing is left, so a second pass changes nothing.
        again = gmail.auto_apply_pending(self.database)
        self.assertEqual((again["applied"], again["left_for_review"]), (0, 0))

    def test_operator_switch_keeps_everything_for_review(self):
        self._scan_review_only()
        gmail.approve_notice(
            self.database, self._notice("m-1")["id"], username="editor"
        )
        self.files.append(self.second)
        self._scan_review_only()
        with patch.dict(os.environ, {"SPORT_GMAIL_AUTO_IMPORT": "false"}):
            outcome = gmail.auto_apply_pending(self.database)
        self.assertEqual(outcome["mode"], "review_only")
        self.assertEqual(outcome["applied"], 0)
        self.assertEqual(self._notice("m-2")["status"], "pending")


class CollectionOrderTests(unittest.TestCase):
    def _run(self, **kwargs):
        calls = []
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        database = SLPDatabase(Path(folder.name) / "slp.db")

        async def refresh():
            calls.append("open_sources")
            return SimpleNamespace(run_id="r1")

        async def vsetv(_database):
            calls.append("tv_guides")
            return {"stats": []}

        async def iptvx(_database):
            calls.append("iptvx")
            return {}

        def sync(_database, **options):
            calls.append(("mail_scan", options["allow_auto_import"]))
            return {"new_attachments": 1, "requires_review": 0,
                    "auto_imported": 1 if options["allow_auto_import"] else 0}

        def apply_pending(_database):
            calls.append("mail_apply_stored")
            return {"applied": 2, "left_for_review": 0, "channels": [],
                    "mode": "automatic"}

        async def validate(_events):
            calls.append("public_check")
            return {"checked": 0, "confirmed": 0, "providers": {},
                    "matches": [], "discrepancies": []}

        state = SimpleNamespace(
            database=database, backup=None, collect_lock=asyncio.Lock(),
            collect_progress={}, free_mail_bridge=True,
            schedule=SimpleNamespace(refresh=refresh),
        )
        with patch.object(web, "refresh_vsetv_web_sources", vsetv), \
             patch.object(web, "refresh_iptvx_sources", iptvx), \
             patch.object(web.freebridge, "sync_inbox", sync), \
             patch.object(web.gmail, "auto_apply_pending", apply_pending), \
             patch.object(web, "validate_events_with_public_apis", validate), \
             patch.object(web.gmail, "list_notices", return_value=[]):
            result = asyncio.run(
                web._run_collection(SimpleNamespace(state=state), **kwargs)
            )
        return calls, result

    def test_editor_collection_runs_sources_then_mail_then_public_check(self):
        calls, result = self._run(apply_supplier_mail=True)
        self.assertEqual(calls, [
            "open_sources", "tv_guides", "iptvx",
            ("mail_scan", True), "mail_apply_stored", "public_check",
        ])
        self.assertTrue(result["ok"])
        self.assertEqual(result["mail_applied"]["applied"], 2)
        self.assertEqual(
            web._mail_applied_detail(result),
            "Из почты применено таблиц поставщиков: 3",
        )

    def test_collection_without_editor_rights_only_stores_mail(self):
        calls, result = self._run()
        self.assertEqual(calls, [
            "open_sources", "tv_guides", "iptvx",
            ("mail_scan", False), "public_check",
        ])
        self.assertIsNone(result["mail_applied"])
        self.assertEqual(web._mail_applied_detail(result), "")

    def test_role_decides_whether_supplier_tables_are_applied(self):
        seen = []

        async def fake_run(_application, *, apply_supplier_mail=False):
            seen.append(apply_supplier_mail)
            return {}

        async def start(role):
            state = SimpleNamespace(collect_task=None, collect_progress={})
            request = SimpleNamespace(app=SimpleNamespace(state=state))
            with patch.object(web, "current_user",
                              return_value={"role": role}), \
                 patch.object(web, "origin_guard"), \
                 patch.object(web, "_run_collection", fake_run):
                await web.collect(request, web.CollectOptions())
                await state.collect_task

        for role in ("editor", "admin", "viewer"):
            asyncio.run(start(role))
        self.assertEqual(seen, [True, True, False])

    def test_only_registered_channels_reach_the_schedule(self):
        self.assertEqual(len(web.ALLOWED_CHANNELS), 14)
        self.assertEqual(set(web.IPTVX_CHANNELS), set(web.ALLOWED_CHANNELS))


if __name__ == "__main__":
    unittest.main()

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import cloudrun_web as web
from services import free_apps_script as bridge
from storage.database import SLPDatabase


class GoogleCloudMigrationTests(unittest.TestCase):
    def test_gcs_and_apps_script_can_coexist_and_drive_bootstraps_empty_gcs(self):
        with tempfile.TemporaryDirectory() as folder:
            old_path = web.DB_PATH
            old_bucket = web.GCS_BUCKET
            try:
                web.DB_PATH = Path(folder) / "slp.db"
                web.GCS_BUCKET = "slp-test-bucket"
                fake_bucket = SimpleNamespace(generation=0, save=Mock())

                def restore_drive(path):
                    Path(path).write_bytes(b"sqlite")
                    return SimpleNamespace(initialized_empty=False)

                with patch.object(web, "BucketSnapshot", return_value=fake_bucket), \
                     patch.object(web.freebridge, "enabled", return_value=True), \
                     patch.object(
                         web.freebridge,
                         "FreeDriveSnapshot",
                         side_effect=restore_drive,
                     ):
                    chosen = web._prepare_primary_storage()

                self.assertIs(chosen, fake_bucket)
                fake_bucket.save.assert_called_once()
            finally:
                web.DB_PATH = old_path
                web.GCS_BUCKET = old_bucket

    def test_existing_gcs_does_not_restore_drive_again(self):
        old_bucket = web.GCS_BUCKET
        try:
            web.GCS_BUCKET = "slp-test-bucket"
            fake_bucket = SimpleNamespace(generation=7, save=Mock())
            with patch.object(web, "BucketSnapshot", return_value=fake_bucket), \
                 patch.object(web.freebridge, "enabled", return_value=True), \
                 patch.object(web.freebridge, "FreeDriveSnapshot") as drive:
                chosen = web._prepare_primary_storage()
            self.assertIs(chosen, fake_bucket)
            drive.assert_not_called()
            fake_bucket.save.assert_not_called()
        finally:
            web.GCS_BUCKET = old_bucket

    def test_script_client_scan_uses_longer_timeout(self):
        client = bridge.ScriptClient.__new__(bridge.ScriptClient)
        client.url = "https://script.google.com/macros/s/test/exec"
        client.secret = "x" * 64
        with patch.object(client, "_call", return_value={"ok": True}) as call:
            result = client.scan()
        self.assertTrue(result["ok"])
        call.assert_called_once_with("scan", timeout_seconds=300)

    def test_old_bridge_without_scan_operation_still_imports_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            database = SLPDatabase(Path(directory) / "sport.db")
            fake_manifest = {
                "nextOffset": 0,
                "hasMore": False,
                "items": [],
            }
            with patch.object(
                bridge,
                "_config",
                return_value=("https://script.google.com/macros/s/test/exec", "x" * 64),
            ), patch.object(
                bridge.ScriptClient,
                "scan",
                side_effect=bridge.FreeDriveError("unknown operation"),
            ), patch.object(
                bridge.ScriptClient,
                "manifest",
                return_value=fake_manifest,
            ):
                result = bridge.sync_inbox(
                    database,
                    allow_auto_import=False,
                    refresh_archive=True,
                )
            self.assertEqual(result["new_attachments"], 0)

    def test_collection_forces_fresh_bridge_scan(self):
        with tempfile.TemporaryDirectory() as directory:
            database = SLPDatabase(Path(directory) / "sport.db")
            state = SimpleNamespace(
                database=database,
                backup=None,
                collect_lock=asyncio.Lock(),
                collect_progress={},
                free_mail_bridge=True,
                schedule=SimpleNamespace(
                    refresh=AsyncMock(return_value=SimpleNamespace(run_id="r1"))
                ),
            )
            mail_result = {
                "new_attachments": 3,
                "requires_review": 3,
                "auto_imported": 0,
            }
            with patch.object(
                web.freebridge, "sync_inbox", return_value=mail_result
            ) as sync, \
                 patch.object(
                     web, "refresh_vsetv_web_sources",
                     new=AsyncMock(return_value={"stats": []}),
                 ), \
                 patch.object(web.gmail, "list_notices", return_value=[]):
                result = asyncio.run(
                    web._run_collection(SimpleNamespace(state=state))
                )
            self.assertTrue(result["ok"])
            self.assertEqual(result["mail"]["new_attachments"], 3)
            self.assertFalse(sync.call_args.kwargs["allow_auto_import"])
            self.assertTrue(sync.call_args.kwargs["refresh_archive"])


if __name__ == "__main__":
    unittest.main()

"""Startup restore: temporary bridge outages are retried, real faults are not."""
import io
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import cloudrun_web as web
from services import free_apps_script as bridge


class StartupRestoreTests(unittest.TestCase):
    def setUp(self):
        self.old_bucket = web.GCS_BUCKET
        web.GCS_BUCKET = ""
        self.sleeps = []
        patcher = patch.object(web.time, "sleep", self.sleeps.append)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(setattr, web, "GCS_BUCKET", self.old_bucket)

    def _prepare(self, outcomes):
        with patch.object(web.freebridge, "enabled", return_value=True), \
             patch.object(web.freebridge, "FreeDriveSnapshot",
                          side_effect=outcomes) as drive:
            try:
                return web._prepare_primary_storage(), drive
            except bridge.FreeDriveError as exc:
                return exc, drive

    def test_temporary_outage_is_retried_and_startup_succeeds(self):
        snapshot = SimpleNamespace(initialized_empty=False)
        result, drive = self._prepare([
            bridge.FreeDriveError("сбой Google", transient=True),
            bridge.FreeDriveError("сбой Google", transient=True),
            snapshot,
        ])
        self.assertIs(result, snapshot)
        self.assertEqual(drive.call_count, 3)
        self.assertEqual(self.sleeps, list(web.STARTUP_RESTORE_RETRY_DELAYS))

    def test_persistent_outage_still_stops_startup(self):
        attempts = len(web.STARTUP_RESTORE_RETRY_DELAYS) + 1
        result, drive = self._prepare(
            [bridge.FreeDriveError("сбой Google", transient=True)] * attempts
        )
        self.assertIsInstance(result, bridge.FreeDriveError)
        self.assertEqual(drive.call_count, attempts)

    def test_configuration_fault_fails_at_once_without_waiting(self):
        result, drive = self._prepare(
            [bridge.FreeDriveError("подпись отклонена")]
        )
        self.assertIsInstance(result, bridge.FreeDriveError)
        self.assertEqual(drive.call_count, 1)
        self.assertEqual(self.sleeps, [])

    def test_bridge_marks_only_recoverable_http_failures_as_transient(self):
        client = bridge.ScriptClient.__new__(bridge.ScriptClient)
        client.url = "https://script.google.com/macros/s/test/exec"
        client.secret = "x" * 64

        def failure(code):
            return HTTPError(client.url, code, "x", {}, io.BytesIO(b""))

        expected = [
            (failure(500), True), (failure(503), True), (failure(429), True),
            (failure(403), False), (failure(404), False),
            (URLError("down"), True), (TimeoutError("slow"), True),
        ]
        for error, transient in expected:
            with patch.object(bridge, "urlopen", side_effect=error):
                with self.assertRaises(bridge.FreeDriveError) as caught:
                    client.backup()
            self.assertIs(caught.exception.transient, transient, repr(error))


if __name__ == "__main__":
    unittest.main()

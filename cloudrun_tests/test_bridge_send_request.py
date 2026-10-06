"""«Запросить расписание» through the Apps Script bridge.

The Python client talks to the REAL script source executed under Node with
in-memory Google services (gs_harness.js), so signature, validation, recipient
selection and replay protection are verified across both languages.
"""
import asyncio
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qsl, urlsplit

from fastapi import HTTPException

from services import free_apps_script as freebridge
from services import gmail_integration as gmail
from storage.database import SLPDatabase
import cloudrun_web as web

ROOT = Path(__file__).resolve().parents[1]
HARNESS = Path(__file__).with_name("gs_harness.js")
KEY = "k" * 64
ENV = {
    "SPORT_FREE_SCRIPT_URL": "https://script.google.com/macros/s/AAA/exec",
    "SPORT_FREE_SCRIPT_KEY": KEY,
}
Q, SETANTA, TEST = "anton@qsport.example", "sabina@setanta.example", "me@test.example"


class FakeScript:
    def __init__(self, **props):
        self.props = {"SLP_BRIDGE_KEY": KEY, **props}
        self.cache, self.mails, self.fail_for = {}, [], []
        self.calls = []

    def __call__(self, request, timeout=0):
        query = dict(parse_qsl(urlsplit(request.full_url).query))
        call = {"method": request.get_method(), "params": query}
        if request.data is not None:
            call["body"] = request.data.decode("utf-8")
        self.calls.append(call)
        done = subprocess.run(
            ["node", str(HARNESS)], check=True, capture_output=True, text=True,
            input=json.dumps({"props": self.props, "cache": self.cache,
                              "failFor": self.fail_for, "calls": [call]}),
        )
        answer = json.loads(done.stdout)
        self.props, self.cache = answer["props"], answer["cache"]
        self.mails.extend(answer["mails"])
        raw = json.dumps(answer["responses"][0]).encode("utf-8")
        return SimpleNamespace(
            read=lambda limit=-1: raw,
            __enter__=lambda self_: self_, __exit__=lambda *a: False,
        )


class _Response:
    def __init__(self, raw):
        self.raw = raw
    def read(self, limit=-1):
        return self.raw
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False


def _wrap(script):
    def opener(request, timeout=0):
        return _Response(script(request, timeout).read())
    return opener


@unittest.skipUnless(shutil.which("node"), "Node.js is required to run the .gs source")
class BridgeSendRequestTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.database = SLPDatabase(Path(self.folder.name) / "slp.db")
        gmail.init_gmail_schema(self.database)
        self.env = patch.dict(os.environ, ENV)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.folder.cleanup()

    def _script(self, **props):
        script = FakeScript(**props)
        patcher = patch.object(freebridge, "urlopen", _wrap(script))
        patcher.start()
        self.addCleanup(patcher.stop)
        return script

    def _rows(self):
        return gmail.list_requests(self.database)

    def test_test_mode_sends_only_to_test_address_never_to_supplier(self):
        script = self._script(SLP_REQUEST_Q_TO=Q, SLP_REQUEST_SETANTA_TO=SETANTA,
                              SLP_REQUEST_TEST_TO=TEST)
        result = freebridge.send_schedule_request(
            self.database, username="editor", category="q",
            period_start="2026-10-06", period_end="2026-10-12",
        )
        self.assertEqual([m["to"] for m in script.mails], [TEST])
        self.assertTrue(script.mails[0]["subject"].startswith("[ТЕСТ] "))
        self.assertIn("Q LEAGUE, Q ARENA и Q FOOTBALL", script.mails[0]["body"])
        self.assertEqual(result["mode"], "test")
        self.assertEqual([r["status"] for r in self._rows()], ["test"])

    def test_production_send_uses_configured_supplier_and_journals_pending(self):
        script = self._script(SLP_REQUEST_Q_TO=Q, SLP_REQUEST_SETANTA_TO=SETANTA,
                              SLP_REQUEST_MODE="production")
        result = freebridge.send_schedule_request(
            self.database, username="admin", category="setanta",
            period_start="2026-10-06", period_end="2026-10-12",
        )
        self.assertEqual([m["to"] for m in script.mails], [SETANTA])
        self.assertEqual(result["transport"], "apps_script")
        row = self._rows()[0]
        self.assertEqual((row["status"], row["category"], row["recipient"]),
                         ("pending", "setanta", SETANTA))

    def test_category_all_sends_two_separate_letters(self):
        script = self._script(SLP_REQUEST_Q_TO=Q, SLP_REQUEST_SETANTA_TO=SETANTA,
                              SLP_REQUEST_MODE="production")
        result = freebridge.send_schedule_request(
            self.database, username="admin", category="all",
            period_start="2026-10-06", period_end="2026-10-12",
        )
        self.assertEqual([m["to"] for m in script.mails], [Q, SETANTA])
        self.assertTrue(result["sent"])
        self.assertFalse(result["partial"])

    def test_category_all_partial_failure_is_reported_per_supplier(self):
        script = self._script(SLP_REQUEST_Q_TO=Q, SLP_REQUEST_SETANTA_TO=SETANTA,
                              SLP_REQUEST_MODE="production")
        script.fail_for = [SETANTA]
        result = freebridge.send_schedule_request(
            self.database, username="admin", category="all",
            period_start="2026-10-06", period_end="2026-10-12",
        )
        self.assertTrue(result["partial"])
        self.assertFalse(result["sent"])
        self.assertIn("Ошибка GmailApp", result["results"][1]["error"])

    def test_missing_recipient_sends_nothing_and_names_the_supplier(self):
        script = self._script(SLP_REQUEST_Q_TO=Q, SLP_REQUEST_MODE="production")
        with self.assertRaises(freebridge.FreeDriveError) as caught:
            freebridge.send_schedule_request(
                self.database, username="admin", category="all",
                period_start="2026-10-06", period_end="2026-10-12",
            )
        self.assertIn("Не настроен адрес Setanta", str(caught.exception))
        self.assertEqual(script.mails, [])

    def test_invalid_category_and_period_are_rejected(self):
        self._script(SLP_REQUEST_TEST_TO=TEST)
        with self.assertRaises(freebridge.FreeDriveError):
            freebridge.send_schedule_request(
                self.database, username="editor", category="everyone",
                period_start="2026-10-06", period_end="2026-10-12",
            )

    def test_replayed_request_and_rapid_repeat_never_send_twice(self):
        script = self._script(SLP_REQUEST_TEST_TO=TEST)
        client = freebridge.ScriptClient()
        client.send_request("q", "2026-10-06", "2026-10-12", request_id="a" * 32)
        with self.assertRaises(freebridge.FreeDriveError):
            client.send_request("q", "2026-10-06", "2026-10-12", request_id="a" * 32)
        self.assertEqual(len(script.mails), 1)

    def test_wrong_key_is_explained_not_reported_as_bare_unauthorized(self):
        script = self._script(SLP_REQUEST_TEST_TO=TEST)
        script.props["SLP_BRIDGE_KEY"] = "z" * 64
        with self.assertRaises(freebridge.FreeDriveError) as caught:
            freebridge.ScriptClient().send_request("q", "2026-10-06", "2026-10-12")
        self.assertIn("отклонил подпись", str(caught.exception))

    def test_existing_operations_still_work(self):
        script = self._script()
        self.assertEqual(freebridge.ScriptClient().template(),
                         {"ok": True, "exists": False})
        self.assertEqual(freebridge.ScriptClient().backup(),
                         {"ok": True, "exists": False})

    def test_send_enabled_true_only_after_signed_capabilities_answer(self):
        self.assertFalse(freebridge.send_status(self.database)["send_enabled"])
        self._script(SLP_REQUEST_Q_TO=Q, SLP_REQUEST_SETANTA_TO=SETANTA,
                     SLP_REQUEST_MODE="production")
        status = freebridge.refresh_send_capabilities(self.database)
        self.assertTrue(status["send_enabled"])
        self.assertEqual(status["mode"], "production")

    def test_send_disabled_without_recipients_and_reason_is_specific(self):
        self._script(SLP_REQUEST_MODE="production", SLP_REQUEST_Q_TO=Q)
        status = freebridge.refresh_send_capabilities(self.database)
        self.assertTrue(status["send_enabled"])
        self.assertEqual(status["reason"], "Не настроен получатель Setanta")

    def test_old_script_without_send_support_keeps_button_disabled(self):
        def old_script(request, timeout=0):
            return _Response(b'{"ok":false,"error":"unknown operation"}')
        with patch.object(freebridge, "urlopen", old_script):
            status = freebridge.refresh_send_capabilities(self.database)
        self.assertFalse(status["send_enabled"])

    def test_url_alone_never_enables_sending(self):
        def down(request, timeout=0):
            raise OSError("no route")
        with patch.object(freebridge, "urlopen", down):
            status = freebridge.refresh_send_capabilities(self.database)
        self.assertFalse(status["send_enabled"])

    def _request(self, bridge=True, backup=True):
        saved = []
        state = SimpleNamespace(
            database=self.database, free_mail_bridge=bridge,
            collect_lock=asyncio.Lock(),
            backup=SimpleNamespace(save=lambda: saved.append(1)) if backup else None,
        )
        return SimpleNamespace(app=SimpleNamespace(state=state)), saved

    def _post(self, request, role, **fields):
        options = web.MailRequest(**fields)
        user = {"username": "u", "role": role}
        with patch.object(web, "require_editor", return_value=user),              patch.object(web, "require_admin", return_value=user),              patch.object(web, "origin_guard"):
            return asyncio.run(web.send_test_request(request, options))

    def test_endpoint_routes_to_bridge_without_gmail_oauth_and_saves_state(self):
        script = self._script(SLP_REQUEST_TEST_TO=TEST)
        request, saved = self._request()
        with patch.object(gmail, "send_schedule_request",
                          side_effect=AssertionError("direct Gmail must not run")):
            result = self._post(request, "editor", category="q",
                                period_start="2026-10-06", period_end="2026-10-12")
        self.assertEqual(result["transport"], "apps_script")
        self.assertEqual(saved, [1])

    def test_endpoint_production_requires_admin(self):
        script = self._script(SLP_REQUEST_Q_TO=Q, SLP_REQUEST_SETANTA_TO=SETANTA,
                              SLP_REQUEST_MODE="production")
        request, _ = self._request()
        with self.assertRaises(HTTPException) as caught:
            self._post(request, "editor", category="q",
                       period_start="2026-10-06", period_end="2026-10-12")
        self.assertEqual(caught.exception.status_code, 403)
        self.assertEqual(script.mails, [])

    def test_endpoint_errors_are_readable(self):
        self._script()
        request, _ = self._request()
        with self.assertRaises(HTTPException) as caught:
            self._post(request, "admin", category="q",
                       period_start="2026-10-06", period_end="2026-10-12")
        self.assertIn("SLP_REQUEST_TEST_TO", caught.exception.detail)

    def test_direct_gmail_mode_is_unchanged(self):
        request, saved = self._request(bridge=False)
        expected = {"sent": True, "to": "x@example.com", "subject": "s"}
        with patch.object(gmail, "mail_mode", return_value="test"),              patch.object(gmail, "send_schedule_request",
                          return_value=expected) as direct:
            result = self._post(request, "editor", category="q",
                                period_start="2026-10-06", period_end="2026-10-12")
        self.assertEqual(result, expected)
        self.assertEqual(saved, [1])

    def test_status_reports_send_enabled_reason_and_capabilities(self):
        self._script(SLP_REQUEST_Q_TO=Q, SLP_REQUEST_SETANTA_TO=SETANTA,
                     SLP_REQUEST_MODE="production")
        request, _ = self._request()
        with patch.object(web, "current_user", return_value={"role": "admin"}),              patch.object(freebridge, "health", return_value={
                 "verified": True, "archiver_active": True,
                 "last_pull_at": "", "script_last_scan_at": ""}):
            status = web.gmail_status(request)
        self.assertTrue(status["bridge_mode"])
        self.assertTrue(status["send_enabled"])
        self.assertEqual(status["capabilities"],
                         {"read_mail": True, "send_request": True})

    def test_button_depends_only_on_backend_send_enabled(self):
        index = (ROOT / "cloudrun_ui" / "index.html").read_text(encoding="utf-8")
        self.assertIn("state.gmailSend=status.send_enabled&&status.connected", index)
        self.assertIn('$("request").disabled=!state.gmailSend', index)
        self.assertIn("status.send_reason||", index)
        self.assertIn('result.transport==="apps_script"', index)


if __name__ == "__main__":
    unittest.main()

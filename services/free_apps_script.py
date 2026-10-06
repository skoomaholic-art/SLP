"""No-new-paid-service SLP backup and inbox ingest via owner's Google Apps Script.

Apps Script reads ONLY selected EPG attachments in Gmail, archives them to
private Drive under the owner's quota and exposes a fresh-HMAC-authenticated
pull interface. The same script stores *encrypted* compressed SQLite snapshots.
This does not make existing Cloud Run hosting guaranteed free; its pricing
and quota must be managed separately.
"""
from __future__ import annotations

import base64
from datetime import date, datetime
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen
import zlib

from cryptography.fernet import Fernet, InvalidToken

from services import gmail_integration as gmail
from services.epg_excel import (
    InvalidEPG, MAX_WORKBOOK_BYTES, OFFICIAL_SOURCE_KEY,
    parse_supported_epg_channels, workbook_fingerprint,
)
from services.time_logic import KZ_TIMEZONE


class FreeDriveError(RuntimeError):
    pass


MAX_HTTP_BYTES = 12 * 1024 * 1024
MAX_COMPRESSED_DB_BYTES = 6 * 1024 * 1024
MAX_UNPACKED_DB_BYTES = 64 * 1024 * 1024
_EXCLUDED = re.compile(
    r"(?:^|[^a-z0-9])setanta[\s_-]+(?:(?:sports)[\s_-]+)?(?:plus|kyrgyzstan|kyrgystan)\b", re.I
)


def enabled() -> bool:
    return bool(os.environ.get("SPORT_FREE_SCRIPT_URL"))


def _config() -> tuple[str, str]:
    url = os.environ.get("SPORT_FREE_SCRIPT_URL", "").strip()
    secret = os.environ.get("SPORT_FREE_SCRIPT_KEY", "").strip()
    parts = urlsplit(url)
    if not (
        parts.scheme == "https"
        and parts.hostname == "script.google.com"
        and parts.path.startswith("/macros/s/")
        and parts.path.endswith("/exec")
        and not parts.query and not parts.fragment
        and len(secret) >= 48
    ):
        raise FreeDriveError(
            "Не настроен HTTPS Apps Script URL и длинный общий ключ; "
            "резервный режим отключён"
        )
    return url, secret


def _http_error_text(code: int) -> str:
    """Name the cause instead of a bare "HTTPError" (kept under 150 chars)."""
    prefix = "Google Apps Script недоступен (HTTP " + str(code) + "): "
    if code in (401, 403):
        return prefix + ("доступ к веб-приложению закрыт. В развёртывании "
                         "нужно «Запуск от моего имени» и «Доступ: Все»")
    if code == 404:
        return prefix + ("развёртывание по этому URL не найдено. Проверь, "
                         "что SPORT_FREE_SCRIPT_URL — текущий адрес /exec")
    if code == 429:
        return prefix + "исчерпана квота Google, повторите позже"
    if code >= 500:
        return prefix + "сбой на стороне Google или ошибка в скрипте"
    return prefix + "неожиданный ответ"


_SCRIPT_ERRORS = (
    ("unknown operation",
     "Apps Script не поддерживает отправку: обновите код скрипта и выпустите "
     "новую версию развёртывания"),
    ("recipient not configured: q",
     "Не настроен адрес QSport (SLP_REQUEST_Q_TO в свойствах скрипта)"),
    ("recipient not configured: setanta",
     "Не настроен адрес Setanta (SLP_REQUEST_SETANTA_TO в свойствах скрипта)"),
    ("recipient not configured: test",
     "Не настроен тестовый адрес (SLP_REQUEST_TEST_TO в свойствах скрипта)"),
    ("bad category", "Неверный тип запроса"),
    ("bad period", "Неверный период"),
    ("bad request", "Apps Script отклонил запрос как некорректный"),
    ("duplicate request", "Этот запрос уже был отправлен"),
    ("rate limited",
     "Запрос этому поставщику уже отправлялся менее 10 минут назад"),
    ("busy", "Apps Script занят, повторите через минуту"),
    ("send failed", "Ошибка GmailApp при отправке письма"),
)


def _script_error_text(code) -> str:
    """Translate the script's short error codes; never surface a bare code."""
    value = " ".join(str(code or "").split())[:160]
    lowered = value.casefold()
    for marker, text in _SCRIPT_ERRORS:
        if lowered.startswith(marker):
            detail = value[len(marker):].strip(" :")
            if marker == "send failed" and detail:
                return text + ": " + detail[:90]
            return text
    return "Apps Script вернул ошибку: " + (value or "без описания")


SEND_CATEGORIES = ("q", "setanta", "all")
_SEND_TARGETS = {"q": ("q",), "setanta": ("setanta",), "all": ("q", "setanta")}
_SEND_LABELS = {"q": "QSport", "setanta": "Setanta"}
CAPABILITY_MAX_AGE_SECONDS = 600


class ScriptClient:
    def __init__(self):
        self.url, self.secret = _config()

    def _signature(self, method: str, timestamp: str, operation: str,
                   argument: str) -> str:
        data = "\n".join((method, timestamp, operation, argument))
        return hmac.new(
            self.secret.encode(), data.encode(), hashlib.sha256,
        ).hexdigest()

    def _call(self, operation: str, *, argument: str = "",
              content: dict | None = None, timeout_seconds: int = 32) -> dict:
        timestamp = str(int(time.time() * 1000))
        method = "POST" if content is not None else "GET"
        body = None
        if content is not None:
            body = json.dumps(content, ensure_ascii=False,
                              separators=(",", ":")).encode()
            if len(body) > MAX_HTTP_BYTES:
                raise FreeDriveError("Сжатая резервная копия превышает лимит 12 МБ")
            argument = hashlib.sha256(body).hexdigest()
        params = {
            "op": operation, "ts": timestamp,
            "sig": self._signature(method, timestamp, operation, argument),
        }
        if operation == "manifest":
            params["offset"] = argument
        elif operation == "file":
            params["id"] = argument
        target = self.url + "?" + urlencode(params)
        request = Request(
            target, data=body, method=method,
            headers={"Content-Type": "application/json",
                     "Accept": "application/json",
                     "User-Agent": "SLP-FreeDrive-Bridge/1.0"},
        )
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                raw = response.read(MAX_HTTP_BYTES + 1)
        except HTTPError as exc:
            raise FreeDriveError(_http_error_text(exc.code)) from exc
        except (URLError, OSError) as exc:
            raise FreeDriveError(
                "Google Apps Script недоступен: " + type(exc).__name__
            ) from exc
        if len(raw) > MAX_HTTP_BYTES:
            raise FreeDriveError("Ответ Apps Script превышает 12 МБ")
        try:
            result = json.loads(raw.decode("utf-8"))
        except (UnicodeError, ValueError) as exc:
            raise FreeDriveError(
                "Apps Script не вернул JSON. Проверь публикацию Anyone/Execute as me."
            ) from exc
        if isinstance(result, dict) and result.get("error") == "unauthorized":
            # The script answers "unauthorized" for exactly two reasons: the
            # HMAC does not match (different key) or the request timestamp is
            # more than 90 seconds off. Say so instead of a bare code word.
            raise FreeDriveError(
                "Apps Script отклонил подпись: SPORT_FREE_SCRIPT_KEY не "
                "совпадает с SLP_BRIDGE_KEY скрипта или URL ведёт на "
                "другой скрипт"
            )
        if (isinstance(result, dict) and not result.get("ok")
                and operation in ("send_request", "capabilities")):
            raise FreeDriveError(_script_error_text(result.get("error")))
        if not isinstance(result, dict) or not result.get("ok"):
            raise FreeDriveError(
                "Apps Script: " + str(result.get("error", "invalid response"))[:180]
                if isinstance(result, dict) else "Некорректный ответ Apps Script"
            )
        return result

    def backup(self) -> dict:
        return self._call("backup")

    def template(self) -> dict:
        return self._call("template")

    def save_template(self, data: bytes, previous_sha: str) -> dict:
        if not data or len(data) > 6 * 1024 * 1024:
            raise FreeDriveError("Бесплатное хранилище принимает XLSX до 6 МБ")
        return self._call("template", content={
            "version": 1, "data": base64.b64encode(data).decode(),
            "sha256": hashlib.sha256(data).hexdigest(),
            "previousSha": previous_sha,
        })

    def save_backup(self, ciphertext: str, previous_sha: str) -> dict:
        return self._call("backup", content={
            "version": 1, "ciphertext": ciphertext,
            "previousSha": previous_sha,
        })

    def capabilities(self) -> dict:
        return self._call("capabilities", timeout_seconds=15)

    def send_request(self, category: str, period_start: str, period_end: str,
                     *, request_id: str = "", username: str = "") -> dict:
        """Ask the script to mail a schedule request.

        Only a whitelisted category and a period are sent. The recipient and
        the letter text are chosen inside the script from its own properties.
        """
        if category not in SEND_CATEGORIES:
            raise FreeDriveError("Неверный тип запроса")
        for value in (period_start, period_end):
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(value or "")):
                raise FreeDriveError("Неверный период")
        request_id = request_id or secrets.token_hex(16)
        if not re.fullmatch(r"[a-f0-9]{32}", request_id):
            raise FreeDriveError("Некорректный идентификатор запроса")
        return self._call("send_request", content={
            "version": 1, "category": category,
            "period_start": period_start, "period_end": period_end,
            "request_id": request_id,
            "username": re.sub(r"[^\w.@-]", "", str(username or ""))[:64],
        }, timeout_seconds=60)

    def scan(self) -> dict:
        # Force Apps Script to scan Gmail immediately instead of waiting for
        # the hourly trigger. A busy mailbox can take longer than the normal
        # manifest/file request timeout.
        return self._call("scan", timeout_seconds=300)

    def manifest(self, offset: int) -> dict:
        return self._call("manifest", argument=str(offset))

    def file(self, identifier: str) -> dict:
        if not re.fullmatch(r"[a-f0-9]{32}", identifier):
            raise FreeDriveError("Недопустимый идентификатор вложения")
        return self._call("file", argument=identifier)


def _cipher(secret: str) -> Fernet:
    material = hashlib.sha256(
        b"SLP_FREE_DRIVE_BACKUP_V1\x00" + secret.encode()
    ).digest()
    return Fernet(base64.urlsafe_b64encode(material))


class FreeDriveSnapshot:
    """Restore before app startup; only one writer allowed (checksum CAS)."""

    def __init__(self, path: Path):
        self.path = path
        self.client = ScriptClient()
        self.cipher = _cipher(self.client.secret)
        response = self.client.backup()
        self.remote_sha = ""
        if not response.get("exists"):
            if not path.exists():
                self.initialized_empty = True
            else:
                self.initialized_empty = False
            return

        ciphertext = str(response.get("ciphertext") or "")
        sha = hashlib.sha256(ciphertext.encode()).hexdigest()
        if str(response.get("sha256") or "") not in ("", sha):
            raise FreeDriveError("В Drive повреждена контрольная сумма резервной копии")
        if len(ciphertext) > MAX_HTTP_BYTES:
            raise FreeDriveError("Резервная копия превышает безопасный лимит")
        try:
            packed = self.cipher.decrypt(ciphertext.encode())
            if len(packed) > MAX_COMPRESSED_DB_BYTES:
                raise FreeDriveError("Сжатая резервная копия слишком большая")
            unpacked = zlib.decompressobj()
            data = unpacked.decompress(packed, MAX_UNPACKED_DB_BYTES + 1)
            if (len(data) > MAX_UNPACKED_DB_BYTES or not unpacked.eof
                    or unpacked.unused_data or unpacked.unconsumed_tail):
                raise FreeDriveError("Неверный размер распакованной базы")
        except (InvalidToken, zlib.error) as exc:
            raise FreeDriveError("Резервная копия не расшифрована") from exc

        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=path.parent, suffix=".db", delete=False,
        ) as tmp:
            local = Path(tmp.name)
            tmp.write(data)
        try:
            with sqlite3.connect(local) as db:
                checked = db.execute("PRAGMA quick_check").fetchone()
            if not checked or checked[0] != "ok":
                raise FreeDriveError("SQLite backup failed integrity check")
            os.replace(local, path)
        finally:
            local.unlink(missing_ok=True)
        self.remote_sha = sha
        self.initialized_empty = False

    def save(self) -> None:
        with tempfile.NamedTemporaryFile(
            dir=self.path.parent, suffix=".db", delete=False,
        ) as tmp:
            local = Path(tmp.name)
        try:
            src = sqlite3.connect(self.path)
            dst = sqlite3.connect(local)
            try:
                src.backup(dst)
            finally:
                dst.close()
                src.close()
            raw = local.read_bytes()
            if len(raw) > MAX_UNPACKED_DB_BYTES:
                raise FreeDriveError("SQLite превышает лимит для бесплатной копии")
            packed = zlib.compress(raw, level=6)
            if len(packed) > MAX_COMPRESSED_DB_BYTES:
                raise FreeDriveError("Архив превышает безопасный лимит")
            ciphertext = self.cipher.encrypt(packed).decode()
            if len(ciphertext) > 11 * 1024 * 1024:
                raise FreeDriveError("Зашифрованная копия слишком большая")
            self.client.save_backup(ciphertext, self.remote_sha)
            self.remote_sha = hashlib.sha256(ciphertext.encode()).hexdigest()
        finally:
            local.unlink(missing_ok=True)


def _exists(database, message_id: str, attachment_id: str) -> bool:
    with database._connect() as conn:
        return conn.execute(
            "SELECT 1 FROM gmail_notices WHERE message_id=? AND attachment_id=?",
            (message_id, attachment_id),
        ).fetchone() is not None


def _record_notice(database, meta: dict, payload: bytes,
                   parsed, reason: str, fingerprint: str,
                   confirmed_channel: str = "") -> int | None:
    identity = str(meta["messageId"])
    attachment = "apps_script:" + str(meta["id"])
    if parsed is not None:
        attachment += "#" + parsed.channel
    if _exists(database, identity, attachment):
        return None
    now = datetime.now(KZ_TIMEZONE).isoformat()
    channel = parsed.channel if parsed else ""
    scope = parsed.scope_dates if parsed else ()
    with database._connect() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO gmail_notices("
            "message_id,attachment_id,filename,subject,sender,snippet,"
            "detected_channel,status,reason,received_at,created_at,"
            "attachment_bytes,classification,classification_method,"
            "original_sender,thread_id,provider,period_start,period_end,"
            "classification_confidence,classification_evidence,"
            "format_fingerprint,channel_detection_method,source_document_sha256"
            ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (identity, attachment, meta["filename"], meta.get("subject", ""),
             meta.get("sender", ""), "",
             channel, "pending" if parsed else "review", reason,
             meta["received"], now, payload, "SCHEDULE_NEW",
             "google_apps_script_free_bridge",
             meta.get("sender", ""), "", "supplier",
             scope[0] if scope else "", scope[-1] if scope else "",
             1.0 if parsed else 0.0, "[]", fingerprint,
             ("confirmed_format" if parsed and confirmed_channel == parsed.channel
              else "workbook_content" if parsed else ""), meta["sha256"]),
        )
        created = int(cur.lastrowid) if cur.rowcount else None
    if created and channel:
        gmail._mark_request_response(
            database, thread_id="", message_id=identity, channel=channel,
            sender_key=gmail._sender_key(str(meta.get("sender") or "")),
        )
    return created



def health(database) -> dict:
    """Only report the free bridge as connected after an actual successful pull.

    A reachable Apps Script does not imply its hourly Gmail trigger is running.
    Expose that as a separate observed status, without probing on every UI load.
    """
    with database._connect() as conn:
        if not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='free_bridge_health'"
        ).fetchone():
            return {"verified": False, "archiver_active": False,
                    "last_pull_at": "", "script_last_scan_at": ""}
        row = conn.execute(
            "SELECT last_pull_at,script_last_scan_at "
            "FROM free_bridge_health WHERE id=1"
        ).fetchone()
    if not row:
        return {"verified": False, "archiver_active": False,
                "last_pull_at": "", "script_last_scan_at": ""}
    scan = str(row["script_last_scan_at"] or "")
    active = False
    try:
        elapsed = (datetime.now(KZ_TIMEZONE) -
                   datetime.fromisoformat(scan.replace("Z", "+00:00"))).total_seconds()
        active = -300 <= elapsed <= 3 * 3600
    except (ValueError, TypeError):
        pass
    return {"verified": True, "archiver_active": active,
            "last_pull_at": str(row["last_pull_at"] or ""),
            "script_last_scan_at": scan}


def _init_send_state(conn) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS free_bridge_send("
        "id INTEGER PRIMARY KEY CHECK(id=1),checked_at TEXT NOT NULL,"
        "supported INTEGER NOT NULL,mode TEXT NOT NULL,"
        "q INTEGER NOT NULL,setanta INTEGER NOT NULL,reason TEXT NOT NULL)"
    )


def refresh_send_capabilities(database, client: "ScriptClient | None" = None) -> dict:
    supported, mode, sendable, reason = False, "test", {}, ""
    try:
        answer = (client or ScriptClient()).capabilities()
        supported = bool((answer.get("capabilities") or {}).get("send_request"))
        mode = "production" if answer.get("mode") == "production" else "test"
        sendable = answer.get("sendable") or {}
        if not supported:
            reason = _script_error_text("unknown operation")
        elif not (sendable.get("q") or sendable.get("setanta")):
            reason = (_script_error_text("recipient not configured: test")
                      if mode == "test"
                      else "Не настроены адреса поставщиков в свойствах скрипта")
    except FreeDriveError as exc:
        reason = str(exc)[:200]
    with database._connect() as conn:
        _init_send_state(conn)
        conn.execute(
            "INSERT INTO free_bridge_send(id,checked_at,supported,mode,q,setanta,reason) "
            "VALUES(1,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
            "checked_at=excluded.checked_at,supported=excluded.supported,"
            "mode=excluded.mode,q=excluded.q,setanta=excluded.setanta,"
            "reason=excluded.reason",
            (datetime.now(KZ_TIMEZONE).isoformat(timespec="seconds"),
             int(supported), mode, int(bool(sendable.get("q"))),
             int(bool(sendable.get("setanta"))), reason),
        )
    return send_status(database)


def send_status(database) -> dict:
    with database._connect() as conn:
        _init_send_state(conn)
        row = conn.execute(
            "SELECT checked_at,supported,mode,q,setanta,reason "
            "FROM free_bridge_send WHERE id=1"
        ).fetchone()
    if not row:
        return {"checked_at": "", "supported": False, "mode": "test",
                "sendable": {"q": False, "setanta": False},
                "send_enabled": False, "stale": True,
                "reason": "Возможность отправки ещё не проверялась"}
    sendable = {"q": bool(row["q"]), "setanta": bool(row["setanta"])}
    stale = True
    try:
        age = (datetime.now(KZ_TIMEZONE)
               - datetime.fromisoformat(str(row["checked_at"]))).total_seconds()
        stale = not (-60 <= age <= CAPABILITY_MAX_AGE_SECONDS)
    except (TypeError, ValueError):
        pass
    enabled = bool(row["supported"]) and any(sendable.values())
    reason = str(row["reason"] or "")
    if enabled and not all(sendable.values()):
        missing = [_SEND_LABELS[key] for key, ready in sendable.items() if not ready]
        reason = "Не настроен получатель " + ", ".join(missing)
    return {"checked_at": str(row["checked_at"] or ""),
            "supported": bool(row["supported"]),
            "mode": str(row["mode"] or "test"), "sendable": sendable,
            "send_enabled": enabled, "stale": stale, "reason": reason}


def send_schedule_request(database, *, username: str, category: str,
                          period_start: str, period_end: str,
                          client: "ScriptClient | None" = None) -> dict:
    if category not in SEND_CATEGORIES:
        raise FreeDriveError("Неверный тип запроса")
    try:
        period_start, period_end = gmail._request_period(period_start, period_end)
    except gmail.GmailTransportError as exc:
        raise FreeDriveError("Неверный период: " + str(exc)) from exc
    gmail.init_gmail_schema(database)
    targets = _SEND_TARGETS[category]
    with database._connect() as conn:
        waiting = conn.execute(
            "SELECT category FROM gmail_requests WHERE period_start=? AND "
            "period_end=? AND status IN ('pending','partial') "
            "AND delivery_mode='production'",
            (period_start, period_end),
        ).fetchall()
    already = {row["category"] for row in waiting}
    if "all" in already:
        already |= {"q", "setanta"}
    blocked = [_SEND_LABELS[item] for item in targets if item in already]
    if blocked:
        raise FreeDriveError(
            "Аналогичный запрос уже ожидает ответа: " + ", ".join(blocked)
        )
    answer = (client or ScriptClient()).send_request(
        category, period_start, period_end, username=username,
    )
    mode = "production" if answer.get("mode") == "production" else "test"
    results = []
    now = datetime.now(KZ_TIMEZONE).isoformat()
    with database._connect() as conn:
        for item in answer.get("results") or []:
            target = str(item.get("category") or "")
            if target not in _SEND_LABELS:
                continue
            entry = {"category": target, "label": _SEND_LABELS[target],
                     "to": str(item.get("to") or "")[:254],
                     "subject": str(item.get("subject") or "")[:200],
                     "sent": bool(item.get("sent")),
                     "error": _script_error_text(item.get("error"))
                     if item.get("error") else ""}
            results.append(entry)
            if not entry["sent"]:
                continue
            conn.execute(
                "INSERT INTO gmail_requests("
                "recipient,subject,category,sent_by,gmail_message_id,sent_at,"
                "thread_id,requested_channels_json,status,"
                "period_start,period_end,delivery_mode"
                ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (entry["to"], entry["subject"], target, username,
                 "apps_script:" + secrets.token_hex(8), now, "",
                 json.dumps(gmail.REQUEST_CHANNELS[target], ensure_ascii=False),
                 "pending" if mode == "production" else "test",
                 period_start, period_end, mode),
            )
    sent = [entry for entry in results if entry["sent"]]
    if not sent:
        raise FreeDriveError(
            results[0]["error"] if results and results[0]["error"]
            else "Apps Script не подтвердил отправку"
        )
    return {"sent": len(sent) == len(targets),
            "partial": len(sent) < len(targets),
            "mode": mode, "category": category, "transport": "apps_script",
            "to": ", ".join(entry["to"] for entry in sent),
            "subject": sent[0]["subject"],
            "channels": [channel for entry in sent
                         for channel in gmail.REQUEST_CHANNELS[entry["category"]]],
            "period_start": period_start, "period_end": period_end,
            "results": results}


def sync_inbox(
    database,
    *,
    allow_auto_import: bool = True,
    refresh_archive: bool = False,
) -> dict:
    """Incremental ingest of archived supplier mail.

    refresh_archive=True asks Apps Script to scan Gmail immediately before the
    manifest is read, so a manual SLP collection does not wait for the hourly
    Apps Script trigger.
    """
    gmail.init_gmail_schema(database)
    with database._connect() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS free_bridge_processed ("
            "file_id TEXT PRIMARY KEY, sha256 TEXT NOT NULL, processed_at TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS free_bridge_cursor ("
            "id INTEGER PRIMARY KEY CHECK(id=1), next_offset INTEGER NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS free_bridge_health ("
            "id INTEGER PRIMARY KEY CHECK(id=1), "
            "last_pull_at TEXT NOT NULL, script_last_scan_at TEXT NOT NULL)"
        )
        saved = conn.execute(
            "SELECT next_offset FROM free_bridge_cursor WHERE id=1"
        ).fetchone()
    client = ScriptClient()
    if refresh_archive:
        try:
            client.scan()
        except FreeDriveError as exc:
            # Backward compatibility with the currently published Apps Script
            # revision. Older bridge deployments do not know the authenticated
            # "scan" operation yet; in that case continue with the latest
            # hourly manifest instead of failing the entire SLP collection.
            if "unknown operation" not in str(exc).casefold():
                raise
    offset = int(saved["next_offset"]) if saved else 0
    created = reviewed = auto_imported = 0
    visited = set()
    last_scan = ""
    deferred = False
    cursor_advanced = False
    for page_number in range(4):
        page = client.manifest(offset)
        total = page.get("total")
        legacy_manifest = not (
            isinstance(total, int) and 0 <= total <= 10000
        )
        # Older published scripts expose only a recent moving window and do
        # not provide a stable total. Read that window from offset 0 on every
        # sync and rely on free_bridge_processed for idempotency instead of
        # advancing a cursor that could skip newly inserted mail.
        if legacy_manifest:
            if offset != 0:
                offset = 0
                page = client.manifest(0)
            total = len(page.get("files") or [])
        if offset > total:
            # Owner reset the Drive archive. The processed-id table still
            # prevents re-import of any files that survived the reset.
            offset = 0
            page = client.manifest(offset)
            total = page.get("total")
            if not isinstance(total, int) or total < 0 or total > 10000:
                raise FreeDriveError("Некорректный размер манифеста")
        last_scan = str(page.get("lastScan") or "")
        files = page.get("files")
        if not isinstance(files, list) or len(files) > 25:
            raise FreeDriveError("Некорректный манифест Apps Script")
        for meta in files:
            identifier = str(meta.get("id") or "")
            message_id = str(meta.get("messageId") or "")
            filename = str(meta.get("filename") or "")
            if not re.fullmatch(r"[a-f0-9]{32}", identifier):
                raise FreeDriveError("Недопустимый идентификатор файла в манифесте")
            if identifier in visited:
                continue
            visited.add(identifier)
            if (_EXCLUDED.search(filename)
                    or not filename.casefold().endswith((".xlsx", ".xls"))):
                continue
            if not message_id or not filename or len(filename) > 180:
                continue
            with database._connect() as conn:
                already_done = conn.execute(
                    "SELECT 1 FROM free_bridge_processed "
                    "WHERE file_id=? AND sha256=?",
                    (identifier, str(meta.get("sha256") or "")),
                ).fetchone()
            if already_done:
                continue
            payload = client.file(identifier)
            try:
                data = base64.b64decode(payload["data"], validate=True)
            except (KeyError, ValueError) as exc:
                raise FreeDriveError("Невозможно прочитать вложение") from exc
            if (not data or len(data) > MAX_WORKBOOK_BYTES
                    or hashlib.sha256(data).hexdigest() != meta.get("sha256")
                    or payload.get("sha256") != meta.get("sha256")):
                raise FreeDriveError("Нарушена целостность вложения")
            try:
                fingerprint = workbook_fingerprint(data, filename)
            except InvalidEPG:
                fingerprint = ""
            # Reuse a supplier + workbook mapping only after a human has
            # approved it. The generic SPORT+ "сетка Канала.xlsx" must not
            # require approval every week when its format is unchanged.
            confirmed_channel = gmail._confirmed_format_channel(
                database, fingerprint,
                gmail._sender_key(str(meta.get("sender") or "")),
            )
            try:
                parsed_items = parse_supported_epg_channels(
                    data, filename,
                    today=date.fromisoformat(str(meta["received"])[:10]),
                    context=" ".join((meta.get("subject", ""), meta.get("sender", ""))),
                    confirmed_channel=confirmed_channel,
                )
            except (InvalidEPG, ValueError) as exc:
                notice = _record_notice(database, meta, data, None,
                                        str(exc)[:200], fingerprint)
                if notice:
                    created += 1
                    reviewed += 1
                with database._connect() as conn:
                    conn.execute(
                        "INSERT OR REPLACE INTO free_bridge_processed"
                        "(file_id,sha256,processed_at) VALUES (?,?,?)",
                        (identifier, str(meta["sha256"]),
                         datetime.now(KZ_TIMEZONE).isoformat()),
                    )
                continue
            for parsed in parsed_items:
                notice = _record_notice(
                    database, meta, data, parsed, "", fingerprint,
                    confirmed_channel=confirmed_channel,
                )
                if not notice:
                    continue
                created += 1
                # The first QAZSPORT/SPORT+ layout requires editorial approval.
                # All channel/schedule conflicts still pass _safe_auto_apply.
                trusted_official = (
                    parsed.channel not in OFFICIAL_SOURCE_KEY
                    or confirmed_channel == parsed.channel
                )
                if (allow_auto_import and gmail._auto_import_enabled()
                        and trusted_official):
                    accepted, why = gmail._safe_auto_apply(database, notice, parsed)
                    if accepted:
                        auto_imported += 1
                        continue
                    with database._connect() as conn:
                        state = conn.execute(
                            "SELECT status FROM gmail_notices WHERE id=?", (notice,)
                        ).fetchone()
                        if state and state["status"] == "imported":
                            # A partial EPG import may have already saved some
                            # approved days and cleared the raw attachment.
                            # Never relabel that record pending without bytes.
                            auto_imported += 1
                            reviewed += 1
                            continue
                        conn.execute(
                            "UPDATE gmail_notices SET status='pending', reason=? "
                            "WHERE id=? AND status='pending'",
                            ((why or "Нужна редакторская проверка")[:200], notice),
                        )
                reviewed += 1
            with database._connect() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO free_bridge_processed"
                    "(file_id,sha256,processed_at) VALUES (?,?,?)",
                    (identifier, str(meta["sha256"]),
                     datetime.now(KZ_TIMEZONE).isoformat()),
                )
        if legacy_manifest:
            next_offset = 0
            finished = True
        else:
            next_offset = page.get("next")
            if next_offset is None:
                next_offset = offset + len(files)
                if next_offset > total:
                    raise FreeDriveError("Манифест изменился во время чтения")
                finished = True
            else:
                if (not isinstance(next_offset, int) or next_offset <= offset
                        or next_offset > total):
                    raise FreeDriveError("Некорректная пагинация манифеста")
                finished = False
        with database._connect() as conn:
            conn.execute(
                "INSERT INTO free_bridge_cursor(id,next_offset) VALUES(1,?) "
                "ON CONFLICT(id) DO UPDATE SET next_offset=excluded.next_offset",
                (next_offset,),
            )
        cursor_advanced = cursor_advanced or next_offset != offset
        offset = next_offset
        if finished:
            break
        if page_number == 3:
            deferred = True
    pulled_at = datetime.now(KZ_TIMEZONE).isoformat()
    with database._connect() as conn:
        conn.execute(
            "UPDATE gmail_sync_state SET last_success_at=?, last_error='' WHERE id=1",
            (pulled_at,),
        )
        conn.execute(
            "INSERT INTO free_bridge_health(id,last_pull_at,script_last_scan_at) "
            "VALUES(1,?,?) ON CONFLICT(id) DO UPDATE SET "
            "last_pull_at=excluded.last_pull_at, "
            "script_last_scan_at=excluded.script_last_scan_at",
            (pulled_at, last_scan),
        )
    return {
        "new_attachments": created, "auto_imported": auto_imported,
        "requires_review": reviewed, "pending": gmail.status(database)["pending"],
        "scanned_messages": len(visited), "mode": "free_apps_script_drive",
        "script_last_scan_at": last_scan,
        "cursor_advanced": cursor_advanced,
        "more_archived_files": deferred,
    }

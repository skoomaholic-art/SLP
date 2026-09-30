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
              content: dict | None = None) -> dict:
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
            with urlopen(request, timeout=32) as response:
                raw = response.read(MAX_HTTP_BYTES + 1)
        except (URLError, HTTPError, OSError) as exc:
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
        return int(cur.lastrowid) if cur.rowcount else None



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


def sync_inbox(database, *, allow_auto_import: bool = True) -> dict:
    """Incremental ingest of already-archived files, no Gmail OAuth or GCS."""
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
    offset = int(saved["next_offset"]) if saved else 0
    created = reviewed = auto_imported = 0
    visited = set()
    last_scan = ""
    deferred = False
    cursor_advanced = False
    for page_number in range(4):
        page = client.manifest(offset)
        total = page.get("total")
        # Older scripts returned only the most recent 21 days and sorted
        # newest first. Resuming their shifting offsets could lose mail.
        if not isinstance(total, int) or total < 0 or total > 10000:
            raise FreeDriveError(
                "Обнови опубликованный Apps Script: нужна стабильная " 
                "пагинация полного архива (поле total)"
            )
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

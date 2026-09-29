"""Gmail transport for the existing SLP web service.

The connected personal Gmail account is authorized by its owner with Google
OAuth. Never use the assistant's connector token as the Cloud Run credential.
No message is deleted, labeled or forwarded. Unambiguous XLSX attachments
are imported automatically when enabled; conflicts remain review-only.
"""
from __future__ import annotations

import base64
from datetime import datetime, timedelta
from email.utils import parseaddr
import hashlib
import json
import os
import re
import secrets
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from cryptography.fernet import Fernet, InvalidToken

from services.epg_excel import (
    InvalidEPG, MAX_WORKBOOK_BYTES, parse_epg_xlsx_channels,
    workbook_fingerprint,
)
from services import ai_pipeline
from services.time_logic import KZ_TIMEZONE

OAUTH_SCOPES = (
    "openid email "
    "https://www.googleapis.com/auth/gmail.readonly "
    "https://www.googleapis.com/auth/gmail.send"
)
TOKEN_URL = "https://oauth2.googleapis.com/token"
AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GMAIL_URL = "https://gmail.googleapis.com/gmail/v1"
OWNER_ACCOUNT = "alexandr.petrossov@gmail.com"
REVIEW_TOKENS = re.compile(
    r"(setanta|сетанта|qsport|q\s*(?:league|arena|football)|"
    r"qazsport|спорт\s*\+|sport\s*\+|sport\s*plus|sportplus|"
    r"viju\s*\+\s*sport)", re.I
)
CHANGE_TOKENS = re.compile(
    r"(изменен|изменени|исправлен|перенос|отмен|корректи|"
    r"обновлен|update|change|revised|cancel|перенес)", re.I
)
MAX_LIST_MESSAGES = 100
MAX_ATTACHMENTS_PER_SYNC = 35
MAX_BODY_BYTES = 6000
SYNC_OVERLAP_SECONDS = 300
MAX_MESSAGE_ATTEMPTS = 3


class GmailNotConfigured(RuntimeError):
    pass


class GmailTransportError(RuntimeError):
    pass


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def _url(path: str) -> str:
    origin = _env("SPORT_PUBLIC_URL")
    if not origin.startswith("https://") or "/" in origin[8:]:
        raise GmailNotConfigured("Для Gmail OAuth необходим SPORT_PUBLIC_URL (HTTPS)")
    return origin + path


def _fernet() -> Fernet:
    key = _env("SPORT_GMAIL_TOKEN_KEY")
    if not key:
        raise GmailNotConfigured("Не задан SPORT_GMAIL_TOKEN_KEY")
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as exc:
        raise GmailNotConfigured("Неверный ключ шифрования Gmail") from exc


def _client() -> tuple[str, str]:
    client_id, client_secret = _env("SPORT_GMAIL_CLIENT_ID"), _env("SPORT_GMAIL_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise GmailNotConfigured("Не настроен OAuth-клиент Gmail")
    return client_id, client_secret


def configured() -> bool:
    return all((_env("SPORT_GMAIL_CLIENT_ID"), _env("SPORT_GMAIL_CLIENT_SECRET"),
                _env("SPORT_GMAIL_TOKEN_KEY"), _env("SPORT_PUBLIC_URL")))


def init_gmail_schema(database) -> None:
    with database._connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS gmail_oauth (
                id INTEGER PRIMARY KEY CHECK (id=1),
                email TEXT NOT NULL,
                refresh_token_cipher BLOB NOT NULL,
                connected_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS gmail_oauth_states (
                state_sha TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS gmail_notices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id TEXT NOT NULL,
                attachment_id TEXT NOT NULL,
                filename TEXT NOT NULL,
                subject TEXT NOT NULL,
                sender TEXT NOT NULL,
                snippet TEXT NOT NULL DEFAULT '',
                detected_channel TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                reason TEXT NOT NULL DEFAULT '',
                received_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                reviewed_by TEXT NOT NULL DEFAULT '',
                reviewed_at TEXT NOT NULL DEFAULT '',
                imported_hash TEXT NOT NULL DEFAULT '',
                classification TEXT NOT NULL DEFAULT '',
                classification_method TEXT NOT NULL DEFAULT '',
                attachment_bytes BLOB,
                UNIQUE(message_id, attachment_id)
            );
            CREATE INDEX IF NOT EXISTS idx_gmail_notices_status
                ON gmail_notices(status, created_at DESC);
            CREATE TABLE IF NOT EXISTS gmail_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recipient TEXT NOT NULL,
                subject TEXT NOT NULL,
                category TEXT NOT NULL,
                sent_by TEXT NOT NULL,
                gmail_message_id TEXT NOT NULL,
                sent_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS gmail_sync_state (
                id INTEGER PRIMARY KEY CHECK (id=1),
                last_internal_date_ms INTEGER NOT NULL DEFAULT 0,
                last_success_at TEXT NOT NULL DEFAULT '',
                last_error TEXT NOT NULL DEFAULT ''
            );
            INSERT OR IGNORE INTO gmail_sync_state(id) VALUES(1);
            CREATE TABLE IF NOT EXISTS gmail_processing_errors (
                message_id TEXT PRIMARY KEY,
                stage TEXT NOT NULL,
                error TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 1,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                resolved_at TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS gmail_format_mappings (
                fingerprint TEXT NOT NULL,
                sender_key TEXT NOT NULL,
                channel TEXT NOT NULL,
                confirmed_by TEXT NOT NULL,
                confirmed_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                PRIMARY KEY(fingerprint, sender_key, channel)
            );
        """)
        existing_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(gmail_notices)")
        }
        if "imported_hash" not in existing_columns:
            conn.execute(
                "ALTER TABLE gmail_notices ADD COLUMN imported_hash "
                "TEXT NOT NULL DEFAULT ''"
            )
        for name in ("classification", "classification_method"):
            if name not in existing_columns:
                conn.execute("ALTER TABLE gmail_notices ADD COLUMN " + name +
                             " TEXT NOT NULL DEFAULT ''")
        notice_columns = {
            "original_sender": "TEXT NOT NULL DEFAULT ''",
            "thread_id": "TEXT NOT NULL DEFAULT ''",
            "provider": "TEXT NOT NULL DEFAULT ''",
            "period_start": "TEXT NOT NULL DEFAULT ''",
            "period_end": "TEXT NOT NULL DEFAULT ''",
            "classification_confidence": "REAL NOT NULL DEFAULT 0",
            "classification_evidence": "TEXT NOT NULL DEFAULT '[]'",
            "format_fingerprint": "TEXT NOT NULL DEFAULT ''",
            "channel_detection_method": "TEXT NOT NULL DEFAULT ''",
            "source_document_sha256": "TEXT NOT NULL DEFAULT ''",
        }
        existing_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(gmail_notices)")
        }
        for name, definition in notice_columns.items():
            if name not in existing_columns:
                conn.execute(
                    "ALTER TABLE gmail_notices ADD COLUMN " + name + " " + definition
                )
        request_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(gmail_requests)")
        }
        for name, definition in {
            "thread_id": "TEXT NOT NULL DEFAULT ''",
            "requested_channels_json": "TEXT NOT NULL DEFAULT '[]'",
            "received_channels_json": "TEXT NOT NULL DEFAULT '[]'",
            "period_start": "TEXT NOT NULL DEFAULT ''",
            "period_end": "TEXT NOT NULL DEFAULT ''",
            "status": "TEXT NOT NULL DEFAULT 'pending'",
            "answered_message_id": "TEXT NOT NULL DEFAULT ''",
            "answered_at": "TEXT NOT NULL DEFAULT ''",
            "delivery_mode": "TEXT NOT NULL DEFAULT 'test'",
        }.items():
            if name not in request_columns:
                conn.execute(
                    "ALTER TABLE gmail_requests ADD COLUMN " + name + " " + definition
                )


def _json_http(url: str, *, data: dict | None = None,
               token: str = "", method: str = "") -> dict:
    headers = {"Accept": "application/json", "User-Agent": "SLP-Sport-EPG/1.0"}
    body = None
    if data is not None:
        body = urlencode(data).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if token:
        headers["Authorization"] = "Bearer " + token
    try:
        req = Request(url, data=body, headers=headers,
                      method=method or ("POST" if body is not None else "GET"))
        with urlopen(req, timeout=20) as response:
            return json.loads(response.read(12 * 1024 * 1024).decode())
    except (HTTPError, URLError, ValueError, OSError) as exc:
        raise GmailTransportError("Не удалось выполнить запрос Gmail: " +
                                  type(exc).__name__) from exc


def _json_api(path: str, token: str, *, payload: dict | None = None) -> dict:
    url = GMAIL_URL + path
    if payload is None:
        return _json_http(url, token=token)
    raw = json.dumps(payload, ensure_ascii=False).encode()
    req = Request(url, data=raw, method="POST", headers={
        "Accept": "application/json", "Content-Type": "application/json",
        "Authorization": "Bearer " + token, "User-Agent": "SLP-Sport-EPG/1.0",
    })
    try:
        with urlopen(req, timeout=20) as response:
            return json.loads(response.read(12 * 1024 * 1024).decode())
    except (HTTPError, URLError, ValueError, OSError) as exc:
        raise GmailTransportError("Ошибка Gmail API: " + type(exc).__name__) from exc


def start_oauth(database, *, username: str) -> str:
    client_id, _ = _client()
    _fernet()
    init_gmail_schema(database)
    state = secrets.token_urlsafe(32)
    now = datetime.now(KZ_TIMEZONE)
    with database._connect() as conn:
        conn.execute("DELETE FROM gmail_oauth_states WHERE expires_at < ?", (now.isoformat(),))
        conn.execute(
            "INSERT INTO gmail_oauth_states(state_sha, username, expires_at) VALUES (?, ?, ?)",
            (hashlib.sha256(state.encode()).hexdigest(), username,
             (now + timedelta(minutes=10)).isoformat()),
        )
    return AUTHORIZE_URL + "?" + urlencode({
        "client_id": client_id, "redirect_uri": _url("/api/gmail/callback"),
        "response_type": "code", "scope": OAUTH_SCOPES, "state": state,
        "access_type": "offline", "prompt": "consent", "login_hint": OWNER_ACCOUNT,
        "include_granted_scopes": "false",
    })


def complete_oauth(database, *, username: str, state: str, code: str) -> None:
    init_gmail_schema(database)
    if len(state) < 32 or not code or len(code) > 4096:
        raise GmailTransportError("Отсутствует корректный OAuth state/code")
    with database._connect() as conn:
        # Consume state before outbound requests. One-time even when exchange
        # fails; retry via a new authorization request.
        row = conn.execute(
            "SELECT username,expires_at FROM gmail_oauth_states WHERE state_sha=?",
            (hashlib.sha256(state.encode()).hexdigest(),),
        ).fetchone()
        conn.execute(
            "DELETE FROM gmail_oauth_states WHERE state_sha=?",
            (hashlib.sha256(state.encode()).hexdigest(),),
        )
    if not row or row["username"] != username or (
            datetime.fromisoformat(row["expires_at"]) <= datetime.now(KZ_TIMEZONE)):
        raise GmailTransportError("OAuth-подтверждение устарело или не принадлежит пользователю")
    client_id, client_secret = _client()
    token_data = _json_http(TOKEN_URL, data={
        "code": code, "client_id": client_id, "client_secret": client_secret,
        "redirect_uri": _url("/api/gmail/callback"),
        "grant_type": "authorization_code",
    })
    access_token = token_data.get("access_token", "")
    refresh_token = token_data.get("refresh_token", "")
    if not access_token or not refresh_token:
        raise GmailTransportError("Google не выдал офлайн-доступ. Повторите авторизацию")
    profile = _json_api("/users/me/profile", access_token)
    if str(profile.get("emailAddress", "")).casefold() != OWNER_ACCOUNT:
        raise GmailTransportError("Неверный Google-аккаунт. Требуется почта владельца")
    # Encrypt before writing to the durable database. The encryption key is
    # deployed as a separate secret and is NEVER persisted in SQLite.
    ciphertext = _fernet().encrypt(refresh_token.encode())
    with database._connect() as conn:
        conn.execute(
            "INSERT INTO gmail_oauth(id,email,refresh_token_cipher,connected_at) "
            "VALUES(1,?,?,?) ON CONFLICT(id) DO UPDATE SET "
            "email=excluded.email,refresh_token_cipher=excluded.refresh_token_cipher,"
            "connected_at=excluded.connected_at",
            (OWNER_ACCOUNT, ciphertext, datetime.now(KZ_TIMEZONE).isoformat()),
        )


def status(database) -> dict:
    init_gmail_schema(database)
    with database._connect() as conn:
        row = conn.execute("SELECT email, connected_at FROM gmail_oauth WHERE id=1").fetchone()
        unread = conn.execute(
            "SELECT count(*) FROM gmail_notices WHERE status IN ('pending','review')"
        ).fetchone()[0]
        sync = conn.execute(
            "SELECT last_internal_date_ms,last_success_at,last_error "
            "FROM gmail_sync_state WHERE id=1"
        ).fetchone()
        errors = conn.execute(
            "SELECT count(*) FROM gmail_processing_errors WHERE resolved_at=''"
        ).fetchone()[0]
        requests = conn.execute(
            "SELECT count(*) FROM gmail_requests WHERE status IN ('pending','partial')"
        ).fetchone()[0]
    return {
        "configured": configured(),
        "connected": bool(row),
        "email": row["email"] if row else "",
        "connected_at": row["connected_at"] if row else "",
        "pending": int(unread),
        "pending_requests": int(requests),
        "processing_errors": int(errors),
        "last_sync_at": sync["last_success_at"] if sync else "",
        "last_sync_error": sync["last_error"] if sync else "",
        "mail_mode": mail_mode(),
        "send_enabled": (
            _env("SPORT_GMAIL_ENABLE_TEST_SEND").lower() == "true"
            if mail_mode() == "test" else bool(
                _env("SPORT_MAIL_QSPORT_TO") or _env("SPORT_MAIL_SETANTA_TO")
            )
        ),
    }


def _access_token(database) -> str:
    init_gmail_schema(database)
    with database._connect() as conn:
        row = conn.execute("SELECT refresh_token_cipher FROM gmail_oauth WHERE id=1").fetchone()
    if not row:
        raise GmailNotConfigured("Gmail ещё не подключён")
    try:
        refresh_token = _fernet().decrypt(bytes(row["refresh_token_cipher"])).decode()
    except (InvalidToken, ValueError) as exc:
        raise GmailNotConfigured("Ключ шифрования не соответствует сохранённому OAuth-токену") from exc
    client_id, client_secret = _client()
    fresh = _json_http(TOKEN_URL, data={
        "client_id": client_id, "client_secret": client_secret,
        "refresh_token": refresh_token, "grant_type": "refresh_token",
    })
    if not fresh.get("access_token"):
        raise GmailTransportError("Обновление доступа Gmail не удалось")
    return fresh["access_token"]


def _walk_parts(payload: dict):
    if payload.get("filename") and payload.get("body", {}).get("attachmentId"):
        yield payload
    for part in payload.get("parts") or []:
        yield from _walk_parts(part)


def _headers(message: dict) -> dict:
    return {str(h.get("name", "")).casefold(): h.get("value", "")
            for h in message.get("payload", {}).get("headers", [])}


def _decode(data: str) -> bytes:
    # Gmail API uses RFC4648 base64url without mandatory '=' padding.
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _repair_forwarded_text(value: str) -> str:
    """Repair common UTF-8-as-CP1251 mojibake from corporate forwarding."""
    text = str(value or "")
    suspicious = text.count("Р") + text.count("С")
    if suspicious < 4:
        return text
    try:
        repaired = text.encode("cp1251").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text
    # Keep the repaired form only when it reduces the characteristic garbage.
    repaired_score = repaired.count("Р") + repaired.count("С")
    return repaired if repaired_score < suspicious else text


def _message_text(message: dict) -> str:
    """Extract bounded human-readable context from forwarded mail.

    Corporate forwarding often replaces Gmail's From header with the user's
    mailbox while preserving the original sender and provider in the quoted
    body. Classification therefore must not depend on the outer From alone.
    """
    fragments: list[str] = []

    def walk(part: dict) -> None:
        mime = str(part.get("mimeType") or "").casefold()
        body = part.get("body") or {}
        encoded = str(body.get("data") or "")
        if encoded and mime in ("text/plain", "text/html"):
            try:
                raw = _decode(encoded)[:MAX_BODY_BYTES * 3]
                text = raw.decode("utf-8", errors="replace")
            except (ValueError, UnicodeError):
                text = ""
            if mime == "text/html":
                text = re.sub(r"(?is)<(?:script|style).*?>.*?</(?:script|style)>", " ", text)
                text = re.sub(r"(?s)<[^>]+>", " ", text)
                text = (text.replace("&nbsp;", " ").replace("&amp;", "&")
                            .replace("&lt;", "<").replace("&gt;", ">"))
            cleaned = " ".join(_repair_forwarded_text(text).split())
            if cleaned:
                fragments.append(cleaned[:MAX_BODY_BYTES])
        for child in part.get("parts") or []:
            walk(child)

    walk(message.get("payload") or {})
    return " ".join(fragments)[:MAX_BODY_BYTES]


def _sender_key(value: str) -> str:
    address = parseaddr(str(value or ""))[1].casefold().strip()
    if not address:
        match = re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", str(value or ""))
        address = match.group(0).casefold() if match else ""
    return address[:250]


def _original_sender(body: str, outer_sender: str) -> str:
    """Extract the first forwarded From/От address without guessing identity."""
    for match in re.finditer(
        r"(?:^|[\s>|])(?:from|от)\s*:\s*([^\n\r]{1,300})",
        str(body or ""), re.I,
    ):
        address = _sender_key(match.group(1))
        if address and address != _sender_key(outer_sender):
            return address
    return ""


def _confirmed_format_channel(database, fingerprint: str,
                              sender_key: str) -> str:
    if not fingerprint or not sender_key:
        return ""
    with database._connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT channel FROM gmail_format_mappings "
            "WHERE fingerprint=? AND sender_key=?",
            (fingerprint, sender_key),
        ).fetchall()
    channels = {str(row["channel"]) for row in rows}
    return next(iter(channels)) if len(channels) == 1 else ""


def _remember_format_mapping(database, *, fingerprint: str, sender_key: str,
                             channel: str, username: str) -> None:
    if not fingerprint or not sender_key or not channel:
        return
    now = datetime.now(KZ_TIMEZONE).isoformat()
    with database._connect() as conn:
        conn.execute(
            "INSERT INTO gmail_format_mappings("
            "fingerprint,sender_key,channel,confirmed_by,confirmed_at,last_seen_at"
            ") VALUES(?,?,?,?,?,?) ON CONFLICT(fingerprint,sender_key,channel) "
            "DO UPDATE SET last_seen_at=excluded.last_seen_at",
            (fingerprint, sender_key, channel, username, now, now),
        )


def _record_processing_error(database, message_id: str, stage: str,
                             exc: Exception) -> int:
    now = datetime.now(KZ_TIMEZONE).isoformat()
    message = (type(exc).__name__ + ": " + str(exc))[:400]
    with database._connect() as conn:
        conn.execute(
            "INSERT INTO gmail_processing_errors("
            "message_id,stage,error,attempts,first_seen_at,last_seen_at,resolved_at"
            ") VALUES(?,?,?,1,?,?,'') ON CONFLICT(message_id) DO UPDATE SET "
            "stage=excluded.stage,error=excluded.error,attempts=attempts+1,"
            "last_seen_at=excluded.last_seen_at,resolved_at=''",
            (message_id, stage, message, now, now),
        )
        row = conn.execute(
            "SELECT attempts FROM gmail_processing_errors WHERE message_id=?",
            (message_id,),
        ).fetchone()
    return int(row["attempts"])


def _resolve_processing_error(database, message_id: str) -> None:
    with database._connect() as conn:
        conn.execute(
            "UPDATE gmail_processing_errors SET resolved_at=? "
            "WHERE message_id=? AND resolved_at=''",
            (datetime.now(KZ_TIMEZONE).isoformat(), message_id),
        )


def _processing_attempts(database, message_id: str) -> int:
    with database._connect() as conn:
        row = conn.execute(
            "SELECT attempts FROM gmail_processing_errors "
            "WHERE message_id=? AND resolved_at=''",
            (message_id,),
        ).fetchone()
    return int(row["attempts"] or 0) if row else 0


def _search_queries(database) -> tuple[str, ...]:
    with database._connect() as conn:
        row = conn.execute(
            "SELECT last_internal_date_ms FROM gmail_sync_state WHERE id=1"
        ).fetchone()
    last_ms = int(row["last_internal_date_ms"] or 0) if row else 0
    boundary = (
        "after:" + str(max(0, last_ms // 1000 - SYNC_OVERLAP_SECONDS))
        if last_ms else "newer_than:21d"
    )
    return (
        boundary + " (filename:xlsx OR filename:xls)",
        boundary + " (setanta OR сетанта OR qsport OR SPORTPLUS OR SPORT+)",
    )


def _save_sync_checkpoint(database, *, internal_date_ms: int,
                          error: str = "") -> None:
    with database._connect() as conn:
        conn.execute(
            "UPDATE gmail_sync_state SET last_internal_date_ms=max("
            "last_internal_date_ms,?),last_success_at=?,last_error=? WHERE id=1",
            (max(0, int(internal_date_ms)),
             datetime.now(KZ_TIMEZONE).isoformat(), str(error)[:400]),
        )


def _candidate(filename: str, subject: str) -> bool:
    # Unknown attachment names can still carry a valid channel inside XLSX.
    # Never classify unrelated documents as sport on the filename alone.
    name = filename.casefold()
    return name.endswith((".xlsx", ".xls")) and (
        bool(REVIEW_TOKENS.search(subject + " " + filename))
        or bool(re.search(r"сетка|программ|epg|schedule", name, re.I))
        or bool(re.search(r"setanta|q[ _-]?sport|viju|qsport", name, re.I))
    )


REQUEST_CHANNELS = {
    "q": ("Q LEAGUE", "Q ARENA", "Q FOOTBALL"),
    "setanta": (
        "SETANTA SPORTS 1", "SETANTA SPORTS 2", "SETANTA SPORTS KZ",
    ),
    "all": (
        "Q LEAGUE", "Q ARENA", "Q FOOTBALL", "SETANTA SPORTS 1",
        "SETANTA SPORTS 2", "SETANTA SPORTS KZ",
    ),
}


def _mark_request_response(database, *, thread_id: str, message_id: str,
                           channel: str, sender_key: str) -> None:
    if not channel:
        return
    with database._connect() as conn:
        rows = conn.execute(
            "SELECT id,recipient,thread_id,requested_channels_json,"
            "received_channels_json "
            "FROM gmail_requests WHERE status IN ('pending','partial') "
            "ORDER BY id DESC"
        ).fetchall()
        for row in rows:
            try:
                requested = set(json.loads(row["requested_channels_json"] or "[]"))
                received = set(json.loads(row["received_channels_json"] or "[]"))
            except (TypeError, ValueError):
                continue
            same_thread = bool(thread_id and row["thread_id"] == thread_id)
            same_sender = bool(
                sender_key and sender_key == _sender_key(row["recipient"])
            )
            if channel not in requested or not (same_thread or same_sender):
                continue
            received.add(channel)
            complete = bool(requested) and requested <= received
            conn.execute(
                "UPDATE gmail_requests SET received_channels_json=?,status=?,"
                "answered_message_id=?,answered_at=? WHERE id=?",
                (json.dumps(sorted(received), ensure_ascii=False),
                 "fulfilled" if complete else "partial", message_id,
                 datetime.now(KZ_TIMEZONE).isoformat(), row["id"]),
            )
            break


def list_requests(database, *, limit: int = 50) -> list[dict]:
    init_gmail_schema(database)
    with database._connect() as conn:
        rows = conn.execute(
            "SELECT id,recipient,subject,category,sent_by,gmail_message_id,"
            "thread_id,requested_channels_json,received_channels_json,"
            "period_start,period_end,status,answered_message_id,answered_at,sent_at,"
            "delivery_mode "
            "FROM gmail_requests ORDER BY id DESC LIMIT ?",
            (max(1, min(200, int(limit))),),
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        for key in ("requested_channels_json", "received_channels_json"):
            try:
                item[key[:-5]] = json.loads(item.pop(key) or "[]")
            except (TypeError, ValueError):
                item[key[:-5]] = []
        result.append(item)
    return result


def _auto_import_enabled() -> bool:
    # OAuth and durable storage are configured separately. The operator can
    # temporarily stop automatic imports without interrupting the inbox scan.
    return _env("SPORT_GMAIL_AUTO_IMPORT").casefold() != "false"


def _safe_auto_apply(database, notice_id: int, parsed) -> tuple[bool, str]:
    """Accept one verified supplier XLSX only if there are no ambiguities.

    Disappearance from an EPG is never interpreted as a confirmed cancellation.
    Avoid promoting an older file over an accepted newer snapshot.
    """
    from services.epg_excel import preview_parsed_epg
    diff = preview_parsed_epg(database, parsed)
    counts = diff["counts"]
    if counts["ambiguous"]:
        return False, "Неоднозначные совпадения событий: требуется проверка"
    # A brand-new empty grid cannot establish an actual live broadcast.
    if not parsed.events and diff["current_count"]:
        return False, "В обновлённой сетке нет LIVE: прежнее расписание сохранено"
    from services.epg_excel import SOURCE_KEY
    source = SOURCE_KEY[parsed.channel]
    for day in parsed.scope_dates:
        if (not any(e.get("date") == day for e in parsed.events)
                and database.load_active_source_snapshot(source, day)):
            return False, ("В новой сетке пропали все LIVE за " + day +
                           ". Прежний день сохранён для проверки")
    try:
        result = approve_notice(database, notice_id, username="SLP_AUTO")
    except (GmailTransportError, InvalidEPG) as exc:
        return False, str(exc)[:200]
    return result["status"] in ("imported", "already_imported"), ""


def list_notices(database, *, limit: int = 100) -> list[dict]:
    init_gmail_schema(database)
    limit = max(1, min(200, int(limit)))
    with database._connect() as conn:
        rows = conn.execute(
            "SELECT id,filename,subject,sender,snippet,detected_channel,"
            "status,reason,received_at,created_at,reviewed_by,reviewed_at,"
            "classification,classification_method,original_sender,thread_id,"
            "provider,period_start,period_end,classification_confidence,"
            "classification_evidence,format_fingerprint,"
            "channel_detection_method,source_document_sha256,"
            "attachment_bytes IS NOT NULL AS has_attachment "
            "FROM gmail_notices ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        try:
            item["classification_evidence"] = json.loads(
                item["classification_evidence"] or "[]"
            )
        except (TypeError, ValueError):
            item["classification_evidence"] = []
        result.append(item)
    return result


def sync_inbox(database, *, allow_auto_import: bool = True) -> dict:
    """Read Gmail read-only; auto-accept only verified unambiguous LIVE EPG."""

    init_gmail_schema(database)
    scan_started_ms = int(datetime.now(KZ_TIMEZONE).timestamp() * 1000)
    token = _access_token(database)
    message_ids: dict[str, dict] = {}
    for query in _search_queries(database):
        response = _json_api("/users/me/messages?" + urlencode({
            "q": query, "maxResults": str(MAX_LIST_MESSAGES),
        }), token)
        for item in response.get("messages", [])[:MAX_LIST_MESSAGES]:
            if item.get("id"):
                message_ids[str(item["id"])] = item
    created = 0
    reviewed = 0
    auto_imported = 0
    failed_messages = 0
    retryable_errors = 0
    quarantined_messages = 0
    max_internal_date_ms = 0
    for item in message_ids.values():
        msg_id = str(item.get("id") or "")
        if not re.fullmatch(r"[a-f0-9]{10,32}", msg_id):
            continue
        if _processing_attempts(database, msg_id) >= MAX_MESSAGE_ATTEMPTS:
            quarantined_messages += 1
            continue
        try:
            message = _json_api(
                "/users/me/messages/" + msg_id + "?format=full", token
            )
        except GmailTransportError as exc:
            failed_messages += 1
            attempts = _record_processing_error(database, msg_id, "message", exc)
            if attempts < MAX_MESSAGE_ATTEMPTS:
                retryable_errors += 1
            else:
                quarantined_messages += 1
            continue
        headers = _headers(message)
        subject = str(headers.get("subject", ""))[:400]
        sender = str(headers.get("from", ""))[:250]
        snippet = str(message.get("snippet") or "")[:MAX_BODY_BYTES]
        body_context = _message_text(message)
        original_sender = _original_sender(body_context, sender)
        sender_identity = _sender_key(original_sender or sender)
        thread_id = str(message.get("threadId") or "")[:80]
        notice_context = " ".join((snippet, body_context))[:MAX_BODY_BYTES]
        millis = str(message.get("internalDate") or "0")
        try:
            internal_date_ms = max(0, int(millis))
            max_internal_date_ms = max(max_internal_date_ms, internal_date_ms)
            received = datetime.fromtimestamp(
                internal_date_ms / 1000, KZ_TIMEZONE
            ).isoformat()
        except (ValueError, OverflowError):
            internal_date_ms = 0
            received = datetime.now(KZ_TIMEZONE).isoformat()
        parts = list(_walk_parts(message.get("payload", {})))
        context = " ".join(
            (subject, sender, original_sender, snippet, body_context)
        )
        decision = ai_pipeline.classify_mail(
            subject, " ".join((sender, original_sender)), body_context or snippet,
            [str(p.get("filename") or "") for p in parts],
        )
        relevant = bool(REVIEW_TOKENS.search(context))
        if not relevant and not any(
            _candidate(p.get("filename", ""), context) for p in parts
        ):
            continue
        # A text-only announcement is a review notification. Neither a
        # subject nor a snippet proves that a fixture has been cancelled.
        if not parts and relevant and CHANGE_TOKENS.search(context):
            with database._connect() as conn:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO gmail_notices("
                    "message_id,attachment_id,filename,subject,sender,snippet,"
                    "detected_channel,status,reason,received_at,created_at,"
                    "classification,classification_method,original_sender,thread_id,"
                    "provider,period_start,period_end,classification_confidence,"
                    "classification_evidence"
                    ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (msg_id, "__message__", "", subject, sender, notice_context,
                     "", "review",
                     "Письмо об изменениях без распознанного XLSX",
                     received, datetime.now(KZ_TIMEZONE).isoformat(),
                     decision.category, decision.method, original_sender, thread_id,
                     decision.provider, decision.period_start, decision.period_end,
                     decision.confidence,
                     json.dumps(decision.evidence, ensure_ascii=False)),
                )
                if cur.rowcount:
                    reviewed += 1
        message_processing_error = False
        for part in parts:
            filename = str(part.get("filename") or "")[:200]
            attachment_id = str(part["body"].get("attachmentId") or "")
            if not filename or not attachment_id or not _candidate(filename, context):
                continue
            with database._connect() as conn:
                if conn.execute(
                    "SELECT 1 FROM gmail_notices WHERE message_id=? "
                    "AND (attachment_id=? OR "
                    "substr(attachment_id,1,length(?)+1)=?||'#')",
                    (msg_id, attachment_id, attachment_id, attachment_id),
                ).fetchone():
                    continue
            if created >= MAX_ATTACHMENTS_PER_SYNC:
                break
            size = int(part["body"].get("size") or 0)
            if size > MAX_WORKBOOK_BYTES:
                continue
            try:
                attachment = _json_api(
                    "/users/me/messages/" + msg_id + "/attachments/" + attachment_id,
                    token,
                )
                raw = _decode(str(attachment.get("data") or ""))
            except (GmailTransportError, ValueError) as exc:
                failed_messages += 1
                message_processing_error = True
                attempts = _record_processing_error(
                    database, msg_id, "attachment", exc
                )
                if attempts < MAX_MESSAGE_ATTEMPTS:
                    retryable_errors += 1
                else:
                    quarantined_messages += 1
                continue
            if not raw or len(raw) > MAX_WORKBOOK_BYTES:
                continue
            source_sha256 = hashlib.sha256(raw).hexdigest()
            try:
                fingerprint = workbook_fingerprint(raw, filename)
            except InvalidEPG:
                fingerprint = ""
            confirmed_channel = _confirmed_format_channel(
                database, fingerprint, sender_identity
            )
            try:
                parsed_list = parse_epg_xlsx_channels(
                    raw, filename, context=context,
                    confirmed_channel=confirmed_channel,
                )
                parse_error = ""
            except InvalidEPG as exc:
                if not relevant:
                    continue
                parsed_list = (None,)
                parse_error = str(exc)[:200]
            for parsed in parsed_list:
                if created >= MAX_ATTACHMENTS_PER_SYNC:
                    break
                if parsed is None:
                    status_value, channel, reason = "review", "", parse_error
                    reviewed += 1
                else:
                    status_value, channel, reason = "pending", parsed.channel, ""
                scope = parsed.scope_dates if parsed is not None else ()
                period_start = scope[0] if scope else decision.period_start
                period_end = scope[-1] if scope else decision.period_end
                detection_method = (
                    "confirmed_format" if confirmed_channel
                    else "workbook_content" if parsed is not None else ""
                )
                evidence = list(decision.evidence)
                if parsed is not None:
                    evidence.append("workbook_channel:" + parsed.channel)
                if confirmed_channel:
                    evidence.append("confirmed_format_mapping")
                classification = (
                    decision.category if decision.category not in ("OTHER", "AMBIGUOUS")
                    else "SCHEDULE_NEW" if parsed is not None else "AMBIGUOUS"
                )
                notice_attachment_id = (
                    attachment_id + "#" + channel
                    if len(parsed_list) > 1 and channel else attachment_id
                )
                with database._connect() as conn:
                    cur = conn.execute(
                        "INSERT OR IGNORE INTO gmail_notices("
                        "message_id,attachment_id,filename,subject,sender,snippet,"
                        "detected_channel,status,reason,received_at,created_at,"
                        "attachment_bytes,classification,classification_method,"
                        "original_sender,thread_id,provider,period_start,period_end,"
                        "classification_confidence,classification_evidence,"
                        "format_fingerprint,channel_detection_method,"
                        "source_document_sha256"
                        ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (msg_id, notice_attachment_id, filename, subject, sender,
                         notice_context, channel, status_value, reason, received,
                         datetime.now(KZ_TIMEZONE).isoformat(), raw,
                         classification, decision.method, original_sender, thread_id,
                         decision.provider, period_start, period_end,
                         decision.confidence,
                         json.dumps(evidence, ensure_ascii=False), fingerprint,
                         detection_method, source_sha256),
                    )
                    notice_id = cur.lastrowid if cur.rowcount else None
                if notice_id and parsed is not None:
                    _mark_request_response(
                        database, thread_id=thread_id, message_id=msg_id,
                        channel=parsed.channel, sender_key=sender_identity,
                    )
                if (notice_id and parsed is not None and allow_auto_import
                        and _auto_import_enabled() and
                        classification != "SCHEDULE_CANCELLATION"):
                    accepted, why = _safe_auto_apply(database, notice_id, parsed)
                    if accepted:
                        auto_imported += 1
                    else:
                        reviewed += 1
                        with database._connect() as conn:
                            conn.execute(
                                "UPDATE gmail_notices SET status='review', reason=? "
                                "WHERE id=? AND status='pending'",
                                (why or "Нужна проверка версии расписания", notice_id),
                            )
                created += 1
        if not message_processing_error:
            _resolve_processing_error(database, msg_id)
    checkpoint_advanced = retryable_errors == 0
    _save_sync_checkpoint(
        database,
        internal_date_ms=(max(max_internal_date_ms, scan_started_ms)
                          if checkpoint_advanced else 0),
        error=("Есть письма для повторной обработки" if retryable_errors else ""),
    )
    return {"new_attachments": created, "auto_imported": auto_imported,
            "requires_review": reviewed, "pending": status(database)["pending"],
            "mode": "automatic" if allow_auto_import and _auto_import_enabled()
                    else "review_only",
            "scanned_messages": len(message_ids),
            "failed_messages": failed_messages,
            "retryable_errors": retryable_errors,
            "quarantined_messages": quarantined_messages,
            "checkpoint_advanced": checkpoint_advanced}



def _parse_notice(row):
    confirmed_channel = (
        str(row["detected_channel"] or "")
        if str(row["channel_detection_method"] or "") == "confirmed_format"
        else ""
    )
    candidates = parse_epg_xlsx_channels(
        bytes(row["attachment_bytes"]), row["filename"],
        context=" ".join((row["subject"], row["sender"], row["snippet"])),
        confirmed_channel=confirmed_channel,
    )
    station = str(row["detected_channel"] or "")
    if len(candidates) == 1 and (not station or candidates[0].channel == station):
        return candidates[0]
    matching = [item for item in candidates if item.channel == station]
    if len(matching) == 1:
        return matching[0]
    raise GmailTransportError("Не удалось подтвердить канал этого листа Excel")


def preview_notice(database, notice_id: int) -> dict:
    """Show the editor a supplier file's precise changes before import."""
    from services.epg_excel import preview_parsed_epg
    init_gmail_schema(database)
    with database._connect() as conn:
        row = conn.execute(
            "SELECT filename,attachment_bytes,status,subject,sender,snippet,"
            "detected_channel,channel_detection_method "
            "FROM gmail_notices WHERE id=?",
            (notice_id,),
        ).fetchone()
    if not row or row["status"] != "pending" or not row["attachment_bytes"]:
        raise GmailTransportError("Файл для сравнения не найден или уже обработан")
    try:
        parsed = _parse_notice(row)
    except InvalidEPG as exc:
        raise GmailTransportError("Не удалось прочитать Excel: " + str(exc)) from exc
    return preview_parsed_epg(database, parsed)


def approve_notice(database, notice_id: int, *, username: str) -> dict:
    init_gmail_schema(database)
    with database._connect() as conn:
        row = conn.execute(
            "SELECT id,filename,attachment_bytes,status,detected_channel,"
            "received_at,subject,sender,snippet,original_sender,"
            "format_fingerprint,channel_detection_method "
            "FROM gmail_notices WHERE id=?",
            (notice_id,),
        ).fetchone()
        if row is None:
            raise GmailTransportError("Уведомление не найдено")
        if row["status"] != "pending" or not row["attachment_bytes"]:
            raise GmailTransportError("Вложение недоступно для импорта")
        filename, raw = row["filename"], bytes(row["attachment_bytes"])
    from services.epg_excel import import_parsed_epg, initialize_epg_imports
    parsed = _parse_notice(row)
    initialize_epg_imports(database)
    # If the editor already approved a more recently received supplier file
    # for any overlapping day on this SAME channel, reject this older file.
    # Manual per-event review is still possible, without reverting an entire
    # current programme to a stale attachment.
    placeholders = ",".join("?" for _ in parsed.scope_dates)
    with database._connect() as conn:
        newer_mail = conn.execute(
            "SELECT 1 FROM gmail_notices n JOIN epg_import_days d "
            "ON d.file_hash=n.imported_hash "
            "WHERE n.status='imported' AND n.detected_channel=? "
            "AND n.received_at>? AND d.scope_date IN (" + placeholders + ") "
            "LIMIT 1",
            (parsed.channel, row["received_at"], *parsed.scope_dates),
        ).fetchone()
    if newer_mail:
        raise GmailTransportError(
            "Более новое расписание этого канала уже загружено. "
            "Старый файл нельзя применять поверх него."
        )
    outcome = import_parsed_epg(database, parsed)
    _remember_format_mapping(
        database, fingerprint=str(row["format_fingerprint"] or ""),
        sender_key=_sender_key(row["original_sender"] or row["sender"]),
        channel=parsed.channel, username=username,
    )
    with database._connect() as conn:
        conn.execute(
            "UPDATE gmail_notices SET status='imported',attachment_bytes=NULL,"
            "imported_hash=?,reviewed_by=?, reviewed_at=? "
            "WHERE id=? AND status='pending'",
            (parsed.content_hash, username, datetime.now(KZ_TIMEZONE).isoformat(),
             notice_id),
        )
    return outcome


def dismiss_notice(database, notice_id: int, *, username: str) -> None:
    init_gmail_schema(database)
    with database._connect() as conn:
        row = conn.execute(
            "UPDATE gmail_notices SET status='dismissed',attachment_bytes=NULL,"
            "reviewed_by=?, reviewed_at=? WHERE id=? AND status IN ('pending','review')",
            (username, datetime.now(KZ_TIMEZONE).isoformat(), notice_id),
        )
        if row.rowcount == 0:
            raise GmailTransportError("Уведомление уже обработано или не найдено")


def mail_mode() -> str:
    mode = _env("SPORT_GMAIL_MAIL_MODE").casefold()
    return mode if mode in ("test", "production") else "test"


def _request_period(period_start: str, period_end: str) -> tuple[str, str]:
    if not period_start or not period_end:
        raise GmailTransportError("Нужно указать период расписания")
    try:
        first = datetime.strptime(period_start, "%Y-%m-%d").date()
        last = datetime.strptime(period_end, "%Y-%m-%d").date()
    except ValueError as exc:
        raise GmailTransportError("Период должен быть в формате YYYY-MM-DD") from exc
    if first > last or (last - first).days > 62:
        raise GmailTransportError("Некорректный или слишком большой период запроса")
    return first.isoformat(), last.isoformat()


def send_schedule_request(database, *, username: str, category: str,
                          period_start: str, period_end: str,
                          destination: str = "") -> dict:
    """Send an explicitly requested mail in configured TEST or PRODUCTION mode."""
    if category not in REQUEST_CHANNELS:
        raise GmailTransportError("Неверный тип запроса")
    period_start, period_end = _request_period(period_start, period_end)
    mode = mail_mode()
    if mode == "test":
        if _env("SPORT_GMAIL_ENABLE_TEST_SEND").lower() != "true":
            raise GmailNotConfigured("Тестовая отправка выключена в настройках")
        expected = _env("SPORT_MAIL_TEST_TO") or "alexandr.petrossov@fmedia.kz"
        if not expected or expected.casefold() != "alexandr.petrossov@fmedia.kz":
            raise GmailNotConfigured("Разрешён только подтверждённый тестовый получатель")
        if destination != expected:
            raise GmailTransportError("Недопустимый получатель")
    else:
        if category == "all":
            raise GmailNotConfigured("Для PRODUCTION выберите одного поставщика")
        expected = _env(
            "SPORT_MAIL_QSPORT_TO" if category == "q" else "SPORT_MAIL_SETANTA_TO"
        )
        if not expected or "@" not in expected:
            raise GmailNotConfigured(
                "В PRODUCTION MODE не настроен подтверждённый получатель поставщика"
            )
    with database._connect() as conn:
        duplicate = conn.execute(
            "SELECT id FROM gmail_requests WHERE recipient=? AND category=? "
            "AND period_start=? AND period_end=? AND status IN ('pending','partial')",
            (expected, category, period_start, period_end),
        ).fetchone()
    if duplicate:
        raise GmailTransportError("Аналогичный запрос уже ожидает ответа")
    texts = {
        "q": ("Запрос расписаний QSport",
              "Антон, добрый день!\n\nПрошу направить актуальные расписания "
              "Q LEAGUE, Q ARENA и Q FOOTBALL в формате Excel.\n\n"
              "В случае изменений прошу направлять обновлённые сетки вещания.\n\nСпасибо!"),
        "setanta": ("Запрос расписаний Setanta",
                    "Сабина, добрый день!\n\nПрошу направить актуальные расписания "
                    "SETANTA SPORTS 1, SETANTA SPORTS 2 и SETANTA SPORTS KZ "
                    "в формате Excel.\n\nВ случае изменений прошу направлять "
                    "обновлённые сетки вещания.\n\nСпасибо!"),
        "all": ("Запрос спортивных расписаний",
                "Антон, Сабина, добрый день!\n\nПрошу направить актуальные "
                "расписания Q LEAGUE, Q ARENA, Q FOOTBALL, SETANTA SPORTS 1, "
                "SETANTA SPORTS 2 и SETANTA SPORTS KZ в формате Excel.\n\n"
                "В случае изменений прошу направлять обновлённые сетки вещания."
                "\n\nСпасибо!"),
    }
    import email.message
    subject, body = texts[category]
    body += (f"\n\nПериод: {period_start} - {period_end}\n"
             "Просьба прислать только подтверждённые сетки в формате Excel.")
    msg = email.message.EmailMessage()
    msg["From"] = OWNER_ACCOUNT
    msg["To"] = expected
    msg["Subject"] = subject
    msg.set_content(body)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode().rstrip("=")
    sent = _json_api("/users/me/messages/send", _access_token(database),
                     payload={"raw": raw})
    message_id = sent.get("id", "")
    if not message_id:
        raise GmailTransportError("Gmail не подтвердил отправку")
    thread_id = str(sent.get("threadId") or "")[:80]
    requested_channels = REQUEST_CHANNELS[category]
    with database._connect() as conn:
        conn.execute(
            "INSERT INTO gmail_requests("
            "recipient,subject,category,sent_by,gmail_message_id,sent_at,"
            "thread_id,requested_channels_json,status,"
            "period_start,period_end,delivery_mode"
            ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (expected, subject, category, username, message_id,
             datetime.now(KZ_TIMEZONE).isoformat(), thread_id,
             json.dumps(requested_channels, ensure_ascii=False), "pending",
             period_start, period_end, mode),
        )
    return {"sent": True, "to": expected, "subject": subject,
            "gmail_message_id": message_id, "thread_id": thread_id,
            "category": category, "channels": list(requested_channels),
            "period_start": period_start, "period_end": period_end,
            "mode": mode}


def test_request(database, *, username: str, category: str, destination: str) -> dict:
    """Compatibility wrapper for the existing TEST MODE endpoint."""
    today = datetime.now(KZ_TIMEZONE).date()
    return send_schedule_request(
        database, username=username, category=category, destination=destination,
        period_start=today.isoformat(), period_end=(today + timedelta(days=6)).isoformat(),
    )

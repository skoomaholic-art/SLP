"""Gmail transport for the existing SLP web service.

The connected personal Gmail account is authorized by its owner with Google
OAuth. Never use the assistant's connector token as the Cloud Run credential.
No message is deleted, labeled or forwarded. New attachments wait for an
editor to approve import; mail without an XLSX remains review-only.
"""
from __future__ import annotations

import base64
from datetime import datetime, timedelta
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

from services.epg_excel import InvalidEPG, MAX_WORKBOOK_BYTES, detect_channel, parse_epg_xlsx
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
        """)
        existing_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(gmail_notices)")
        }
        if "imported_hash" not in existing_columns:
            conn.execute(
                "ALTER TABLE gmail_notices ADD COLUMN imported_hash "
                "TEXT NOT NULL DEFAULT ''"
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
    return {
        "configured": configured(),
        "connected": bool(row),
        "email": row["email"] if row else "",
        "connected_at": row["connected_at"] if row else "",
        "pending": int(unread),
        "send_enabled": _env("SPORT_GMAIL_ENABLE_TEST_SEND").lower() == "true",
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


def _candidate(filename: str, subject: str) -> bool:
    name = filename.casefold()
    if not name.endswith(".xlsx"):
        return False
    try:
        detect_channel(filename)
        return True
    except InvalidEPG:
        return bool(REVIEW_TOKENS.search(subject + " " + filename))


def list_notices(database, *, limit: int = 100) -> list[dict]:
    init_gmail_schema(database)
    limit = max(1, min(200, int(limit)))
    with database._connect() as conn:
        rows = conn.execute(
            "SELECT id,filename,subject,sender,snippet,detected_channel,"
            "status,reason,received_at,created_at,reviewed_by,reviewed_at,"
            "attachment_bytes IS NOT NULL AS has_attachment "
            "FROM gmail_notices ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(x) for x in rows]


def sync_inbox(database) -> dict:
    """Read-only Gmail sync; never imports programmes before user approval."""
    init_gmail_schema(database)
    token = _access_token(database)
    searches = (
        "newer_than:21d (filename:xlsx OR filename:xls)",
        "newer_than:21d (setanta OR сетанта OR qsport OR SPORTPLUS OR SPORT+)",
    )
    message_ids: dict[str, dict] = {}
    for query in searches:
        response = _json_api("/users/me/messages?" + urlencode({
            "q": query, "maxResults": str(MAX_LIST_MESSAGES),
        }), token)
        for item in response.get("messages", [])[:MAX_LIST_MESSAGES]:
            if item.get("id"):
                message_ids[str(item["id"])] = item
    created = 0
    reviewed = 0
    for item in message_ids.values():
        msg_id = str(item.get("id") or "")
        if not re.fullmatch(r"[a-f0-9]{10,32}", msg_id):
            continue
        message = _json_api("/users/me/messages/" + msg_id + "?format=full", token)
        headers = _headers(message)
        subject = str(headers.get("subject", ""))[:400]
        sender = str(headers.get("from", ""))[:250]
        snippet = str(message.get("snippet") or "")[:MAX_BODY_BYTES]
        body_context = _message_text(message)
        millis = str(message.get("internalDate") or "0")
        try:
            received = datetime.fromtimestamp(int(millis) / 1000, KZ_TIMEZONE).isoformat()
        except (ValueError, OverflowError):
            received = datetime.now(KZ_TIMEZONE).isoformat()
        parts = list(_walk_parts(message.get("payload", {})))
        context = " ".join((subject, sender, snippet, body_context))
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
                    "detected_channel,status,reason,received_at,created_at"
                    ") VALUES(?,'__message__','',?,?,?,'','review',?,?,?)",
                    (msg_id, subject, sender, snippet,
                     "Письмо об изменениях без распознанного XLSX",
                     received, datetime.now(KZ_TIMEZONE).isoformat()),
                )
                if cur.rowcount:
                    reviewed += 1
        for part in parts:
            filename = str(part.get("filename") or "")[:200]
            attachment_id = str(part["body"].get("attachmentId") or "")
            if not filename or not attachment_id or not _candidate(filename, context):
                continue
            with database._connect() as conn:
                if conn.execute(
                    "SELECT 1 FROM gmail_notices WHERE message_id=? AND attachment_id=?",
                    (msg_id, attachment_id),
                ).fetchone():
                    continue
            if created >= MAX_ATTACHMENTS_PER_SYNC:
                break
            size = int(part["body"].get("size") or 0)
            if size > MAX_WORKBOOK_BYTES:
                continue
            attachment = _json_api(
                "/users/me/messages/" + msg_id + "/attachments/" + attachment_id,
                token,
            )
            raw = _decode(str(attachment.get("data") or ""))
            if not raw or len(raw) > MAX_WORKBOOK_BYTES:
                continue
            try:
                parsed = parse_epg_xlsx(
                    raw, filename, context=context,
                )
            except InvalidEPG as exc:
                status_value, channel, reason = "review", "", str(exc)[:200]
                reviewed += 1
            else:
                status_value, channel, reason = "pending", parsed.channel, ""
            with database._connect() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO gmail_notices("
                    "message_id,attachment_id,filename,subject,sender,snippet,"
                    "detected_channel,status,reason,received_at,created_at,attachment_bytes"
                    ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (msg_id, attachment_id, filename, subject, sender, snippet,
                     channel, status_value, reason, received,
                     datetime.now(KZ_TIMEZONE).isoformat(), raw),
                )
            created += 1
    return {"new_attachments": created, "requires_review": reviewed,
            "pending": status(database)["pending"],
            "scanned_messages": len(message_ids)}



def preview_notice(database, notice_id: int) -> dict:
    """Show the editor a supplier file's precise changes before import."""
    from services.epg_excel import preview_parsed_epg
    init_gmail_schema(database)
    with database._connect() as conn:
        row = conn.execute(
            "SELECT filename,attachment_bytes,status,subject,sender,snippet "
            "FROM gmail_notices WHERE id=?",
            (notice_id,),
        ).fetchone()
    if not row or row["status"] != "pending" or not row["attachment_bytes"]:
        raise GmailTransportError("Файл для сравнения не найден или уже обработан")
    try:
        parsed = parse_epg_xlsx(
            bytes(row["attachment_bytes"]), row["filename"],
            context=" ".join((row["subject"], row["sender"], row["snippet"])),
        )
    except InvalidEPG as exc:
        raise GmailTransportError("Не удалось прочитать Excel: " + str(exc)) from exc
    return preview_parsed_epg(database, parsed)


def approve_notice(database, notice_id: int, *, username: str) -> dict:
    init_gmail_schema(database)
    with database._connect() as conn:
        row = conn.execute(
            "SELECT id,filename,attachment_bytes,status,detected_channel,"
            "received_at,subject,sender,snippet FROM gmail_notices WHERE id=?",
            (notice_id,),
        ).fetchone()
        if row is None:
            raise GmailTransportError("Уведомление не найдено")
        if row["status"] != "pending" or not row["attachment_bytes"]:
            raise GmailTransportError("Вложение недоступно для импорта")
        filename, raw = row["filename"], bytes(row["attachment_bytes"])
    from services.epg_excel import import_parsed_epg, initialize_epg_imports
    parsed = parse_epg_xlsx(
        raw, filename,
        context=" ".join((row["subject"], row["sender"], row["snippet"])),
    )
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


def test_request(database, *, username: str, category: str, destination: str) -> dict:
    """Send ONLY to the owner's fixed test mailbox after explicit enable.

    Never accept arbitrary addresses from the browser. To: Anton/Sabina is
    intentionally NOT implemented until the owner approves production sending.
    """
    if _env("SPORT_GMAIL_ENABLE_TEST_SEND").lower() != "true":
        raise GmailNotConfigured("Тестовая отправка выключена в настройках")
    expected = _env("SPORT_MAIL_TEST_TO") or "alexandr.petrossov@fmedia.kz"
    if not expected or expected.casefold() != "alexandr.petrossov@fmedia.kz":
        raise GmailNotConfigured("Разрешён только подтверждённый тестовый получатель")
    if destination != expected:
        raise GmailTransportError("Недопустимый получатель")
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
    if category not in texts:
        raise GmailTransportError("Неверный тип запроса")
    import email.message
    subject, body = texts[category]
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
    with database._connect() as conn:
        conn.execute(
            "INSERT INTO gmail_requests("
            "recipient,subject,category,sent_by,gmail_message_id,sent_at"
            ") VALUES(?,?,?,?,?,?)",
            (expected, subject, category, username, message_id,
             datetime.now(KZ_TIMEZONE).isoformat()),
        )
    return {"sent": True, "to": expected, "subject": subject,
            "gmail_message_id": message_id, "category": category}

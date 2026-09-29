"""Cloud Run WEB entrypoint for the existing SLP parser, without Telegram polling.

This is the standalone web transport. Source parsers, evidence rules and SQLite
storage remain the existing SLP modules. No Gmail actions are performed here.
"""
from __future__ import annotations

import asyncio
import base64
from contextlib import asynccontextmanager
from copy import copy
from datetime import date, datetime, timedelta
from hashlib import sha256, scrypt
from io import BytesIO
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import time

from fastapi import FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, StreamingResponse, RedirectResponse, PlainTextResponse
from openpyxl import load_workbook
from pydantic import BaseModel

from agents.runtime_orchestrator import RuntimeParserOrchestrator
from services.live_evidence import event_is_live_broadcast, event_is_schedule_candidate
from services.editorial_export import InvalidTemplate, build_working_xlsx, validate_template
from services import gmail_integration as gmail
from services import assistant_bridge
from services import ai_pipeline
from services import editorial_store as editorial
from services.channel_registry import CHANNELS
from services.vsetv_sources import WEB_CHANNEL_IDS, refresh_vsetv_web_sources
from services.browser_schedule import browser_fallback_enabled, install_browser_for_python_runtime
from services.epg_excel import (MAX_WORKBOOK_BYTES, InvalidEPG, import_parsed_epg,
                               imported_epg_status, imported_official_epg_status,
                               initialize_epg_imports,
                               parse_epg_xlsx, parse_epg_xlsx_channels,
                               parse_supported_epg_channels, preview_parsed_epg)
from services.schedule_merge import same_sporting_event, normalize_match_text
from services.schedule_service import ScheduleService, is_user_event
from services.time_logic import KZ_TIMEZONE, get_scheduled_datetimes
from storage.database import SLPDatabase

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("SLP_DB_PATH", "/tmp/slp/slp.db"))
PUBLIC_URL = os.getenv("SPORT_PUBLIC_URL", "").rstrip("/")
SESSION_SECRET = os.getenv("SPORT_WEB_SECRET", "")
GCS_BUCKET = os.getenv("SPORT_GCS_BUCKET", "")
GCS_OBJECT = os.getenv("SPORT_GCS_OBJECT", "sport-epg/slp-web.db")
TEMPLATE_OBJECT = os.getenv("SPORT_TEMPLATE_OBJECT", "sport-epg/template.xlsx")
# Preview-only recipient configuration. Sending is disabled until owner OAuth.
# The owner's work address is the test recipient; later it becomes CC.
MAIL_TEST_TO = os.getenv("SPORT_MAIL_TEST_TO", "alexandr.petrossov@fmedia.kz")
MAIL_FUTURE_CC = os.getenv("SPORT_MAIL_FUTURE_CC", MAIL_TEST_TO)
PROFILE_AVATARS = {
    "skoomaholic": "/assets/skoomaholic.webp",
    "дания": "/assets/daniya.webp",
    "вадим": "/assets/vadim.webp",
}


def profile_avatar(username: str) -> str:
    return PROFILE_AVATARS.get(username.strip().casefold(), "")


Q_SETANTA = ("Q LEAGUE", "Q ARENA", "Q FOOTBALL",
             "SETANTA SPORTS 1", "SETANTA SPORTS 2", "SETANTA SPORTS KZ")
PRIORITY = ("QAZSPORT HD", "SPORT+ Qazaqstan", "KHL PRIME", "KHL HD",
            "EUROSPORT 1", "EUROSPORT 2", "МАТЧ! ПЛАНЕТА",
            "SETANTA SPORTS 1", "SETANTA SPORTS 2", "SETANTA SPORTS KZ",
            "Q LEAGUE", "Q ARENA", "Q FOOTBALL", "viju+ Sport")
ALLOWED_CHANNELS = frozenset(channel.name for channel in CHANNELS)
BOOKMAKERS = re.compile(
    r"\b(?:фонбет|fonbet|betboom|бетбум|бетсити|betcity|"
    r"winline|винлайн|parimatch|1xbet|лига ставок)\b\s*", re.I
)
EXCLUDE = re.compile(
    r"студия|студийн|студиялық|обзор|шолу|повтор|запись|"
    r"news|новости|тележурнал|подробно|перед матчем|"
    r"матч қарсаңында|на пульсе|дневник|дайджест|превью|"
    r"көрсетілімге дейін|итоги|podcast", re.I
)


def configured_users() -> dict:
    try:
        users = json.loads(os.getenv("SPORT_WEB_USERS", "{}"))
    except (ValueError, TypeError):
        return {}
    return users if isinstance(users, dict) else {}


def origin_guard(request: Request) -> None:
    # Mutating browser requests are accepted only from the exact deployed origin.
    origin = request.headers.get("origin", "")
    if not PUBLIC_URL or origin != PUBLIC_URL:
        raise HTTPException(403, "Недопустимый источник запроса")


def _password_matches(password: str, encoded: str) -> bool:
    try:
        kind, salt64, digest64 = encoded.split("$")
        if kind != "scrypt":
            return False
        salt = base64.b64decode(salt64, validate=True)
        expected = base64.b64decode(digest64, validate=True)
        computed = scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
        return hmac.compare_digest(expected, computed)
    except (ValueError, TypeError, KeyError):
        return False


def _session(username: str, fingerprint: str) -> str:
    data = {"user": username, "expires": int(time.time()) + 8 * 3600,
            "fingerprint": fingerprint}
    payload = base64.urlsafe_b64encode(
        json.dumps(data, separators=(",", ":")).encode()
    ).rstrip(b"=").decode()
    sig = hmac.new(SESSION_SECRET.encode(), payload.encode(), sha256).hexdigest()
    return payload + "." + sig


def current_user(request: Request) -> dict:
    if len(SESSION_SECRET) < 32:
        raise HTTPException(503, "SPORT_WEB_SECRET не настроен")
    users = configured_users()
    if not users:
        raise HTTPException(503, "Пользователи не настроены")
    token = request.cookies.get("sport_web_session", "")
    try:
        payload, signature = token.split(".")
        verified = hmac.new(SESSION_SECRET.encode(), payload.encode(), sha256).hexdigest()
        if not hmac.compare_digest(signature, verified):
            raise ValueError()
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        username = data["user"]
        user = users[username]
        if data["expires"] <= time.time() or data["fingerprint"] != sha256(
            user["password_hash"].encode()
        ).hexdigest():
            raise ValueError()
        return {"username": username, "role": user.get("role", "editor"),
                "avatar": user.get("avatar", ""),
                "avatar_url": profile_avatar(username)}
    except (ValueError, TypeError, KeyError, IndexError):
        raise HTTPException(401, "Войдите в систему") from None


class Login(BaseModel):
    username: str
    password: str


class CollectOptions(BaseModel):
    allow_partial: bool = False


def clean(value: str) -> str:
    return " ".join(BOOKMAKERS.sub("", str(value or "")).split()).strip(" .,;-")


def channel_name(name: str) -> str:
    raw = " ".join(str(name or "").split()).strip()
    key = raw.casefold()
    aliases = {
        "qazsport": "QAZSPORT HD",
        "qazsport hd": "QAZSPORT HD",
        "setanta 1": "SETANTA SPORTS 1",
        "setanta sports 1": "SETANTA SPORTS 1",
        "setanta 2": "SETANTA SPORTS 2",
        "setanta sports 2": "SETANTA SPORTS 2",
        "setanta kz": "SETANTA SPORTS KZ",
        "setanta sports kz": "SETANTA SPORTS KZ",
        "setanta sports kazakhstan": "SETANTA SPORTS KZ",
        "eurosport": "EUROSPORT 1",
        "eurosport 1": "EUROSPORT 1",
        "eurosport 2": "EUROSPORT 2",
        "khl prime": "KHL PRIME",
        "khl hd": "KHL HD",
        "матч! планета": "МАТЧ! ПЛАНЕТА",
        "матч планета": "МАТЧ! ПЛАНЕТА",
        "sport+ qazaqstan": "SPORT+ Qazaqstan",
        "sport+ kazakhstan": "SPORT+ Qazaqstan",
        "q league": "Q LEAGUE",
        "q arena": "Q ARENA",
        "q football": "Q FOOTBALL",
        "viju+ sport": "viju+ Sport",
    }
    return aliases.get(key, raw)


def event_rows(
    database: SLPDatabase, first: str, last: str, *, include_inactive: bool = False
) -> list[dict]:
    """Keep each verified TV broadcast distinct; group only actual simulcasts.

    The archive view can include inactive source snapshots. Such snapshots are
    not mistaken for confirmed cancellations or completed broadcasts.
    """
    with database._connect() as conn:
        rows = conn.execute(
            "SELECT storage_id,payload_json,active,first_seen_at,last_seen_at "
            "FROM events WHERE (?=1 OR active=1) "
            "AND (?='' OR event_date>=?) AND (?='' OR event_date<=?) "
            "ORDER BY start_at,channel LIMIT 20000",
            (int(include_inactive), first, first, last, last),
        ).fetchall()
    now = datetime.now(KZ_TIMEZONE)
    overrides = editorial.get_editorial(
        database, [row["storage_id"] for row in rows]
    )
    exact: dict[tuple, dict] = {}
    for row in rows:
        try:
            raw = json.loads(row["payload_json"])
            patch = overrides.get(row["storage_id"], {})
            raw.update({key: value for key, value in patch.items()
                        if key != "end_time"})
            if patch.get("end_time"):
                raw["estimated_broadcast_end_date"] = raw["date"]
                raw["estimated_broadcast_end"] = patch["end_time"]
                raw["end_estimation_method"] = "explicit"
            if not (event_is_live_broadcast(raw)
                    and event_is_schedule_candidate(raw)
                    and is_user_event(raw)):
                continue
            title = clean(raw.get("title") or raw.get("raw_title") or "")
            sport = clean(raw.get("sport") or "")
            tournament = clean(raw.get("tournament") or "")
            if not title or not sport or EXCLUDE.search(title + " " + tournament):
                continue
            channel = channel_name(raw.get("channel", ""))
            if channel not in ALLOWED_CHANNELS or channel == "QAZSPORT HD" and re.search(
                r"барыс|barys", title, re.I
            ):
                continue
            start, end = get_scheduled_datetimes(raw)
            known_end = bool(
                raw.get("estimated_broadcast_end_date")
                and raw.get("estimated_broadcast_end")
                and str(raw.get("end_estimation_method") or "").casefold()
                in ("next_program", "provider_duration", "provider_epg",
                    "epg", "source_epg", "explicit", "explicit_end")
            )
            record = {
                "title": title, "sport": sport, "tournament": tournament,
                "channel": channel, "source": raw.get("source", ""),
                "source_record_id": row["storage_id"],
                "date": start.date().isoformat(), "time": start.strftime("%H:%M"),
                "start": start, "end": end + (
                    timedelta(0) if known_end else timedelta(minutes=10)
                ), "end_known": known_end, "active": bool(row["active"]),
                "first_seen_at": row["first_seen_at"],
                "last_seen_at": row["last_seen_at"],
                "editorial": patch,
                "team1_ru": raw.get("team1_ru", ""),
                "team1_kz": raw.get("team1_kz", ""),
                "team2_ru": raw.get("team2_ru", ""),
                "team2_kz": raw.get("team2_kz", ""),
                "subtitle_ru": raw.get("subtitle_ru", ""),
                "subtitle_kz": raw.get("subtitle_kz", ""),
                "status": "past" if end < now else (
                    "live" if start <= now else "upcoming"
                ),
            }
        except (ValueError, TypeError, KeyError):
            continue
        # Multiple providers may describe exactly one broadcast. Prefer a
        # current confirmed supplier XLSX over a third-party copy.
        key = (record["date"], record["time"], channel,
               normalize_match_text(title), normalize_match_text(tournament),
               normalize_match_text(sport))
        old = exact.get(key)
        if old is None or (
            int(record["active"]), int(str(record["source"]).startswith("email_epg"))
        ) > (
            int(old["active"]), int(str(old["source"]).startswith("email_epg"))
        ):
            exact[key] = record

    groups: list[list[dict]] = []
    for record in sorted(
        exact.values(), key=lambda e: (e["start"], e["channel"], e["title"])
    ):
        for group in groups:
            # Require every member to match and reject duplicate channels.
            # Avoid chaining two independent sessions via an intermediate
            # simulcast 30 minutes apart.
            if all(
                same_sporting_event(record, other, max_start_difference_minutes=30)
                for other in group
            ):
                group.append(record)
                break
        else:
            groups.append([record])

    merged: list[dict] = []
    for group in groups:
        group.sort(key=lambda e: (
            not e["active"],
            PRIORITY.index(e["channel"]) if e["channel"] in PRIORITY else 999,
            e["start"], e["channel"],
        ))
        chosen = group[0]
        channels = list(dict.fromkeys(e["channel"] for e in group))
        entry = {
            k: v for k, v in chosen.items()
            if k not in ("start", "end", "source_record_id", "first_seen_at",
                         "last_seen_at")
        }
        entry["start_at"] = chosen["start"].isoformat()
        entry["platform_start_at"] = (
            chosen["start"] - timedelta(minutes=10)
        ).isoformat()
        entry["end_at"] = chosen["end"].isoformat()
        entry["channels"] = channels
        entry["archived"] = not any(e["active"] for e in group)
        entry["broadcasts"] = [{
            "channel": e["channel"], "source_record_id": e["source_record_id"],
            "start_at": e["start"].isoformat(),
            "end_at": e["end"].isoformat(), "source": e["source"],
            "active": e["active"], "first_seen_at": e["first_seen_at"],
            "last_seen_at": e["last_seen_at"],
        } for e in group]
        entry["id"] = hashlib.sha1(
            ("|".join((entry["date"], normalize_match_text(entry["sport"]),
                     normalize_match_text(entry["tournament"]),
                     normalize_match_text(entry["title"])))).encode()
        ).hexdigest()[:16]
        merged.append(entry)
    return sorted(merged, key=lambda e: (e["start_at"], e["title"]))


class BucketSnapshot:
    """Optional SQLite backup as a single GCS object, safe for one Cloud Run instance.

    Uses SQLite's online backup API, never mounts a GCS bucket as a SQLite filesystem.
    Writes use generation preconditions, so a concurrent writer fails rather than
    silently losing history. Configure Cloud Run max instances=1, concurrency=1.
    """

    def __init__(self, bucket_name: str):
        from google.cloud import storage
        from google.api_core.exceptions import NotFound
        client = storage.Client()
        self.blob = client.bucket(bucket_name).blob(GCS_OBJECT)
        try:
            self.blob.reload()
        except NotFound:
            self.generation = 0
        else:
            self.generation = self.blob.generation
            self.blob.download_to_filename(str(DB_PATH))

    def save(self):
        from google.api_core.exceptions import PreconditionFailed
        with tempfile.NamedTemporaryFile(dir=DB_PATH.parent, suffix=".db", delete=False) as f:
            backup_path = f.name
        try:
            src = sqlite3.connect(DB_PATH)
            dst = sqlite3.connect(backup_path)
            try:
                src.backup(dst)
            finally:
                dst.close()
                src.close()
            try:
                self.blob.upload_from_filename(
                    backup_path, content_type="application/octet-stream",
                    if_generation_match=self.generation,
                )
            except PreconditionFailed as exc:
                raise RuntimeError("Хранилище изменилось: нужна синхронизация") from exc
            self.generation = self.blob.generation
        finally:
            Path(backup_path).unlink(missing_ok=True)


@asynccontextmanager
async def lifespan(application: FastAPI):
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    application.state.backup = BucketSnapshot(GCS_BUCKET) if GCS_BUCKET else None
    database = SLPDatabase(DB_PATH)
    initialize_epg_imports(database)
    gmail.init_gmail_schema(database)
    editorial.init_editorial(database)
    application.state.database = database
    application.state.schedule = ScheduleService(
        RuntimeParserOrchestrator(database=database)
    )
    application.state.collect_lock = asyncio.Lock()
    browser_install_task = None
    if browser_fallback_enabled():
        browser_install_task = asyncio.create_task(
            asyncio.to_thread(install_browser_for_python_runtime)
        )
    yield
    if browser_install_task is not None:
        await browser_install_task


app = FastAPI(title="SLP Sport EPG Web", docs_url=None, redoc_url=None, lifespan=lifespan)


@app.get("/healthz")
def healthz(request: Request):
    database = request.app.state.database
    return {"status": "ok", "runtime": "web", "telegram_polling": False,
            "events": database.active_event_count(),
            "durable_storage": bool(request.app.state.backup)}


@app.get("/")
def home():
    return FileResponse(ROOT / "cloudrun_ui" / "index.html")


@app.get("/assets/logos.js")
def channel_logos():
    return FileResponse(ROOT / "cloudrun_ui" / "logos.js",
                        media_type="text/javascript; charset=utf-8")


@app.get("/assets/{filename}")
def ui_image(filename: str):
    media = {
        "app-icon.png": "image/png",
        "skoomaholic.webp": "image/webp",
        "daniya.webp": "image/webp",
        "vadim.webp": "image/webp",
    }.get(filename)
    if not media:
        raise HTTPException(404, "Изображение не найдено")
    return FileResponse(ROOT / "cloudrun_ui" / "assets" / filename,
                        media_type=media)


@app.get("/favicon.ico")
def favicon():
    return FileResponse(ROOT / "cloudrun_ui" / "assets" / "app-icon.png",
                        media_type="image/png")


@app.post("/api/login")
def login(request: Request, credentials: Login, response: Response):
    origin_guard(request)
    if len(SESSION_SECRET) < 32:
        raise HTTPException(503, "SPORT_WEB_SECRET не настроен")
    users = configured_users()
    user = users.get(credentials.username)
    if not user or not _password_matches(credentials.password, user.get("password_hash", "")):
        raise HTTPException(401, "Неверный логин или пароль")
    fingerprint = sha256(user["password_hash"].encode()).hexdigest()
    response.set_cookie("sport_web_session", _session(credentials.username, fingerprint),
                        httponly=True, secure=PUBLIC_URL.startswith("https://"),
                        samesite="lax", max_age=8 * 3600)
    return {"user": {"username": credentials.username,
                     "role": user.get("role", "editor"),
                     "avatar": user.get("avatar", ""),
                     "avatar_url": profile_avatar(credentials.username)}}


@app.get("/api/me")
def me(request: Request):
    return {"user": current_user(request)}


@app.post("/api/logout")
def logout(request: Request, response: Response):
    origin_guard(request)
    response.delete_cookie("sport_web_session")
    return {"ok": True}


def _validate_period(start: str, end: str) -> None:
    for value in (start, end):
        if value:
            try:
                date.fromisoformat(value)
            except ValueError:
                raise HTTPException(400, "Неверная дата") from None
    if start and end and start > end:
        raise HTTPException(400, "Начальная дата позже конечной")


@app.get("/api/events")
def events(request: Request, start: str = "", end: str = ""):
    current_user(request)
    _validate_period(start, end)
    return {"events": event_rows(request.app.state.database, start, end),
            "timezone": "Asia/Almaty"}


@app.get("/api/archive")
def archive(request: Request, start: str = "", end: str = ""):
    current_user(request)
    _validate_period(start, end)
    return {
        "events": event_rows(
            request.app.state.database, start, end, include_inactive=True
        ), "timezone": "Asia/Almaty",
        "history_complete": False,
        "note": "Доступны сохранённые версии источников. Полный журнал "
                "каждого изменения одного и того же поля ещё не подключён.",
    }


def _source_status(request: Request) -> dict:
    database = request.app.state.database
    today = datetime.now(KZ_TIMEZONE).date()
    files = imported_epg_status(
        database, first=today, last=today + timedelta(days=6)
    )
    official_files = imported_official_epg_status(
        database, first=today, last=today + timedelta(days=6)
    )
    pending_notices = gmail.list_notices(database, limit=200)
    mail_requests = gmail.list_requests(database, limit=100)
    pending_channels = {
        item["detected_channel"] for item in pending_notices
        if item["status"] == "pending" and item["detected_channel"]
    }
    awaiting_response = {
        channel
        for item in mail_requests
        if item["status"] in ("pending", "partial")
        for channel in item.get("requested_channels", [])
        if channel not in set(item.get("received_channels", []))
    }
    for item in files:
        if (item["channel"] in pending_channels
                and item["status"] in ("missing", "outdated", "partial")):
            item["status"] = "awaiting_approval"
        elif (item["channel"] in awaiting_response
              and item["status"] in ("missing", "outdated", "partial")):
            item["status"] = "awaiting_response"
    source_runs = database.latest_source_runs()
    # A provider's single tvguide run contains multiple distinct channels.
    # Count the actual persisted direct broadcasts per channel, not the
    # provider-wide parser run total.
    with database._connect() as conn:
        active_counts = conn.execute(
            "SELECT source,channel,COUNT(*) AS n FROM events "
            "WHERE active=1 AND is_live=1 AND event_date>=? "
            "AND event_date<=? GROUP BY source,channel",
            (today.isoformat(), (today + timedelta(days=6)).isoformat()),
        ).fetchall()
    by_channel = {}
    for row in active_counts:
        label = channel_name(row["channel"])
        by_channel[label] = by_channel.get(label, 0) + int(row["n"])
    websites = []
    for channel in CHANNELS:
        sources = []
        if channel.name == "QAZSPORT HD":
            sources.append("qazsport")
        if channel.name == "SPORT+ Qazaqstan":
            sources.append("sportplus")
        if channel.vsetv_id is not None:
            sources.append("web_vsetv_" + str(channel.vsetv_id))
        if channel.tvplus_id:
            sources.append("tvguide")
        runs = [source_runs[name] for name in sources if name in source_runs]
        newest = max(
            runs, key=lambda item: str(item.get("created_at") or ""),
            default=None,
        )
        count = by_channel.get(channel.name, 0)
        status = (
            "ok" if count else
            "warning" if newest and newest.get("status") in ("ok", "warning") else
            newest.get("status", "not_checked") if newest else "not_checked"
        )
        websites.append({
            "channel": channel.name, "kind": "website",
            "status": status,
            "checked_at": (newest or {}).get("created_at"),
            "event_count": count,
            "official_excel": official_files.get(channel.name),
            "guide_sources": sources,
            "official_site": channel.official_site,
            "secondary_guide": channel.secondary_guide,
            "provider_channel_id": channel.tvplus_id or None,
            "source_verified": bool(count),
        })
    return {
        "websites": websites, "excel": files,
        "mail_request": {
            "enabled": bool(gmail.status(database)["send_enabled"]
                            and gmail.status(database)["connected"]),
            "mode": gmail.mail_mode(), "test_to": MAIL_TEST_TO,
            "future_cc": MAIL_FUTURE_CC,
            "pending": sum(
                1 for item in mail_requests
                if item["status"] in ("pending", "partial")
            ),
        },
        "pending_channels": [
            x["channel"] for x in files if x["status"] == "awaiting_approval"
        ],
        "missing_channels": [
            x["channel"] for x in files
            if x["status"] in ("missing", "outdated", "partial")
        ], "gmail_connected": gmail.status(database)["connected"],
        "ai": ai_pipeline.status(),
        "auto_import": bool(request.app.state.backup
                            and gmail._auto_import_enabled()),
        "note": ("Подтверждённые Excel загружаются автоматически; "
                 "на проверку попадают только спорные данные."
                 if request.app.state.backup and gmail._auto_import_enabled()
                 else "Автозагрузка отключена до настройки постоянного хранения."
                 if gmail.status(database)["connected"]
                 else "Для автоматической обработки нужно подключить OAuth владельца."),
    }


@app.get("/api/sources")
def sources(request: Request):
    current_user(request)
    return _source_status(request)


@app.post("/api/preview-epg")
async def preview_epg(request: Request, upload: UploadFile = File(...)):
    """Show an editor the actual diff without mutating accepted schedules."""
    user = current_user(request)
    origin_guard(request)
    if user["role"] not in ("editor", "admin"):
        raise HTTPException(403, "Недостаточно прав на просмотр импорта")
    filename = upload.filename or ""
    try:
        data = await upload.read(MAX_WORKBOOK_BYTES + 1)
    finally:
        await upload.close()
    try:
        parsed_batches = parse_supported_epg_channels(data, filename)
    except InvalidEPG as exc:
        raise HTTPException(422, str(exc)) from exc
    async with request.app.state.collect_lock:
        previews = [
            preview_parsed_epg(request.app.state.database, parsed)
            for parsed in parsed_batches
        ]
    return {
        "filename": filename,
        "previews": previews,
        "channels": [parsed.channel for parsed in parsed_batches],
        "total_live": sum(len(parsed.events) for parsed in parsed_batches),
        "durable_storage": bool(request.app.state.backup),
        "warning": (
            "Постоянное хранилище не подключено: после перезапуска импорт может исчезнуть."
            if not request.app.state.backup else ""
        ),
    }


@app.post("/api/import-epg")
async def import_epg(request: Request, upload: UploadFile = File(...)):
    user = current_user(request)
    origin_guard(request)
    if user["role"] not in ("editor", "admin"):
        raise HTTPException(403, "Недостаточно прав на импорт")
    filename = upload.filename or ""
    try:
        data = await upload.read(MAX_WORKBOOK_BYTES + 1)
    finally:
        await upload.close()
    try:
        parsed_batches = parse_supported_epg_channels(data, filename)
    except InvalidEPG as exc:
        raise HTTPException(422, str(exc)) from exc
    async with request.app.state.collect_lock:
        results = [
            import_parsed_epg(request.app.state.database, parsed)
            for parsed in parsed_batches
        ]
        if request.app.state.backup and any(
            result["status"] in ("imported", "partial_review") for result in results
        ):
            try:
                await asyncio.to_thread(request.app.state.backup.save)
            except Exception as exc:
                raise HTTPException(503, "Импорт выполнен, но резервная копия "
                                    "в GCS не сохранена: " + type(exc).__name__) from exc
    response = results[0] if len(results) == 1 else {
        "status": "imported" if all(
            result["status"] in ("imported", "already_imported")
            for result in results
        ) else "partial_review",
        "channel": ", ".join(result["channel"] for result in results),
        "live_events": sum(result["live_events"] for result in results),
        "results": results,
    }
    response["durable_storage"] = bool(request.app.state.backup)
    return response



class MailRequest(BaseModel):
    category: str
    period_start: str = ""
    period_end: str = ""


def require_editor(request: Request) -> dict:
    user = current_user(request)
    if user["role"] not in ("editor", "admin"):
        raise HTTPException(403, "Недостаточно прав")
    return user


def require_admin(request: Request) -> dict:
    user = current_user(request)
    if user["role"] != "admin":
        raise HTTPException(403, "Подключить Gmail может только администратор")
    return user


def _gmail_failure(exc: Exception) -> HTTPException:
    if isinstance(exc, gmail.GmailNotConfigured):
        return HTTPException(503, str(exc))
    if isinstance(exc, gmail.GmailTransportError):
        return HTTPException(422, str(exc))
    return HTTPException(503, "Ошибка подключения Gmail: " + type(exc).__name__)


async def _save_state(request: Request) -> None:
    if request.app.state.backup:
        try:
            await asyncio.to_thread(request.app.state.backup.save)
        except Exception as exc:
            raise HTTPException(503, "Данные изменены, но резервное "
                                "копирование не удалось: " + type(exc).__name__) from exc



class EditorialChange(BaseModel):
    values: dict[str, str]


@app.patch("/api/editorial/{storage_id}")
async def save_editorial_change(request: Request, storage_id: str,
                                change: EditorialChange):
    user = require_editor(request)
    origin_guard(request)
    async with request.app.state.collect_lock:
        try:
            result = editorial.apply_edit(
                request.app.state.database, storage_id=storage_id,
                values=change.values, username=user["username"],
            )
        except editorial.EditorialError as exc:
            raise HTTPException(422, str(exc)) from exc
        await _save_state(request)
    return result


@app.get("/api/editorial/{storage_id}/history")
def editorial_history(request: Request, storage_id: str):
    current_user(request)
    return {"history": editorial.edit_history(
        request.app.state.database, storage_id=storage_id
    )}


@app.get("/api/archive/revisions")
def archive_revisions(request: Request, storage_id: str = ""):
    current_user(request)
    if storage_id and not re.fullmatch(r"[a-f0-9]{24}", storage_id):
        raise HTTPException(400, "Неверный идентификатор")
    return {"revisions": request.app.state.database.event_revisions(storage_id)}


def _notification_items(database: SLPDatabase, limit: int = 80) -> list[dict]:
    """Unify actionable supplier/source notices without inventing cancellations."""
    items: list[dict] = []
    gmail.init_gmail_schema(database)
    with database._connect() as conn:
        mail_rows = conn.execute(
            "SELECT id,status,filename,subject,detected_channel,reason,"
            "received_at,created_at FROM gmail_notices "
            "WHERE status IN ('pending','review') ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        incident_rows = conn.execute(
            "SELECT id,source,scope_date,incident_type,severity,message,"
            "created_at FROM incidents WHERE resolved_at IS NULL "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        revision_rows = conn.execute(
            "SELECT id,storage_id,source,scope_date,change_kind,before_json,"
            "after_json,created_at FROM event_revisions "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()

    for row in mail_rows:
        items.append({
            "kind": "mail", "id": f"mail:{row['id']}",
            "level": "attention" if row["status"] == "review" else "info",
            "title": (
                (row["detected_channel"] or "Письмо требует проверки") +
                (" · " + row["filename"] if row["filename"] else "")
            ),
            "message": row["reason"] or row["subject"],
            "created_at": row["received_at"] or row["created_at"],
            "action": "gmail",
        })
    for row in incident_rows:
        items.append({
            "kind": "incident", "id": f"incident:{row['id']}",
            "level": row["severity"] or "warning",
            "title": f"{row['source']} · {row['scope_date']}",
            "message": row["message"],
            "created_at": row["created_at"],
            "action": "sources",
        })
    for row in revision_rows:
        try:
            before = json.loads(row["before_json"]) if row["before_json"] else {}
            after = json.loads(row["after_json"]) if row["after_json"] else {}
        except (TypeError, json.JSONDecodeError):
            before, after = {}, {}
        event = after or before
        title = clean(event.get("title") or event.get("raw_title") or "Событие")
        channel = channel_name(event.get("channel") or "")
        change = row["change_kind"]
        if change == "removed_from_source":
            message = "Запись исчезла из новой сетки. Это не подтверждает отмену."
            level = "attention"
        elif change == "source_changed":
            fields = []
            for field, label in (
                ("time", "время"), ("date", "дата"),
                ("title", "название"), ("tournament", "турнир"),
            ):
                if before.get(field) != after.get(field):
                    fields.append(label)
            message = "Источник изменил: " + (", ".join(fields) or "данные события")
            level = "attention"
        else:
            message = "Новое подтверждённое событие в источнике"
            level = "info"
        items.append({
            "kind": "source_change", "id": f"revision:{row['id']}",
            "level": level,
            "title": " · ".join(x for x in (channel, title) if x),
            "message": message, "created_at": row["created_at"],
            "action": "archive", "storage_id": row["storage_id"],
        })
    items.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return items[:limit]


@app.get("/api/notifications")
def notifications(request: Request, limit: int = 80):
    current_user(request)
    return {"notifications": _notification_items(
        request.app.state.database, min(max(limit, 1), 150)
    )}


@app.get("/api/gmail/status")
def gmail_status(request: Request):
    current_user(request)
    result = gmail.status(request.app.state.database)
    result["auto_import"] = bool(
        request.app.state.backup and gmail._auto_import_enabled()
    )
    result["ai"] = ai_pipeline.status()
    return result


@app.get("/api/gmail/connect")
async def gmail_connect(request: Request):
    user = require_admin(request)
    if not request.app.state.backup:
        raise HTTPException(503, "Сначала подключите постоянное хранилище GCS")
    async with request.app.state.collect_lock:
        try:
            url = gmail.start_oauth(request.app.state.database,
                                    username=user["username"])
        except (gmail.GmailTransportError, gmail.GmailNotConfigured) as exc:
            raise _gmail_failure(exc) from exc
        await _save_state(request)
    return RedirectResponse(url=url, status_code=303)


@app.get("/api/gmail/callback")
async def gmail_callback(request: Request, state: str = "", code: str = "",
                         error: str = ""):
    user = require_admin(request)
    if error or not code:
        return PlainTextResponse("Подключение Gmail отменено", status_code=422)
    async with request.app.state.collect_lock:
        try:
            gmail.complete_oauth(request.app.state.database,
                                 username=user["username"], state=state, code=code)
        except (gmail.GmailTransportError, gmail.GmailNotConfigured) as exc:
            raise _gmail_failure(exc) from exc
        await _save_state(request)
    return RedirectResponse(url="/?gmail=connected", status_code=303)


@app.post("/api/gmail/sync")
async def gmail_sync(request: Request):
    require_editor(request)
    origin_guard(request)
    async with request.app.state.collect_lock:
        try:
            result = await asyncio.to_thread(
                gmail.sync_inbox, request.app.state.database,
                allow_auto_import=bool(request.app.state.backup)
            )
        except (gmail.GmailTransportError, gmail.GmailNotConfigured) as exc:
            raise _gmail_failure(exc) from exc
        # Only existing SLP editorial notices are sent; never mail bodies or Excel.
        bridge = (await asyncio.to_thread(
            assistant_bridge.deliver_pending, request.app.state.database
        ) if request.app.state.backup else
            {"enabled": False, "delivered": 0, "failed": 0})
        if result["new_attachments"] or result["requires_review"] or bridge["delivered"]:
            await _save_state(request)
        result["assistant_notifications"] = bridge
    return result



@app.post("/api/jobs/gmail-sync")
async def scheduled_gmail_sync(request: Request):
    # Optional Cloud Scheduler OIDC trigger. No public cron secret, no
    # unauthenticated internet requests and no browser cookie needed.
    allowed_email = os.getenv("SPORT_SCHEDULER_SERVICE_ACCOUNT", "").strip()
    if not allowed_email or not PUBLIC_URL:
        raise HTTPException(503, "Cloud Scheduler ещё не настроен")
    authorization = request.headers.get("authorization", "")
    if not authorization.startswith("Bearer ") or len(authorization) > 8192:
        raise HTTPException(401, "Требуется OIDC-токен Cloud Scheduler")
    try:
        from google.oauth2 import id_token
        from google.auth.transport.requests import Request as GoogleRequest
        claims = await asyncio.to_thread(
            id_token.verify_oauth2_token, authorization[7:],
            GoogleRequest(), PUBLIC_URL + "/api/jobs/gmail-sync",
        )
    except Exception:
        raise HTTPException(401, "OIDC-токен не прошёл проверку") from None
    if str(claims.get("email") or "").casefold() != allowed_email.casefold() or (
        not claims.get("email_verified", False)
    ):
        raise HTTPException(403, "Неизвестный сервисный аккаунт")
    async with request.app.state.collect_lock:
        try:
            result = await asyncio.to_thread(
                gmail.sync_inbox, request.app.state.database,
                allow_auto_import=bool(request.app.state.backup)
            )
        except (gmail.GmailTransportError, gmail.GmailNotConfigured) as exc:
            raise _gmail_failure(exc) from exc
        # Only existing SLP editorial notices are sent; never mail bodies or Excel.
        bridge = (await asyncio.to_thread(
            assistant_bridge.deliver_pending, request.app.state.database
        ) if request.app.state.backup else
            {"enabled": False, "delivered": 0, "failed": 0})
        if result["new_attachments"] or result["requires_review"] or bridge["delivered"]:
            await _save_state(request)
        result["assistant_notifications"] = bridge
    return result


@app.post("/api/jobs/refresh")
async def scheduled_refresh(request: Request):
    """Optional authenticated one-shot refresh for Cloud Scheduler.

    No task is created automatically and no cloud service is provisioned.
    This endpoint must never be reachable without an OIDC identity from
    the configured dedicated service account.
    """
    if not request.app.state.backup:
        raise HTTPException(503, "Для фонового обновления нужен постоянный GCS")
    allowed = os.getenv("SPORT_SCHEDULER_SERVICE_ACCOUNT", "").strip()
    if not allowed or not PUBLIC_URL:
        raise HTTPException(503, "Cloud Scheduler ещё не настроен")
    authorization = request.headers.get("authorization", "")
    if not authorization.startswith("Bearer ") or len(authorization) > 8192:
        raise HTTPException(401, "Требуется OIDC-токен Cloud Scheduler")
    try:
        from google.oauth2 import id_token
        from google.auth.transport.requests import Request as GoogleRequest
        claims = await asyncio.to_thread(
            id_token.verify_oauth2_token, authorization[7:],
            GoogleRequest(), PUBLIC_URL + "/api/jobs/refresh",
        )
    except Exception:
        raise HTTPException(401, "OIDC-токен не прошёл проверку") from None
    if not claims.get("email_verified") or (
        str(claims.get("email") or "").casefold() != allowed.casefold()
    ):
        raise HTTPException(403, "Неизвестный сервисный аккаунт")
    async with request.app.state.collect_lock:
        results = {"gmail": None, "websites": None, "errors": []}
        try:
            await request.app.state.schedule.refresh()
        except Exception as exc:
            results["errors"].append(
                "official_sources: " + type(exc).__name__
            )
        try:
            results["websites"] = await refresh_vsetv_web_sources(
                request.app.state.database
            )
        except Exception as exc:
            results["errors"].append("vsetv: " + type(exc).__name__)
        if gmail.status(request.app.state.database)["connected"]:
            try:
                results["gmail"] = await asyncio.to_thread(
                    gmail.sync_inbox, request.app.state.database,
                    allow_auto_import=True,
                )
            except (gmail.GmailTransportError, gmail.GmailNotConfigured) as exc:
                results["errors"].append("gmail: " + type(exc).__name__)
        await _save_state(request)
    results["ok"] = not bool(results["errors"])
    return results


@app.get("/api/gmail/notices")
def gmail_notices(request: Request):
    current_user(request)
    return {"notices": gmail.list_notices(request.app.state.database)}


@app.get("/api/gmail/requests")
def gmail_requests(request: Request):
    require_editor(request)
    return {"requests": gmail.list_requests(request.app.state.database)}



@app.get("/api/gmail/notices/{notice_id}/preview")
def preview_mail_notice(request: Request, notice_id: int):
    require_editor(request)
    try:
        return gmail.preview_notice(request.app.state.database, notice_id)
    except gmail.GmailTransportError as exc:
        raise _gmail_failure(exc) from exc


@app.post("/api/gmail/notices/{notice_id}/approve")
async def approve_mail(request: Request, notice_id: int):
    user = require_editor(request)
    origin_guard(request)
    async with request.app.state.collect_lock:
        try:
            result = gmail.approve_notice(
                request.app.state.database, notice_id, username=user["username"]
            )
        except (gmail.GmailTransportError, gmail.GmailNotConfigured,
                InvalidEPG) as exc:
            raise _gmail_failure(exc) from exc
        await _save_state(request)
    return result


@app.post("/api/gmail/notices/{notice_id}/dismiss")
async def dismiss_mail(request: Request, notice_id: int):
    user = require_editor(request)
    origin_guard(request)
    async with request.app.state.collect_lock:
        try:
            gmail.dismiss_notice(
                request.app.state.database, notice_id, username=user["username"]
            )
        except (gmail.GmailTransportError, gmail.GmailNotConfigured) as exc:
            raise _gmail_failure(exc) from exc
        await _save_state(request)
    return {"dismissed": True}


@app.post("/api/gmail/request")
async def send_test_request(request: Request, options: MailRequest):
    user = require_editor(request)
    origin_guard(request)
    if gmail.mail_mode() == "production":
        user = require_admin(request)
    if not request.app.state.backup:
        raise HTTPException(503, "Отправка требует постоянного хранилища журнала")
    async with request.app.state.collect_lock:
        try:
            result = await asyncio.to_thread(
                gmail.send_schedule_request, request.app.state.database,
                username=user["username"], category=options.category,
                period_start=options.period_start,
                period_end=options.period_end,
                destination=MAIL_TEST_TO,
            )
        except (gmail.GmailTransportError, gmail.GmailNotConfigured) as exc:
            raise _gmail_failure(exc) from exc
        await _save_state(request)
    return result


@app.post("/api/collect")
async def collect(request: Request, options: CollectOptions):
    current_user(request)
    origin_guard(request)
    # One-button flow: first look for newly arrived mail, but never import
    # attachments before the editor approves the supplier file.
    if gmail.status(request.app.state.database)["connected"]:
        async with request.app.state.collect_lock:
            try:
                mail_sync = await asyncio.to_thread(
                    gmail.sync_inbox, request.app.state.database,
                allow_auto_import=bool(request.app.state.backup)
                )
            except (gmail.GmailTransportError, gmail.GmailNotConfigured):
                mail_sync = {"new_attachments": 0, "requires_review": 0}
            if mail_sync.get("new_attachments") or mail_sync.get("requires_review"):
                await _save_state(request)
    status = _source_status(request)
    if status["pending_channels"]:
        raise HTTPException(409, {
            "message": "Новые расписания уже получены по почте и ждут подтверждения.",
            "pending_channels": status["pending_channels"],
            "missing_channels": status["missing_channels"],
            "requires_approval": True,
        })
    if status["missing_channels"] and not options.allow_partial:
        raise HTTPException(409, {
            "message": "Нет актуальных Excel некоторых телеканалов. Собрать без них?",
            "missing_channels": status["missing_channels"],
            "requires_approval": False,
        })
    async with request.app.state.collect_lock:
        try:
            await request.app.state.schedule.refresh()
            vsetv = await refresh_vsetv_web_sources(request.app.state.database)
            if request.app.state.backup:
                await asyncio.to_thread(request.app.state.backup.save)
        except Exception as exc:
            raise HTTPException(502, "Ошибка обновления или сохранения: "
                                + type(exc).__name__) from exc
    db = request.app.state.database
    return {"ok": True, "event_count": db.active_event_count(),
            "durable_storage": bool(request.app.state.backup),
            "last_run": db.latest_agent_run(),
            "vsetv": vsetv}



def _local_template_path() -> Path | None:
    """An explicitly configured path, independent of the eventual web host."""
    raw = os.getenv("SPORT_TEMPLATE_PATH", "").strip()
    if not raw:
        return None
    target = Path(raw).expanduser()
    if not target.is_absolute() or target.suffix.casefold() != ".xlsx":
        raise HTTPException(503, "SPORT_TEMPLATE_PATH должен быть абсолютным путём к XLSX")
    return target


@app.get("/api/template/status")
def template_status(request: Request):
    current_user(request)
    local = _local_template_path()
    if local and local.is_file():
        return {"available": True, "location": "local", "name": local.name,
                "message": "Постоянство локального файла зависит от диска хостинга"}
    if not GCS_BUCKET:
        return {"available": False, "location": "not_configured",
                "message": ("Настройте SPORT_TEMPLATE_PATH или GCS"
                            if not local else "По SPORT_TEMPLATE_PATH файл пока не найден")}
    try:
        from google.cloud import storage
        blob = storage.Client().bucket(GCS_BUCKET).blob(TEMPLATE_OBJECT)
        return {"available": bool(blob.exists()), "location": "gcs",
                "name": TEMPLATE_OBJECT if blob.exists() else ""}
    except Exception as exc:
        return {"available": False, "location": "error",
                "message": type(exc).__name__}


@app.post("/api/template")
async def upload_template(request: Request, upload: UploadFile = File(...)):
    require_admin(request)
    origin_guard(request)
    local_target = _local_template_path()
    if not local_target and (not GCS_BUCKET or not request.app.state.backup):
        raise HTTPException(503, "Настройте SPORT_TEMPLATE_PATH или постоянный GCS")
    if not str(upload.filename or "").casefold().endswith(".xlsx"):
        raise HTTPException(422, "Требуется XLSX-шаблон")
    try:
        raw = await upload.read(12 * 1024 * 1024 + 1)
    finally:
        await upload.close()
    if not raw or len(raw) > 12 * 1024 * 1024:
        raise HTTPException(422, "Шаблон пуст или превышает 12 МБ")
    from zipfile import ZipFile, BadZipFile
    try:
        with ZipFile(BytesIO(raw)) as zipped:
            parts = zipped.infolist()
            if len(parts) > 600 or sum(x.file_size for x in parts) > 60 * 1024 * 1024:
                raise HTTPException(422, "Подозрительный XLSX")
            if any(x.filename.endswith(("vbaProject.bin", ".exe")) for x in parts):
                raise HTTPException(422, "Шаблон не должен содержать макросов")
        workbook = load_workbook(BytesIO(raw), read_only=False, data_only=False)
        try:
            validate_template(workbook)
        finally:
            workbook.close()
    except (BadZipFile, ValueError, InvalidTemplate) as exc:
        raise HTTPException(422, "Неверный шаблон: " + str(exc)) from exc
    if local_target:
        # Explicitly configured filesystem storage works with any host.
        # Never claim that a filesystem is durable merely because it exists:
        # the hosting operator must mount persistent storage separately.
        temporary = None
        try:
            async with request.app.state.collect_lock:
                local_target.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(
                    mode="wb", dir=local_target.parent, suffix=".xlsx", delete=False,
                ) as saved:
                    temporary = Path(saved.name)
                    saved.write(raw)
                    saved.flush()
                    os.fsync(saved.fileno())
                os.replace(temporary, local_target)
        except OSError as exc:
            raise HTTPException(
                503, "Не удалось сохранить локальный шаблон: " + type(exc).__name__
            ) from exc
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return {
            "uploaded": True, "name": str(upload.filename),
            "bytes": len(raw), "destination": "local",
            "warning": "Без постоянного тома шаблон исчезнет при перезапуске сервера",
        }
    # GCS uses a generation precondition against concurrent replacements.
    try:
        from google.cloud import storage
        from google.api_core.exceptions import NotFound, PreconditionFailed
        blob = storage.Client().bucket(GCS_BUCKET).blob(TEMPLATE_OBJECT)
        try:
            blob.reload()
            generation = blob.generation
        except NotFound:
            generation = 0
        blob.upload_from_string(
            raw,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            if_generation_match=generation,
        )
    except PreconditionFailed as exc:
        raise HTTPException(409, "Шаблон изменился параллельно. Повторите загрузку") from exc
    except Exception as exc:
        raise HTTPException(503, "Не удалось сохранить шаблон: " + type(exc).__name__) from exc
    return {"uploaded": True, "name": str(upload.filename),
            "bytes": len(raw), "destination": TEMPLATE_OBJECT}


def load_template():
    local = _local_template_path()
    if local and local.is_file():
        return load_workbook(local)
    if GCS_BUCKET:
        try:
            from google.cloud import storage
            raw = storage.Client().bucket(GCS_BUCKET).blob(TEMPLATE_OBJECT).download_as_bytes()
            return load_workbook(BytesIO(raw))
        except Exception:
            pass
    raise HTTPException(503, "Загрузите утверждённый Excel-шаблон в SPORT_TEMPLATE_PATH "
                        "или Google Cloud Storage: " + TEMPLATE_OBJECT)


def xlsx_content(data: list[dict]) -> bytes:
    try:
        workbook = build_working_xlsx(load_template(), data)
    except InvalidTemplate as exc:
        raise HTTPException(422, str(exc)) from exc
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


@app.get("/api/export")
def export(request: Request, start: str = "", end: str = ""):
    current_user(request)
    _validate_period(start, end)
    data = event_rows(request.app.state.database, start, end)
    raw = xlsx_content(data)
    filename = "sport_epg_schedule.xlsx"
    return StreamingResponse(
        BytesIO(raw),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="' + filename + '"'},
    )



@app.get("/api/export-archive")
def export_archive(request: Request, start: str = "", end: str = "",
                   channel: str = ""):
    current_user(request)
    _validate_period(start, end)
    from openpyxl import Workbook
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "История трансляций"
    sheet.append([
        "Дата события", "Время события", "Вид спорта", "Турнир",
        "Событие", "Канал", "Начало (Алматы)", "Окончание (Алматы)",
        "Активная запись", "Источник", "Впервые получено", "Последняя версия",
    ])
    for entry in event_rows(
        request.app.state.database, start, end, include_inactive=True
    ):
        for broadcast in entry["broadcasts"]:
            if channel and channel != broadcast["channel"]:
                continue
            sheet.append([
                entry["date"], entry["time"], entry["sport"],
                entry["tournament"], entry["title"], broadcast["channel"],
                datetime.fromisoformat(broadcast["start_at"]).replace(tzinfo=None),
                datetime.fromisoformat(broadcast["end_at"]).replace(tzinfo=None),
                "Да" if broadcast["active"] else "Нет",
                broadcast["source"], broadcast["first_seen_at"],
                broadcast["last_seen_at"],
            ])
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    sheet.column_dimensions["E"].width = 45
    sheet.column_dimensions["F"].width = 24
    for label in ("G", "H"):
        sheet.column_dimensions[label].width = 23
        for row in sheet.iter_rows(min_row=2, min_col=ord(label)-64,
                                   max_col=ord(label)-64):
            row[0].number_format = "DD.MM.YYYY HH:MM"
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return StreamingResponse(
        BytesIO(output.getvalue()),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition":
                 'attachment; filename="sport_epg_archive.xlsx"'},
    )

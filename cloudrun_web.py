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
from parsers.tvplus import tvplus_channel_diagnostics
from services.live_evidence import event_is_live_broadcast, event_is_schedule_candidate
from services.editorial_export import (
    HEADERS as EXPORT_HEADERS, InvalidTemplate, build_working_xlsx,
    validate_template,
)
from services import gmail_integration as gmail
from services import free_apps_script as freebridge
from services import assistant_bridge
from services import ai_pipeline
from services import editorial_store as editorial
from services import editorial_notifications as important_notifications
from services.channel_registry import CHANNELS, IPTVX_CHANNELS
from services.event_text_ru import normalize_event_fields, strip_bookmakers
from services.source_routing import route_for_channel, runtime_source_names
from services.supplier_overlay import apply_supplier_overlay
from services.vsetv_sources import WEB_CHANNEL_IDS, refresh_vsetv_web_sources
from services.iptvx_sources import page_url_for, probe_iptvx_source, refresh_iptvx_sources
from services.event_api_validation import validate_events_with_public_apis
from services.channel_normalization import canonical_channel_name
from services.browser_schedule import browser_fallback_enabled, install_browser_for_python_runtime
from services.epg_excel import (MAX_WORKBOOK_BYTES, InvalidEPG, import_parsed_epg,
                               imported_epg_status, imported_official_epg_status,
                               initialize_epg_imports,
                               parse_epg_xlsx, parse_epg_xlsx_channels,
                               parse_supported_epg_channels, preview_parsed_epg)
from services.schedule_anomalies import find_schedule_anomalies, summarize_anomalies
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
    return strip_bookmakers(str(value or ""))


def channel_name(name: str) -> str:
    return canonical_channel_name(name)


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
            "ORDER BY start_at,channel LIMIT 20000",
            (int(include_inactive),),
        ).fetchall()
    now = datetime.now(KZ_TIMEZONE)
    overrides = editorial.get_editorial(
        database, [row["storage_id"] for row in rows]
    )
    records: list[dict] = []
    for row in rows:
        try:
            raw = json.loads(row["payload_json"])
            patch = overrides.get(row["storage_id"], {})
            cancelled = str(patch.get("cancelled") or "").casefold() == "true"
            if cancelled and not include_inactive:
                continue
            raw.update({key: value for key, value in patch.items()
                        if key not in ("end_time", "cancelled")})
            if patch.get("end_time"):
                raw["estimated_broadcast_end_date"] = raw["date"]
                raw["estimated_broadcast_end"] = patch["end_time"]
                raw["end_estimation_method"] = "explicit"
            if not (event_is_live_broadcast(raw)
                    and event_is_schedule_candidate(raw)
                    and is_user_event(raw)):
                continue
            normalized = normalize_event_fields(
                title=str(raw.get("title") or raw.get("raw_title") or ""),
                sport=str(raw.get("sport") or ""),
                tournament=str(raw.get("tournament") or ""),
            )
            title = normalized["title"]
            sport = normalized["sport"]
            tournament = normalized["tournament"]
            if not title or not sport or EXCLUDE.search(title + " " + tournament):
                continue
            channel = channel_name(raw.get("channel", ""))
            if channel not in ALLOWED_CHANNELS or channel == "QAZSPORT HD" and re.search(
                r"барыс|barys", title, re.I
            ):
                continue
            start, end = get_scheduled_datetimes(raw)
            visible_date = start.date().isoformat()
            if first and visible_date < first:
                continue
            if last and visible_date > last:
                continue
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
                ), "end_known": known_end,
                "active": bool(row["active"]) and not cancelled,
                "editorially_cancelled": cancelled,
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
        records.append(record)

    # Accepted supplier mail is the final authority for a matching
    # channel/fixture. It overrides scraped time/title/tournament in the
    # current schedule without deleting the scraped history. Supplier-only
    # fixtures remain as additions.
    current_records = (
        records if include_inactive else apply_supplier_overlay(records)
    )

    def slot_rank(record: dict) -> tuple:
        source = str(record.get("source") or "")
        supplier = source.startswith("email_epg_")
        official_direct = source in {"qazsport", "sportplus"}
        verified_vsetv = source.startswith("web_vsetv_")
        verified_iptvx = source.startswith("web_iptvx_")
        return (
            int(record["active"]),
            int(supplier),
            int(official_direct),
            int(verified_vsetv),
            int(verified_iptvx),
            int(bool(record.get("tournament"))),
            -len(str(record.get("title") or "")),
        )

    exact: dict[tuple, dict] = {}
    for record in current_records:
        # One linear TV channel cannot carry two different LIVE broadcasts at
        # the exact same minute. Different source wording for the same slot is
        # therefore one broadcast, not two separate cards. Keep archive mode
        # granular so historical source versions remain inspectable.
        if include_inactive:
            key = (
                record["date"], record["time"], record["channel"],
                normalize_match_text(record["title"]),
                normalize_match_text(record["tournament"]),
                normalize_match_text(record["sport"]),
            )
        else:
            key = (
                record["date"],
                record["time"],
                record["channel"],
            )
        old = exact.get(key)
        if old is None or slot_rank(record) > slot_rank(old):
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
        entry["source_record_id"] = chosen["source_record_id"]
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


# Pauses before repeating a startup restore that failed for a temporary
# reason. Three attempts cover the short Apps Script outages seen in practice
# while keeping startup well inside the platform's boot timeout.
STARTUP_RESTORE_RETRY_DELAYS = (2.0, 5.0)


def _restore_drive_snapshot():
    """Restore the Drive backup, retrying only temporary bridge failures.

    The app must not come up with an empty database and look healthy, so a
    restore that keeps failing still stops startup. A short Google-side
    outage, however, no longer crashes the process on the first attempt.
    """
    import logging
    log = logging.getLogger("uvicorn.error")
    for attempt, delay in enumerate((*STARTUP_RESTORE_RETRY_DELAYS, None), 1):
        try:
            return freebridge.FreeDriveSnapshot(DB_PATH)
        except freebridge.FreeDriveError as exc:
            if delay is None or not getattr(exc, "transient", False):
                raise
            log.warning(
                "SLP Drive restore attempt %d failed (%s); retrying in %.0f s",
                attempt, exc, delay,
            )
            time.sleep(delay)


def _prepare_primary_storage():
    """Prefer GCS on Google Cloud while keeping Apps Script as mail transport.

    If the GCS database object is empty on the first Google Cloud boot and the
    legacy Apps Script/Drive bridge is configured, restore the existing Drive
    snapshot once and immediately migrate it into GCS. After that, GCS is the
    authoritative database backup and Apps Script is used only for mail/Excel.
    """
    if GCS_BUCKET:
        bucket = BucketSnapshot(GCS_BUCKET)
        if bucket.generation == 0 and freebridge.enabled():
            drive = _restore_drive_snapshot()
            if not getattr(drive, "initialized_empty", False) and DB_PATH.exists():
                bucket.save()
        return bucket
    if freebridge.enabled():
        return _restore_drive_snapshot()
    return None


def _migrate_template_to_gcs_if_needed() -> None:
    if not (GCS_BUCKET and freebridge.enabled()):
        return
    try:
        from google.cloud import storage
        blob = storage.Client().bucket(GCS_BUCKET).blob(TEMPLATE_OBJECT)
        if blob.exists():
            return
        result = freebridge.ScriptClient().template()
        if not result.get("exists"):
            return
        raw = base64.b64decode(result["data"], validate=True)
        if not raw or len(raw) > 12 * 1024 * 1024:
            return
        if hashlib.sha256(raw).hexdigest() != result.get("sha256"):
            return
        blob.upload_from_string(
            raw,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            if_generation_match=0,
        )
    except Exception:
        import logging
        logging.getLogger("uvicorn.error").exception(
            "SLP template migration Drive -> GCS failed"
        )


@asynccontextmanager
async def lifespan(application: FastAPI):
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    application.state.backup = _prepare_primary_storage()
    application.state.free_mail_bridge = freebridge.enabled()
    _migrate_template_to_gcs_if_needed()
    database = SLPDatabase(DB_PATH)
    initialize_epg_imports(database)
    gmail.init_gmail_schema(database)
    editorial.init_editorial(database)
    important_notifications.init_notifications(database)
    application.state.database = database
    application.state.schedule = ScheduleService(
        RuntimeParserOrchestrator(database=database)
    )
    application.state.collect_lock = asyncio.Lock()
    application.state.collect_task = None
    application.state.collect_progress = {
        "running": False,
        "phase": "idle",
        "message": "Расписание готово к обновлению",
        "detail": (
            "Нажмите «Собрать расписание», чтобы проверить сайты, API и почту. "
            f"Событий в базе: {database.active_event_count()}"
        ),
        "updated_at": datetime.now(KZ_TIMEZONE).isoformat(timespec="seconds"),
    }
    browser_install_task = None
    if browser_fallback_enabled():
        browser_install_task = asyncio.create_task(
            asyncio.to_thread(install_browser_for_python_runtime)
        )

    async def initial_drive_pull():
        # Apps Script archives while Render sleeps. Resume the archive
        # automatically when the free instance wakes, even without an open UI.
        async with application.state.collect_lock:
            try:
                result = await asyncio.to_thread(
                    freebridge.sync_inbox, database, allow_auto_import=False,
                )
                if result["new_attachments"] or result["requires_review"]:
                    await asyncio.to_thread(application.state.backup.save)
                import logging
                logging.getLogger("uvicorn.error").info(
                    "SLP free Drive pull: new=%d, auto=%d, review=%d",
                    result["new_attachments"],
                    result["auto_imported"],
                    result["requires_review"],
                )
            except Exception:
                # Stay accessible so the owner can repair the connection,
                # retry using the UI, and inspect logs. Never delete the
                # existing sqlite DB on a failed fetch.
                import logging
                logging.getLogger("uvicorn.error").exception(
                    "SLP free Drive initial pull failed"
                )

    free_mail_task = (
        asyncio.create_task(initial_drive_pull())
        if application.state.free_mail_bridge else None
    )
    yield
    if browser_install_task is not None:
        await browser_install_task
    if free_mail_task is not None and free_mail_task.done():
        try:
            free_mail_task.result()
        except Exception:
            pass


app = FastAPI(title="SLP Sport EPG Web", docs_url=None, redoc_url=None, lifespan=lifespan)


@app.get("/health")
@app.get("/healthz", include_in_schema=False)
def health(request: Request):
    database = request.app.state.database
    return {"status": "ok", "runtime": "web", "telegram_polling": False,
            "events": database.active_event_count(),
            "durable_storage": bool(request.app.state.backup)}


@app.get("/health/iptvx")
async def health_iptvx(request: Request):
    """Aggregate-only connectivity probe; exposes no user or mailbox data."""
    return await probe_iptvx_source()


@app.get("/health/iptvx/storage")
def health_iptvx_storage(request: Request):
    """Aggregate-only proof that direct iptvX rows reached production SQLite."""
    database = request.app.state.database
    today = datetime.now(KZ_TIMEZONE).date()
    first = (today - timedelta(days=1)).isoformat()
    last = (today + timedelta(days=8)).isoformat()
    expected = {
        channel: "web_iptvx_" + page_id
        for channel, page_id in IPTVX_CHANNELS.items()
    }
    with database._connect() as conn:
        rows = conn.execute(
            "SELECT source,channel,COUNT(*) AS n FROM events "
            "WHERE active=1 AND event_date>=? AND event_date<=? "
            "AND source LIKE 'web_iptvx_%' "
            "GROUP BY source,channel",
            (first, last),
        ).fetchall()
    counts = {
        (str(row["source"]), canonical_channel_name(str(row["channel"]))):
        int(row["n"])
        for row in rows
    }
    channels = {
        channel: counts.get((source, channel), 0)
        for channel, source in expected.items()
    }
    ready = sum(1 for value in channels.values() if value > 0)
    return {
        "status": "ok" if ready == len(expected) else "partial",
        "ready_channels": ready,
        "expected_channels": len(expected),
        "stored_current_window": sum(channels.values()),
        "channels": channels,
    }


@app.get("/api/migration/snapshot")
async def migration_snapshot(request: Request):
    """One-time Render -> Google Cloud database transfer.

    Disabled unless SPORT_ENABLE_GCP_MIGRATION=true. Access requires a Google
    OIDC token issued to the configured Google Cloud deployer service account.
    The response is a gzip-compressed SQLite online backup; no environment
    secrets are returned.
    """
    if os.getenv("SPORT_ENABLE_GCP_MIGRATION", "").casefold() not in {
        "1", "true", "yes",
    }:
        raise HTTPException(404, "Migration endpoint disabled")
    allowed = os.getenv("SPORT_MIGRATION_SERVICE_ACCOUNT", "").strip()
    audience = PUBLIC_URL + "/api/migration/snapshot"
    if not allowed or not PUBLIC_URL:
        raise HTTPException(503, "Migration identity is not configured")
    authorization = request.headers.get("authorization", "")
    if not authorization.startswith("Bearer ") or len(authorization) > 8192:
        raise HTTPException(401, "Google OIDC token required")
    try:
        from google.oauth2 import id_token
        from google.auth.transport.requests import Request as GoogleRequest
        claims = await asyncio.to_thread(
            id_token.verify_oauth2_token,
            authorization[7:],
            GoogleRequest(),
            audience,
        )
    except Exception:
        raise HTTPException(401, "Google OIDC token rejected") from None
    if (
        str(claims.get("email") or "").casefold() != allowed.casefold()
        or not claims.get("email_verified", False)
    ):
        raise HTTPException(403, "Unknown migration identity")

    import gzip
    with tempfile.NamedTemporaryFile(
        dir=DB_PATH.parent, suffix=".db", delete=False,
    ) as tmp:
        snapshot_path = Path(tmp.name)
    try:
        source = sqlite3.connect(DB_PATH)
        target = sqlite3.connect(snapshot_path)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        with sqlite3.connect(snapshot_path) as checked:
            quick = checked.execute("PRAGMA quick_check").fetchone()
        if not quick or quick[0] != "ok":
            raise HTTPException(500, "SQLite snapshot failed integrity check")
        raw = await asyncio.to_thread(snapshot_path.read_bytes)
        packed = await asyncio.to_thread(gzip.compress, raw, 6)
        return Response(
            content=packed,
            media_type="application/gzip",
            headers={
                "Cache-Control": "no-store",
                "Content-Disposition": 'attachment; filename="slp-render.db.gz"',
                "X-SLP-SHA256": hashlib.sha256(raw).hexdigest(),
            },
        )
    finally:
        snapshot_path.unlink(missing_ok=True)


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
        "logo.png": "image/png",
        "favicon-32.png": "image/png",
        "apple-touch-icon.png": "image/png",
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
    return FileResponse(ROOT / "cloudrun_ui" / "assets" / "favicon.ico",
                        media_type="image/x-icon")


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


def schedule_anomalies(database: SLPDatabase, start: str = "", end: str = "") -> dict:
    """Self-check of the current schedule; read-only, never edits events.

    Without an explicit period the check starts yesterday and has no upper
    bound, so a row that landed months away by mistake is still inspected.
    """
    now = datetime.now(KZ_TIMEZONE)
    first = start or (now.date() - timedelta(days=1)).isoformat()
    rows = event_rows(database, first, end)
    findings = find_schedule_anomalies(rows, now=now)
    return {
        "anomalies": findings,
        "summary": summarize_anomalies(findings),
        "checked_events": len(rows),
        "period": {"start": first, "end": end},
        "checked_at": now.isoformat(timespec="seconds"),
        "timezone": "Asia/Almaty",
    }


@app.get("/api/anomalies")
def anomalies(request: Request, start: str = "", end: str = ""):
    current_user(request)
    _validate_period(start, end)
    return schedule_anomalies(request.app.state.database, start, end)


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
    provider_diagnostics = tvplus_channel_diagnostics()
    # Parsing and supplier mail are separate signals. A supplier XLSX must not
    # make a failed website/API look healthy in the source panel.
    with database._connect() as conn:
        active_counts = conn.execute(
            "SELECT source,channel,COUNT(*) AS n FROM events "
            "WHERE active=1 AND is_live=1 AND source NOT LIKE 'email_epg_%' "
            "AND event_date>=? AND event_date<=? GROUP BY source,channel",
            (today.isoformat(), (today + timedelta(days=6)).isoformat()),
        ).fetchall()
    by_channel = {}
    for row in active_counts:
        label = channel_name(row["channel"])
        by_channel[label] = by_channel.get(label, 0) + int(row["n"])

    def human_error(value: str) -> str:
        text = " ".join(str(value or "").split())
        replacements = {
            "TimeoutError": "тайм-аут соединения",
            "TV+/Mobikino EPG returned no events": "TV+/Mobikino не вернул события",
            "scheduleId не найден": "провайдер не вернул идентификатор телепрограммы",
            "отсутствует pagesWithEvents": "телепрограмма на эту дату ещё не опубликована",
        }
        for source, target in replacements.items():
            text = text.replace(source, target)
        return text[:260]

    websites = []
    for channel in CHANNELS:
        sources = list(runtime_source_names(channel))
        route = route_for_channel(channel.name)
        runs = [source_runs[name] for name in sources if name in source_runs]
        newest = max(
            runs, key=lambda item: str(item.get("created_at") or ""),
            default=None,
        )
        count = by_channel.get(channel.name, 0)
        diagnostic = provider_diagnostics.get(channel.tvplus_name, {})
        errors = [human_error(value) for value in diagnostic.get("errors", []) if value]
        if newest and newest.get("error"):
            message = human_error(newest.get("error"))
            if message and message not in errors:
                errors.append(message)
        newest_status = str((newest or {}).get("status") or "not_checked")
        if count:
            status = "warning" if errors or newest_status in {"warning", "blocked", "error"} else "ok"
        elif errors or newest_status in {"blocked", "error", "unavailable"}:
            status = "error"
        elif newest_status == "warning":
            status = "warning"
        else:
            status = newest_status
        websites.append({
            "channel": channel.name, "kind": "website",
            "status": status,
            "checked_at": (newest or {}).get("created_at"),
            "event_count": count,
            "error_reason": "; ".join(errors[:3]),
            "official_excel": official_files.get(channel.name),
            "guide_sources": sources,
            "official_site": channel.official_site,
            "secondary_guide": channel.secondary_guide,
            "iptvx_page": page_url_for(channel.name),
            "provider_channel_id": channel.tvplus_id or None,
            "source_verified": bool(count),
            "supplier_expected": bool(channel.supplier_source),
            "primary_transport": route.primary_transport,
            "fallback_transports": list(route.fallback_transports),
            "confirmation_transports": list(route.confirmation_transports),
            "agent_reach_role": route.agent_reach_role,
            "agent_reach_enabled": route.uses_agent_reach,
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
        ], "gmail_connected": (gmail.status(database)["connected"]
                                      or getattr(request.app.state, "free_mail_bridge", False)),
        "ai": ai_pipeline.status(),
        "auto_import": False,
        "note": ("Почта работает в режиме редакторского подтверждения: "
                 "новые Excel попадают в уведомления и изменяют расписание "
                 "только после нажатия «Добавить в расписание»."
                 if (gmail.status(database)["connected"]
                     or getattr(request.app.state, "free_mail_bridge", False))
                 else "Для получения расписаний с почты нужно подключить Gmail/Drive."),
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
    """Only high-signal, persistent editorial notices belong in this inbox."""
    return important_notifications.list_notifications(database, limit=limit)


@app.get("/api/notifications")
def notifications(request: Request, limit: int = 80):
    current_user(request)
    items = _notification_items(
        request.app.state.database, min(max(limit, 1), 150)
    )
    return {
        "notifications": items,
        "unread_count": sum(1 for item in items if not item.get("read")),
    }


@app.post("/api/notifications/{notice_id}/read")
async def read_notification(request: Request, notice_id: int):
    require_editor(request)
    origin_guard(request)
    async with request.app.state.collect_lock:
        item = important_notifications.mark_read(
            request.app.state.database, notice_id
        )
        if item is None:
            raise HTTPException(404, "Уведомление не найдено")
        await _save_state(request)
    return item


@app.post("/api/notifications/{notice_id}/apply")
async def apply_notification(request: Request, notice_id: int):
    user = require_editor(request)
    origin_guard(request)
    async with request.app.state.collect_lock:
        item = important_notifications.get_notification(
            request.app.state.database, notice_id
        )
        if item is None:
            raise HTTPException(404, "Уведомление не найдено")
        if item.get("status") == "applied":
            return item
        patch = item.get("patch") or {}
        storage_id = str(item.get("storage_id") or "")
        if not patch or not storage_id:
            important_notifications.mark_read(
                request.app.state.database, notice_id
            )
            await _save_state(request)
            raise HTTPException(
                409, "Для этого уведомления нет автоматического изменения"
            )
        evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
        storage_ids = [storage_id]
        for candidate_id in evidence.get("storage_ids") or []:
            candidate_id = str(candidate_id or "")
            if candidate_id and candidate_id not in storage_ids:
                storage_ids.append(candidate_id)
        try:
            for target_id in storage_ids:
                editorial.apply_edit(
                    request.app.state.database,
                    storage_id=target_id,
                    values=patch,
                    username=user["username"],
                )
        except editorial.EditorialError as exc:
            raise HTTPException(422, str(exc)) from exc
        result = important_notifications.mark_applied(
            request.app.state.database, notice_id
        )
        await _save_state(request)
    return result


@app.get("/api/gmail/status")
def gmail_status(request: Request):
    current_user(request)
    result = gmail.status(request.app.state.database)
    result["auto_import"] = False
    if request.app.state.free_mail_bridge:
        # Apps Script is authorized by the owner, not Cloud Run Gmail OAuth.
        verified = freebridge.health(request.app.state.database)
        # Sending is enabled only by a signed capabilities answer from the
        # deployed script, re-checked when the stored answer is stale.
        sending = freebridge.send_status(request.app.state.database)
        if sending["stale"]:
            sending = freebridge.refresh_send_capabilities(
                request.app.state.database
            )
        send_enabled = bool(sending["send_enabled"] and request.app.state.backup)
        send_reason = "" if send_enabled else (
            sending["reason"] if not sending["send_enabled"]
            else "Отправка требует постоянного хранилища журнала"
        )
        result.update({
            "configured": True, "connected": verified["verified"],
            "email": gmail.OWNER_ACCOUNT, "send_enabled": send_enabled,
            "send_reason": send_reason or sending["reason"],
            "send_targets": sending["sendable"],
            "send_checked_at": sending["checked_at"],
            "capabilities": {
                "read_mail": bool(verified["verified"]),
                "send_request": bool(sending["supported"]),
            },
            "bridge_mode": True, "mail_mode": sending["mode"],
            "last_sync_at": verified["last_pull_at"],
            "script_last_scan_at": verified["script_last_scan_at"],
            "archiver_active": verified["archiver_active"],
        })
    result["ai"] = ai_pipeline.status()
    return result


@app.get("/api/gmail/connect")
async def gmail_connect(request: Request):
    user = require_admin(request)
    if request.app.state.free_mail_bridge:
        raise HTTPException(
            409, "Gmail уже подключён через бесплатный Apps Script и Drive"
        )
    if not request.app.state.backup:
        raise HTTPException(503, "Сначала подключите постоянное хранилище")
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
            if request.app.state.free_mail_bridge:
                result = await asyncio.to_thread(
                    freebridge.sync_inbox, request.app.state.database,
                    allow_auto_import=False, refresh_archive=True
                )
            else:
                result = await asyncio.to_thread(
                    gmail.sync_inbox, request.app.state.database,
                    allow_auto_import=False
                )
        except freebridge.FreeDriveError as exc:
            raise HTTPException(502, str(exc)) from exc
        except (gmail.GmailTransportError, gmail.GmailNotConfigured) as exc:
            raise _gmail_failure(exc) from exc
        # Only existing SLP editorial notices are sent; never mail bodies or Excel.
        bridge = (await asyncio.to_thread(
            assistant_bridge.deliver_pending, request.app.state.database
        ) if request.app.state.backup else
            {"enabled": False, "delivered": 0, "failed": 0})
        if (result["new_attachments"] or result["requires_review"]
                or result.get("cursor_advanced") or bridge["delivered"]):
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
            if request.app.state.free_mail_bridge:
                result = await asyncio.to_thread(
                    freebridge.sync_inbox,
                    request.app.state.database,
                    allow_auto_import=False,
                    refresh_archive=True,
                )
            else:
                result = await asyncio.to_thread(
                    gmail.sync_inbox,
                    request.app.state.database,
                    allow_auto_import=False,
                )
        except freebridge.FreeDriveError as exc:
            raise HTTPException(502, str(exc)) from exc
        except (gmail.GmailTransportError, gmail.GmailNotConfigured) as exc:
            raise _gmail_failure(exc) from exc
        # Only existing SLP editorial notices are sent; never mail bodies or Excel.
        bridge = (await asyncio.to_thread(
            assistant_bridge.deliver_pending, request.app.state.database
        ) if request.app.state.backup else
            {"enabled": False, "delivered": 0, "failed": 0})
        if (result["new_attachments"] or result["requires_review"]
                or result.get("cursor_advanced") or bridge["delivered"]):
            await _save_state(request)
        result["assistant_notifications"] = bridge
    return result


@app.post("/api/jobs/iptvx-refresh")
async def scheduled_iptvx_refresh(request: Request):
    """Authenticated lightweight refresh for the 14 direct iptvX pages."""
    if not request.app.state.backup:
        raise HTTPException(503, "Для фонового обновления нужен постоянный GCS")
    allowed = os.getenv("SPORT_SCHEDULER_SERVICE_ACCOUNT", "").strip()
    if not allowed or not PUBLIC_URL:
        raise HTTPException(503, "Cloud Scheduler ещё не настроен")
    authorization = request.headers.get("authorization", "")
    if not authorization.startswith("Bearer ") or len(authorization) > 8192:
        raise HTTPException(401, "Требуется OIDC-токен Cloud Scheduler")
    audience = PUBLIC_URL + "/api/jobs/iptvx-refresh"
    try:
        from google.oauth2 import id_token
        from google.auth.transport.requests import Request as GoogleRequest
        claims = await asyncio.to_thread(
            id_token.verify_oauth2_token,
            authorization[7:],
            GoogleRequest(),
            audience,
        )
    except Exception:
        raise HTTPException(401, "OIDC-токен не прошёл проверку") from None
    if not claims.get("email_verified") or (
        str(claims.get("email") or "").casefold() != allowed.casefold()
    ):
        raise HTTPException(403, "Неизвестный сервисный аккаунт")
    async with request.app.state.collect_lock:
        result = await refresh_iptvx_sources(request.app.state.database)
        await _save_state(request)
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
        results = {"gmail": None, "websites": None, "iptvx": None, "errors": []}
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
        try:
            results["iptvx"] = await refresh_iptvx_sources(
                request.app.state.database
            )
        except Exception as exc:
            results["errors"].append("iptvx: " + type(exc).__name__)
        if (request.app.state.free_mail_bridge
                or gmail.status(request.app.state.database)["connected"]):
            try:
                if request.app.state.free_mail_bridge:
                    results["gmail"] = await asyncio.to_thread(
                        freebridge.sync_inbox,
                        request.app.state.database,
                        allow_auto_import=False,
                        refresh_archive=True,
                    )
                else:
                    results["gmail"] = await asyncio.to_thread(
                        gmail.sync_inbox,
                        request.app.state.database,
                        allow_auto_import=False,
                    )
            except freebridge.FreeDriveError as exc:
                results["errors"].append("gmail_bridge: " + type(exc).__name__)
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


async def _send_request_via_bridge(request: Request, options: "MailRequest",
                                   user: dict) -> dict:
    """Schedule request through the Apps Script bridge (no Gmail OAuth)."""
    database = request.app.state.database
    category = str(options.category or "")[:16]
    if category not in freebridge.SEND_CATEGORIES:
        raise HTTPException(422, "Неверный тип запроса")
    if not request.app.state.backup:
        raise HTTPException(503, "Отправка требует постоянного хранилища журнала")
    sending = await asyncio.to_thread(freebridge.send_status, database)
    if sending["stale"]:
        sending = await asyncio.to_thread(
            freebridge.refresh_send_capabilities, database
        )
    if sending["mode"] == "production" and user["role"] != "admin":
        raise HTTPException(403, "Отправка запрещена для текущего пользователя")
    if not sending["send_enabled"]:
        raise HTTPException(503, sending["reason"] or "Отправка не настроена")
    async with request.app.state.collect_lock:
        try:
            result = await asyncio.to_thread(
                freebridge.send_schedule_request, database,
                username=user["username"], category=category,
                period_start=str(options.period_start or "")[:10],
                period_end=str(options.period_end or "")[:10],
            )
        except freebridge.FreeDriveError as exc:
            message = str(exc)
            code = (422 if message.startswith(("Неверный", "Некорректный"))
                    else 409 if "уже" in message else 502)
            raise HTTPException(code, message) from exc
        await _save_state(request)
    return result


@app.post("/api/gmail/request")
async def send_test_request(request: Request, options: MailRequest):
    user = require_editor(request)
    origin_guard(request)
    if request.app.state.free_mail_bridge:
        return await _send_request_via_bridge(request, options, user)
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


def _set_collect_progress(
    application: FastAPI,
    *,
    phase: str,
    message: str,
    detail: str = "",
    running: bool = True,
    result: dict | None = None,
) -> None:
    payload = {
        "running": running,
        "phase": phase,
        "message": message,
        "detail": detail,
        "updated_at": datetime.now(KZ_TIMEZONE).isoformat(timespec="seconds"),
    }
    if result is not None:
        payload["result"] = result
    application.state.collect_progress = payload


def _current_week_bounds() -> tuple[date, date]:
    today = datetime.now(KZ_TIMEZONE).date()
    first = today - timedelta(days=today.weekday())
    return first, first + timedelta(days=6)


def _collection_error_code(message: str) -> int:
    rules = (
        ("Основные сайты", 101),
        ("Дополнительные телегиды", 102),
        ("Почта/Drive", 201),
        ("Почта", 202),
        ("Проверка спортивных API", 301),
        ("Сохранение", 401),
        ("Итоговая сетка", 402),
        ("Внутренняя ошибка", 500),
    )
    for prefix, code in rules:
        if str(message or "").startswith(prefix):
            return code
    return 599


async def _run_collection(
    application: FastAPI, *, apply_supplier_mail: bool = False,
) -> dict:
    """One collection run, in a fixed order.

    1. Open sources: channel sites, APIs and TV guides.
    2. Mail: supplier spreadsheets. With ``apply_supplier_mail`` they replace
       the open-source rows for their channel and days, because the supplier
       table is the more current statement of the schedule.
    3. Check of the resulting schedule against public sports data.

    Only the registered channels reach the schedule (``ALLOWED_CHANNELS``).
    """
    database = application.state.database
    result = {
        "ok": True,
        "errors": [],
        "source_warnings": [],
        "official_sources": None,
        "vsetv": None,
        "iptvx": None,
        "event_api_validation": None,
        "important_notifications": 0,
        "mail": None,
        "mail_applied": None,
    }

    try:
        async with application.state.collect_lock:
            _set_collect_progress(
                application,
                phase="parsers",
                message="Собираю расписание, подождите немного",
                detail="Основные сайты и API спортивных каналов",
            )
            try:
                refreshed = await application.state.schedule.refresh()
                result["official_sources"] = {
                    "run_id": getattr(refreshed, "run_id", ""),
                    "event_count": database.active_event_count(),
                }
                latest = database.latest_agent_run() or {}
                summary = latest.get("summary") or {}
                result["source_warnings"].extend(
                    summary.get("source_errors") or []
                )
                result["source_warnings"].extend(
                    summary.get("source_warnings") or []
                )
            except Exception as exc:
                result["errors"].append(
                    "Основные сайты: " + type(exc).__name__
                )

            _set_collect_progress(
                application,
                phase="fallbacks",
                message="Уже почти собрал",
                detail="Проверяю VseTV, iptvX и резервные подтверждения",
            )
            try:
                result["vsetv"] = await refresh_vsetv_web_sources(database)
            except Exception as exc:
                result["errors"].append(
                    "Дополнительные телегиды: " + type(exc).__name__
                )

            try:
                result["iptvx"] = await refresh_iptvx_sources(database)
            except Exception as exc:
                result["source_warnings"].append(
                    "iptvX: " + type(exc).__name__
                )

            _set_collect_progress(
                application,
                phase="mail",
                message="Наврал - ещё собираю",
                detail="Проверяю новые письма и Excel поставщиков",
            )
            mail_available = (
                application.state.free_mail_bridge
                or gmail.status(database)["connected"]
            )
            if mail_available:
                try:
                    if application.state.free_mail_bridge:
                        result["mail"] = await asyncio.to_thread(
                            freebridge.sync_inbox,
                            database,
                            allow_auto_import=apply_supplier_mail,
                            refresh_archive=True,
                        )
                    else:
                        result["mail"] = await asyncio.to_thread(
                            gmail.sync_inbox,
                            database,
                            allow_auto_import=apply_supplier_mail,
                        )
                    if apply_supplier_mail:
                        # Files stored earlier by the background scan are
                        # applied here too; unclear ones stay for review.
                        result["mail_applied"] = await asyncio.to_thread(
                            gmail.auto_apply_pending, database
                        )
                except freebridge.FreeDriveError as exc:
                    result["errors"].append(
                        "Почта/Drive: " + str(exc)[:160]
                    )
                except (
                    gmail.GmailTransportError,
                    gmail.GmailNotConfigured,
                ) as exc:
                    result["errors"].append(
                        "Почта: " + str(exc)[:160]
                    )
            else:
                result["source_warnings"].append(
                    "Почта не подключена: новые Excel не проверялись"
                )

            _set_collect_progress(
                application,
                phase="validation",
                message="Проверяю, не наврали ли источники",
                detail="Сверяю уже собранную сетку с внешними спортивными данными",
            )
            try:
                today = datetime.now(KZ_TIMEZONE).date()
                validation_end = today + timedelta(days=6)
                candidates = event_rows(
                    database, today.isoformat(), validation_end.isoformat()
                )
                validation = await validate_events_with_public_apis(candidates)
                result["event_api_validation"] = validation
                result["important_notifications"] = (
                    important_notifications.record_validation_notifications(
                        database, validation
                    )
                )
            except Exception as exc:
                result["source_warnings"].append(
                    "Проверка спортивных API: " + type(exc).__name__
                )

            try:
                result["schedule_anomalies"] = schedule_anomalies(
                    database
                )["summary"]
            except Exception as exc:
                result["source_warnings"].append(
                    "Проверка аномалий: " + type(exc).__name__
                )

            _set_collect_progress(
                application,
                phase="merge",
                message="Вот-вот",
                detail="Собираю финальную сетку и сохраняю изменения",
            )
            if application.state.backup:
                try:
                    await asyncio.to_thread(application.state.backup.save)
                except Exception as exc:
                    result["errors"].append(
                        "Сохранение: " + type(exc).__name__
                    )
    except Exception as exc:
        result["errors"].append(
            "Внутренняя ошибка сбора: " + type(exc).__name__
        )

    result["ok"] = not bool(result["errors"])
    try:
        result["event_count"] = len(event_rows(database, "", ""))
        week_first, week_last = _current_week_bounds()
        result["week_event_count"] = len(event_rows(
            database, week_first.isoformat(), week_last.isoformat()
        ))
    except Exception as exc:
        result["event_count"] = database.active_event_count()
        result["week_event_count"] = result["event_count"]
        result["errors"].append(
            "Итоговая сетка: " + type(exc).__name__
        )
        result["ok"] = False

    result["durable_storage"] = bool(application.state.backup)
    result["last_run"] = database.latest_agent_run()
    pending_notices = gmail.list_notices(database, limit=200)
    result["pending_mail_channels"] = sorted({
        item["detected_channel"]
        for item in pending_notices
        if item["status"] == "pending" and item["detected_channel"]
    })

    if result["ok"]:
        result["summary_message"] = (
            f"Расписание собрано: {result['week_event_count']} "
            "событий на этой неделе"
        )
        final_detail = _mail_applied_detail(result)
    else:
        first_error = result["errors"][0] if result["errors"] else "Неизвестная ошибка"
        code = _collection_error_code(first_error)
        result["summary_message"] = f"Ошибка #{code}: {first_error}"
        extra = result["errors"][1:]
        final_detail = "; ".join(extra[:3])

    _set_collect_progress(
        application,
        phase="done" if result["ok"] else "done_with_errors",
        message=result["summary_message"],
        detail=final_detail,
        running=False,
        result=result,
    )
    return result


def _mail_applied_detail(result: dict) -> str:
    """One line on what supplier mail changed during this collection."""
    fresh = int((result.get("mail") or {}).get("auto_imported") or 0)
    stored = result.get("mail_applied") or {}
    applied = fresh + int(stored.get("applied") or 0)
    waiting = len(result.get("pending_mail_channels") or [])
    parts = []
    if applied:
        parts.append(f"Из почты применено таблиц поставщиков: {applied}")
    if waiting:
        parts.append(
            "Ждут проверки в почте: "
            + ", ".join(result["pending_mail_channels"])
        )
    return ". ".join(parts)


@app.get("/api/collect/status")
def collect_status(request: Request):
    current_user(request)
    return dict(getattr(request.app.state, "collect_progress", {
        "running": False,
        "phase": "idle",
        "message": "Расписание готово к обновлению",
        "detail": "Нажмите «Собрать расписание», чтобы проверить сайты, API и почту",
    }))


@app.post("/api/collect", status_code=202)
async def collect(request: Request, options: CollectOptions):
    user = current_user(request)
    origin_guard(request)
    existing = getattr(request.app.state, "collect_task", None)
    if existing is not None and not existing.done():
        return {
            "accepted": True,
            "already_running": True,
        }

    _set_collect_progress(
        request.app,
        phase="start",
        message="Собираю расписание, подождите немного",
        detail="Сайты, API, резервные телегиды и почта",
        running=True,
    )
    request.app.state.collect_task = asyncio.create_task(
        _run_collection(
            request.app,
            # Replacing schedule rows is an editorial action: a viewer may
            # start a collection, but supplier tables are applied only when
            # an editor or administrator starts it.
            apply_supplier_mail=user["role"] in ("editor", "admin"),
        )
    )
    return {
        "accepted": True,
        "already_running": False,
    }



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
    if GCS_BUCKET:
        try:
            from google.cloud import storage
            blob = storage.Client().bucket(GCS_BUCKET).blob(TEMPLATE_OBJECT)
            return {
                "available": bool(blob.exists()),
                "location": "gcs",
                "name": TEMPLATE_OBJECT if blob.exists() else "",
                "message": "Шаблон хранится в Google Cloud Storage",
            }
        except Exception as exc:
            return {"available": False, "location": "error",
                    "message": type(exc).__name__}
    if request.app.state.free_mail_bridge:
        try:
            result = freebridge.ScriptClient().template()
            return {
                "available": bool(result.get("exists")),
                "location": "private_drive",
                "name": "slp_approved_template.xlsx" if result.get("exists") else "",
                "message": "Шаблон хранится в личном Google Drive",
            }
        except freebridge.FreeDriveError as exc:
            return {"available": False, "location": "error",
                    "message": str(exc)[:180]}
    return {"available": False, "location": "not_configured",
            "message": ("Настройте SPORT_TEMPLATE_PATH или постоянное хранилище"
                        if not local else "По SPORT_TEMPLATE_PATH файл пока не найден")}


@app.post("/api/template")
async def upload_template(request: Request, upload: UploadFile = File(...)):
    require_admin(request)
    origin_guard(request)
    local_target = _local_template_path()
    if (not local_target and not GCS_BUCKET
            and not request.app.state.free_mail_bridge):
        raise HTTPException(503, "Настройте постоянное хранилище или локальный путь")
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
        _remember_template(raw)
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
    if request.app.state.free_mail_bridge and not GCS_BUCKET:
        try:
            async with request.app.state.collect_lock:
                client = freebridge.ScriptClient()
                current = await asyncio.to_thread(client.template)
                if hashlib.sha256(raw).hexdigest() != current.get("sha256"):
                    await asyncio.to_thread(
                        client.save_template, raw,
                        str(current.get("sha256") or ""),
                    )
            return {
                "uploaded": True, "name": str(upload.filename),
                "bytes": len(raw), "destination": "private_drive",
            }
        except freebridge.FreeDriveError as exc:
            raise HTTPException(503, str(exc)) from exc
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


# Last approved template successfully read from remote storage in this
# process. A storage outage must not stop the editor from downloading the
# schedule, so the previous good copy is reused until storage answers again.
_TEMPLATE_CACHE: dict = {}


def _remember_template(raw: bytes) -> None:
    _TEMPLATE_CACHE["raw"] = raw
    _TEMPLATE_CACHE["sha256"] = hashlib.sha256(raw).hexdigest()


def _builtin_template():
    """Plain 25-column workbook used only when no approved template is reachable."""
    from openpyxl import Workbook
    from openpyxl.styles import Font
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "EPG"
    sheet.append(list(EXPORT_HEADERS))
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    sheet.freeze_panes = "A2"
    for index in range(1, len(EXPORT_HEADERS) + 1):
        sheet.column_dimensions[
            sheet.cell(1, index).column_letter
        ].width = 18
    return workbook


def _load_template_with_origin():
    """Return (workbook, origin); origin is approved | cached."""
    local = _local_template_path()
    if local and local.is_file():
        return load_workbook(local), "approved"
    problem = ""
    if GCS_BUCKET:
        try:
            from google.cloud import storage
            raw = storage.Client().bucket(GCS_BUCKET).blob(
                TEMPLATE_OBJECT
            ).download_as_bytes()
            workbook = load_workbook(BytesIO(raw))
            _remember_template(raw)
            return workbook, "approved"
        except Exception as exc:
            problem = "GCS: " + type(exc).__name__
    if freebridge.enabled():
        try:
            result = freebridge.ScriptClient().template()
            if not result.get("exists"):
                raise HTTPException(503, "Сначала загрузи утверждённый XLSX-шаблон")
            try:
                raw = base64.b64decode(result.get("data") or "", validate=True)
            except (ValueError, TypeError) as exc:
                raise HTTPException(503, "XLSX-шаблон в Drive повреждён") from exc
            if (not raw or len(raw) > 6 * 1024 * 1024
                    or hashlib.sha256(raw).hexdigest() != result.get("sha256")):
                raise HTTPException(503, "XLSX-шаблон в Drive повреждён")
            workbook = load_workbook(BytesIO(raw))
            _remember_template(raw)
            return workbook, "approved"
        except freebridge.FreeDriveError as exc:
            problem = "Drive: " + str(exc)
        except HTTPException as exc:
            problem = str(exc.detail)
    if _TEMPLATE_CACHE.get("raw"):
        return load_workbook(BytesIO(_TEMPLATE_CACHE["raw"])), "cached"
    raise HTTPException(503, problem or (
        "Загрузите утверждённый Excel-шаблон в SPORT_TEMPLATE_PATH "
        "или Google Cloud Storage: " + TEMPLATE_OBJECT
    ))


def load_template():
    return _load_template_with_origin()[0]


def xlsx_export(data: list[dict]) -> tuple[bytes, str, str]:
    """Build XLSX and report approved, cached or builtin template origin."""
    reason = ""
    try:
        template, origin = _load_template_with_origin()
    except HTTPException as exc:
        if exc.status_code != 503:
            raise
        template, origin, reason = _builtin_template(), "builtin", str(exc.detail)
    try:
        workbook = build_working_xlsx(template, data)
    except InvalidTemplate as exc:
        raise HTTPException(422, str(exc)) from exc
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue(), origin, reason


def xlsx_content(data: list[dict]) -> bytes:
    return xlsx_export(data)[0]


@app.get("/api/export")
def export(request: Request, start: str = "", end: str = ""):
    current_user(request)
    _validate_period(start, end)
    data = event_rows(request.app.state.database, start, end)
    raw, origin, reason = xlsx_export(data)
    if reason:
        logging.getLogger("uvicorn.error").warning(
            "SLP export used %s template: %s", origin, reason[:200]
        )
    filename = "sport_epg_schedule.xlsx"
    return StreamingResponse(
        BytesIO(raw),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": 'attachment; filename="' + filename + '"',
            "X-SLP-Template": origin,
            "Cache-Control": "no-store",
        },
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

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
from fastapi.responses import FileResponse, StreamingResponse
from openpyxl import load_workbook
from pydantic import BaseModel

from agents.runtime_orchestrator import RuntimeParserOrchestrator
from services.live_evidence import event_is_live_broadcast, event_is_schedule_candidate
from services.epg_excel import (MAX_WORKBOOK_BYTES, InvalidEPG, import_parsed_epg,
                               imported_epg_status, initialize_epg_imports,
                               parse_epg_xlsx)
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
Q_SETANTA = ("Q LEAGUE", "Q ARENA", "Q FOOTBALL",
             "SETANTA SPORTS 1", "SETANTA SPORTS 2", "SETANTA SPORTS KZ")
PRIORITY = ("QAZSPORT HD", "SPORT+ Qazaqstan", "KHL PRIME", "KHL HD",
            "EUROSPORT 1", "EUROSPORT 2", "МАТЧ! ПЛАНЕТА",
            "SETANTA SPORTS 1", "SETANTA SPORTS 2", "SETANTA SPORTS KZ",
            "Q LEAGUE", "Q ARENA", "Q FOOTBALL", "viju+ Sport")
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
                "avatar": user.get("avatar", "")}
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
    name = str(name or "").strip()
    return {
        "QAZSPORT": "QAZSPORT HD",
        "Qazsport": "QAZSPORT HD",
        "SETANTA 1": "SETANTA SPORTS 1",
        "SETANTA 2": "SETANTA SPORTS 2",
        "SETANTA KZ": "SETANTA SPORTS KZ",
    }.get(name, name)


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
    exact: dict[tuple, dict] = {}
    for row in rows:
        try:
            raw = json.loads(row["payload_json"])
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
            if not channel or channel == "QAZSPORT HD" and re.search(
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
            "channel": e["channel"], "start_at": e["start"].isoformat(),
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
    application.state.database = database
    application.state.schedule = ScheduleService(
        RuntimeParserOrchestrator(database=database)
    )
    application.state.collect_lock = asyncio.Lock()
    yield


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
                     "avatar": user.get("avatar", "")}}


@app.get("/api/me")
def me(request: Request):
    return {"user": current_user(request)}


@app.post("/api/logout")
def logout(request: Request, response: Response):
    origin_guard(request)
    response.delete_cookie("sport_web_session")
    return {"ok": True}


@app.get("/api/events")
def events(request: Request, start: str = "", end: str = ""):
    current_user(request)
    for value in (start, end):
        if value:
            try:
                date.fromisoformat(value)
            except ValueError:
                raise HTTPException(400, "Неверная дата") from None
    return {"events": event_rows(request.app.state.database, start, end),
            "timezone": "Asia/Almaty"}


def _source_status(request: Request) -> dict:
    database = request.app.state.database
    today = datetime.now(KZ_TIMEZONE).date()
    files = imported_epg_status(
        database, first=today, last=today + timedelta(days=6)
    )
    source_runs = database.latest_source_runs()
    websites = []
    for source, label in (
        ("qazsport", "QAZSPORT HD"),
        ("sportplus", "SPORT+ Qazaqstan"),
        ("tvguide", "TVGuide (проверенные LIVE)"),
    ):
        latest = source_runs.get(source)
        websites.append({
            "channel": label, "kind": "website",
            "status": (latest or {}).get("status", "not_checked"),
            "checked_at": (latest or {}).get("created_at"),
            "event_count": (latest or {}).get("event_count", 0),
        })
    return {
        "websites": websites, "excel": files,
        "missing_channels": [
            x["channel"] for x in files
            if x["status"] in ("missing", "outdated")
        ], "gmail_connected": False,
        "note": "Excel сейчас загружаются вручную. Gmail OAuth ещё не подключён.",
    }


@app.get("/api/sources")
def sources(request: Request):
    current_user(request)
    return _source_status(request)


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
        parsed = parse_epg_xlsx(data, filename)
    except InvalidEPG as exc:
        raise HTTPException(422, str(exc)) from exc
    async with request.app.state.collect_lock:
        result = import_parsed_epg(request.app.state.database, parsed)
        if request.app.state.backup and result["status"] == "imported":
            try:
                await asyncio.to_thread(request.app.state.backup.save)
            except Exception as exc:
                raise HTTPException(503, "Импорт выполнен, но резервная копия "
                                    "в GCS не сохранена: " + type(exc).__name__) from exc
    result["durable_storage"] = bool(request.app.state.backup)
    return result


@app.post("/api/collect")
async def collect(request: Request, options: CollectOptions):
    current_user(request)
    origin_guard(request)
    status = _source_status(request)
    if status["missing_channels"] and not options.allow_partial:
        raise HTTPException(409, {
            "message": "Нет актуальных Excel некоторых телеканалов. Собрать без них?",
            "missing_channels": status["missing_channels"],
        })
    async with request.app.state.collect_lock:
        try:
            await request.app.state.schedule.refresh()
            if request.app.state.backup:
                await asyncio.to_thread(request.app.state.backup.save)
        except Exception as exc:
            raise HTTPException(502, "Ошибка обновления или сохранения: "
                                + type(exc).__name__) from exc
    db = request.app.state.database
    return {"ok": True, "event_count": db.active_event_count(),
            "durable_storage": bool(request.app.state.backup),
            "last_run": db.latest_agent_run()}


def load_template():
    local = os.getenv("SPORT_TEMPLATE_PATH", "")
    if local and Path(local).is_file():
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
    wb = load_template()
    sheet = wb.worksheets[0]
    styles = [copy(sheet.cell(2, col)._style) for col in range(1, 26)]
    if sheet.max_row > 1:
        sheet.delete_rows(2, sheet.max_row - 1)
    for n, event in enumerate(data, 2):
        start = datetime.fromisoformat(event["start_at"])
        platform_start = datetime.fromisoformat(event["platform_start_at"])
        end = datetime.fromisoformat(event["end_at"])
        title = event["title"]
        teams = re.split(r"\s+[-–]\s+", title, maxsplit=1)
        team1, team2 = (teams[0], teams[1]) if len(teams) == 2 else (title, "")
        label = (start.strftime("%d%m%y") + "_" +
                 hashlib.sha1((title + event["start_at"]).encode()).hexdigest()[:12].upper())
        subtitle = event["sport"] + (
            ". " + event["tournament"] if event["tournament"] else ""
        )
        values = [
            60000 - (n - 2) * 10, start.strftime("%d.%m"), start.strftime("%H:%M"),
            event["sport"], event["tournament"], title, event["channel"],
            platform_start.replace(tzinfo=None), end.replace(tzinfo=None),
            label + "_LIVE_RU", label + "_LIVE_KZ", label + "_SOON_RU",
            label + "_SOON_KZ", "", "", "", subtitle,
            label + "_ARCH_RU", label + "_ARCH_KZ",
            team1, team1, team2, team2, subtitle, subtitle,
        ]
        for col, value in enumerate(values, 1):
            cell = sheet.cell(n, col)
            cell._style = copy(styles[col - 1])
            cell.value = value
    output = BytesIO()
    wb.save(output)
    return output.getvalue()


@app.get("/api/export")
def export(request: Request, start: str = "", end: str = ""):
    current_user(request)
    data = event_rows(request.app.state.database, start, end)
    raw = xlsx_content(data)
    filename = "sport_epg_schedule.xlsx"
    return StreamingResponse(
        BytesIO(raw),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="' + filename + '"'},
    )

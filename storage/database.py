from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Iterable

from services.time_logic import KZ_TIMEZONE, get_event_status, get_scheduled_datetimes


DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "slp.db"


def _now_iso() -> str:
    return datetime.now(KZ_TIMEZONE).isoformat()


def _event_storage_id(source: str, scope_date: str, event: dict) -> str:
    raw_title = str(event.get("raw_title") or event.get("title") or "")
    raw = "|".join(
        (
            source,
            scope_date,
            str(event.get("date") or ""),
            str(event.get("time") or ""),
            str(event.get("channel") or ""),
            raw_title,
        )
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]


class SLPDatabase:
    """Small SQLite persistence layer for parser/agent state.

    A new connection is opened for each operation so the object stays safe to
    use from the bot's async tasks without keeping a shared sqlite cursor alive.
    """

    def __init__(self, path: str | Path | None = None):
        configured = path or os.getenv("SLP_DB_PATH") or DEFAULT_DB_PATH
        self.path = Path(configured)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def init_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS events (
                    storage_id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    scope_date TEXT NOT NULL,
                    source_url TEXT NOT NULL DEFAULT '',
                    channel TEXT NOT NULL DEFAULT '',
                    raw_title TEXT NOT NULL DEFAULT '',
                    normalized_title TEXT NOT NULL DEFAULT '',
                    sport TEXT NOT NULL DEFAULT '',
                    tournament TEXT NOT NULL DEFAULT '',
                    event_date TEXT NOT NULL,
                    event_time TEXT NOT NULL,
                    start_at TEXT NOT NULL,
                    end_at TEXT NOT NULL,
                    timezone TEXT NOT NULL DEFAULT 'Asia/Almaty',
                    is_live INTEGER NOT NULL DEFAULT 0,
                    state_at_ingest TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    last_run_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_events_source_scope_active
                    ON events(source, scope_date, active);
                CREATE INDEX IF NOT EXISTS idx_events_start_at
                    ON events(start_at);

                CREATE TABLE IF NOT EXISTS event_revisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    storage_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    scope_date TEXT NOT NULL,
                    change_kind TEXT NOT NULL,
                    before_json TEXT,
                    after_json TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_revisions_event
                    ON event_revisions(storage_id, id DESC);
                CREATE INDEX IF NOT EXISTS idx_revisions_source
                    ON event_revisions(source, scope_date, id DESC);

                CREATE TABLE IF NOT EXISTS parser_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    scope_date TEXT NOT NULL,
                    status TEXT NOT NULL,
                    event_count INTEGER NOT NULL,
                    previous_count INTEGER,
                    error TEXT,
                    details_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_parser_runs_source_scope
                    ON parser_runs(source, scope_date, id DESC);

                CREATE TABLE IF NOT EXISTS agent_runs (
                    run_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    summary_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS incidents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    scope_date TEXT NOT NULL,
                    incident_type TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    message TEXT NOT NULL,
                    details_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    resolved_at TEXT
                );
                """
            )

    def start_agent_run(self, run_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO agent_runs(
                    run_id, status, started_at, finished_at, summary_json
                ) VALUES (?, 'running', ?, NULL, '{}')
                """,
                (run_id, _now_iso()),
            )

    def finish_agent_run(self, run_id: str, status: str, summary: dict) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE agent_runs
                SET status = ?, finished_at = ?, summary_json = ?
                WHERE run_id = ?
                """,
                (
                    status,
                    _now_iso(),
                    json.dumps(summary, ensure_ascii=False, sort_keys=True),
                    run_id,
                ),
            )

    def latest_successful_count(self, source: str, scope_date: str) -> int | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT event_count
                FROM parser_runs
                WHERE source = ? AND scope_date = ? AND status IN ('ok', 'warning')
                ORDER BY id DESC
                LIMIT 1
                """,
                (source, scope_date),
            ).fetchone()
        return int(row["event_count"]) if row else None

    def record_parser_run(
        self,
        *,
        run_id: str,
        source: str,
        scope_date: str,
        status: str,
        event_count: int,
        previous_count: int | None,
        error: str | None = None,
        details: dict | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO parser_runs(
                    run_id, source, scope_date, status, event_count,
                    previous_count, error, details_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    source,
                    scope_date,
                    status,
                    int(event_count),
                    previous_count,
                    error,
                    json.dumps(details or {}, ensure_ascii=False, sort_keys=True),
                    _now_iso(),
                ),
            )

    def record_incident(
        self,
        *,
        run_id: str,
        source: str,
        scope_date: str,
        incident_type: str,
        severity: str,
        message: str,
        details: dict | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO incidents(
                    run_id, source, scope_date, incident_type, severity,
                    message, details_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    source,
                    scope_date,
                    incident_type,
                    severity,
                    message,
                    json.dumps(details or {}, ensure_ascii=False, sort_keys=True),
                    _now_iso(),
                ),
            )

    def upsert_source_snapshot(
        self,
        *,
        run_id: str,
        source: str,
        scope_date: str,
        events: Iterable[dict],
        preserve_editorial: bool = False,
    ) -> None:
        now_iso = _now_iso()
        event_list = list(events)
        if preserve_editorial:
            from services.editorial_store import init_editorial
            init_editorial(self)

        with self._connect() as connection:
            before = {
                row["storage_id"]: row
                for row in connection.execute(
                    "SELECT storage_id,payload_json FROM events "
                    "WHERE source=? AND scope_date=? AND active=1",
                    (source, scope_date),
                ).fetchall()
            }
            # Identify only unique same-day/channel/fixture replacements.
            # If a source moved a fixture's kickoff, its storage ID changes.
            transfers = []
            if preserve_editorial and before and event_list:
                from collections import defaultdict
                from services.schedule_merge import normalize_match_text

                def fixture_key(item):
                    return tuple(normalize_match_text(str(item.get(field) or ""))
                                 for field in ("date", "channel", "sport",
                                               "tournament", "title"))

                old_keys = defaultdict(list)
                new_keys = defaultdict(list)
                for old_id, old_row in before.items():
                    old_keys[fixture_key(json.loads(old_row["payload_json"]))].append(old_id)
                for event in event_list:
                    new_keys[fixture_key(event)].append(
                        _event_storage_id(source, scope_date, event))
                for key, old_ids in old_keys.items():
                    new_ids = new_keys.get(key, ())
                    if len(old_ids) == len(new_ids) == 1 and old_ids[0] != new_ids[0]:
                        transfers.append((old_ids[0], new_ids[0]))
            # Record a superseded programme as removed from the EPG, not
            # as cancelled. A disappearing listing is not proof of cancellation.
            next_ids = {
                _event_storage_id(source, scope_date, event)
                for event in event_list
            }
            for removed_id in before.keys() - next_ids:
                connection.execute(
                    "INSERT INTO event_revisions("
                    "storage_id,run_id,source,scope_date,change_kind,"
                    "before_json,after_json,created_at) "
                    "VALUES(?,?,?,?,'removed_from_source',?,NULL,?)",
                    (removed_id,run_id,source,scope_date,
                     before[removed_id]["payload_json"],now_iso),
                )
            connection.execute(
                "UPDATE events SET active = 0 WHERE source = ? AND scope_date = ?",
                (source, scope_date),
            )

            for event in event_list:
                storage_id = _event_storage_id(source, scope_date, event)
                start, end = get_scheduled_datetimes(event)
                payload = json.dumps(event, ensure_ascii=False, sort_keys=True)
                normalized_title = str(
                    event.get("title") or event.get("raw_title") or ""
                ).strip()
                old_payload = before.get(storage_id)
                if old_payload is None or old_payload["payload_json"] != payload:
                    connection.execute(
                        "INSERT INTO event_revisions("
                        "storage_id,run_id,source,scope_date,change_kind,"
                        "before_json,after_json,created_at) "
                        "VALUES(?,?,?,?,?,?,?,?)",
                        (storage_id,run_id,source,scope_date,
                         "added_to_source" if old_payload is None else "source_changed",
                         old_payload["payload_json"] if old_payload else None,
                         payload,now_iso),
                    )

                connection.execute(
                    """
                    INSERT INTO events(
                        storage_id, source, scope_date, source_url, channel,
                        raw_title, normalized_title, sport, tournament,
                        event_date, event_time, start_at, end_at, timezone,
                        is_live, state_at_ingest, active, first_seen_at,
                        last_seen_at, last_run_id, payload_json
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Asia/Almaty',
                        ?, ?, 1, ?, ?, ?, ?
                    )
                    ON CONFLICT(storage_id) DO UPDATE SET
                        source_url = excluded.source_url,
                        channel = excluded.channel,
                        raw_title = excluded.raw_title,
                        normalized_title = excluded.normalized_title,
                        sport = excluded.sport,
                        tournament = excluded.tournament,
                        event_date = excluded.event_date,
                        event_time = excluded.event_time,
                        start_at = excluded.start_at,
                        end_at = excluded.end_at,
                        timezone = excluded.timezone,
                        is_live = excluded.is_live,
                        state_at_ingest = excluded.state_at_ingest,
                        active = 1,
                        last_seen_at = excluded.last_seen_at,
                        last_run_id = excluded.last_run_id,
                        payload_json = excluded.payload_json
                    """,
                    (
                        storage_id,
                        source,
                        scope_date,
                        str(event.get("source_url") or ""),
                        str(event.get("channel") or ""),
                        str(event.get("raw_title") or ""),
                        normalized_title,
                        str(event.get("sport") or ""),
                        str(event.get("tournament") or ""),
                        str(event.get("date") or ""),
                        str(event.get("time") or ""),
                        start.isoformat(),
                        end.isoformat(),
                        int(bool(event.get("is_live", False))),
                        get_event_status(event),
                        now_iso,
                        now_iso,
                        run_id,
                        payload,
                    ),
                )

            for old_id, new_id in transfers:
                old_edit = connection.execute(
                    "SELECT values_json,updated_by,updated_at "
                    "FROM editorial_overrides WHERE storage_id=?", (old_id,)
                ).fetchone()
                if old_edit is None:
                    continue
                created = connection.execute(
                    "INSERT OR IGNORE INTO editorial_overrides("
                    "storage_id,values_json,updated_by,updated_at) VALUES(?,?,?,?)",
                    (new_id, old_edit["values_json"], old_edit["updated_by"],
                     old_edit["updated_at"]),
                )
                if created.rowcount:
                    connection.execute(
                        "INSERT INTO editorial_audit("
                        "storage_id,editor,before_json,after_json,changed_at)"
                        " VALUES(?,?,?,?,?)",
                        (new_id, "SLP_IMPORT_TRANSFER",
                         json.dumps({"copied_from_storage_id": old_id}),
                         old_edit["values_json"], now_iso),
                    )

    def event_revisions(self, storage_id: str = "", limit: int = 100) -> list[dict]:
        limit = min(max(int(limit), 1), 500)
        with self._connect() as connection:
            if storage_id:
                rows = connection.execute(
                    "SELECT * FROM event_revisions WHERE storage_id=? "
                    "ORDER BY id DESC LIMIT ?",
                    (storage_id, limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM event_revisions ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [dict(row) for row in rows]

    def load_active_source_snapshot(self, source: str, scope_date: str) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT payload_json
                FROM events
                WHERE source = ? AND scope_date = ? AND active = 1
                ORDER BY start_at, channel
                """,
                (source, scope_date),
            ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def latest_source_runs(self) -> dict[str, dict]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM parser_runs
                ORDER BY id DESC
                LIMIT 100
                """
            ).fetchall()

        result: dict[str, dict] = {}
        for row in rows:
            source = str(row["source"])
            if source in result:
                continue
            result[source] = dict(row)
            try:
                result[source]["details"] = json.loads(row["details_json"] or "{}")
            except json.JSONDecodeError:
                result[source]["details"] = {}
        return result

    def latest_agent_run(self) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM agent_runs
                ORDER BY started_at DESC
                LIMIT 1
                """
            ).fetchone()
        if not row:
            return None
        result = dict(row)
        try:
            result["summary"] = json.loads(row["summary_json"] or "{}")
        except json.JSONDecodeError:
            result["summary"] = {}
        return result

    def unresolved_incidents(self, limit: int = 10) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM incidents
                WHERE resolved_at IS NULL
                ORDER BY id DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        return [dict(row) for row in rows]

    def active_event_count(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS value FROM events WHERE active = 1"
            ).fetchone()
        return int(row["value"] if row else 0)

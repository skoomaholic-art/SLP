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
    ) -> None:
        now_iso = _now_iso()
        event_list = list(events)

        with self._connect() as connection:
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

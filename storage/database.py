from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Iterable

from services.event_status import (
    KZ_TIMEZONE,
    get_event_start,
    get_event_status,
    get_scheduled_datetimes,
    is_live_broadcast,
)


DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "slp.db"


def _now_iso() -> str:
    return datetime.now(KZ_TIMEZONE).isoformat()


def _event_dedup_key(source: str, event: dict) -> str:
    raw_title = str(event.get("raw_title") or event.get("title") or "")
    raw = "|".join(
        (
            source,
            str(event.get("date") or ""),
            str(event.get("start_time") or event.get("time") or ""),
            str(event.get("channel") or ""),
            raw_title,
        )
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]


def _event_storage_id(source: str, scope_date: str, event: dict) -> str:
    raw = f"{scope_date}|{_event_dedup_key(source, event)}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]


def _upgrade_payload(payload: dict) -> dict:
    result = dict(payload)
    if "is_live_broadcast" not in result:
        result["is_live_broadcast"] = bool(result.get("is_live", False))
    result.setdefault("is_live", bool(result.get("is_live_broadcast", False)))
    result.setdefault("timezone", "Asia/Almaty")
    return result


class SLPDatabase:
    """SQLite persistence shared by parser refresh and Telegram reads."""

    def __init__(self, path: str | Path | None = None):
        configured = path or os.getenv("SLP_DB_PATH") or DEFAULT_DB_PATH
        self.path = Path(configured).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @staticmethod
    def _ensure_column(
        connection: sqlite3.Connection,
        table: str,
        column: str,
        definition: str,
    ) -> None:
        columns = {
            str(row[1])
            for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def init_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS events (
                    storage_id TEXT PRIMARY KEY,
                    dedup_key TEXT NOT NULL DEFAULT '',
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
                    is_live_broadcast INTEGER NOT NULL DEFAULT 0,
                    state_at_ingest TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    last_run_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );

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

            # Safe in-place migration for databases created by agent-network v1.
            self._ensure_column(connection, "events", "dedup_key", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(
                connection,
                "events",
                "is_live_broadcast",
                "INTEGER NOT NULL DEFAULT 0",
            )
            connection.execute(
                """
                UPDATE events
                SET is_live_broadcast = is_live
                WHERE is_live_broadcast = 0 AND is_live = 1
                """
            )
            connection.executescript(
                """
                CREATE INDEX IF NOT EXISTS idx_events_source_scope_active
                    ON events(source, scope_date, active);
                CREATE INDEX IF NOT EXISTS idx_events_start_at
                    ON events(start_at);
                CREATE INDEX IF NOT EXISTS idx_events_date_live_active
                    ON events(event_date, is_live_broadcast, active);
                CREATE INDEX IF NOT EXISTS idx_events_dedup_key
                    ON events(dedup_key);
                CREATE INDEX IF NOT EXISTS idx_parser_runs_source_scope
                    ON parser_runs(source, scope_date, id DESC);
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

            for original_event in event_list:
                event = _upgrade_payload(original_event)
                source_live = is_live_broadcast(event)
                event["is_live_broadcast"] = source_live
                event["is_live"] = source_live
                start, end = get_scheduled_datetimes(event)
                event["timezone"] = "Asia/Almaty"
                event["start_time"] = start.isoformat()
                event["end_time"] = end.isoformat()

                storage_id = _event_storage_id(source, scope_date, event)
                dedup_key = _event_dedup_key(source, event)
                payload = json.dumps(event, ensure_ascii=False, sort_keys=True)
                normalized_title = str(
                    event.get("title") or event.get("raw_title") or ""
                ).strip()

                connection.execute(
                    """
                    INSERT INTO events(
                        storage_id, dedup_key, source, scope_date, source_url,
                        channel, raw_title, normalized_title, sport, tournament,
                        event_date, event_time, start_at, end_at, timezone,
                        is_live, is_live_broadcast, state_at_ingest, active,
                        first_seen_at, last_seen_at, last_run_id, payload_json
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Asia/Almaty',
                        ?, ?, ?, 1, ?, ?, ?, ?
                    )
                    ON CONFLICT(storage_id) DO UPDATE SET
                        dedup_key = excluded.dedup_key,
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
                        is_live_broadcast = excluded.is_live_broadcast,
                        state_at_ingest = excluded.state_at_ingest,
                        active = 1,
                        last_seen_at = excluded.last_seen_at,
                        last_run_id = excluded.last_run_id,
                        payload_json = excluded.payload_json
                    """,
                    (
                        storage_id,
                        dedup_key,
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
                        int(source_live),
                        int(source_live),
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
        return [_upgrade_payload(json.loads(row["payload_json"])) for row in rows]

    def load_active_events(
        self,
        *,
        start_date: str,
        end_date: str,
        live_broadcast_only: bool = False,
    ) -> list[dict]:
        where = ["active = 1", "event_date >= ?", "event_date <= ?"]
        params: list[object] = [start_date, end_date]
        if live_broadcast_only:
            where.append("is_live_broadcast = 1")

        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT dedup_key, source, payload_json, last_seen_at
                FROM events
                WHERE {' AND '.join(where)}
                ORDER BY last_seen_at DESC
                """,
                params,
            ).fetchall()

        by_key: dict[str, dict] = {}
        for row in rows:
            payload = _upgrade_payload(json.loads(row["payload_json"]))
            key = str(row["dedup_key"] or "") or _event_dedup_key(
                str(row["source"]), payload
            )
            by_key.setdefault(key, payload)

        return sorted(
            by_key.values(),
            key=lambda event: (
                get_event_start(event),
                str(event.get("channel") or ""),
            ),
        )

    def latest_source_runs(self) -> dict[str, dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM parser_runs ORDER BY id DESC LIMIT 100"
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
                "SELECT * FROM agent_runs ORDER BY started_at DESC LIMIT 1"
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

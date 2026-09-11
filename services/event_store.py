from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Iterable

from services.event_contract import KZ_TIMEZONE, TIMEZONE_NAME, build_sport_event

DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "slp_events.sqlite3"


def get_db_path() -> Path:
    configured = os.getenv("SLP_DB_PATH")
    return Path(configured).expanduser().resolve() if configured else DEFAULT_DB_PATH


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    target = Path(path).expanduser().resolve() if path else get_db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(target)
    db.row_factory = sqlite3.Row
    return db


def init_db(path: str | Path | None = None) -> None:
    with connect(path) as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS sport_events (
                event_key TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                source_url TEXT NOT NULL,
                channel TEXT NOT NULL,
                raw_title TEXT NOT NULL,
                normalized_title TEXT NOT NULL,
                sport TEXT NOT NULL DEFAULT '',
                tournament TEXT NOT NULL DEFAULT '',
                start_time TEXT NOT NULL,
                end_time TEXT,
                timezone TEXT NOT NULL,
                is_live_broadcast INTEGER NOT NULL,
                end_estimation_method TEXT,
                end_confidence TEXT NOT NULL DEFAULT 'unknown',
                updated_at TEXT NOT NULL
            )
        """)
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_start ON sport_events(start_time)"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_live ON sport_events(is_live_broadcast, start_time)"
        )


def upsert_events(events: Iterable[dict], path: str | Path | None = None) -> int:
    rows = list(events)
    init_db(path)
    with connect(path) as db:
        for event in rows:
            db.execute(
                """
                INSERT INTO sport_events (
                    event_key, source, source_url, channel, raw_title,
                    normalized_title, sport, tournament, start_time, end_time,
                    timezone, is_live_broadcast, end_estimation_method,
                    end_confidence, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_key) DO UPDATE SET
                    source_url=excluded.source_url,
                    raw_title=excluded.raw_title,
                    normalized_title=excluded.normalized_title,
                    sport=excluded.sport,
                    tournament=excluded.tournament,
                    end_time=excluded.end_time,
                    timezone=excluded.timezone,
                    is_live_broadcast=excluded.is_live_broadcast,
                    end_estimation_method=excluded.end_estimation_method,
                    end_confidence=excluded.end_confidence,
                    updated_at=excluded.updated_at
                """,
                (
                    event["event_key"], event["source"], event["source_url"],
                    event["channel"], event["raw_title"], event["normalized_title"],
                    event.get("sport", ""), event.get("tournament", ""),
                    event["start_time"].astimezone(KZ_TIMEZONE).isoformat(),
                    event["end_time"].astimezone(KZ_TIMEZONE).isoformat()
                    if event.get("end_time") else None,
                    TIMEZONE_NAME, 1 if event.get("is_live_broadcast") else 0,
                    event.get("end_estimation_method"),
                    event.get("end_confidence", "unknown"),
                    event["updated_at"].astimezone(KZ_TIMEZONE).isoformat(),
                ),
            )
    return len(rows)


def _row_to_event(row: sqlite3.Row) -> dict:
    start = datetime.fromisoformat(row["start_time"]).astimezone(KZ_TIMEZONE)
    end = datetime.fromisoformat(row["end_time"]).astimezone(KZ_TIMEZONE) if row["end_time"] else None
    updated = datetime.fromisoformat(row["updated_at"]).astimezone(KZ_TIMEZONE)
    return build_sport_event(
        {
            "event_key": row["event_key"],
            "channel": row["channel"],
            "raw_title": row["raw_title"],
            "normalized_title": row["normalized_title"],
            "title": row["normalized_title"],
            "sport": row["sport"],
            "tournament": row["tournament"],
            "start_time": start,
            "end_time": end,
            "is_live_broadcast": bool(row["is_live_broadcast"]),
            "end_estimation_method": row["end_estimation_method"],
            "end_confidence": row["end_confidence"],
            "updated_at": updated,
        },
        source=row["source"],
        source_url=row["source_url"],
    )


def load_events(*, live_broadcasts_only: bool = False, path: str | Path | None = None) -> list[dict]:
    init_db(path)
    where = "WHERE is_live_broadcast = 1" if live_broadcasts_only else ""
    with connect(path) as db:
        rows = db.execute(
            f"SELECT * FROM sport_events {where} ORDER BY start_time, channel"
        ).fetchall()
    return [_row_to_event(row) for row in rows]


def get_store_diagnostics(path: str | Path | None = None) -> dict:
    init_db(path)
    target = Path(path).expanduser().resolve() if path else get_db_path()
    with connect(path) as db:
        row = db.execute(
            "SELECT COUNT(*) total, "
            "SUM(CASE WHEN is_live_broadcast = 1 THEN 1 ELSE 0 END) live_marked, "
            "MAX(updated_at) last_updated FROM sport_events"
        ).fetchone()
    return {
        "path": str(target),
        "total": int(row["total"] or 0),
        "live_marked": int(row["live_marked"] or 0),
        "last_updated": row["last_updated"],
        "timezone": TIMEZONE_NAME,
    }

"""Persistent, high-signal editorial notifications for SLP."""
from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import json
from urllib.parse import urlparse

from services.time_logic import KZ_TIMEZONE


VALID_LEVELS = {"attention", "warning", "error", "critical"}
VALID_STATUSES = {"unread", "read", "applied"}


def init_notifications(database) -> None:
    with database._connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS editorial_notifications(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dedupe_key TEXT NOT NULL UNIQUE,
                kind TEXT NOT NULL,
                level TEXT NOT NULL,
                title TEXT NOT NULL,
                message TEXT NOT NULL,
                storage_id TEXT NOT NULL DEFAULT '',
                source_name TEXT NOT NULL DEFAULT '',
                source_url TEXT NOT NULL DEFAULT '',
                patch_json TEXT NOT NULL DEFAULT '{}',
                evidence_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'unread',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                read_at TEXT NOT NULL DEFAULT '',
                applied_at TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_editorial_notifications_status
                ON editorial_notifications(status, id DESC);
        """)


def _now() -> str:
    return datetime.now(KZ_TIMEZONE).isoformat(timespec="seconds")


def _safe_source_url(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlparse(raw)
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    if parsed.username or parsed.password:
        return ""
    return raw[:1500]


def _row_to_item(row) -> dict:
    item = dict(row)
    try:
        item["patch"] = json.loads(item.pop("patch_json") or "{}")
    except (TypeError, json.JSONDecodeError):
        item["patch"] = {}
    try:
        item["evidence"] = json.loads(item.pop("evidence_json") or "{}")
    except (TypeError, json.JSONDecodeError):
        item["evidence"] = {}
    item["read"] = item.get("status") != "unread"
    item["can_apply"] = bool(item["patch"]) and item.get("status") != "applied"
    return item


def create_notification(
    database,
    *,
    dedupe_key: str,
    kind: str,
    level: str,
    title: str,
    message: str,
    storage_id: str = "",
    source_name: str = "",
    source_url: str = "",
    patch: dict | None = None,
    evidence: dict | list | None = None,
) -> dict:
    init_notifications(database)
    level = level if level in VALID_LEVELS else "attention"
    now = _now()
    cleaned_patch = patch if isinstance(patch, dict) else {}
    cleaned_evidence = evidence if isinstance(evidence, (dict, list)) else {}
    with database._connect() as conn:
        conn.execute(
            """
            INSERT INTO editorial_notifications(
                dedupe_key,kind,level,title,message,storage_id,
                source_name,source_url,patch_json,evidence_json,status,
                created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,'unread',?,?)
            ON CONFLICT(dedupe_key) DO UPDATE SET
                level=excluded.level,
                title=excluded.title,
                message=excluded.message,
                storage_id=excluded.storage_id,
                source_name=excluded.source_name,
                source_url=excluded.source_url,
                patch_json=excluded.patch_json,
                evidence_json=excluded.evidence_json,
                updated_at=excluded.updated_at
            """,
            (
                str(dedupe_key)[:120],
                str(kind)[:80],
                level,
                " ".join(str(title or "Изменение расписания").split())[:300],
                str(message or "")[:1600],
                str(storage_id or "")[:64],
                " ".join(str(source_name or "").split())[:160],
                _safe_source_url(source_url),
                json.dumps(cleaned_patch, ensure_ascii=False, sort_keys=True),
                json.dumps(cleaned_evidence, ensure_ascii=False, sort_keys=True),
                now,
                now,
            ),
        )
        row = conn.execute(
            "SELECT * FROM editorial_notifications WHERE dedupe_key=?",
            (str(dedupe_key)[:120],),
        ).fetchone()
    return _row_to_item(row)


def list_notifications(database, limit: int = 80) -> list[dict]:
    init_notifications(database)
    limit = min(max(int(limit), 1), 150)
    with database._connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM editorial_notifications
            ORDER BY CASE status WHEN 'unread' THEN 0 WHEN 'read' THEN 1 ELSE 2 END,
                     updated_at DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [_row_to_item(row) for row in rows]


def get_notification(database, notice_id: int) -> dict | None:
    init_notifications(database)
    with database._connect() as conn:
        row = conn.execute(
            "SELECT * FROM editorial_notifications WHERE id=?",
            (int(notice_id),),
        ).fetchone()
    return _row_to_item(row) if row else None


def mark_read(database, notice_id: int) -> dict | None:
    init_notifications(database)
    now = _now()
    with database._connect() as conn:
        conn.execute(
            """
            UPDATE editorial_notifications
               SET status=CASE WHEN status='unread' THEN 'read' ELSE status END,
                   read_at=CASE WHEN read_at='' THEN ? ELSE read_at END,
                   updated_at=?
             WHERE id=?
            """,
            (now, now, int(notice_id)),
        )
        row = conn.execute(
            "SELECT * FROM editorial_notifications WHERE id=?",
            (int(notice_id),),
        ).fetchone()
    return _row_to_item(row) if row else None


def mark_applied(database, notice_id: int) -> dict | None:
    init_notifications(database)
    now = _now()
    with database._connect() as conn:
        conn.execute(
            """
            UPDATE editorial_notifications
               SET status='applied',
                   read_at=CASE WHEN read_at='' THEN ? ELSE read_at END,
                   applied_at=?,
                   updated_at=?
             WHERE id=?
            """,
            (now, now, now, int(notice_id)),
        )
        row = conn.execute(
            "SELECT * FROM editorial_notifications WHERE id=?",
            (int(notice_id),),
        ).fetchone()
    return _row_to_item(row) if row else None


def record_validation_notifications(database, validation: dict) -> int:
    created = 0
    for discrepancy in validation.get("discrepancies") or []:
        if not isinstance(discrepancy, dict):
            continue
        storage_id = str(discrepancy.get("storage_id") or "")
        source_url = str(discrepancy.get("source_url") or "")
        patch = discrepancy.get("patch") if isinstance(discrepancy.get("patch"), dict) else {}
        fingerprint = json.dumps(
            {
                "storage_id": storage_id,
                "kind": discrepancy.get("kind"),
                "patch": patch,
                "source_url": source_url,
                "external_id": discrepancy.get("external_id"),
                "external_status": discrepancy.get("external_status"),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        dedupe = "network:" + sha256(fingerprint.encode("utf-8")).hexdigest()[:32]
        create_notification(
            database,
            dedupe_key=dedupe,
            kind=str(discrepancy.get("kind") or "network_mismatch"),
            level=str(discrepancy.get("level") or "attention"),
            title=str(discrepancy.get("title") or "Изменение расписания"),
            message=str(discrepancy.get("message") or ""),
            storage_id=storage_id,
            source_name=str(discrepancy.get("source_name") or ""),
            source_url=source_url,
            patch=patch,
            evidence=discrepancy.get("evidence") or {},
        )
        created += 1
    return created

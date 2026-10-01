"""Persistent editorial corrections on top of immutable source snapshots."""
from __future__ import annotations

from datetime import datetime
import json
import re

from services.time_logic import KZ_TIMEZONE

TEXT_FIELDS = frozenset({
    "title", "sport", "tournament", "team1_ru", "team1_kz",
    "team2_ru", "team2_kz", "subtitle_ru", "subtitle_kz",
})
TIME_FIELDS = frozenset({"time", "end_time"})
EDITABLE_FIELDS = TEXT_FIELDS | TIME_FIELDS
TIME_RE = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d")


class EditorialError(ValueError):
    pass


def init_editorial(database) -> None:
    with database._connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS editorial_overrides(
                storage_id TEXT PRIMARY KEY REFERENCES events(storage_id),
                values_json TEXT NOT NULL,
                updated_by TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS editorial_audit(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                storage_id TEXT NOT NULL,
                editor TEXT NOT NULL,
                before_json TEXT NOT NULL,
                after_json TEXT NOT NULL,
                changed_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS editorial_audit_storage_id
                ON editorial_audit(storage_id, id DESC);
        """)


def get_editorial(database, ids: list[str]) -> dict[str, dict]:
    init_editorial(database)
    if not ids:
        return {}
    result = {}
    with database._connect() as conn:
        for begin in range(0, len(ids), 500):
            selected = ids[begin:begin + 500]
            placeholders = ",".join("?" for _ in selected)
            rows = conn.execute(
                "SELECT storage_id,values_json FROM editorial_overrides WHERE "
                f"storage_id IN ({placeholders})", selected
            )
            for row in rows:
                result[row["storage_id"]] = json.loads(row["values_json"])
    return result


def apply_edit(database, *, storage_id: str, values: dict,
               username: str) -> dict:
    if not re.fullmatch(r"[a-f0-9]{24}", storage_id):
        raise EditorialError("Недопустимый идентификатор события")
    if not isinstance(values, dict) or not values:
        raise EditorialError("Нет изменений")
    if set(values) - EDITABLE_FIELDS:
        raise EditorialError("Попытка изменить запрещённое поле")
    cleaned = {}
    for field, value in values.items():
        if not isinstance(value, str):
            raise EditorialError("Значения должны быть строками")
        v = " ".join(value.split())
        if len(v) > 250:
            raise EditorialError("Значение длиннее 250 символов")
        if field in TIME_FIELDS and v and not TIME_RE.fullmatch(v):
            raise EditorialError("Неверный формат времени, требуется ЧЧ:ММ")
        if field in ("title", "sport") and not v:
            raise EditorialError("Нельзя удалить название события или вид спорта")
        cleaned[field] = v
    init_editorial(database)
    with database._connect() as conn:
        row = conn.execute(
            "SELECT storage_id FROM events WHERE storage_id=?", (storage_id,)
        ).fetchone()
        if row is None:
            raise EditorialError("Событие не найдено в базе")
        old = conn.execute(
            "SELECT values_json FROM editorial_overrides WHERE storage_id=?",
            (storage_id,),
        ).fetchone()
        before = json.loads(old["values_json"]) if old else {}
        after = {**before, **cleaned}
        now = datetime.now(KZ_TIMEZONE).isoformat()
        conn.execute(
            "INSERT INTO editorial_overrides(storage_id,values_json,updated_by,updated_at) "
            "VALUES(?,?,?,?) ON CONFLICT(storage_id) DO UPDATE SET "
            "values_json=excluded.values_json,updated_by=excluded.updated_by,"
            "updated_at=excluded.updated_at",
            (storage_id, json.dumps(after, ensure_ascii=False, sort_keys=True),
             username, now),
        )
        conn.execute(
            "INSERT INTO editorial_audit("
            "storage_id,editor,before_json,after_json,changed_at) VALUES(?,?,?,?,?)",
            (storage_id, username, json.dumps(before, ensure_ascii=False, sort_keys=True),
             json.dumps(after, ensure_ascii=False, sort_keys=True), now),
        )
    return {"storage_id": storage_id, "values": after,
            "updated_by": username, "updated_at": now}


def edit_history(database, *, storage_id: str, limit: int = 30) -> list[dict]:
    init_editorial(database)
    with database._connect() as conn:
        rows = conn.execute(
            "SELECT id,editor,before_json,after_json,changed_at "
            "FROM editorial_audit WHERE storage_id=? ORDER BY id DESC LIMIT ?",
            (storage_id, min(max(int(limit), 1), 100)),
        ).fetchall()
    return [dict(row) for row in rows]

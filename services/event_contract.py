from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from models import SportEvent
from services.event_status import (
    KZ_TIMEZONE,
    get_event_start,
    is_live_broadcast,
    resolve_event_end,
)


REQUIRED_EVENT_FIELDS = (
    "source",
    "source_url",
    "channel",
    "date",
    "time",
    "timezone",
    "start_time",
    "end_time",
    "sport",
    "tournament",
    "title",
    "is_live_broadcast",
    "raw_title",
    "estimated_broadcast_end_date",
    "estimated_broadcast_end",
    "end_estimation_method",
    "end_confidence",
)


def build_sport_event(
    event: dict[str, Any],
    *,
    source: str,
    source_url: str,
) -> SportEvent:
    """Приводит событие любого адаптера к единому контракту SLP."""

    normalized: dict[str, Any] = dict(event)

    normalized["source"] = str(source or "").strip()
    normalized["source_url"] = str(source_url or "").strip()
    normalized["channel"] = str(normalized.get("channel") or "").strip()
    normalized["date"] = str(normalized.get("date") or "").strip()
    normalized["time"] = str(normalized.get("time") or "").strip()
    normalized["sport"] = str(normalized.get("sport") or "").strip()
    normalized["tournament"] = str(normalized.get("tournament") or "").strip()
    normalized["title"] = str(
        normalized.get("title")
        or normalized.get("raw_title")
        or ""
    ).strip()
    normalized["raw_title"] = str(
        normalized.get("raw_title")
        or normalized.get("title")
        or ""
    ).strip()

    source_live = is_live_broadcast(normalized)
    normalized["is_live_broadcast"] = source_live
    # Kept only so old code/snapshots can be upgraded without a flag loss.
    normalized["is_live"] = source_live
    normalized["timezone"] = "Asia/Almaty"

    normalized.setdefault("estimated_broadcast_end_date", None)
    normalized.setdefault("estimated_broadcast_end", None)
    normalized.setdefault("end_estimation_method", None)
    normalized.setdefault("end_confidence", "unknown")

    start = get_event_start(normalized)
    normalized["start_time"] = start.isoformat()
    end, end_method = resolve_event_end(normalized)
    normalized["end_time"] = end.isoformat()

    if not normalized.get("end_estimation_method"):
        normalized["end_estimation_method"] = end_method
    if normalized.get("end_confidence") in (None, "", "unknown"):
        normalized["end_confidence"] = (
            "low" if end_method == "fallback_duration" else "high"
        )

    validate_sport_event(normalized)
    return cast(SportEvent, normalized)


def _validate_aware_iso(value: object, field: str) -> None:
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"SportEvent: {field} должен содержать timezone")


def validate_sport_event(event: dict[str, Any]) -> None:
    """Проверяет универсальный контракт и timezone-aware временную модель."""

    missing = [field for field in REQUIRED_EVENT_FIELDS if field not in event]
    if missing:
        raise ValueError(
            "SportEvent: отсутствуют обязательные поля: " + ", ".join(missing)
        )

    for field in (
        "source",
        "source_url",
        "channel",
        "date",
        "time",
        "timezone",
        "start_time",
        "end_time",
        "title",
        "raw_title",
    ):
        if not isinstance(event[field], str):
            raise TypeError(f"SportEvent: поле {field} должно быть строкой")

    if event["timezone"] != "Asia/Almaty":
        raise ValueError("SportEvent: timezone должен быть Asia/Almaty")

    if not isinstance(event["is_live_broadcast"], bool):
        raise TypeError("SportEvent: поле is_live_broadcast должно быть bool")

    try:
        datetime.strptime(event["date"], "%Y-%m-%d")
    except ValueError as error:
        raise ValueError("SportEvent: date должен быть YYYY-MM-DD") from error

    try:
        datetime.strptime(event["time"], "%H:%M")
    except ValueError as error:
        raise ValueError("SportEvent: time должен быть HH:MM") from error

    _validate_aware_iso(event["start_time"], "start_time")
    _validate_aware_iso(event["end_time"], "end_time")

    start = datetime.fromisoformat(event["start_time"]).astimezone(KZ_TIMEZONE)
    end = datetime.fromisoformat(event["end_time"]).astimezone(KZ_TIMEZONE)
    if end <= start:
        raise ValueError("SportEvent: end_time должен быть позже start_time")

    end_date = event.get("estimated_broadcast_end_date")
    end_time = event.get("estimated_broadcast_end")
    if (end_date is None) != (end_time is None):
        raise ValueError(
            "SportEvent: дата и время окончания должны быть заданы вместе или оба быть None"
        )

    if end_date is not None:
        datetime.strptime(str(end_date), "%Y-%m-%d")
        datetime.strptime(str(end_time), "%H:%M")

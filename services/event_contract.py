from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, cast
from zoneinfo import ZoneInfo

from models import SportEvent


KZ_TIMEZONE = ZoneInfo("Asia/Almaty")
TIMEZONE_NAME = "Asia/Almaty"

REQUIRED_EVENT_FIELDS = (
    "event_key",
    "source",
    "source_url",
    "channel",
    "raw_title",
    "normalized_title",
    "title",
    "sport",
    "tournament",
    "timezone",
    "start_time",
    "end_time",
    "is_live_broadcast",
    "updated_at",
    "end_estimation_method",
    "end_confidence",
)


def _aware_datetime(value: datetime, *, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"SportEvent: {field} должен быть timezone-aware")
    return value.astimezone(KZ_TIMEZONE)


def _datetime_from_legacy(date_text: str, time_text: str) -> datetime:
    return datetime.strptime(
        f"{date_text} {time_text}",
        "%Y-%m-%d %H:%M",
    ).replace(tzinfo=KZ_TIMEZONE)


def _build_start_time(event: dict[str, Any]) -> datetime:
    value = event.get("start_time")
    if isinstance(value, datetime):
        return _aware_datetime(value, field="start_time")

    date_text = str(event.get("date") or "").strip()
    time_text = str(event.get("time") or "").strip()
    if not date_text or not time_text:
        raise ValueError("SportEvent: нужны start_time либо date + time")
    return _datetime_from_legacy(date_text, time_text)


def _build_end_time(event: dict[str, Any]) -> datetime | None:
    value = event.get("end_time")
    if isinstance(value, datetime):
        return _aware_datetime(value, field="end_time")
    if value is not None:
        raise TypeError("SportEvent: end_time должен быть datetime или None")

    end_date = event.get("estimated_broadcast_end_date")
    end_clock = event.get("estimated_broadcast_end")
    if (end_date is None) != (end_clock is None):
        raise ValueError(
            "SportEvent: дата и время окончания должны быть заданы вместе"
        )
    if end_date is None:
        return None
    return _datetime_from_legacy(str(end_date), str(end_clock))


def make_event_key(event: dict[str, Any]) -> str:
    start = event["start_time"]
    raw = "|".join(
        (
            str(event.get("source") or "").casefold(),
            str(event.get("channel") or "").casefold(),
            start.isoformat(),
            str(event.get("raw_title") or event.get("title") or "").casefold(),
        )
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def build_sport_event(
    event: dict[str, Any],
    *,
    source: str,
    source_url: str,
) -> SportEvent:
    """Normalize any adapter event into the timezone-aware SLP contract."""

    normalized: dict[str, Any] = dict(event)
    normalized["source"] = str(source or "").strip()
    normalized["source_url"] = str(source_url or "").strip()
    normalized["channel"] = str(normalized.get("channel") or "").strip()
    normalized["sport"] = str(normalized.get("sport") or "").strip()
    normalized["tournament"] = str(normalized.get("tournament") or "").strip()

    normalized["raw_title"] = str(
        normalized.get("raw_title") or normalized.get("title") or ""
    ).strip()
    normalized["title"] = str(
        normalized.get("title") or normalized["raw_title"]
    ).strip()
    normalized["normalized_title"] = str(
        normalized.get("normalized_title") or normalized["title"]
    ).strip()

    normalized["start_time"] = _build_start_time(normalized)
    normalized["end_time"] = _build_end_time(normalized)
    normalized["timezone"] = TIMEZONE_NAME
    normalized["is_live_broadcast"] = bool(
        normalized.get(
            "is_live_broadcast",
            normalized.get("is_live", False),
        )
    )
    # Compatibility only. Business logic must never use this alias to mean
    # "live now".
    normalized["is_live"] = normalized["is_live_broadcast"]

    normalized["date"] = normalized["start_time"].date().isoformat()
    normalized["time"] = normalized["start_time"].strftime("%H:%M")
    if normalized["end_time"] is not None:
        normalized["estimated_broadcast_end_date"] = (
            normalized["end_time"].date().isoformat()
        )
        normalized["estimated_broadcast_end"] = (
            normalized["end_time"].strftime("%H:%M")
        )
    else:
        normalized.setdefault("estimated_broadcast_end_date", None)
        normalized.setdefault("estimated_broadcast_end", None)

    normalized.setdefault("end_estimation_method", None)
    normalized.setdefault("end_confidence", "unknown")

    updated_at = normalized.get("updated_at")
    if isinstance(updated_at, datetime):
        normalized["updated_at"] = _aware_datetime(
            updated_at,
            field="updated_at",
        )
    else:
        normalized["updated_at"] = datetime.now(KZ_TIMEZONE)

    normalized["event_key"] = str(
        normalized.get("event_key") or make_event_key(normalized)
    )

    validate_sport_event(normalized)
    return cast(SportEvent, normalized)


def validate_sport_event(event: dict[str, Any]) -> None:
    missing = [field for field in REQUIRED_EVENT_FIELDS if field not in event]
    if missing:
        raise ValueError(
            "SportEvent: отсутствуют обязательные поля: " + ", ".join(missing)
        )

    for field in (
        "event_key",
        "source",
        "source_url",
        "channel",
        "raw_title",
        "normalized_title",
        "title",
        "timezone",
    ):
        if not isinstance(event[field], str):
            raise TypeError(f"SportEvent: поле {field} должно быть строкой")

    if event["timezone"] != TIMEZONE_NAME:
        raise ValueError("SportEvent: timezone должен быть Asia/Almaty")

    for field in ("start_time", "updated_at"):
        if not isinstance(event[field], datetime):
            raise TypeError(f"SportEvent: {field} должен быть datetime")
        _aware_datetime(event[field], field=field)

    end_time = event.get("end_time")
    if end_time is not None:
        if not isinstance(end_time, datetime):
            raise TypeError("SportEvent: end_time должен быть datetime или None")
        _aware_datetime(end_time, field="end_time")
        if end_time <= event["start_time"]:
            raise ValueError("SportEvent: end_time должен быть позже start_time")

    if not isinstance(event["is_live_broadcast"], bool):
        raise TypeError("SportEvent: is_live_broadcast должен быть bool")

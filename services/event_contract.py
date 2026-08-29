from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from models import SportEvent


REQUIRED_EVENT_FIELDS = (
    "source",
    "source_url",
    "channel",
    "date",
    "time",
    "sport",
    "tournament",
    "title",
    "is_live",
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
    normalized["is_live"] = bool(normalized.get("is_live", False))

    normalized.setdefault("estimated_broadcast_end_date", None)
    normalized.setdefault("estimated_broadcast_end", None)
    normalized.setdefault("end_estimation_method", None)
    normalized.setdefault("end_confidence", "unknown")

    validate_sport_event(normalized)
    return cast(SportEvent, normalized)


def validate_sport_event(event: dict[str, Any]) -> None:
    """Проверяет только универсальный контракт, не бизнес-логику источника."""

    missing = [
        field
        for field in REQUIRED_EVENT_FIELDS
        if field not in event
    ]

    if missing:
        raise ValueError(
            "SportEvent: отсутствуют обязательные поля: "
            + ", ".join(missing)
        )

    for field in (
        "source",
        "source_url",
        "channel",
        "date",
        "time",
        "title",
        "raw_title",
    ):
        if not isinstance(event[field], str):
            raise TypeError(
                f"SportEvent: поле {field} должно быть строкой"
            )

    if not isinstance(event["is_live"], bool):
        raise TypeError(
            "SportEvent: поле is_live должно быть bool"
        )

    try:
        datetime.strptime(
            event["date"],
            "%Y-%m-%d",
        )
    except ValueError as error:
        raise ValueError(
            "SportEvent: date должен быть YYYY-MM-DD"
        ) from error

    try:
        datetime.strptime(
            event["time"],
            "%H:%M",
        )
    except ValueError as error:
        raise ValueError(
            "SportEvent: time должен быть HH:MM"
        ) from error

    end_date = event.get("estimated_broadcast_end_date")
    end_time = event.get("estimated_broadcast_end")

    if (end_date is None) != (end_time is None):
        raise ValueError(
            "SportEvent: дата и время окончания должны быть "
            "заданы вместе или оба быть None"
        )

    if end_date is not None:
        datetime.strptime(
            str(end_date),
            "%Y-%m-%d",
        )
        datetime.strptime(
            str(end_time),
            "%H:%M",
        )

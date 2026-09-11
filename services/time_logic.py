from __future__ import annotations

from datetime import datetime

from services.event_status import (
    KZ_TIMEZONE,
    get_event_status,
    get_event_window,
    get_fallback_duration_minutes,
)


MONTHS = {
    1: "января",
    2: "февраля",
    3: "марта",
    4: "апреля",
    5: "мая",
    6: "июня",
    7: "июля",
    8: "августа",
    9: "сентября",
    10: "октября",
    11: "ноября",
    12: "декабря",
}


def parse_kz_datetime(date_text: str, time_text: str) -> datetime:
    """Compatibility helper for parser/tests; always returns aware Almaty time."""
    return datetime.strptime(
        f"{date_text} {time_text}",
        "%Y-%m-%d %H:%M",
    ).replace(tzinfo=KZ_TIMEZONE)


def get_scheduled_datetimes(event: dict) -> tuple[datetime, datetime]:
    """Compatibility alias around the canonical status service."""
    return get_event_window(event)


def format_short_date(value: datetime) -> str:
    return f"{value.day} {MONTHS[value.month]}"


def format_full_date(value: datetime) -> str:
    return f"{value.day} {MONTHS[value.month]} {value.year}"


def get_time_window_text(event: dict) -> str:
    start, end = get_scheduled_datetimes(event)

    if start.date() == end.date():
        return f"{start:%H:%M}–{end:%H:%M}"

    return (
        f"{format_short_date(start)}, {start:%H:%M}–"
        f"{format_short_date(end)}, {end:%H:%M}"
    )


def get_end_text(event: dict) -> str:
    start, end = get_scheduled_datetimes(event)
    if start.date() == end.date():
        return end.strftime("%H:%M")
    return f"{format_short_date(end)}, {end:%H:%M}"


def get_end_full_text(event: dict) -> str:
    _, end = get_scheduled_datetimes(event)
    return f"{format_full_date(end)}, {end:%H:%M}"


def get_start_full_text(event: dict) -> str:
    start, _ = get_scheduled_datetimes(event)
    return f"{format_full_date(start)}, {start:%H:%M}"


__all__ = [
    "KZ_TIMEZONE",
    "get_event_status",
    "get_fallback_duration_minutes",
    "get_scheduled_datetimes",
    "get_time_window_text",
    "get_end_text",
    "get_end_full_text",
    "get_start_full_text",
    "parse_kz_datetime",
]

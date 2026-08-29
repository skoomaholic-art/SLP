from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

KZ_TIMEZONE = ZoneInfo("Asia/Almaty")

DEFAULT_DURATION_MINUTES = 120

SPORT_DURATION_MINUTES = {
    "футбол": 150,
    "хоккей": 150,
    "баскетбол": 150,
    "волейбол": 150,
    "теннис": 180,
    "бокс": 180,
    "дзюдо": 150,
    "биатлон": 120,
    "лыжный спорт": 120,
    "лёгкая атлетика": 180,
    "легкая атлетика": 180,
    "мотоспорт": 150,
    "снукер": 180,
}

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
    return datetime.strptime(
        f"{date_text} {time_text}",
        "%Y-%m-%d %H:%M",
    ).replace(tzinfo=KZ_TIMEZONE)


def get_fallback_duration_minutes(event: dict) -> int:
    sport = str(event.get("sport") or "").strip().casefold()
    return SPORT_DURATION_MINUTES.get(
        sport,
        DEFAULT_DURATION_MINUTES,
    )


def get_scheduled_datetimes(event: dict) -> tuple[datetime, datetime]:
    """Return broadcast start/end using TV schedule only.

    Priority for end time:
    1. End supplied by parser (usually next programme).
    2. If parser end accidentally resolves not later than start, roll it
       to the next calendar day.
    3. If no end exists, use a conservative sport-duration fallback.
    """
    start = parse_kz_datetime(
        event["date"],
        event["time"],
    )

    end_date = event.get("estimated_broadcast_end_date")
    end_time = event.get("estimated_broadcast_end")

    if end_date and end_time:
        end = parse_kz_datetime(end_date, end_time)

        if end <= start:
            end += timedelta(days=1)

        return start, end

    fallback_minutes = get_fallback_duration_minutes(event)
    return start, start + timedelta(minutes=fallback_minutes)


def get_event_status(
    event: dict,
    now: datetime | None = None,
) -> str:
    """Calculate LIVE/SOON/OVER strictly from broadcast schedule."""
    start, end = get_scheduled_datetimes(event)

    if now is None:
        now = datetime.now(KZ_TIMEZONE)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=KZ_TIMEZONE)
    else:
        now = now.astimezone(KZ_TIMEZONE)

    if now < start:
        return "upcoming"

    if now < end:
        return "live"

    return "finished"


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
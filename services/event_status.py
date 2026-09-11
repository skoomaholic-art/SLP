from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo


KZ_TIMEZONE = ZoneInfo("Asia/Almaty")
EventStatus = Literal["upcoming", "live_now", "on_air", "finished"]

DEFAULT_DURATION_MINUTES = 120
MAX_FALLBACK_DURATION_MINUTES = 360
SPORT_DURATION_MINUTES = {
    "футбол": 150,
    "хоккей": 150,
    "баскетбол": 150,
    "волейбол": 150,
    "теннис": 180,
    "бокс": 180,
    "mma": 180,
    "ufc": 180,
    "дзюдо": 150,
    "биатлон": 120,
    "лыжный спорт": 120,
    "лёгкая атлетика": 180,
    "легкая атлетика": 180,
    "мотоспорт": 150,
    "формула 1": 180,
    "снукер": 180,
}


def normalize_now(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(KZ_TIMEZONE)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(KZ_TIMEZONE)


def _aware(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} is timezone-naive")
    return value.astimezone(KZ_TIMEZONE)


def get_event_start(event: dict) -> datetime:
    value = event.get("start_time")
    if isinstance(value, datetime):
        return _aware(value, "start_time")

    date_text = str(event.get("date") or "")
    time_text = str(event.get("time") or "")
    return datetime.strptime(
        f"{date_text} {time_text}",
        "%Y-%m-%d %H:%M",
    ).replace(tzinfo=KZ_TIMEZONE)


def get_fallback_duration_minutes(event: dict) -> int:
    sport = str(event.get("sport") or "").strip().casefold()
    value = SPORT_DURATION_MINUTES.get(sport, DEFAULT_DURATION_MINUTES)
    return min(max(int(value), 1), MAX_FALLBACK_DURATION_MINUTES)


def _legacy_end(event: dict, start: datetime) -> datetime | None:
    end_date = event.get("estimated_broadcast_end_date")
    end_clock = event.get("estimated_broadcast_end")
    if not end_date or not end_clock:
        return None

    end = datetime.strptime(
        f"{end_date} {end_clock}",
        "%Y-%m-%d %H:%M",
    ).replace(tzinfo=KZ_TIMEZONE)
    if end <= start:
        end += timedelta(days=1)
    return end


def get_event_end(event: dict, next_event: dict | None = None) -> datetime:
    """Resolve a safe end time.

    Priority: explicit/source end, legacy parser end, next programme on the
    same channel, then a bounded sport-specific fallback.
    """
    start = get_event_start(event)

    value = event.get("end_time")
    if isinstance(value, datetime):
        end = _aware(value, "end_time")
        if end > start:
            return end

    legacy = _legacy_end(event, start)
    if legacy is not None:
        return legacy

    if next_event is not None:
        same_channel = str(next_event.get("channel") or "") == str(
            event.get("channel") or ""
        )
        if same_channel:
            candidate = get_event_start(next_event)
            if candidate > start:
                return candidate

    fallback = get_fallback_duration_minutes(event)
    return start + timedelta(minutes=fallback)


def get_event_window(
    event: dict,
    next_event: dict | None = None,
) -> tuple[datetime, datetime]:
    return get_event_start(event), get_event_end(event, next_event)


def get_event_status(
    event: dict,
    now: datetime | None = None,
    next_event: dict | None = None,
) -> EventStatus:
    current = normalize_now(now)
    start, end = get_event_window(event, next_event)

    if current < start:
        return "upcoming"
    if current >= end:
        return "finished"
    if bool(event.get("is_live_broadcast", event.get("is_live", False))):
        return "live_now"
    return "on_air"


def is_live_now(
    event: dict,
    now: datetime | None = None,
    next_event: dict | None = None,
) -> bool:
    return get_event_status(event, now, next_event) == "live_now"


def diagnose_live_candidate(
    event: dict,
    now: datetime | None = None,
    next_event: dict | None = None,
) -> dict:
    current = normalize_now(now)
    source_live = bool(
        event.get("is_live_broadcast", event.get("is_live", False))
    )

    try:
        start, end = get_event_window(event, next_event)
    except (TypeError, ValueError) as error:
        return {
            "included": False,
            "reason": "invalid_timezone",
            "error": str(error),
            "source_live": source_live,
            "now": current,
        }

    if not source_live:
        included = False
        reason = "source_not_live"
    elif current < start:
        included = False
        reason = "starts_in_future"
    elif current >= end:
        included = False
        reason = "event_finished"
    else:
        included = True
        reason = "live_now"

    return {
        "included": included,
        "reason": reason,
        "source_live": source_live,
        "status": get_event_status(event, current, next_event),
        "start": start,
        "end": end,
        "now": current,
    }


def log_live_candidates(
    events: list[dict],
    *,
    now: datetime | None = None,
    logger: logging.Logger | None = None,
) -> list[dict]:
    log = logger or logging.getLogger("slp.live")
    current = normalize_now(now)
    diagnostics: list[dict] = []

    by_channel: dict[str, list[dict]] = {}
    for event in events:
        by_channel.setdefault(str(event.get("channel") or ""), []).append(event)
    for channel_events in by_channel.values():
        channel_events.sort(key=get_event_start)

    for channel_events in by_channel.values():
        for index, event in enumerate(channel_events):
            if not bool(
                event.get("is_live_broadcast", event.get("is_live", False))
            ):
                continue
            next_event = (
                channel_events[index + 1]
                if index + 1 < len(channel_events)
                else None
            )
            info = diagnose_live_candidate(event, current, next_event)
            diagnostics.append(info)
            log.info(
                "LIVE candidate: channel=%s title=%s start=%s end=%s "
                "source_live=%s now=%s status=%s included=%s reason=%s",
                event.get("channel"),
                event.get("title") or event.get("raw_title"),
                info.get("start"),
                info.get("end"),
                info.get("source_live"),
                info.get("now"),
                info.get("status"),
                info.get("included"),
                info.get("reason"),
            )

    return diagnostics

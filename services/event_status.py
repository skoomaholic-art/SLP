from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable
from zoneinfo import ZoneInfo

KZ_TIMEZONE = ZoneInfo("Asia/Almaty")

DEFAULT_DURATION_MINUTES = 120
MAX_LIVE_DURATION_MINUTES = 360
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


@dataclass(frozen=True)
class LiveDecision:
    included: bool
    reason: str
    status: str
    source_live: bool
    start: datetime | None
    end: datetime | None


def normalize_now(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(KZ_TIMEZONE)
    if now.tzinfo is None or now.utcoffset() is None:
        return now.replace(tzinfo=KZ_TIMEZONE)
    return now.astimezone(KZ_TIMEZONE)


def is_live_broadcast(event: dict) -> bool:
    """Whether the source explicitly marks this programme as a live broadcast.

    ``is_live`` is read only as a migration fallback for snapshots created by
    older SLP versions. New code writes and reasons about ``is_live_broadcast``.
    """
    if "is_live_broadcast" in event:
        return bool(event.get("is_live_broadcast"))
    return bool(event.get("is_live", False))


def parse_kz_datetime(date_text: str, time_text: str) -> datetime:
    return datetime.strptime(
        f"{date_text} {time_text}",
        "%Y-%m-%d %H:%M",
    ).replace(tzinfo=KZ_TIMEZONE)


def _parse_aware_iso(value: object, *, field: str) -> datetime | None:
    if value in (None, ""):
        return None
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return parsed.astimezone(KZ_TIMEZONE)


def get_event_start(event: dict) -> datetime:
    explicit = _parse_aware_iso(event.get("start_time"), field="start_time")
    if explicit is not None:
        return explicit
    return parse_kz_datetime(str(event["date"]), str(event["time"]))


def get_fallback_duration_minutes(event: dict) -> int:
    sport = str(event.get("sport") or "").strip().casefold()
    return SPORT_DURATION_MINUTES.get(sport, DEFAULT_DURATION_MINUTES)


def _legacy_end(event: dict, start: datetime) -> datetime | None:
    end_date = event.get("estimated_broadcast_end_date")
    end_time = event.get("estimated_broadcast_end")
    if not end_date or not end_time:
        return None
    end = parse_kz_datetime(str(end_date), str(end_time))
    if end <= start:
        end += timedelta(days=1)
    return end


def _safe_end(start: datetime, end: datetime, source_live: bool) -> datetime:
    if end <= start:
        raise ValueError("end_time must be later than start_time")
    if source_live:
        hard_limit = start + timedelta(minutes=MAX_LIVE_DURATION_MINUTES)
        if end > hard_limit:
            return hard_limit
    return end


def _next_program_end(
    event: dict,
    start: datetime,
    next_event: dict | None,
) -> datetime | None:
    if next_event is None:
        return None
    if str(next_event.get("channel") or "") != str(event.get("channel") or ""):
        return None
    next_start = get_event_start(next_event)
    return next_start if next_start > start else None


def resolve_event_end(
    event: dict,
    *,
    next_event: dict | None = None,
) -> tuple[datetime, str]:
    """Resolve end: source/EPG -> next same-channel programme -> fallback."""
    start = get_event_start(event)
    source_live = is_live_broadcast(event)
    end_method = str(event.get("end_estimation_method") or "")

    explicit = _parse_aware_iso(event.get("end_time"), field="end_time")
    # A serialized fallback is not authoritative: if the batch now contains a
    # next same-channel programme, that boundary is more precise.
    if explicit is not None and end_method != "fallback_duration":
        return _safe_end(start, explicit, source_live), end_method or "source"

    legacy = _legacy_end(event, start)
    if legacy is not None and end_method != "fallback_duration":
        return _safe_end(start, legacy, source_live), end_method or "next_program"

    next_program = _next_program_end(event, start, next_event)
    if next_program is not None:
        return _safe_end(start, next_program, source_live), "next_program"

    if explicit is not None:
        return _safe_end(start, explicit, source_live), "fallback_duration"

    fallback = start + timedelta(minutes=get_fallback_duration_minutes(event))
    return _safe_end(start, fallback, source_live), "fallback_duration"


def get_scheduled_datetimes(
    event: dict,
    *,
    next_event: dict | None = None,
) -> tuple[datetime, datetime]:
    start = get_event_start(event)
    end, _ = resolve_event_end(event, next_event=next_event)
    return start, end


def get_event_status(
    event: dict,
    now: datetime | None = None,
    *,
    next_event: dict | None = None,
) -> str:
    """Return upcoming/live_now/current/finished without conflating LIVE flags."""
    current = normalize_now(now)
    start, end = get_scheduled_datetimes(event, next_event=next_event)

    if current < start:
        return "upcoming"
    if current >= end:
        return "finished"
    if is_live_broadcast(event):
        return "live_now"
    return "current"


def evaluate_live_candidate(
    event: dict,
    now: datetime | None = None,
    *,
    next_event: dict | None = None,
) -> LiveDecision:
    source_live = is_live_broadcast(event)
    if not source_live:
        return LiveDecision(False, "not_live_broadcast", "not_live_broadcast", False, None, None)

    current = normalize_now(now)
    try:
        start, end = get_scheduled_datetimes(event, next_event=next_event)
    except (KeyError, TypeError, ValueError) as error:
        return LiveDecision(False, f"invalid_timezone_or_datetime:{error}", "invalid", True, None, None)

    if current < start:
        return LiveDecision(False, "starts_in_future", "upcoming", True, start, end)
    if current >= end:
        return LiveDecision(False, "event_finished", "finished", True, start, end)
    return LiveDecision(True, "live_now", "live_now", True, start, end)


def _next_same_channel(event: dict, events: list[dict]) -> dict | None:
    try:
        start = get_event_start(event)
    except (KeyError, TypeError, ValueError):
        return None
    channel = str(event.get("channel") or "")
    candidates: list[tuple[datetime, dict]] = []
    for candidate in events:
        if candidate is event or str(candidate.get("channel") or "") != channel:
            continue
        try:
            candidate_start = get_event_start(candidate)
        except (KeyError, TypeError, ValueError):
            continue
        if candidate_start > start:
            candidates.append((candidate_start, candidate))
    return min(candidates, key=lambda item: item[0])[1] if candidates else None


def filter_live_now(
    events: Iterable[dict],
    now: datetime | None = None,
    *,
    logger: logging.Logger | None = None,
) -> list[dict]:
    """Return only broadcasts explicitly marked LIVE and actually airing now."""
    event_list = list(events)
    current = normalize_now(now)
    result: list[dict] = []

    for event in event_list:
        if not is_live_broadcast(event):
            continue
        decision = evaluate_live_candidate(
            event,
            current,
            next_event=_next_same_channel(event, event_list),
        )
        if logger is not None:
            logger.info(
                "LIVE candidate: channel=%s title=%s start=%s end=%s "
                "source_live=%s now=%s status=%s included=%s reason=%s",
                event.get("channel"),
                event.get("title") or event.get("raw_title"),
                decision.start.isoformat() if decision.start else None,
                decision.end.isoformat() if decision.end else None,
                decision.source_live,
                current.isoformat(),
                decision.status,
                decision.included,
                decision.reason,
            )
        if decision.included:
            result.append(event)

    return result

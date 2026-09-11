from __future__ import annotations

import asyncio
import logging
import os
from datetime import date, datetime, timedelta

import aiohttp

from parsers.qazsport import get_qazsport_schedule
from parsers.sportplus import BASE_URL as SPORTPLUS_URL
from parsers.sportplus import parse_sportplus_html
from services.event_status import KZ_TIMEZONE
from services.event_store import save_complete_snapshot, upsert_events
from services.schedule_merge import merge_source_schedules


LOGGER = logging.getLogger("slp.refresh")
DEFAULT_DISCOVERY_LOOKAHEAD_DAYS = 14


def nearest_monday(value: date) -> date:
    days = (7 - value.weekday()) % 7
    return value + timedelta(days=days)


def _discovery_days() -> int:
    raw = os.getenv("SLP_DISCOVERY_LOOKAHEAD_DAYS", "")
    try:
        return max(0, min(int(raw), 31)) if raw else DEFAULT_DISCOVERY_LOOKAHEAD_DAYS
    except ValueError:
        return DEFAULT_DISCOVERY_LOOKAHEAD_DAYS


def _date_range(start: date, end: date) -> list[date]:
    count = (end - start).days
    return [start + timedelta(days=offset) for offset in range(count + 1)]


def calculate_actual_horizon(
    events: list[dict],
    *,
    today: date,
    minimum_horizon: date,
) -> date:
    live_dates = [
        event["start_time"].astimezone(KZ_TIMEZONE).date()
        for event in events
        if bool(event.get("is_live_broadcast", False))
        and event["start_time"].astimezone(KZ_TIMEZONE).date() >= today
    ]
    return max([minimum_horizon, *live_dates])


async def _load_qazsport_day(
    target: date,
    today: date,
) -> tuple[date, list[dict], Exception | None]:
    try:
        events = await get_qazsport_schedule(
            target,
            include_current_live=(target == today),
        )
        return target, events, None
    except Exception as error:  # source boundary: keep refresh alive
        return target, [], error


async def _load_sportplus_days(
    days: list[date],
    reference: date,
) -> tuple[list[dict], Exception | None]:
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept-Language": "ru-RU,ru;q=0.9",
    }
    timeout = aiohttp.ClientTimeout(total=20)
    try:
        async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
            async with session.get(SPORTPLUS_URL) as response:
                response.raise_for_status()
                html = await response.text()
    except Exception as error:
        return [], error

    result: list[dict] = []
    for target in days:
        try:
            result.extend(
                parse_sportplus_html(
                    html,
                    target_date=target,
                    reference_date=reference,
                )
            )
        except RuntimeError:
            # A date outside the programme headers is normal while probing the
            # furthest published horizon.
            continue
    return result, None


async def refresh_schedule(
    *,
    now: datetime | None = None,
) -> tuple[list[dict], list[str], date]:
    """Refresh source data, persist it, and return the operational schedule.

    Required range is yesterday (for cross-midnight live events) through the
    nearest Monday. A bounded discovery window is probed beyond Monday so the
    final horizon expands to the furthest already-published LIVE broadcast.

    A new active SQLite snapshot is created only when all required sources are
    healthy. Partial data is stored for diagnosis without replacing the last
    complete snapshot.
    """
    current = now or datetime.now(KZ_TIMEZONE)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("refresh_schedule now must be timezone-aware")
    current = current.astimezone(KZ_TIMEZONE)

    today = current.date()
    required_end = nearest_monday(today)
    probe_end = required_end + timedelta(days=_discovery_days())
    days = _date_range(today - timedelta(days=1), probe_end)

    qaz_results, sportplus_result = await asyncio.gather(
        asyncio.gather(*[_load_qazsport_day(day, today) for day in days]),
        _load_sportplus_days(days, today),
    )

    required_days = set(_date_range(today, required_end))
    errors: list[str] = []
    qaz_events: list[dict] = []
    for target, events, error in qaz_results:
        qaz_events.extend(events)
        if error is not None:
            if target in required_days:
                errors.append(f"Qazsport {target.isoformat()}: {error!r}")
            else:
                LOGGER.debug("Qazsport probe %s unavailable: %r", target, error)

    sportplus_events, sportplus_error = sportplus_result
    if sportplus_error is not None:
        errors.append(f"Sport+ Qazaqstan: {sportplus_error!r}")

    merged = merge_source_schedules(qaz_events, sportplus_events)
    actual_horizon = calculate_actual_horizon(
        merged,
        today=today,
        minimum_horizon=required_end,
    )

    operational = [
        event
        for event in merged
        if today - timedelta(days=1)
        <= event["start_time"].astimezone(KZ_TIMEZONE).date()
        <= actual_horizon
    ]

    if errors:
        upsert_events(operational)
    else:
        save_complete_snapshot(operational)

    LOGGER.info(
        "Schedule refresh: events=%d live_marked=%d required_end=%s "
        "actual_horizon=%s complete_snapshot=%s errors=%d",
        len(operational),
        sum(1 for event in operational if event.get("is_live_broadcast")),
        required_end,
        actual_horizon,
        not errors,
        len(errors),
    )
    return operational, errors, actual_horizon

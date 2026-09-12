from __future__ import annotations

import asyncio
import time
from datetime import date, datetime, timedelta

import aiohttp
from bs4 import BeautifulSoup

from parsers.sportplus import (
    BASE_URL,
    CHANNEL,
    SOURCE,
    KZ_TIMEZONE,
    _coerce_date,
    _extract_headers,
    _extract_program_pairs,
    _split_program_blocks,
    is_direct_broadcast,
    parse_sportplus_title,
    time_to_minutes,
)
from services.event_contract import build_sport_event

_CACHE_TTL_SECONDS = 120
_html_cache: tuple[float, str] | None = None
_html_lock = asyncio.Lock()


async def fetch_sportplus_html(*, force_refresh: bool = False) -> str:
    global _html_cache
    now = time.monotonic()
    if not force_refresh and _html_cache and now - _html_cache[0] < _CACHE_TTL_SECONDS:
        return _html_cache[1]

    async with _html_lock:
        now = time.monotonic()
        if not force_refresh and _html_cache and now - _html_cache[0] < _CACHE_TTL_SECONDS:
            return _html_cache[1]

        headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept-Language": "ru-RU,ru;q=0.9",
        }
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
            async with session.get(BASE_URL) as response:
                response.raise_for_status()
                html = await response.text()

        _html_cache = (time.monotonic(), html)
        return html


def get_published_dates_from_html(
    html: str,
    *,
    reference_date: date,
) -> list[date]:
    soup = BeautifulSoup(html, "html.parser")
    strings = [" ".join(value.split()) for value in soup.stripped_strings]
    dates, _ = _extract_headers(strings, reference_date)
    return dates


def parse_sportplus_epg_html(
    html: str,
    target_date: date | datetime | str | None = None,
    *,
    reference_date: date | None = None,
) -> list[dict]:
    """Parse all EPG programmes; only explicit direct sports get LIVE=True."""
    requested_date = _coerce_date(target_date)
    reference = reference_date or requested_date

    soup = BeautifulSoup(html, "html.parser")
    strings = [" ".join(value.split()) for value in soup.stripped_strings]
    page_dates, last_header_index = _extract_headers(strings, reference)
    if not page_dates:
        raise RuntimeError("Sport+ Qazaqstan: не удалось определить даты телепрограммы")

    pairs = _extract_program_pairs(strings, last_header_index)
    blocks = _split_program_blocks(pairs)
    if requested_date not in page_dates:
        return []

    date_index = page_dates.index(requested_date)
    if date_index >= len(blocks):
        return []

    programs: list[dict] = []
    day_offset = 0
    previous_minutes: int | None = None
    for time_text, raw_title in blocks[date_index]:
        current_minutes = time_to_minutes(time_text)
        if previous_minutes is not None and current_minutes < previous_minutes:
            day_offset += 1
        actual_date = requested_date + timedelta(days=day_offset)
        programs.append(
            {
                "date": actual_date.isoformat(),
                "time": time_text,
                "raw_title": raw_title,
                "schedule_offset": current_minutes + day_offset * 1440,
            }
        )
        previous_minutes = current_minutes

    result: list[dict] = []
    for index, program in enumerate(programs):
        raw_title = program["raw_title"]
        parsed = parse_sportplus_title(raw_title)
        source_says_live = bool(is_direct_broadcast(raw_title) and parsed["sport"])

        if index + 1 < len(programs):
            next_program = programs[index + 1]
            end_date = next_program["date"]
            end_time = next_program["time"]
            end_method = "next_program"
            end_confidence = "high"
        else:
            end_date = None
            end_time = None
            end_method = None
            end_confidence = "unknown"

        event = {
            "date": program["date"],
            "time": program["time"],
            "channel": CHANNEL,
            "is_live_broadcast": source_says_live,
            "raw_title": raw_title,
            "sport": parsed["sport"],
            "tournament": parsed["tournament"],
            "title": parsed["title"],
            "raw_event_title": parsed["raw_event_title"],
            "raw_sport": parsed["sport"],
            "raw_tournament": parsed["tournament"],
            "schedule_offset": program["schedule_offset"],
            "estimated_broadcast_end_date": end_date,
            "estimated_broadcast_end": end_time,
            "end_estimation_method": end_method,
            "end_confidence": end_confidence,
        }
        result.append(build_sport_event(event, source=SOURCE, source_url=BASE_URL))

    return result


async def get_sportplus_epg_schedule(
    target_date: date | datetime | str | None = None,
) -> list[dict]:
    html = await fetch_sportplus_html()
    return parse_sportplus_epg_html(html, target_date=target_date)

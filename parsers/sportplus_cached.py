from __future__ import annotations

import asyncio
import logging
import time
from datetime import date, datetime

import aiohttp
from bs4 import BeautifulSoup

from parsers.sportplus import (
    BASE_URL,
    KZ_TIMEZONE,
    _extract_headers,
    parse_sportplus_html,
)


logger = logging.getLogger(__name__)
_CACHE_TTL_SECONDS = 45.0
_cache_html: str | None = None
_cache_monotonic = 0.0
_cache_lock: asyncio.Lock | None = None


def _get_lock() -> asyncio.Lock:
    global _cache_lock
    if _cache_lock is None:
        _cache_lock = asyncio.Lock()
    return _cache_lock


async def _fetch_tvguide_html() -> str:
    global _cache_html, _cache_monotonic

    current = time.monotonic()
    if _cache_html is not None and current - _cache_monotonic < _CACHE_TTL_SECONDS:
        return _cache_html

    async with _get_lock():
        current = time.monotonic()
        if _cache_html is not None and current - _cache_monotonic < _CACHE_TTL_SECONDS:
            return _cache_html

        headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept-Language": "ru-RU,ru;q=0.9",
        }
        timeout = aiohttp.ClientTimeout(total=15)
        last_error: Exception | None = None

        for attempt in range(1, 4):
            try:
                async with aiohttp.ClientSession(
                    headers=headers,
                    timeout=timeout,
                ) as session:
                    async with session.get(BASE_URL) as response:
                        response.raise_for_status()
                        html = await response.text()

                if not html.strip():
                    raise RuntimeError("Sport+ returned empty HTML")

                _cache_html = html
                _cache_monotonic = time.monotonic()
                logger.debug(
                    "[SPORTPLUS] tvguide fetched bytes=%d attempt=%d",
                    len(html),
                    attempt,
                )
                return html
            except Exception as error:
                last_error = error
                if attempt >= 3:
                    break
                logger.warning(
                    "[SPORTPLUS] tvguide retry attempt=%d/3",
                    attempt,
                    exc_info=True,
                )
                await asyncio.sleep(0.5 * (2 ** (attempt - 1)))

        assert last_error is not None
        raise last_error


async def get_sportplus_schedule_cached(
    target_date: date | datetime | str | None = None,
) -> list[dict]:
    """Fetch the multi-day guide once per refresh burst and parse one date."""
    html = await _fetch_tvguide_html()
    return parse_sportplus_html(html, target_date=target_date)


async def get_sportplus_available_dates(
    reference_date: date | None = None,
) -> list[date]:
    """Return dates explicitly published in the current official TV guide."""
    reference = reference_date or datetime.now(KZ_TIMEZONE).date()
    html = await _fetch_tvguide_html()
    soup = BeautifulSoup(html, "html.parser")
    strings = [
        " ".join(value.split())
        for value in soup.stripped_strings
    ]
    page_dates, _ = _extract_headers(strings, reference)
    return page_dates

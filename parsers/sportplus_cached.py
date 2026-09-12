from __future__ import annotations

import asyncio
import logging
import re
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
from services.broadcast_evidence import add_broadcast_evidence


logger = logging.getLogger(__name__)
_CACHE_TTL_SECONDS = 45.0
_cache_html: str | None = None
_cache_monotonic = 0.0
_cache_lock: asyncio.Lock | None = None
_TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


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


def extract_sportplus_on_air_times(html: str) -> set[str]:
    """Extract the official site's current-slot marker without calling it DIRECT.

    Sport+ uses ``div.blink a[title=LIVE]`` for the programme that is currently
    on air. The marker can appear on the anthem, so it must remain temporal
    evidence only and must never by itself create a sports LIVE event.
    """
    soup = BeautifulSoup(str(html or ""), "html.parser")
    result: set[str] = set()
    for blink in soup.select("div.blink"):
        link = blink.find("a")
        if link is None:
            continue
        marker = " ".join(
            str(value or "")
            for value in (link.get("title"), link.get_text(" ", strip=True))
        ).casefold()
        if "live" not in marker:
            continue
        row = blink.find_parent("li")
        if row is None:
            continue
        for span in row.find_all("span"):
            value = " ".join(span.stripped_strings)
            if _TIME_RE.fullmatch(value):
                result.add(value)
                break
    return result


def _annotate_sportplus_evidence(events: list[dict], html: str) -> list[dict]:
    on_air_times = extract_sportplus_on_air_times(html)
    result: list[dict] = []
    for event in events:
        item = add_broadcast_evidence(
            event,
            method="official_live_text",
            source="sportplustv.kz",
            value="Прямая трансляция",
            confidence="high",
        )
        if str(item.get("time") or "") in on_air_times:
            item = add_broadcast_evidence(
                item,
                method="official_on_air_marker",
                source="sportplustv.kz",
                value="div.blink a[title=LIVE]",
                confidence="high",
                on_air_now=True,
            )
        result.append(item)
    return result


async def get_sportplus_schedule_cached(
    target_date: date | datetime | str | None = None,
) -> list[dict]:
    """Fetch the multi-day guide once per refresh burst and parse one date."""
    html = await _fetch_tvguide_html()
    events = parse_sportplus_html(html, target_date=target_date)
    return _annotate_sportplus_evidence(events, html)


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

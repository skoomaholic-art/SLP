from __future__ import annotations

import asyncio
import hashlib
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
_STABLE_CHANGE_CONFIRMATIONS = 2
_cache_html: str | None = None
_cache_monotonic = 0.0
_cache_lock: asyncio.Lock | None = None
_candidate_html: str | None = None
_candidate_signature: str | None = None
_candidate_seen = 0
_TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


def _get_lock() -> asyncio.Lock:
    global _cache_lock
    if _cache_lock is None:
        _cache_lock = asyncio.Lock()
    return _cache_lock


def _canonical_schedule_signature(html: str) -> str:
    """Hash visible schedule text while ignoring transient DOM attributes.

    Sport+ can switch the current ``blink/LIVE`` marker without changing the
    schedule itself. Those attribute-only changes must not make a new guide
    revision. Visible text changes, including date headers and programme rows,
    do make a new revision.
    """
    soup = BeautifulSoup(str(html or ""), "html.parser")
    strings = [
        " ".join(value.split())
        for value in soup.stripped_strings
    ]
    payload = "\n".join(strings).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _select_stable_html(html: str) -> tuple[str, bool]:
    """Require the same changed guide twice before replacing the stable guide.

    The Sport+ page has occasionally exposed a transient one-day tab/content
    mismatch. Without this guard one refresh can move every future event by
    exactly 24 hours and the notification layer interprets it as real schedule
    changes. A genuine guide revision is accepted on the second consecutive
    observation (normally one scheduler interval later).
    """
    global _candidate_html, _candidate_signature, _candidate_seen

    if _cache_html is None:
        _candidate_html = None
        _candidate_signature = None
        _candidate_seen = 0
        return html, True

    signature = _canonical_schedule_signature(html)
    stable_signature = _canonical_schedule_signature(_cache_html)

    if signature == stable_signature:
        _candidate_html = None
        _candidate_signature = None
        _candidate_seen = 0
        # Keep the newest DOM so the current on-air marker can still update.
        return html, True

    if signature == _candidate_signature:
        _candidate_seen += 1
        _candidate_html = html
    else:
        _candidate_signature = signature
        _candidate_html = html
        _candidate_seen = 1

    if _candidate_seen >= _STABLE_CHANGE_CONFIRMATIONS:
        selected = _candidate_html or html
        _candidate_html = None
        _candidate_signature = None
        _candidate_seen = 0
        return selected, True

    return _cache_html, False


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

                selected_html, promoted = _select_stable_html(html)
                _cache_html = selected_html
                _cache_monotonic = time.monotonic()

                if promoted:
                    logger.debug(
                        "[SPORTPLUS] tvguide accepted bytes=%d attempt=%d",
                        len(html),
                        attempt,
                    )
                else:
                    logger.warning(
                        "[SPORTPLUS] tvguide revision held for confirmation candidate_seen=%d required=%d",
                        _candidate_seen,
                        _STABLE_CHANGE_CONFIRMATIONS,
                    )
                return _cache_html
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

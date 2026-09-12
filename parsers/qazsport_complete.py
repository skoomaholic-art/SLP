from __future__ import annotations

import logging
import re
from datetime import date, datetime

import aiohttp
from bs4 import BeautifulSoup

from parsers.qazsport import (
    build_url,
    get_qazsport_schedule,
    parse_qazsport_title,
)


logger = logging.getLogger(__name__)
TIME_PATTERN = re.compile(r"\b([0-2]\d:[0-5]\d)\b")
LIVE_TIME_PATTERN = re.compile(r"\bLIVE\s+(\d{2}:\d{2})\b", re.IGNORECASE)
LIVE_HTML_PATTERN = re.compile(
    r"(?:>\s*LIVE\s*<|"
    r"(?:alt|title|class|src|data-[\w-]+)\s*=\s*[\"'][^\"']*\blive\b[^\"']*[\"'])",
    re.IGNORECASE | re.DOTALL,
)


def extract_page_live_times(page_text: str) -> set[str]:
    """Extract visible ``LIVE HH:MM`` sequences from flattened page text."""
    return set(LIVE_TIME_PATTERN.findall(" ".join(str(page_text or "").split())))


def extract_html_live_times(page_html: str) -> set[str]:
    """Recover LIVE badges rendered outside an event anchor.

    Qazsport uses more than one markup shape. Some dates render the LIVE badge
    as text or an image/class between the previous programme and the next
    programme time. The old parser inspected only ``<a>`` text and therefore
    silently missed those broadcasts.

    For each clock occurrence we inspect only the HTML slice after the previous
    clock and before the current clock. A LIVE token/asset in that slice belongs
    to the current programme; an older programme's LIVE marker is outside it.
    """
    html = str(page_html or "")
    matches = list(TIME_PATTERN.finditer(html))
    live_times: set[str] = set()
    previous_end = 0

    for match in matches:
        between = html[previous_end : match.start()]
        if LIVE_HTML_PATTERN.search(between):
            live_times.add(match.group(1))
        previous_end = match.end()

    return live_times


def _mark_direct(event: dict, time_text: str) -> None:
    event["is_live"] = True
    event["is_live_broadcast"] = True
    event["live_state"] = "live"
    event["live_evidence_method"] = "qazsport_page_live_text"
    event["live_evidence_value"] = f"LIVE {time_text}"
    event["live_evidence_confidence"] = "high"
    event["is_sport_event"] = True

    parsed = parse_qazsport_title(str(event.get("raw_title") or ""))
    event["raw_sport"] = parsed.get("raw_sport", "")
    event["raw_tournament"] = parsed.get("raw_tournament", "")
    event["raw_event_title"] = parsed.get(
        "raw_event_title", event.get("raw_title", "")
    )
    event["sport"] = parsed.get("sport", "")
    event["tournament"] = parsed.get("tournament", "")
    event["title"] = parsed.get("title") or event.get("raw_title", "")


def apply_page_live_markers(
    events: list[dict],
    page_text: str,
    *,
    page_html: str = "",
) -> tuple[list[dict], set[str], set[str]]:
    live_times = extract_page_live_times(page_text)
    live_times.update(extract_html_live_times(page_html))

    matched: set[str] = set()
    for event in events:
        time_text = str(event.get("time") or "")
        if time_text not in live_times:
            continue
        matched.add(time_text)
        _mark_direct(event, time_text)

    return events, live_times, matched


async def _fetch_page(target_date: date | datetime | str | None) -> tuple[str, str]:
    url = build_url(target_date)
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(
        headers={"User-Agent": "Mozilla/5.0"}, timeout=timeout
    ) as session:
        async with session.get(url) as response:
            response.raise_for_status()
            html = await response.text()
    text = " ".join(BeautifulSoup(html, "html.parser").stripped_strings)
    return html, text


async def get_qazsport_schedule_complete(
    target_date: date | datetime | str | None = None,
    include_current_live: bool = True,
) -> list[dict]:
    """Qazsport schedule plus page-level LIVE-marker reconciliation."""
    events = await get_qazsport_schedule(
        target_date,
        include_current_live=include_current_live,
    )
    base_direct = sum(bool(event.get("is_live_broadcast", event.get("is_live"))) for event in events)

    try:
        page_html, page_text = await _fetch_page(target_date)
    except Exception:
        logger.warning(
            "qazsport page-level LIVE reconciliation unavailable date=%s",
            target_date,
            exc_info=True,
        )
        return events

    events, detected, matched = apply_page_live_markers(
        events,
        page_text,
        page_html=page_html,
    )
    final_direct = sum(bool(event.get("is_live_broadcast", event.get("is_live"))) for event in events)
    unmatched = sorted(detected - matched)

    logger.info(
        "qazsport LIVE reconciliation date=%s detected=%s matched=%s "
        "base_direct=%d final_direct=%d",
        target_date,
        sorted(detected),
        sorted(matched),
        base_direct,
        final_direct,
    )
    if unmatched:
        logger.warning(
            "qazsport page LIVE markers unmatched date=%s times=%s",
            target_date,
            unmatched,
        )
    return events

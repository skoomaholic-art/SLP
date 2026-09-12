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
LIVE_TIME_PATTERN = re.compile(r"\bLIVE\s+(\d{2}:\d{2})\b", re.IGNORECASE)


def extract_page_live_times(page_text: str) -> set[str]:
    """Extract page-level Qazsport LIVE markers.

    Qazsport sometimes renders ``LIVE`` as a sibling of the programme link,
    so inspecting only each ``<a>`` element misses legitimate broadcasts.
    """
    return set(LIVE_TIME_PATTERN.findall(" ".join(str(page_text or "").split())))


def apply_page_live_markers(events: list[dict], page_text: str) -> list[dict]:
    live_times = extract_page_live_times(page_text)
    if not live_times:
        return events

    matched: set[str] = set()
    for event in events:
        time_text = str(event.get("time") or "")
        if time_text not in live_times:
            continue

        matched.add(time_text)
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

    unmatched = sorted(live_times - matched)
    if unmatched:
        logger.warning("qazsport page LIVE markers unmatched times=%s", unmatched)
    return events


async def _fetch_page_text(target_date: date | datetime | str | None) -> str:
    url = build_url(target_date)
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(
        headers={"User-Agent": "Mozilla/5.0"}, timeout=timeout
    ) as session:
        async with session.get(url) as response:
            response.raise_for_status()
            html = await response.text()
    return " ".join(BeautifulSoup(html, "html.parser").stripped_strings)


async def get_qazsport_schedule_complete(
    target_date: date | datetime | str | None = None,
    include_current_live: bool = True,
) -> list[dict]:
    """Qazsport schedule plus page-level LIVE-marker reconciliation."""
    events = await get_qazsport_schedule(
        target_date,
        include_current_live=include_current_live,
    )
    try:
        page_text = await _fetch_page_text(target_date)
    except Exception:
        logger.warning(
            "qazsport page-level LIVE reconciliation unavailable date=%s",
            target_date,
            exc_info=True,
        )
        return events
    return apply_page_live_markers(events, page_text)

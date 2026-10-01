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
from services.agent_reach_web import read_public_url
from services.broadcast_evidence import add_broadcast_evidence


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


def _tag_has_live_badge(tag) -> bool:
    text = " ".join(tag.stripped_strings).strip().casefold()
    if text == "live":
        return True

    for attr in ("title", "alt", "src"):
        value = str(tag.get(attr) or "").casefold()
        if re.search(r"(?:^|[/_.-])live(?:[/_.-]|$)", value):
            return True

    classes = " ".join(str(value) for value in (tag.get("class") or [])).casefold()
    return bool(re.search(r"(?:^|[-_])live(?:[-_]|$)", classes))


def extract_row_live_times(page_html: str) -> set[str]:
    """Read Qazsport's LIVE badge from the same ``program-item`` row.

    The current Qazsport markup places a visible LIVE badge inside the anchor
    that also contains the programme time and title. This row-level evidence is
    preferred over flattened-page heuristics because it cannot drift to the
    neighbouring programme.
    """
    soup = BeautifulSoup(str(page_html or ""), "html.parser")
    live_times: set[str] = set()

    for row in soup.find_all("a"):
        row_text = " ".join(row.stripped_strings)
        time_match = TIME_PATTERN.search(row_text)
        if not time_match:
            continue
        if any(_tag_has_live_badge(tag) for tag in row.find_all(True)):
            live_times.add(time_match.group(1))

    return live_times


def extract_html_live_times(page_html: str) -> set[str]:
    """Legacy fallback for Qazsport markup with a badge outside the anchor."""
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


def _mark_direct(
    event: dict,
    time_text: str,
    *,
    method: str,
    confidence: str,
) -> bool:
    parsed = parse_qazsport_title(str(event.get("raw_title") or ""))
    event["raw_sport"] = parsed.get("raw_sport", "")
    event["raw_tournament"] = parsed.get("raw_tournament", "")
    event["raw_event_title"] = parsed.get(
        "raw_event_title", event.get("raw_title", "")
    )
    event["sport"] = parsed.get("sport", "")
    event["tournament"] = parsed.get("tournament", "")
    event["title"] = parsed.get("title") or event.get("raw_title", "")

    enriched = add_broadcast_evidence(
        event,
        method=method,
        source="qazsporttv.kz",
        value=f"LIVE {time_text}",
        confidence=confidence,
    )
    event.clear()
    event.update(enriched)

    # An official LIVE badge confirms a direct broadcast, but SLP publishes
    # sports only. A studio/anthem/non-sport row may be ON AIR and must not be
    # promoted into the sports feed solely because it carries a badge.
    if not str(event.get("sport") or "").strip():
        event["is_live"] = False
        event["is_live_broadcast"] = False
        event["is_sport_event"] = False
        return False

    event["is_live"] = True
    event["is_live_broadcast"] = True
    event["live_state"] = "live"
    event["live_evidence_method"] = method
    event["live_evidence_value"] = f"LIVE {time_text}"
    event["live_evidence_confidence"] = confidence
    event["is_sport_event"] = True
    return True


def apply_page_live_markers(
    events: list[dict],
    page_text: str,
    *,
    page_html: str = "",
) -> tuple[list[dict], set[str], set[str]]:
    row_live_times = extract_row_live_times(page_html)
    fallback_live_times = extract_page_live_times(page_text)
    fallback_live_times.update(extract_html_live_times(page_html))
    live_times = row_live_times | fallback_live_times

    matched: set[str] = set()
    for event in events:
        time_text = str(event.get("time") or "")
        if time_text not in live_times:
            continue
        matched.add(time_text)
        if time_text in row_live_times:
            _mark_direct(
                event,
                time_text,
                method="official_live_badge",
                confidence="high",
            )
        else:
            _mark_direct(
                event,
                time_text,
                method="qazsport_page_live_text",
                confidence="medium",
            )

    return events, live_times, matched


async def _fetch_page(target_date: date | datetime | str | None) -> tuple[str, str]:
    url = build_url(target_date)
    timeout = aiohttp.ClientTimeout(total=20)
    try:
        async with aiohttp.ClientSession(
            headers={"User-Agent": "Mozilla/5.0"}, timeout=timeout
        ) as session:
            async with session.get(url) as response:
                response.raise_for_status()
                html = await response.text()
        text = " ".join(BeautifulSoup(html, "html.parser").stripped_strings)
        return html, text
    except Exception as direct_error:
        logger.warning(
            "qazsport direct page fetch failed; trying Agent Reach date=%s error=%s",
            target_date,
            type(direct_error).__name__,
        )
        reader_text = await read_public_url(url, timeout_seconds=35)
        return "", reader_text


async def get_qazsport_schedule_complete(
    target_date: date | datetime | str | None = None,
    include_current_live: bool = True,
) -> list[dict]:
    """Qazsport schedule plus official row-level LIVE reconciliation."""
    events = await get_qazsport_schedule(
        target_date,
        include_current_live=include_current_live,
    )
    base_direct = sum(
        bool(event.get("is_live_broadcast", event.get("is_live")))
        for event in events
    )

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
    row_detected = extract_row_live_times(page_html)
    final_direct = sum(
        bool(event.get("is_live_broadcast", event.get("is_live")))
        for event in events
    )
    unmatched = sorted(detected - matched)

    logger.info(
        "qazsport LIVE reconciliation date=%s row_badges=%s detected=%s matched=%s "
        "base_direct=%d final_direct=%d",
        target_date,
        sorted(row_detected),
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

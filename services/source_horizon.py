from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

import aiohttp
from bs4 import BeautifulSoup

from parsers.qazsport import BASE_URL as QAZSPORT_BASE_URL, build_url
from parsers.sportplus_epg import fetch_sportplus_html, get_published_dates_from_html

QAZSPORT_DATE_HREF_RE = re.compile(r"/ru/program/(\d{4}-\d{2}-\d{2})(?:$|[?#/])")
MAX_DISCOVERY_DAYS = 31


@dataclass(frozen=True)
class HorizonDiscovery:
    dates_by_source: dict[str, set[date]]
    warnings: list[str]


def nearest_monday(value: date) -> date:
    return value + timedelta(days=(7 - value.weekday()) % 7)


def _within_discovery_window(value: date, today: date) -> bool:
    return today - timedelta(days=1) <= value <= today + timedelta(days=MAX_DISCOVERY_DAYS)


def extract_qazsport_published_dates(html: str, *, today: date) -> set[date]:
    soup = BeautifulSoup(html, "html.parser")
    result: set[date] = set()

    for element in soup.find_all("a", href=True):
        href = str(element.get("href") or "")
        match = QAZSPORT_DATE_HREF_RE.search(href)
        if not match:
            continue
        try:
            parsed = date.fromisoformat(match.group(1))
        except ValueError:
            continue
        if _within_discovery_window(parsed, today):
            result.add(parsed)

    return result


async def _fetch_text(url: str) -> str:
    headers = {"User-Agent": "Mozilla/5.0", "Accept-Language": "ru-RU,ru;q=0.9"}
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
        async with session.get(url) as response:
            response.raise_for_status()
            return await response.text()


async def discover_source_dates(today: date) -> HorizonDiscovery:
    """Discover dates actually published by each adapter without inventing data."""
    minimum_horizon = nearest_monday(today)
    dates_by_source: dict[str, set[date]] = {
        "qazsport": {today},
        "sportplus": {today},
    }
    warnings: list[str] = []

    try:
        html = await fetch_sportplus_html()
        dates_by_source["sportplus"].update(
            value
            for value in get_published_dates_from_html(html, reference_date=today)
            if _within_discovery_window(value, today)
        )
    except Exception as error:
        warnings.append(f"Sport+ horizon discovery: {type(error).__name__}: {error}")

    qazsport_anchors = {today, minimum_horizon}
    for anchor in sorted(qazsport_anchors):
        try:
            html = await _fetch_text(build_url(anchor))
        except Exception as error:
            warnings.append(
                f"Qazsport horizon {anchor.isoformat()}: {type(error).__name__}: {error}"
            )
            continue
        dates_by_source["qazsport"].add(anchor)
        dates_by_source["qazsport"].update(
            extract_qazsport_published_dates(html, today=today)
        )

    return HorizonDiscovery(dates_by_source=dates_by_source, warnings=warnings)

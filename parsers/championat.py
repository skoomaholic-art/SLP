from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from datetime import date, datetime
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import aiohttp
from bs4 import BeautifulSoup

from services.time_logic import KZ_TIMEZONE

logger = logging.getLogger(__name__)

BASE_URL = "https://www.championat.com"
PRIMARY_MATCH_CENTER = "/stat/?from=rasp"
MSK_TIMEZONE = ZoneInfo("Europe/Moscow")
CACHE_TTL_SECONDS = 300.0
DEFAULT_LOOKBACK_DAYS = 2
DEFAULT_LOOKAHEAD_DAYS = 7
REQUEST_TIMEOUT_SECONDS = 20
REQUEST_CONCURRENCY = 5

# Section homepages expose a compact server-rendered Match Center and do not
# require the /stat/* route. They are a fallback when /stat is redirected to
# SberID for datacenter traffic.
SECTION_PAGES = {
    "Футбол": "/football/",
    "Хоккей": "/hockey/",
    "Теннис": "/tennis/",
    "Баскетбол": "/basketball/",
    "Волейбол": "/volleyball/",
    "Автоспорт": "/auto/",
    "MMA": "/boxing/",
    "Биатлон": "/biathlon/",
    "Лыжи": "/ski/",
    "Фигурное катание": "/figureskating/",
}
_SPORT_BY_PATH = {
    "football": "Футбол",
    "hockey": "Хоккей",
    "tennis": "Теннис",
    "basketball": "Баскетбол",
    "volleyball": "Волейбол",
    "auto": "Автоспорт",
    "boxing": "MMA",
    "biathlon": "Биатлон",
    "ski": "Лыжи",
    "figureskating": "Фигурное катание",
    "other": "Прочее",
}

_DATE_TIME_RE = re.compile(r"(?<!\d)(\d{1,2})[.]([01]?\d)[.](20\d{2})\s+(\d{1,2}):(\d{2})(?!\d)")
_SHORT_DATE_TIME_RE = re.compile(r"(?<!\d)(\d{1,2})[.]([01]?\d)\s+(\d{1,2}):(\d{2})(?!\d)")
_TIME_RE = re.compile(r"(?<!\d)([0-2]?\d):(\d{2})(?!\d)")
_MATCH_HREF_RE = re.compile(r"/[^?#\s]+/match/\d+/?(?:[?#]|$)", re.I)
_STATUS_MARKERS = (
    "Не начался", "Не началось", "Окончен", "Окончено", "Идёт", "Идет",
    "Перерыв", "Отложен", "Отложено", "Перенесен", "Перенесён", "Отменен", "Отменён",
)


@dataclass
class ChampionatCalendar:
    events: list[dict]
    errors: list[str]
    fetched_dates: list[str]


_cache_lock = asyncio.Lock()
_cache_anchor: date | None = None
_cache_expires_at = 0.0
_cache_result = ChampionatCalendar(events=[], errors=[], fetched_dates=[])


def build_match_center_url(path: str) -> str:
    return urljoin(f"{BASE_URL}/", path.lstrip("/"))


def _sport_from_url(url: str, fallback: str = "") -> str:
    parts = [part.casefold() for part in urlparse(url).path.split("/") if part]
    return _SPORT_BY_PATH.get(parts[0], fallback) if parts else fallback


def _status_from_text(text: str) -> str:
    lowered = text.casefold()
    for marker in _STATUS_MARKERS:
        if marker.casefold() in lowered:
            return marker
    if re.search(r"\b(?:1-й|2-й|3-й|4-й)\s+(?:тайм|период|сет)\b", text, re.I):
        return "Идёт"
    return ""


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _container_text(anchor) -> str:
    best = _clean_text(anchor.get_text(" ", strip=True))
    node = anchor
    for _ in range(8):
        node = getattr(node, "parent", None)
        if node is None:
            break
        text = _clean_text(node.get_text(" ", strip=True))
        if not text or len(text) > 1400:
            continue
        if _TIME_RE.search(text):
            best = text
            if any(marker.casefold() in text.casefold() for marker in _STATUS_MARKERS):
                break
    return best


def _infer_year(anchor_date: date, month: int) -> int:
    if anchor_date.month == 12 and month == 1:
        return anchor_date.year + 1
    if anchor_date.month == 1 and month == 12:
        return anchor_date.year - 1
    return anchor_date.year


def _extract_start(text: str, anchor_date: date) -> datetime | None:
    full = _DATE_TIME_RE.search(text)
    if full:
        day, month, year, hour, minute = map(int, full.groups())
    else:
        short = _SHORT_DATE_TIME_RE.search(text)
        if short:
            day, month, hour, minute = map(int, short.groups())
            year = _infer_year(anchor_date, month)
        else:
            clock = _TIME_RE.search(text)
            if not clock:
                return None
            hour, minute = map(int, clock.groups())
            day, month, year = anchor_date.day, anchor_date.month, anchor_date.year
    if hour > 23 or minute > 59:
        return None
    try:
        source = datetime(year, month, day, hour, minute, tzinfo=MSK_TIMEZONE)
    except ValueError:
        return None
    return source.astimezone(KZ_TIMEZONE)


def parse_match_center_html(html: str, anchor_date: date, *, sport: str = "") -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    by_url: dict[str, dict] = {}
    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href") or "")
        if not _MATCH_HREF_RE.search(href):
            continue
        url = urljoin(f"{BASE_URL}/", href.split("#", 1)[0])
        raw_text = _container_text(anchor)
        start_kz = _extract_start(raw_text, anchor_date)
        if start_kz is None:
            continue
        anchor_text = _clean_text(anchor.get_text(" ", strip=True))
        title = anchor_text if 3 <= len(anchor_text) <= 240 else raw_text[:240]
        event = {
            "source": "championat",
            "source_url": url,
            "title": title,
            "raw_title": raw_text,
            "search_text": raw_text,
            "sport": _sport_from_url(url, sport),
            "status": _status_from_text(raw_text),
            "source_timezone": "Europe/Moscow",
            "timezone": "Asia/Almaty",
            "date": start_kz.date().isoformat(),
            "time": start_kz.strftime("%H:%M"),
            "start_at_kz": start_kz.isoformat(),
        }
        current = by_url.get(url)
        if current is None or len(raw_text) > len(str(current.get("raw_title") or "")):
            by_url[url] = event
    events = list(by_url.values())
    events.sort(key=lambda item: (item["date"], item["time"], item["source_url"]))
    return events


def _blocked_page_diagnostic(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    title = _clean_text(soup.title.get_text(" ", strip=True) if soup.title else "")[:120]
    preview = _clean_text(soup.get_text(" ", strip=True))[:200]
    return f"bytes={len(html)}:title={title!r}:text={preview!r}"


async def _download(session: aiohttp.ClientSession, url: str) -> tuple[str, str | None]:
    try:
        async with session.get(url) as response:
            if response.status != 200:
                return "", f"HTTP_{response.status}:final={response.url}"
            return await response.text(), None
    except Exception as error:
        return "", f"{type(error).__name__}:{error}"


async def _fetch_section(session, semaphore, anchor_date, sport, path):
    url = build_match_center_url(path)
    async with semaphore:
        html, error = await _download(session, url)
    if error:
        return [], f"{sport}:{error}"
    events = parse_match_center_html(html, anchor_date, sport=sport)
    if not events:
        return [], f"{sport}:no_events:{_blocked_page_diagnostic(html)}"
    return events, None


async def fetch_championat_calendar(anchor_date: date) -> ChampionatCalendar:
    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/128 Safari/537.36",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.5",
    }
    semaphore = asyncio.Semaphore(REQUEST_CONCURRENCY)
    errors: list[str] = []

    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        primary_url = build_match_center_url(PRIMARY_MATCH_CENTER)
        html, primary_error = await _download(session, primary_url)
        primary_events = parse_match_center_html(html, anchor_date) if html else []
        if primary_events:
            events = primary_events
            logger.info("championat transport=stat_rasp events=%d", len(events))
        else:
            if primary_error:
                errors.append(f"primary:{primary_error}")
            elif html:
                errors.append(f"primary:no_events:{_blocked_page_diagnostic(html)}")
            rows = await asyncio.gather(*(
                _fetch_section(session, semaphore, anchor_date, sport, path)
                for sport, path in SECTION_PAGES.items()
            ))
            events = []
            seen: set[str] = set()
            for page_events, error in rows:
                if error:
                    errors.append(error)
                for event in page_events:
                    url = str(event.get("source_url") or "")
                    if url and url in seen:
                        continue
                    if url:
                        seen.add(url)
                    events.append(event)
            if events:
                logger.info("championat transport=section_pages events=%d", len(events))

    events.sort(key=lambda item: (str(item.get("date") or ""), str(item.get("time") or ""), str(item.get("source_url") or "")))
    fetched_dates = sorted({str(event.get("date") or "") for event in events if event.get("date")})
    logger.info(
        "championat calendar fetched events=%d sports=%d dates=%s errors=%d timezone=Asia/Almaty",
        len(events), len({str(event.get("sport") or "") for event in events}), fetched_dates, len(errors),
    )
    return ChampionatCalendar(events=events, errors=errors, fetched_dates=fetched_dates)


async def get_championat_calendar(anchor: date | None = None, *, lookback_days=DEFAULT_LOOKBACK_DAYS, lookahead_days=DEFAULT_LOOKAHEAD_DAYS, force_refresh=False) -> ChampionatCalendar:
    del lookback_days, lookahead_days
    global _cache_anchor, _cache_expires_at, _cache_result
    anchor = anchor or datetime.now(KZ_TIMEZONE).date()
    async with _cache_lock:
        if force_refresh or _cache_anchor != anchor or time.monotonic() >= _cache_expires_at:
            _cache_result = await fetch_championat_calendar(anchor)
            _cache_anchor = anchor
            _cache_expires_at = time.monotonic() + CACHE_TTL_SECONDS
        return ChampionatCalendar(
            events=[dict(event) for event in _cache_result.events],
            errors=list(_cache_result.errors),
            fetched_dates=list(_cache_result.fetched_dates),
        )

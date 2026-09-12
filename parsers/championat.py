from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import aiohttp
from bs4 import BeautifulSoup

from services.time_logic import KZ_TIMEZONE

logger = logging.getLogger(__name__)

BASE_URL = "https://www.championat.com/stat/"
MSK_TIMEZONE = ZoneInfo("Europe/Moscow")
CACHE_TTL_SECONDS = 600.0
DEFAULT_LOOKBACK_DAYS = 2
DEFAULT_LOOKAHEAD_DAYS = 7
REQUEST_TIMEOUT_SECONDS = 20
REQUEST_CONCURRENCY = 4

_DATE_TIME_RE = re.compile(r"(?<!\d)(\d{1,2})[.]([01]?\d)[.](20\d{2})\s+(\d{1,2}):(\d{2})(?!\d)")
_TIME_RE = re.compile(r"(?<!\d)([0-2]?\d):(\d{2})(?!\d)")
_MATCH_HREF_RE = re.compile(r"/(?:football|hockey|tennis|basketball|volleyball|other|mma|auto|biathlon|ski|figureskating|cybersport|chess)/.+?/match/\d+/?", re.I)
_STATUS_MARKERS = (
    "Не начался",
    "Окончен",
    "Идёт",
    "Идет",
    "Перерыв",
    "Отложен",
    "Перенесен",
    "Перенесён",
    "Отменен",
    "Отменён",
)
_RU_MONTHS = {
    1: "января",
    2: "февраля",
    3: "марта",
    4: "апреля",
    5: "мая",
    6: "июня",
    7: "июля",
    8: "августа",
    9: "сентября",
    10: "октября",
    11: "ноября",
    12: "декабря",
}
_SPORT_BY_PATH = {
    "football": "Футбол",
    "hockey": "Хоккей",
    "tennis": "Теннис",
    "basketball": "Баскетбол",
    "volleyball": "Волейбол",
    "mma": "MMA",
    "auto": "Автоспорт",
    "biathlon": "Биатлон",
    "ski": "Лыжи",
    "figureskating": "Фигурное катание",
    "cybersport": "Киберспорт",
    "chess": "Шахматы",
    "other": "Прочее",
}


@dataclass
class ChampionatCalendar:
    events: list[dict]
    errors: list[str]
    fetched_dates: list[str]


_cache_lock = asyncio.Lock()
_cache_anchor: date | None = None
_cache_expires_at = 0.0
_cache_result = ChampionatCalendar(events=[], errors=[], fetched_dates=[])


def build_match_center_url(target_date: date) -> str:
    return f"{BASE_URL}?date={target_date.isoformat()}"


def _page_mentions_date(html: str, target_date: date) -> bool:
    markers = (
        target_date.isoformat(),
        target_date.strftime("%d.%m.%Y"),
        f"{target_date.day} {_RU_MONTHS[target_date.month]} {target_date.year}",
    )
    lowered = html.casefold()
    return any(marker.casefold() in lowered for marker in markers)


def _sport_from_url(url: str) -> str:
    parts = [part for part in urlparse(url).path.split("/") if part]
    return _SPORT_BY_PATH.get(parts[0].casefold(), "") if parts else ""


def _status_from_text(text: str) -> str:
    lowered = text.casefold()
    for marker in _STATUS_MARKERS:
        if marker.casefold() in lowered:
            return marker
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
        if not text or len(text) > 1800:
            continue
        if _TIME_RE.search(text):
            best = text
            if any(marker.casefold() in text.casefold() for marker in _STATUS_MARKERS):
                break
    return best


def _extract_start(text: str, target_date: date, *, page_date_confirmed: bool) -> datetime | None:
    full = _DATE_TIME_RE.search(text)
    if full:
        day, month, year, hour, minute = map(int, full.groups())
        try:
            source = datetime(year, month, day, hour, minute, tzinfo=MSK_TIMEZONE)
        except ValueError:
            return None
        return source.astimezone(KZ_TIMEZONE)

    if not page_date_confirmed:
        return None

    clock = _TIME_RE.search(text)
    if not clock:
        return None
    hour, minute = map(int, clock.groups())
    if hour > 23 or minute > 59:
        return None
    source = datetime(
        target_date.year,
        target_date.month,
        target_date.day,
        hour,
        minute,
        tzinfo=MSK_TIMEZONE,
    )
    return source.astimezone(KZ_TIMEZONE)


def parse_match_center_html(html: str, target_date: date) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    page_date_confirmed = _page_mentions_date(html, target_date)
    by_url: dict[str, dict] = {}

    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href") or "")
        if not _MATCH_HREF_RE.search(href):
            continue
        url = urljoin(BASE_URL, href.split("#", 1)[0])
        raw_text = _container_text(anchor)
        start_kz = _extract_start(
            raw_text,
            target_date,
            page_date_confirmed=page_date_confirmed,
        )
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
            "sport": _sport_from_url(url),
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


async def _fetch_day(session: aiohttp.ClientSession, target_date: date, semaphore: asyncio.Semaphore) -> tuple[date, list[dict], str | None]:
    url = build_match_center_url(target_date)
    try:
        async with semaphore:
            async with session.get(url) as response:
                if response.status != 200:
                    return target_date, [], f"{target_date.isoformat()}:HTTP_{response.status}"
                html = await response.text()
        events = parse_match_center_html(html, target_date)
        if not events and not _page_mentions_date(html, target_date):
            return target_date, [], f"{target_date.isoformat()}:date_not_present_in_response"
        return target_date, events, None
    except Exception as error:
        return target_date, [], f"{target_date.isoformat()}:{type(error).__name__}:{error}"


async def fetch_championat_calendar(
    dates: list[date],
) -> ChampionatCalendar:
    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; SLP/2.0; +https://github.com/skoomaholic-art/SLP)",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.5",
    }
    semaphore = asyncio.Semaphore(REQUEST_CONCURRENCY)
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        rows = await asyncio.gather(*(
            _fetch_day(session, target_date, semaphore)
            for target_date in dates
        ))

    events: list[dict] = []
    errors: list[str] = []
    fetched_dates: list[str] = []
    seen_urls: set[str] = set()
    for target_date, day_events, error in rows:
        if error:
            errors.append(error)
            continue
        fetched_dates.append(target_date.isoformat())
        for event in day_events:
            url = str(event.get("source_url") or "")
            if url and url in seen_urls:
                continue
            if url:
                seen_urls.add(url)
            events.append(event)

    events.sort(key=lambda item: (str(item.get("date") or ""), str(item.get("time") or ""), str(item.get("source_url") or "")))
    logger.info(
        "championat calendar fetched dates=%d events=%d errors=%d",
        len(fetched_dates),
        len(events),
        len(errors),
    )
    return ChampionatCalendar(events=events, errors=errors, fetched_dates=fetched_dates)


async def get_championat_calendar(
    anchor: date | None = None,
    *,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    lookahead_days: int = DEFAULT_LOOKAHEAD_DAYS,
    force_refresh: bool = False,
) -> ChampionatCalendar:
    global _cache_anchor, _cache_expires_at, _cache_result
    anchor = anchor or datetime.now(KZ_TIMEZONE).date()
    async with _cache_lock:
        if (
            force_refresh
            or _cache_anchor != anchor
            or time.monotonic() >= _cache_expires_at
        ):
            dates = [
                anchor + timedelta(days=offset)
                for offset in range(-lookback_days, lookahead_days + 1)
            ]
            _cache_result = await fetch_championat_calendar(dates)
            _cache_anchor = anchor
            _cache_expires_at = time.monotonic() + CACHE_TTL_SECONDS
        return ChampionatCalendar(
            events=[dict(event) for event in _cache_result.events],
            errors=list(_cache_result.errors),
            fetched_dates=list(_cache_result.fetched_dates),
        )

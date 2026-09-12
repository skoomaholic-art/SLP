from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import date, datetime

import aiohttp
from bs4 import BeautifulSoup

from services.time_logic import KZ_TIMEZONE


logger = logging.getLogger(__name__)
BASE_URLS = (
    "http://www.vsetv.com",
    "http://vsetv.com",
    "https://www.vsetv.com",
)
CACHE_TTL_SECONDS = 300.0
REQUEST_TIMEOUT_SECONDS = 20
RETRY_ATTEMPTS = 3
RETRY_BASE_DELAY_SECONDS = 0.75
RETRYABLE_STATUSES = {429, 500, 502, 503, 504}

# IDs verified against public VseTV channel pages. More channels can be added
# without changing the parser.
CHANNEL_IDS = {
    "Setanta Sports 1": 771,
    "Setanta Sports 2": 984,
}

_TIME_RE = re.compile(r"\b([0-2]?\d:[0-5]\d)\b")
_LIVE_SRC_RE = re.compile(r"(?:^|/)ico_live[.]gif(?:$|[?#])", re.I)
_DIRECT_TEXT_RE = re.compile(r"\bпрямая\s+трансляция\b", re.I)
_DATE_HEADING_RE = re.compile(
    r"(?:понедельник|вторник|среда|четверг|пятница|суббота|воскресенье)?"
    r"\s*,?\s*(\d{1,2})\s+"
    r"(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)",
    re.I,
)
_MONTHS = {
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
}

_cache_lock = asyncio.Lock()
_cache_expires_at = 0.0
_cache_week_key: str | None = None
_cache_rows_by_date: dict[str, list[dict]] = {}
_cache_errors: list[str] = []
_last_good_rows_by_date: dict[str, list[dict]] = {}


def build_week_url(channel_id: int, *, base_url: str | None = None) -> str:
    base = (base_url or BASE_URLS[0]).rstrip("/")
    return f"{base}/schedule_channel_{channel_id}_week.html"


def build_week_urls(channel_id: int) -> list[str]:
    return [build_week_url(channel_id, base_url=base_url) for base_url in BASE_URLS]


def _clean(value: str) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split())


def _week_key(value: date) -> str:
    monday = value.fromordinal(value.toordinal() - value.weekday())
    return monday.isoformat()


def _parse_heading_date(text: str, *, anchor_date: date) -> date | None:
    match = _DATE_HEADING_RE.search(_clean(text))
    if not match:
        return None

    day = int(match.group(1))
    month = _MONTHS[match.group(2).casefold()]
    year = anchor_date.year
    try:
        candidate = date(year, month, day)
    except ValueError:
        return None

    # VseTV week pages can cross New Year. Keep the parsed heading close to the
    # current Kazakhstan date rather than blindly forcing the current year.
    delta_days = (candidate - anchor_date).days
    if delta_days > 180:
        candidate = candidate.replace(year=year - 1)
    elif delta_days < -180:
        candidate = candidate.replace(year=year + 1)
    return candidate


def _programme_time(programme) -> str | None:
    # Day pages often keep the time as a direct sibling. Weekly pages can wrap
    # the programme in extra containers, so fall back to the nearest preceding
    # div.time in document order. This mirrors the actual VseTV DOM where every
    # programme's time is emitted immediately before its prname2 block.
    time_node = programme.find_previous_sibling("div", class_="time")
    if time_node is None:
        time_node = programme.find_previous("div", class_="time")
    if time_node is None:
        return None

    match = _TIME_RE.search(_clean(time_node.get_text(" ", strip=True)))
    if not match:
        return None
    return match.group(1).zfill(5)


def _programme_is_live(programme) -> bool:
    return programme.find(
        "img",
        src=lambda value: bool(value and _LIVE_SRC_RE.search(str(value))),
    ) is not None


def _programme_row(
    programme,
    *,
    channel: str,
    target_date: date,
    source_url: str,
) -> dict | None:
    if not _programme_is_live(programme):
        return None

    time_text = _programme_time(programme)
    if not time_text:
        return None

    title = _clean(programme.get_text(" ", strip=True))
    if not title:
        return None

    return {
        "source": "vsetv",
        "source_url": source_url,
        "channel": channel,
        "date": target_date.isoformat(),
        "time": time_text,
        "title": title,
        "raw_title": title,
        "third_party_live_badge": True,
        "explicit_direct_text": bool(_DIRECT_TEXT_RE.search(title)),
    }


def parse_vsetv_live_html(
    html: str,
    *,
    channel: str,
    target_date: date,
    source_url: str = "",
) -> list[dict]:
    """Extract LIVE programmes from one VseTV day fragment/page."""
    soup = BeautifulSoup(str(html or ""), "html.parser")
    rows: list[dict] = []
    seen: set[tuple[str, str]] = set()

    for programme in soup.select("div.prname2"):
        row = _programme_row(
            programme,
            channel=channel,
            target_date=target_date,
            source_url=source_url,
        )
        if row is None:
            continue
        key = (row["time"], row["title"].casefold())
        if key in seen:
            continue
        seen.add(key)
        rows.append(row)

    rows.sort(key=lambda row: (row["time"], row["title"]))
    return rows


def _nearest_programme_date(programme, *, anchor_date: date) -> date | None:
    # The weekly layout prints a heading like "Понедельник, 7 сентября"
    # before each schedule block. Walking previous text nodes is resilient to
    # the site's repeated schedule_container ids and extra wrapper elements.
    for text_node in programme.find_all_previous(string=True, limit=250):
        parsed = _parse_heading_date(str(text_node), anchor_date=anchor_date)
        if parsed is not None:
            return parsed
    return None


def parse_vsetv_week_html(
    html: str,
    *,
    channel: str,
    anchor_date: date,
    source_url: str = "",
) -> list[dict]:
    """Extract all VseTV LIVE markers from one weekly channel page."""
    soup = BeautifulSoup(str(html or ""), "html.parser")
    rows: list[dict] = []
    seen: set[tuple[str, str, str]] = set()

    for programme in soup.select("div.prname2"):
        if not _programme_is_live(programme):
            continue
        programme_date = _nearest_programme_date(programme, anchor_date=anchor_date)
        if programme_date is None:
            continue
        row = _programme_row(
            programme,
            channel=channel,
            target_date=programme_date,
            source_url=source_url,
        )
        if row is None:
            continue
        key = (row["date"], row["time"], row["title"].casefold())
        if key in seen:
            continue
        seen.add(key)
        rows.append(row)

    rows.sort(key=lambda row: (row["date"], row["time"], row["title"]))
    return rows


async def _request_with_retry(
    session: aiohttp.ClientSession,
    url: str,
) -> tuple[str | None, str | None, str | None]:
    last_error: str | None = None

    for attempt in range(RETRY_ATTEMPTS):
        retryable = True
        try:
            async with session.get(url, allow_redirects=True) as response:
                final_url = str(response.url)
                if response.status == 200:
                    return await response.text(), final_url, None
                last_error = f"{url}:HTTP_{response.status}"
                retryable = response.status in RETRYABLE_STATUSES
        except Exception as error:
            last_error = f"{url}:{type(error).__name__}:{error}"

        if not retryable or attempt + 1 >= RETRY_ATTEMPTS:
            break
        await asyncio.sleep(RETRY_BASE_DELAY_SECONDS * (2**attempt))

    return None, None, last_error or f"{url}:unknown_error"


async def _fetch_week_channel(
    session: aiohttp.ClientSession,
    channel: str,
    channel_id: int,
    anchor_date: date,
) -> tuple[list[dict], str | None]:
    errors: list[str] = []

    # Fetch one weekly page per channel, sequentially. This avoids hammering
    # VseTV with one request per EPG date and greatly reduces 503 responses.
    for url in build_week_urls(channel_id):
        html, final_url, error = await _request_with_retry(session, url)
        if html is None:
            if error:
                errors.append(error)
            continue

        rows = parse_vsetv_week_html(
            html,
            channel=channel,
            anchor_date=anchor_date,
            source_url=final_url or url,
        )
        logger.info(
            "vsetv weekly fetched channel=%s url=%s rows=%d dates=%s",
            channel,
            final_url or url,
            len(rows),
            sorted({row["date"] for row in rows}),
        )
        return rows, None

    return [], f"{channel}:" + " | ".join(errors)


def _copy_rows(rows: list[dict]) -> list[dict]:
    return [dict(row) for row in rows]


async def get_vsetv_live_evidence(
    target_date: date | datetime | str | None = None,
    *,
    force_refresh: bool = False,
) -> tuple[list[dict], list[str]]:
    global _cache_expires_at, _cache_week_key, _cache_rows_by_date, _cache_errors
    global _last_good_rows_by_date

    now_date = datetime.now(KZ_TIMEZONE).date()
    if target_date is None:
        requested = now_date
    elif isinstance(target_date, datetime):
        requested = (
            target_date.astimezone(KZ_TIMEZONE).date()
            if target_date.tzinfo
            else target_date.date()
        )
    elif isinstance(target_date, date):
        requested = target_date
    else:
        requested = datetime.strptime(str(target_date), "%Y-%m-%d").date()

    # VseTV's /week.html endpoint represents the site's currently selected TV
    # week. All date lookups during one SLP refresh share this single fetch.
    key = _week_key(now_date)
    async with _cache_lock:
        if (
            not force_refresh
            and _cache_week_key == key
            and time.monotonic() < _cache_expires_at
        ):
            return _copy_rows(_cache_rows_by_date.get(requested.isoformat(), [])), list(
                _cache_errors
            )

        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/140.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.5",
            "Connection": "keep-alive",
        }

        rows: list[dict] = []
        errors: list[str] = []
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            # Intentionally sequential: VseTV is sensitive to bursts from
            # datacenter IPs and responded with 503 under parallel day fetches.
            for channel, channel_id in CHANNEL_IDS.items():
                page_rows, error = await _fetch_week_channel(
                    session,
                    channel,
                    channel_id,
                    now_date,
                )
                rows.extend(page_rows)
                if error:
                    errors.append(error)
                await asyncio.sleep(0.35)

        rows_by_date: dict[str, list[dict]] = {}
        for row in rows:
            rows_by_date.setdefault(str(row["date"]), []).append(row)

        if rows:
            _last_good_rows_by_date = {
                day: _copy_rows(day_rows) for day, day_rows in rows_by_date.items()
            }
        elif errors and _last_good_rows_by_date:
            # A temporary 503 must not erase evidence that was fetched minutes
            # earlier. The cache is short-lived and evidence remains third-party
            # only, so last-good reuse is safe and observable in logs.
            rows_by_date = {
                day: _copy_rows(day_rows)
                for day, day_rows in _last_good_rows_by_date.items()
            }
            logger.warning(
                "vsetv weekly fetch failed; using in-process last-good rows dates=%s",
                sorted(rows_by_date),
            )

        _cache_week_key = key
        _cache_rows_by_date = rows_by_date
        _cache_errors = errors
        _cache_expires_at = time.monotonic() + CACHE_TTL_SECONDS

        requested_rows = _copy_rows(rows_by_date.get(requested.isoformat(), []))
        logger.info(
            "vsetv LIVE evidence week=%s requested=%s channels=%d total_rows=%d "
            "returned=%d dates=%s errors=%d",
            key,
            requested.isoformat(),
            len(CHANNEL_IDS),
            sum(len(day_rows) for day_rows in rows_by_date.values()),
            len(requested_rows),
            sorted(rows_by_date),
            len(errors),
        )
        return requested_rows, list(errors)

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
    "https://www.vsetv.com",
)
CACHE_TTL_SECONDS = 180.0
REQUEST_TIMEOUT_SECONDS = 15

# IDs verified against public VseTV channel pages. More channels can be added
# without changing the parser.
CHANNEL_IDS = {
    "Setanta Sports 1": 771,
    "Setanta Sports 2": 984,
}

_TIME_RE = re.compile(r"\b([0-2]?\d:[0-5]\d)\b")
_LIVE_SRC_RE = re.compile(r"(?:^|/)ico_live[.]gif(?:$|[?#])", re.I)
_DIRECT_TEXT_RE = re.compile(r"\bпрямая\s+трансляция\b", re.I)

_cache_lock = asyncio.Lock()
_cache_expires_at = 0.0
_cache_key: str | None = None
_cache_rows: list[dict] = []
_cache_errors: list[str] = []


def build_day_url(channel_id: int, target_date: date, *, base_url: str | None = None) -> str:
    base = (base_url or BASE_URLS[0]).rstrip("/")
    return (
        f"{base}/schedule_channel_{channel_id}_day_"
        f"{target_date.isoformat()}.html"
    )


def build_day_urls(channel_id: int, target_date: date) -> list[str]:
    return [
        build_day_url(channel_id, target_date, base_url=base_url)
        for base_url in BASE_URLS
    ]


def _clean(value: str) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split())


def parse_vsetv_live_html(
    html: str,
    *,
    channel: str,
    target_date: date,
    source_url: str = "",
) -> list[dict]:
    """Extract programmes carrying VseTV's ``ico_live.gif`` marker.

    The marker is attached to the concrete ``div.prname2`` programme row. It is
    recorded as third-party broadcast evidence; it never confirms DIRECT by
    itself because VseTV is an EPG aggregator rather than the broadcaster.
    """
    soup = BeautifulSoup(str(html or ""), "html.parser")
    rows: list[dict] = []
    seen: set[tuple[str, str]] = set()

    for programme in soup.select("div.prname2"):
        live_icon = programme.find(
            "img",
            src=lambda value: bool(value and _LIVE_SRC_RE.search(str(value))),
        )
        if live_icon is None:
            continue

        time_node = programme.find_previous_sibling("div", class_="time")
        if time_node is None:
            # Some page layouts wrap the pair in an extra container.
            parent = programme.parent
            time_node = parent.find("div", class_="time") if parent else None
        if time_node is None:
            continue

        time_match = _TIME_RE.search(_clean(time_node.get_text(" ", strip=True)))
        if not time_match:
            continue

        title = _clean(programme.get_text(" ", strip=True))
        if not title:
            continue

        key = (time_match.group(1).zfill(5), title.casefold())
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "source": "vsetv",
                "source_url": source_url,
                "channel": channel,
                "date": target_date.isoformat(),
                "time": key[0],
                "title": title,
                "raw_title": title,
                "third_party_live_badge": True,
                "explicit_direct_text": bool(_DIRECT_TEXT_RE.search(title)),
            }
        )

    rows.sort(key=lambda row: (row["time"], row["title"]))
    return rows


async def _fetch_one(
    session: aiohttp.ClientSession,
    channel: str,
    channel_id: int,
    target_date: date,
) -> tuple[list[dict], str | None]:
    errors: list[str] = []

    # VseTV is still served over plain HTTP in some environments. Railway's
    # current egress cannot establish a TLS connection to vsetv.com:443, while
    # the same public pages are available on port 80. Try HTTP first and retain
    # HTTPS only as a fallback so a future site migration does not break us.
    for url in build_day_urls(channel_id, target_date):
        try:
            async with session.get(url, allow_redirects=True) as response:
                if response.status != 200:
                    errors.append(f"{url}:HTTP_{response.status}")
                    continue
                html = await response.text()
        except Exception as error:
            errors.append(f"{url}:{type(error).__name__}:{error}")
            continue

        rows = parse_vsetv_live_html(
            html,
            channel=channel,
            target_date=target_date,
            source_url=str(response.url),
        )
        logger.debug(
            "vsetv fetched channel=%s date=%s url=%s rows=%d",
            channel,
            target_date,
            response.url,
            len(rows),
        )
        return rows, None

    return [], f"{channel}:" + " | ".join(errors)


async def get_vsetv_live_evidence(
    target_date: date | datetime | str | None = None,
    *,
    force_refresh: bool = False,
) -> tuple[list[dict], list[str]]:
    global _cache_expires_at, _cache_key, _cache_rows, _cache_errors

    if target_date is None:
        requested = datetime.now(KZ_TIMEZONE).date()
    elif isinstance(target_date, datetime):
        requested = target_date.astimezone(KZ_TIMEZONE).date() if target_date.tzinfo else target_date.date()
    elif isinstance(target_date, date):
        requested = target_date
    else:
        requested = datetime.strptime(str(target_date), "%Y-%m-%d").date()

    key = requested.isoformat()
    async with _cache_lock:
        if (
            not force_refresh
            and _cache_key == key
            and time.monotonic() < _cache_expires_at
        ):
            return [dict(row) for row in _cache_rows], list(_cache_errors)

        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.5",
        }
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            loaded = await asyncio.gather(*(
                _fetch_one(session, channel, channel_id, requested)
                for channel, channel_id in CHANNEL_IDS.items()
            ))

        rows: list[dict] = []
        errors: list[str] = []
        for page_rows, error in loaded:
            rows.extend(page_rows)
            if error:
                errors.append(error)

        _cache_key = key
        _cache_rows = rows
        _cache_errors = errors
        _cache_expires_at = time.monotonic() + CACHE_TTL_SECONDS
        logger.info(
            "vsetv LIVE evidence date=%s channels=%d rows=%d errors=%d",
            key,
            len(CHANNEL_IDS),
            len(rows),
            len(errors),
        )
        return [dict(row) for row in rows], list(errors)

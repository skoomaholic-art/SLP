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

from services.agent_reach_web import read_public_url\nfrom services.time_logic import KZ_TIMEZONE

logger = logging.getLogger(__name__)

BASE_URL = "https://www.championat.com"
PRIMARY_MATCH_CENTER = "/stat/?from=rasp"
READER_BASE_URL = "https://r.jina.ai/https://www.championat.com"
MSK_TIMEZONE = ZoneInfo("Europe/Moscow")
CACHE_TTL_SECONDS = 300.0
DEFAULT_LOOKBACK_DAYS = 2
DEFAULT_LOOKAHEAD_DAYS = 7
REQUEST_TIMEOUT_SECONDS = 30
REQUEST_CONCURRENCY = 5
# A healthy all-sports match center normally contains far more than this.
# Below this threshold we batch-fetch sport-specific reader pages and merge them.
MIN_ROOT_READER_EVENTS = 25

SECTION_PAGES = {
    "Футбол": "/stat/football/",
    "Хоккей": "/stat/hockey/",
    "Теннис": "/stat/tennis/",
    "Баскетбол": "/stat/basketball/",
    "Волейбол": "/stat/volleyball/",
    "Автоспорт": "/stat/auto/",
    "MMA": "/stat/boxing/",
    "Биатлон": "/stat/biathlon/",
    "Лыжи": "/stat/ski/",
    "Фигурное катание": "/stat/figureskating/",
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
_SPORT_HEADINGS = {
    "футбол": "Футбол",
    "хоккей": "Хоккей",
    "теннис": "Теннис",
    "баскетбол": "Баскетбол",
    "волейбол": "Волейбол",
    "авто": "Автоспорт",
    "автоспорт": "Автоспорт",
    "мма": "MMA",
    "бокс": "MMA",
    "бокс/мма": "MMA",
    "биатлон": "Биатлон",
    "лыжи": "Лыжи",
    "фигурное катание": "Фигурное катание",
    "футзал": "Футзал",
    "регби": "Регби",
    "гандбол": "Гандбол",
    "шахматы": "Шахматы",
    "киберспорт": "Киберспорт",
    "прочие": "Прочее",
}
_NON_MATCH_SPORTS = {
    "Автоспорт",
    "Биатлон",
    "Лыжи",
    "Фигурное катание",
    "Прочее",
}

_DATE_TIME_RE = re.compile(
    r"(?<!\d)(\d{1,2})[.]([01]?\d)[.](20\d{2})\s+(\d{1,2}):(\d{2})(?!\d)"
)
_SHORT_DATE_TIME_RE = re.compile(
    r"(?<!\d)(\d{1,2})[.]([01]?\d)\s+(\d{1,2}):(\d{2})(?!\d)"
)
_TIME_RE = re.compile(r"(?<!\d)([0-2]?\d):(\d{2})(?!\d)")
_MATCH_HREF_RE = re.compile(r"/[^?#\s]+/match/\d+/?(?:[?#]|$)", re.I)
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_TEXT_EVENT_RE = re.compile(
    r"^\s*(?:[-*•]\s*)?"
    r"(?:(\d{1,2})[.]([01]?\d)(?:[.](20\d{2}))?\s+)?"
    r"([0-2]?\d):(\d{2})\s+(.+?)\s*$"
)
# Hyphens inside club names (e.g. Динамо-Барнаул) are not separators unless
# surrounded by spaces. En/em dashes may be compact on Championat home widgets.
_MATCH_SEPARATOR_RE = re.compile(
    r"(?:\s+-\s+|\s*[–—]\s*|\s+(?:vs\.?|v\.)\s+)",
    re.I,
)
_SCORE_RE = re.compile(r"\s+\d+\s*:\s*\d+(?:\s*\([^)]*\))?")
_STATUS_RE = re.compile(
    r"\s+(?:Не начался|Не началось|Окончен|Окончено|Идёт|Идет|Перерыв|"
    r"Отложен|Отложено|Перенесен|Перенесён|Отменен|Отменён|"
    r"\d+-й\s+(?:тайм|период|сет)|\d+-я\s+партия)(?:\b|[,'])",
    re.I,
)
_STATUS_MARKERS = (
    "Не начался",
    "Не началось",
    "Окончен",
    "Окончено",
    "Идёт",
    "Идет",
    "Перерыв",
    "Отложен",
    "Отложено",
    "Перенесен",
    "Перенесён",
    "Отменен",
    "Отменён",
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


def build_reader_url(path: str) -> str:
    normalized = "/" + path.lstrip("/")
    return f"{READER_BASE_URL}{normalized}"


def _sport_from_url(url: str, fallback: str = "") -> str:
    parts = [part.casefold() for part in urlparse(url).path.split("/") if part]
    if not parts:
        return fallback
    if parts[0] == "stat" and len(parts) > 1:
        return _SPORT_BY_PATH.get(parts[1], fallback)
    return _SPORT_BY_PATH.get(parts[0], fallback)


def _status_from_text(text: str) -> str:
    lowered = text.casefold()
    for marker in _STATUS_MARKERS:
        if marker.casefold() in lowered:
            return marker
    if re.search(
        r"\b(?:1-й|2-й|3-й|4-й)\s+(?:тайм|период|сет)\b|\b\d+-я\s+партия\b",
        text,
        re.I,
    ):
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
            if _status_from_text(text):
                break
    return best


def _infer_year(anchor_date: date, month: int) -> int:
    if anchor_date.month == 12 and month == 1:
        return anchor_date.year + 1
    if anchor_date.month == 1 and month == 12:
        return anchor_date.year - 1
    return anchor_date.year


def _to_kz_datetime(
    year: int,
    month: int,
    day: int,
    hour: int,
    minute: int,
) -> datetime | None:
    if hour > 23 or minute > 59:
        return None
    try:
        source = datetime(year, month, day, hour, minute, tzinfo=MSK_TIMEZONE)
    except ValueError:
        return None
    return source.astimezone(KZ_TIMEZONE)


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
    return _to_kz_datetime(year, month, day, hour, minute)


def _event_dict(
    *,
    title: str,
    raw_text: str,
    sport: str,
    start_kz: datetime,
    source_url: str,
    tournament: str = "",
) -> dict:
    return {
        "source": "championat",
        "source_url": source_url,
        "title": title,
        "raw_title": raw_text,
        "search_text": " ".join(part for part in (tournament, raw_text) if part),
        "sport": sport,
        "tournament": tournament,
        "status": _status_from_text(raw_text),
        "source_timezone": "Europe/Moscow",
        "timezone": "Asia/Almaty",
        "date": start_kz.date().isoformat(),
        "time": start_kz.strftime("%H:%M"),
        "start_at_kz": start_kz.isoformat(),
    }


def parse_match_center_html(
    html: str,
    anchor_date: date,
    *,
    sport: str = "",
) -> list[dict]:
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
        event = _event_dict(
            title=title,
            raw_text=raw_text,
            sport=_sport_from_url(url, sport),
            start_kz=start_kz,
            source_url=url,
        )
        current = by_url.get(url)
        if current is None or len(raw_text) > len(str(current.get("raw_title") or "")):
            by_url[url] = event
    events = list(by_url.values())
    events.sort(key=lambda item: (item["date"], item["time"], item["source_url"]))
    return events


def _strip_markdown(value: str) -> str:
    value = _MARKDOWN_LINK_RE.sub(r"\1", value)
    value = value.replace("**", "").replace("__", "")
    return _clean_text(value)


def _match_title_from_tail(tail: str) -> str:
    clean = _strip_markdown(tail)
    score = _SCORE_RE.search(clean)
    status = _STATUS_RE.search(clean)
    cut_positions = [match.start() for match in (score, status) if match]
    if cut_positions:
        clean = clean[: min(cut_positions)].strip()
    clean = re.sub(r"\s+[-–—]\s*$", "", clean).strip()
    return clean


def _looks_like_tournament_heading(line: str) -> bool:
    if not line or len(line) > 180:
        return False
    if _TIME_RE.search(line):
        return False
    lowered = line.casefold()
    if lowered in _SPORT_HEADINGS:
        return False
    return not line.startswith(("[", "(", "http"))


def parse_match_center_text(
    text: str,
    anchor_date: date,
    *,
    sport: str = "",
    source_url: str | None = None,
) -> list[dict]:
    """Parse reader/search-friendly Match Center text without DOM classes."""
    events: list[dict] = []
    current_sport = sport
    current_tournament = ""
    seen: set[tuple[str, str, str]] = set()
    source_url = source_url or build_match_center_url(PRIMARY_MATCH_CENTER)

    for raw_line in str(text or "").splitlines():
        line = _strip_markdown(raw_line).strip(" #\t")
        if not line:
            continue

        heading = _SPORT_HEADINGS.get(line.casefold())
        if heading:
            current_sport = heading
            current_tournament = ""
            continue

        match = _TEXT_EVENT_RE.match(line)
        if not match:
            if current_sport and _looks_like_tournament_heading(line):
                current_tournament = line
            continue

        day_text, month_text, year_text, hour_text, minute_text, tail = match.groups()
        raw_tail = _strip_markdown(tail)
        title = _match_title_from_tail(raw_tail)
        has_separator = bool(_MATCH_SEPARATOR_RE.search(title))
        if not title or (not has_separator and current_sport not in _NON_MATCH_SPORTS):
            continue
        if not (_status_from_text(raw_tail) or _SCORE_RE.search(raw_tail)):
            continue

        if day_text and month_text:
            day = int(day_text)
            month = int(month_text)
            year = int(year_text) if year_text else _infer_year(anchor_date, month)
        else:
            day, month, year = anchor_date.day, anchor_date.month, anchor_date.year

        start_kz = _to_kz_datetime(
            year,
            month,
            day,
            int(hour_text),
            int(minute_text),
        )
        if start_kz is None:
            continue

        identity = (start_kz.isoformat(), current_sport, title.casefold())
        if identity in seen:
            continue
        seen.add(identity)
        events.append(
            _event_dict(
                title=title,
                raw_text=raw_tail,
                sport=current_sport,
                tournament=current_tournament,
                start_kz=start_kz,
                source_url=source_url,
            )
        )

    events.sort(
        key=lambda item: (
            item["date"],
            item["time"],
            item["sport"],
            item["title"],
        )
    )
    return events


def _blocked_page_diagnostic(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    title = _clean_text(
        soup.title.get_text(" ", strip=True) if soup.title else ""
    )[:120]
    preview = _clean_text(soup.get_text(" ", strip=True))[:200]
    return f"bytes={len(html)}:title={title!r}:text={preview!r}"


def _text_diagnostic(text: str) -> str:
    return f"bytes={len(text)}:text={_clean_text(text)[:260]!r}"


async def _download(
    session: aiohttp.ClientSession,
    url: str,
) -> tuple[str, str | None]:
    try:
        async with session.get(url) as response:
            if response.status != 200:
                return "", f"HTTP_{response.status}:final={response.url}"
            return await response.text(), None
    except Exception as error:
        return "", f"{type(error).__name__}:{error}"


async def _fetch_reader_section(
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    anchor_date: date,
    sport: str,
    path: str,
) -> tuple[list[dict], str | None]:
    source_url = build_match_center_url(path)
    try:
        async with semaphore:
            text = await read_public_url(
                source_url,
                timeout_seconds=REQUEST_TIMEOUT_SECONDS + 5,
            )
    except Exception as error:
        return [], f"reader:{sport}:agent_reach:{type(error).__name__}:{error}"
    events = parse_match_center_text(
        text,
        anchor_date,
        sport=sport,
        source_url=build_match_center_url(path),
    )
    if not events:
        return [], f"reader:{sport}:no_events:{_text_diagnostic(text)}"
    return events, None


def _merge_events(*groups: list[dict]) -> list[dict]:
    merged: list[dict] = []
    seen: set[tuple[str, str, str, str]] = set()
    for group in groups:
        for event in group:
            key = (
                str(event.get("date") or ""),
                str(event.get("time") or ""),
                str(event.get("sport") or ""),
                _clean_text(str(event.get("title") or "")).casefold(),
            )
            if key in seen:
                continue
            seen.add(key)
            merged.append(event)
    merged.sort(
        key=lambda item: (
            str(item.get("date") or ""),
            str(item.get("time") or ""),
            str(item.get("sport") or ""),
            str(item.get("title") or ""),
        )
    )
    return merged


async def fetch_championat_calendar(anchor_date: date) -> ChampionatCalendar:
    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 Chrome/128 Safari/537.36"
        ),
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.5",
    }
    semaphore = asyncio.Semaphore(REQUEST_CONCURRENCY)
    errors: list[str] = []

    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        primary_url = build_match_center_url(PRIMARY_MATCH_CENTER)
        html, primary_error = await _download(session, primary_url)
        direct_events = parse_match_center_html(html, anchor_date) if html else []
        if direct_events:
            logger.info(
                "championat transport=direct_stat events=%d",
                len(direct_events),
            )
        else:
            if primary_error:
                errors.append(f"direct:{primary_error}")
            elif html:
                errors.append(
                    f"direct:no_events:{_blocked_page_diagnostic(html)}"
                )

        try:
            reader_text = await read_public_url(
                primary_url,
                timeout_seconds=REQUEST_TIMEOUT_SECONDS + 5,
            )
            reader_error = None
        except Exception as error:
            reader_text = ""
            reader_error = (
                f"agent_reach:{type(error).__name__}:{error}"
            )

        reader_events = (
            parse_match_center_text(
                reader_text,
                anchor_date,
                source_url=primary_url,
            )
            if reader_text
            else []
        )
        if reader_error:
            errors.append(f"reader:root:{reader_error}")
        elif reader_text and not reader_events:
            errors.append(
                f"reader:root:no_events:{_text_diagnostic(reader_text)}"
            )

        section_events: list[dict] = []
        if len(reader_events) < MIN_ROOT_READER_EVENTS:
            rows = await asyncio.gather(
                *(
                    _fetch_reader_section(
                        session,
                        semaphore,
                        anchor_date,
                        sport,
                        path,
                    )
                    for sport, path in SECTION_PAGES.items()
                )
            )
            for page_events, error in rows:
                if error:
                    errors.append(error)
                section_events.extend(page_events)
            logger.info(
                "championat reader root incomplete root_events=%d section_events=%d",
                len(reader_events),
                len(section_events),
            )

    events = _merge_events(direct_events, reader_events, section_events)
    transport = (
        "direct+reader"
        if direct_events and (reader_events or section_events)
        else "reader_sections"
        if section_events
        else "reader"
        if reader_events
        else "direct"
        if direct_events
        else "none"
    )
    fetched_dates = sorted(
        {
            str(event.get("date") or "")
            for event in events
            if event.get("date")
        }
    )
    logger.info(
        "championat calendar fetched transport=%s events=%d sports=%d dates=%s errors=%d timezone=Asia/Almaty",
        transport,
        len(events),
        len({str(event.get("sport") or "") for event in events}),
        fetched_dates,
        len(errors),
    )
    return ChampionatCalendar(
        events=events,
        errors=errors,
        fetched_dates=fetched_dates,
    )


async def get_championat_calendar(
    anchor: date | None = None,
    *,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    lookahead_days: int = DEFAULT_LOOKAHEAD_DAYS,
    force_refresh: bool = False,
) -> ChampionatCalendar:
    # The current Match Center transport is a snapshot around `anchor`; the
    # parameters stay in the public contract for a future historical/calendar
    # transport and for caller compatibility.
    del lookback_days, lookahead_days
    global _cache_anchor, _cache_expires_at, _cache_result
    anchor = anchor or datetime.now(KZ_TIMEZONE).date()
    async with _cache_lock:
        if (
            force_refresh
            or _cache_anchor != anchor
            or time.monotonic() >= _cache_expires_at
        ):
            _cache_result = await fetch_championat_calendar(anchor)
            _cache_anchor = anchor
            _cache_expires_at = time.monotonic() + CACHE_TTL_SECONDS
        return ChampionatCalendar(
            events=[dict(event) for event in _cache_result.events],
            errors=list(_cache_result.errors),
            fetched_dates=list(_cache_result.fetched_dates),
        )

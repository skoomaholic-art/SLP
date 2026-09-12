from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import aiohttp

from services.time_logic import KZ_TIMEZONE
from verifiers.web_search import normalize

logger = logging.getLogger(__name__)

BASE_URL = "https://www.championat.com"
READER_BASE_URL = "https://r.jina.ai/https://www.championat.com"
MSK_TIMEZONE = ZoneInfo("Europe/Moscow")
REQUEST_TIMEOUT_SECONDS = 30
REQUEST_CONCURRENCY = 6
LOOKAHEAD_DAYS = 7
SECTION_CACHE_TTL_SECONDS = 3600.0
CALENDAR_CACHE_TTL_SECONDS = 1800.0
MAX_TOURNAMENT_CALENDARS = 28
MIN_TOURNAMENT_SCORE = 0.40

SPORT_SECTION_PATHS = {
    "футбол": "/stat/football/",
    "хоккей": "/stat/hockey/",
    "теннис": "/stat/tennis/",
    "баскетбол": "/stat/basketball/",
    "волейбол": "/stat/volleyball/",
    "автоспорт": "/stat/auto/",
    "мотоспорт": "/stat/auto/",
    "mma": "/stat/boxing/",
    "мма": "/stat/boxing/",
    "бокс": "/stat/boxing/",
    "биатлон": "/stat/biathlon/",
    "лыжи": "/stat/ski/",
    "фигурное катание": "/stat/figureskating/",
    "футзал": "/stat/futsal/",
    "регби": "/stat/rugby/",
    "гандбол": "/stat/handball/",
    "шахматы": "/stat/chess/",
}

SPORT_BY_SECTION = {
    "/stat/football/": "Футбол",
    "/stat/hockey/": "Хоккей",
    "/stat/tennis/": "Теннис",
    "/stat/basketball/": "Баскетбол",
    "/stat/volleyball/": "Волейбол",
    "/stat/auto/": "Автоспорт",
    "/stat/boxing/": "MMA",
    "/stat/biathlon/": "Биатлон",
    "/stat/ski/": "Лыжи",
    "/stat/figureskating/": "Фигурное катание",
    "/stat/futsal/": "Футзал",
    "/stat/rugby/": "Регби",
    "/stat/handball/": "Гандбол",
    "/stat/chess/": "Шахматы",
}

_NON_MATCH_SPORTS = {
    "Автоспорт",
    "Биатлон",
    "Лыжи",
    "Фигурное катание",
    "Прочее",
}

_TOURNAMENT_LINK_RE = re.compile(
    r"\[([^\]]{2,220})\]\(([^)\s]*championat\.com/[^)\s]*/tournament/\d+/?[^)\s]*)\)",
    re.I,
)
_RELATIVE_TOURNAMENT_LINK_RE = re.compile(
    r"\[([^\]]{2,220})\]\((/[^)\s]*/tournament/\d+/?[^)\s]*)\)",
    re.I,
)
_FULL_DATETIME_RE = re.compile(
    r"(?<!\d)(\d{1,2})[.](\d{1,2})[.](20\d{2})\s+(\d{1,2}):(\d{2})(?!\d)"
)
_MATCH_SEPARATOR_RE = re.compile(
    r"(?:\s+-\s+|\s*[–—]\s*|\s+(?:vs\.?|v\.)\s+)",
    re.I,
)
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_SCORE_CELL_RE = re.compile(r"^(?:[-–—]\s*:\s*[-–—]|\d+\s*:\s*\d+)")
_GENERIC_NOISE = {
    "все турниры",
    "календарь",
    "результаты",
    "турнирная таблица",
    "статистика игроков",
    "команды",
    "тренеры",
}


@dataclass
class ChampionatFutureCalendar:
    events: list[dict]
    errors: list[str]
    tournament_urls: list[str]
    fetched_dates: list[str]


_section_cache: dict[str, tuple[float, str]] = {}
_calendar_cache: dict[str, tuple[float, list[dict]]] = {}
_cache_lock = asyncio.Lock()


def _clean_text(value: str) -> str:
    value = _MARKDOWN_LINK_RE.sub(r"\1", str(value or ""))
    value = value.replace("**", "").replace("__", "")
    return re.sub(r"\s+", " ", value).strip()


def _reader_url(url_or_path: str) -> str:
    if url_or_path.startswith("http://") or url_or_path.startswith("https://"):
        parsed = urlparse(url_or_path)
        path = parsed.path
        if parsed.query:
            path += "?" + parsed.query
    else:
        path = "/" + url_or_path.lstrip("/")
    return f"{READER_BASE_URL}{path}"


def _canonical_tournament_url(url: str) -> str:
    absolute = urljoin(f"{BASE_URL}/", url)
    parsed = urlparse(absolute)
    path = parsed.path
    marker = "/tournament/"
    if marker not in path:
        return ""
    prefix, suffix = path.split(marker, 1)
    tournament_id = suffix.strip("/").split("/", 1)[0]
    if not tournament_id.isdigit():
        return ""
    return f"{BASE_URL}{prefix}{marker}{tournament_id}/"


def _calendar_url(tournament_url: str) -> str:
    return tournament_url.rstrip("/") + "/calendar/"


def _sport_key(value: str) -> str:
    text = normalize(value)
    aliases = {
        "formula 1": "автоспорт",
        "формула 1": "автоспорт",
        "мотоспорт": "мотоспорт",
        "ufc": "mma",
        "мма": "мма",
    }
    for marker, result in aliases.items():
        if marker in text:
            return result
    return text


def _candidate_hints(events: list[dict]) -> tuple[set[str], list[str]]:
    section_paths: set[str] = set()
    hints: list[str] = []
    seen_hints: set[str] = set()

    for event in events:
        sport_value = str(event.get("sport") or "")
        sport_key = _sport_key(sport_value)
        for marker, path in SPORT_SECTION_PATHS.items():
            if marker in sport_key or sport_key in marker:
                section_paths.add(path)
                break

        tournament = _clean_text(str(event.get("tournament") or ""))
        raw_title = _clean_text(
            str(event.get("raw_title") or event.get("title") or "")
        )
        values = [tournament]
        if ". " in raw_title:
            parts = [part.strip() for part in raw_title.split(". ") if part.strip()]
            if len(parts) >= 2:
                values.extend((parts[0], ". ".join(parts[:-1])))
        for value in values:
            normalized = normalize(value)
            if len(normalized) < 4 or normalized in seen_hints:
                continue
            seen_hints.add(normalized)
            hints.append(value)

    return section_paths, hints


def extract_tournament_links(text: str, section_path: str) -> list[dict]:
    links: list[dict] = []
    seen: set[str] = set()
    sport = SPORT_BY_SECTION.get(section_path, "")
    for pattern in (_TOURNAMENT_LINK_RE, _RELATIVE_TOURNAMENT_LINK_RE):
        for match in pattern.finditer(str(text or "")):
            label = _clean_text(match.group(1))
            if not label or normalize(label) in _GENERIC_NOISE:
                continue
            url = _canonical_tournament_url(match.group(2))
            if not url or url in seen:
                continue
            seen.add(url)
            links.append({"name": label, "url": url, "sport": sport})
    return links


def _tokens(value: str) -> set[str]:
    return {
        token
        for token in normalize(value).split()
        if len(token) >= 3
        and token
        not in {
            "чемпионат",
            "кубок",
            "лига",
            "турнир",
            "сезон",
            "этап",
            "2026",
            "2027",
        }
    }


def _tournament_score(name: str, hint: str) -> float:
    left = normalize(name)
    right = normalize(hint)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    if left in right or right in left:
        shorter = min(len(left), len(right))
        longer = max(len(left), len(right))
        return 0.72 + 0.28 * (shorter / max(longer, 1))
    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    union = left_tokens | right_tokens
    token_score = (
        len(left_tokens & right_tokens) / len(union)
        if union
        else 0.0
    )
    sequence_score = SequenceMatcher(None, left, right).ratio()
    return max(token_score, sequence_score * 0.72)


def select_tournament_links(links: list[dict], hints: list[str]) -> list[dict]:
    ranked: list[tuple[float, dict]] = []
    for link in links:
        score = max(
            (_tournament_score(str(link["name"]), hint) for hint in hints),
            default=0.0,
        )
        if score >= MIN_TOURNAMENT_SCORE:
            ranked.append((score, link))
    ranked.sort(key=lambda item: (-item[0], str(item[1]["name"])))

    selected: list[dict] = []
    seen: set[str] = set()
    for score, link in ranked:
        url = str(link["url"])
        if url in seen:
            continue
        seen.add(url)
        selected.append({**link, "score": round(score, 3)})
        if len(selected) >= MAX_TOURNAMENT_CALENDARS:
            break
    return selected


def _parse_table_title(cell: str) -> tuple[datetime | None, str]:
    matches = list(_FULL_DATETIME_RE.finditer(cell))
    if not matches:
        return None, ""
    marker = matches[-1]
    day, month, year, hour, minute = map(int, marker.groups())
    try:
        start_msk = datetime(
            year,
            month,
            day,
            hour,
            minute,
            tzinfo=MSK_TIMEZONE,
        )
    except ValueError:
        return None, ""
    title = _clean_text(cell[marker.end() :]).strip(" |-–—")
    return start_msk.astimezone(KZ_TIMEZONE), title


def parse_tournament_calendar_text(
    text: str,
    *,
    tournament: str,
    sport: str,
    source_url: str,
    start_date: date,
    end_date: date,
) -> list[dict]:
    events: list[dict] = []
    seen: set[tuple[str, str, str]] = set()

    for raw_line in str(text or "").splitlines():
        if not _FULL_DATETIME_RE.search(raw_line):
            continue
        cells = [_clean_text(cell) for cell in raw_line.split("|")]
        cells = [cell for cell in cells if cell]
        if not cells:
            continue

        best_start: datetime | None = None
        best_title = ""
        for cell in cells:
            start, title = _parse_table_title(cell)
            if start is None or len(title) <= len(best_title):
                continue
            best_start, best_title = start, title
        if best_start is None or not best_title:
            continue
        if not (start_date <= best_start.date() <= end_date):
            continue

        # Remove a score cell accidentally attached after the title.
        best_title = re.sub(
            r"\s+(?:[-–—]\s*:\s*[-–—]|\d+\s*:\s*\d+)\s*$",
            "",
            best_title,
        ).strip()
        if not best_title:
            continue
        has_match_separator = bool(_MATCH_SEPARATOR_RE.search(best_title))
        if not has_match_separator and sport not in _NON_MATCH_SPORTS:
            continue

        identity = (
            best_start.isoformat(),
            sport,
            normalize(best_title),
        )
        if identity in seen:
            continue
        seen.add(identity)
        events.append(
            {
                "source": "championat",
                "source_url": source_url,
                "title": best_title,
                "raw_title": best_title,
                "search_text": f"{tournament} {best_title}".strip(),
                "sport": sport,
                "tournament": tournament,
                "status": "",
                "source_timezone": "Europe/Moscow",
                "timezone": "Asia/Almaty",
                "date": best_start.date().isoformat(),
                "time": best_start.strftime("%H:%M"),
                "start_at_kz": best_start.isoformat(),
            }
        )

    events.sort(
        key=lambda event: (
            event["date"],
            event["time"],
            event["sport"],
            event["title"],
        )
    )
    return events


async def _download(session: aiohttp.ClientSession, url: str) -> tuple[str, str | None]:
    try:
        async with session.get(url) as response:
            if response.status != 200:
                return "", f"HTTP_{response.status}:final={response.url}"
            return await response.text(), None
    except Exception as error:
        return "", f"{type(error).__name__}:{error}"


async def _cached_text(
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    url: str,
) -> tuple[str, str | None]:
    cached = _section_cache.get(url)
    if cached and cached[0] > time.monotonic():
        return cached[1], None
    async with semaphore:
        text, error = await _download(session, url)
    if not error and text:
        _section_cache[url] = (
            time.monotonic() + SECTION_CACHE_TTL_SECONDS,
            text,
        )
    return text, error


async def _calendar_events(
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    link: dict,
    start_date: date,
    end_date: date,
) -> tuple[list[dict], str | None]:
    calendar_url = _calendar_url(str(link["url"]))
    cache_key = f"{calendar_url}|{start_date.isoformat()}|{end_date.isoformat()}"
    cached = _calendar_cache.get(cache_key)
    if cached and cached[0] > time.monotonic():
        return [dict(event) for event in cached[1]], None

    async with semaphore:
        text, error = await _download(session, _reader_url(calendar_url))
    if error:
        return [], f"calendar:{link['name']}:{error}"
    events = parse_tournament_calendar_text(
        text,
        tournament=str(link["name"]),
        sport=str(link["sport"]),
        source_url=calendar_url,
        start_date=start_date,
        end_date=end_date,
    )
    if not events:
        return [], f"calendar:{link['name']}:no_events"
    _calendar_cache[cache_key] = (
        time.monotonic() + CALENDAR_CACHE_TTL_SECONDS,
        [dict(event) for event in events],
    )
    return events, None


async def get_championat_future_calendar(
    candidate_events: list[dict],
    *,
    anchor: date | None = None,
    lookahead_days: int = LOOKAHEAD_DAYS,
) -> ChampionatFutureCalendar:
    anchor = anchor or datetime.now(KZ_TIMEZONE).date()
    end_date = anchor + timedelta(days=lookahead_days)
    section_paths, hints = _candidate_hints(candidate_events)
    if not section_paths or not hints:
        return ChampionatFutureCalendar([], [], [], [])

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
    all_links: list[dict] = []

    async with _cache_lock:
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            section_rows = await asyncio.gather(
                *(
                    _cached_text(session, semaphore, _reader_url(path))
                    for path in sorted(section_paths)
                )
            )
            for path, (text, error) in zip(sorted(section_paths), section_rows):
                if error:
                    errors.append(f"section:{path}:{error}")
                    continue
                all_links.extend(extract_tournament_links(text, path))

            selected = select_tournament_links(all_links, hints)
            calendar_rows = await asyncio.gather(
                *(
                    _calendar_events(
                        session,
                        semaphore,
                        link,
                        anchor - timedelta(days=1),
                        end_date,
                    )
                    for link in selected
                )
            )

    events: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for page_events, error in calendar_rows:
        if error:
            errors.append(error)
        for event in page_events:
            key = (
                str(event.get("date") or ""),
                str(event.get("time") or ""),
                normalize(str(event.get("title") or "")),
            )
            if key in seen:
                continue
            seen.add(key)
            events.append(event)

    events.sort(
        key=lambda event: (
            str(event.get("date") or ""),
            str(event.get("time") or ""),
            str(event.get("sport") or ""),
            str(event.get("title") or ""),
        )
    )
    fetched_dates = sorted(
        {str(event.get("date") or "") for event in events if event.get("date")}
    )
    logger.info(
        "championat future horizon candidate_events=%d sections=%d tournament_links=%d selected_calendars=%d events=%d dates=%s errors=%d",
        len(candidate_events),
        len(section_paths),
        len(all_links),
        len(selected),
        len(events),
        fetched_dates,
        len(errors),
    )
    if selected:
        logger.info(
            "championat future selected tournaments=%s",
            [
                f"{link['name']}:{link['score']}"
                for link in selected[:MAX_TOURNAMENT_CALENDARS]
            ],
        )
    return ChampionatFutureCalendar(
        events=events,
        errors=errors,
        tournament_urls=[_calendar_url(str(link["url"])) for link in selected],
        fetched_dates=fetched_dates,
    )

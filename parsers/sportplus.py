from __future__ import annotations

import asyncio
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import aiohttp
from bs4 import BeautifulSoup

from services.event_contract import build_sport_event
from services.live_evidence import LIVE_TEXT_MARKERS, classify_live_evidence


BASE_URL = "https://sportplustv.kz/ru/tvguide"
CHANNEL = "Sport+ Qazaqstan"
SOURCE = "sportplus"
KZ_TIMEZONE = ZoneInfo("Asia/Almaty")

TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
DAY_RE = re.compile(
    r"^(?:[^\d\n]+?\s+)?"
    r"(\d{2})\.(\d{2})"
    r"(?:\s+[^\d\n]+)?$",
    flags=re.IGNORECASE,
)

LIVE_ASSET_PATTERNS: tuple[str, ...] = ()

SPORT_PREFIXES = {
    "ФУТБОЛ": "Футбол",
    "ВОЛЕЙБОЛ": "Волейбол",
    "БАСКЕТБОЛ": "Баскетбол",
    "БАСКЕТБОЛ 3Х3": "Баскетбол",
    "БАСКЕТБОЛ 3X3": "Баскетбол",
    "ХОККЕЙ": "Хоккей",
    "ТЕННИС": "Теннис",
    "БОКС": "Бокс",
    "КӘСІПҚОЙ БОКС": "Бокс",
    "ММА": "MMA",
    "ДЗЮДО": "Дзюдо",
    "JUDO": "Дзюдо",
    "КАРАТЕ": "Карате",
    "ТАЕКВОНДО": "Таеквондо",
    "БОРЬБА": "Борьба",
    "КҮРЕС": "Борьба",
    "ПАДЕЛ": "Падел",
    "НАСТОЛЬНЫЙ ТЕННИС": "Настольный теннис",
    "ҮСТЕЛ ТЕННИСІ": "Настольный теннис",
    "БӘЙГЕ": "Конный спорт",
    "АТ СПОРТЫ": "Конный спорт",
    "ЖЕҢІЛ АТЛЕТИКА": "Лёгкая атлетика",
    "ЛЕГКАЯ АТЛЕТИКА": "Лёгкая атлетика",
    "MEDIA BASKET ALMATY": "Баскетбол",
    "КИБЕРСПОРТ": "Киберспорт",
    "ПАРАСПОРТ": "Параспорт",
}

TEXT_REPLACEMENTS = (
    ("1\\4", "1/4"),
    ("1\\2", "1/2"),
    ("ҚАЙРАТ", "Кайрат"),
    ("Қайрат", "Кайрат"),
    ("АҚТӨБЕ", "Актобе"),
    ("Ақтөбе", "Актобе"),
    ("ЕРТІС", "Иртыш"),
    ("Ертіс", "Иртыш"),
    ("ҚПЛ", "КПЛ"),
    ("ҚАЗАҚСТАН", "Казахстан"),
    ("Қазақстан", "Казахстан"),
)

RU_PHRASE_REPLACEMENTS = (
    (r"\bАТ ЖАРЫСЫ МАУСЫМЫ\b", "Сезон конных скачек"),
    (r"\bҚАЗАҚСТАН ЧЕМПИОНАТЫ\b", "Чемпионат Казахстана"),
    (r"\bКАЗАХСТАН ЧЕМПИОНАТЫ\b", "Чемпионат Казахстана"),
    (r"\b[ІI]Р[ІI]КТЕУ КЕЗЕҢ[ІI]\b", "Отборочный этап"),
    (r"\b[ІI]Р[ІI]КТЕУ\b", "Отборочный этап"),
    (r"\bБІРІНШІ МАТЧ\b", "Первый матч"),
    (r"\bАЛМАТЫДАН\b", "Алматы"),
    (r"\bШЫМКЕНТТЕН\b", "Шымкент"),
)


def time_to_minutes(value: str) -> int:
    hour, minute = map(int, value.split(":"))
    return hour * 60 + minute


def normalize_text(value: str) -> str:
    result = str(value or "")

    for source, replacement in TEXT_REPLACEMENTS:
        result = result.replace(source, replacement)

    for pattern, replacement in RU_PHRASE_REPLACEMENTS:
        result = re.sub(
            pattern,
            replacement,
            result,
            flags=re.IGNORECASE,
        )

    result = re.sub(r"\s+", " ", result)
    result = re.sub(r"\s*\.\s*", ". ", result)
    return result.strip(" .-–—")


def is_direct_broadcast(value: str) -> bool:
    return classify_live_evidence(value).is_live


def strip_live_marker(value: str) -> str:
    text = str(value or "")
    marker_pattern = "|".join(
        re.escape(marker)
        for marker in LIVE_TEXT_MARKERS
    )

    text = re.sub(
        rf"\s*\.\s*(?:{marker_pattern})(?:\s+ИЗ\s+.+)?\s*$",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        rf"\s+(?:{marker_pattern})(?:\s+ИЗ\s+.+)?\s*$",
        "",
        text,
        flags=re.IGNORECASE,
    )

    return normalize_text(text)


def extract_sport_and_remainder(value: str) -> tuple[str, str]:
    text = normalize_text(value)

    for raw_prefix, sport in sorted(
        SPORT_PREFIXES.items(),
        key=lambda item: len(item[0]),
        reverse=True,
    ):
        match = re.match(
            rf"^{re.escape(raw_prefix)}\s*\.\s*",
            text,
            flags=re.IGNORECASE,
        )
        if match:
            return sport, text[match.end():].strip(" .-–—")

    return "", text


def parse_sportplus_title(raw_title: str) -> dict[str, str]:
    cleaned = strip_live_marker(raw_title)
    sport, remainder = extract_sport_and_remainder(cleaned)

    if not sport:
        return {
            "sport": "",
            "tournament": "",
            "title": normalize_text(remainder),
            "raw_event_title": normalize_text(remainder),
        }

    segments = [
        segment.strip()
        for segment in re.split(r"\s*\.\s*", remainder)
        if segment.strip()
    ]

    if (
        sport == "Конный спорт"
        and segments
        and segments[0].casefold().startswith("сезон конных скачек")
    ):
        return {
            "sport": sport,
            "tournament": normalize_text(segments[0]),
            "title": normalize_text(remainder),
            "raw_event_title": normalize_text(remainder),
        }

    match_index = None
    for index, segment in enumerate(segments):
        if re.search(r"\s+[–—-]\s+", segment):
            match_index = index

    if match_index is not None:
        tournament = ". ".join(segments[:match_index]).strip()
        title = ". ".join(segments[match_index:]).strip()
    else:
        tournament = remainder
        title = remainder

    return {
        "sport": sport,
        "tournament": normalize_text(tournament),
        "title": normalize_text(title),
        "raw_event_title": normalize_text(remainder),
    }


def _resolve_header_dates(
    header_values: list[tuple[int, int]],
    reference_date: date,
) -> list[date]:
    result: list[date] = []
    previous: date | None = None

    for day, month in header_values:
        candidates = [
            date(year, month, day)
            for year in (
                reference_date.year - 1,
                reference_date.year,
                reference_date.year + 1,
            )
        ]

        if previous is None:
            chosen = min(
                candidates,
                key=lambda item: abs(
                    (item - reference_date).days
                ),
            )
        else:
            future = [item for item in candidates if item > previous]
            chosen = min(future) if future else max(candidates)

        result.append(chosen)
        previous = chosen

    return result


def _extract_headers(
    strings: list[str],
    reference_date: date,
) -> tuple[list[date], int]:
    values: list[tuple[int, int]] = []
    last_index = -1

    # На Sport+ список дат расположен перед первой строкой времени.
    # Названия дней могут быть русскими или казахскими, поэтому
    # ориентируемся на сам формат DD.MM, а не на конкретный weekday.
    for index, value in enumerate(strings):
        cleaned = value.strip()

        if TIME_RE.fullmatch(cleaned):
            break

        match = DAY_RE.fullmatch(cleaned)
        if not match:
            continue

        day = int(match.group(1))
        month = int(match.group(2))

        try:
            date(reference_date.year, month, day)
        except ValueError:
            continue

        values.append((day, month))
        last_index = index

    return _resolve_header_dates(values, reference_date), last_index


def _extract_program_pairs(
    strings: list[str],
    start_index: int,
) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    index = max(start_index + 1, 0)

    while index < len(strings):
        value = strings[index].strip()

        if not TIME_RE.fullmatch(value):
            index += 1
            continue

        next_index = index + 1
        title = ""

        while next_index < len(strings):
            candidate = strings[next_index].strip()

            if not candidate:
                next_index += 1
                continue

            if TIME_RE.fullmatch(candidate):
                break

            if DAY_RE.fullmatch(candidate):
                next_index += 1
                continue

            if candidate in {"◀", "▶"}:
                next_index += 1
                continue

            title = candidate
            break

        if title:
            pairs.append((value, title))

        index = max(next_index, index + 1)

    return pairs


def _split_program_blocks(
    pairs: list[tuple[str, str]],
) -> list[list[tuple[str, str]]]:
    blocks: list[list[tuple[str, str]]] = []
    current: list[tuple[str, str]] = []
    previous_minutes: int | None = None

    for time_text, title in pairs:
        current_minutes = time_to_minutes(time_text)
        title_lower = title.casefold()

        anthem_boundary = (
            bool(current)
            and time_text == "07:00"
            and "әнұраны" in title_lower
        )
        morning_boundary = (
            bool(current)
            and previous_minutes is not None
            and previous_minutes <= 5 * 60 + 59
            and current_minutes >= 6 * 60
        )

        if anthem_boundary or morning_boundary:
            blocks.append(current)
            current = []

        current.append((time_text, title))
        previous_minutes = current_minutes

    if current:
        blocks.append(current)

    return blocks


def _coerce_date(value: date | datetime | str | None) -> date:
    if value is None:
        return datetime.now(KZ_TIMEZONE).date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(value, "%Y-%m-%d").date()


def parse_sportplus_html(
    html: str,
    target_date: date | datetime | str | None = None,
    *,
    reference_date: date | None = None,
) -> list[dict]:
    requested_date = _coerce_date(target_date)
    reference = reference_date or requested_date

    soup = BeautifulSoup(html, "html.parser")
    strings = [
        " ".join(value.split())
        for value in soup.stripped_strings
    ]

    page_dates, last_header_index = _extract_headers(
        strings,
        reference,
    )

    if not page_dates:
        raise RuntimeError(
            "Sport+ Qazaqstan: не удалось определить даты телепрограммы"
        )

    pairs = _extract_program_pairs(strings, last_header_index)
    blocks = _split_program_blocks(pairs)

    if requested_date not in page_dates:
        return []

    date_index = page_dates.index(requested_date)
    if date_index >= len(blocks):
        return []

    block = blocks[date_index]
    programs: list[dict] = []
    day_offset = 0
    previous_minutes: int | None = None

    for time_text, raw_title in block:
        current_minutes = time_to_minutes(time_text)

        if (
            previous_minutes is not None
            and current_minutes < previous_minutes
        ):
            day_offset += 1

        actual_date = requested_date + timedelta(days=day_offset)

        programs.append(
            {
                "date": actual_date.isoformat(),
                "time": time_text,
                "raw_title": raw_title,
                "schedule_offset": current_minutes + day_offset * 1440,
            }
        )
        previous_minutes = current_minutes

    result = []

    for index, program in enumerate(programs):
        raw_title = program["raw_title"]
        parsed = parse_sportplus_title(raw_title)
        live_evidence = classify_live_evidence(raw_title, live_asset_patterns=LIVE_ASSET_PATTERNS)
        direct = live_evidence.is_live

        # В SLP нужны именно спортивные прямые трансляции.
        # Прямые студийные программы без распознанного вида спорта исключаем.
        if not direct or not parsed["sport"]:
            continue

        if index + 1 < len(programs):
            next_program = programs[index + 1]
            end_date = next_program["date"]
            end_time = next_program["time"]
            end_method = "next_program"
            end_confidence = "high"
        else:
            end_date = None
            end_time = None
            end_method = None
            end_confidence = "unknown"

        event = {
            "date": program["date"],
            "time": program["time"],
            "channel": CHANNEL,
            "is_live": True,
            "live_state": live_evidence.state,
            "live_evidence_method": live_evidence.method,
            "live_evidence_value": live_evidence.value,
            "live_evidence_confidence": live_evidence.confidence,
            "raw_title": raw_title,
            "sport": parsed["sport"],
            "tournament": parsed["tournament"],
            "title": parsed["title"],
            "raw_event_title": parsed["raw_event_title"],
            "raw_sport": parsed["sport"],
            "raw_tournament": parsed["tournament"],
            "schedule_offset": program["schedule_offset"],
            "estimated_broadcast_end_date": end_date,
            "estimated_broadcast_end": end_time,
            "end_estimation_method": end_method,
            "end_confidence": end_confidence,
        }

        result.append(
            build_sport_event(
                event,
                source=SOURCE,
                source_url=BASE_URL,
            )
        )

    return result


async def fetch_sportplus_html() -> str:
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept-Language": "ru-RU,ru;q=0.9",
    }
    timeout = aiohttp.ClientTimeout(total=20)

    async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
        async with session.get(BASE_URL) as response:
            response.raise_for_status()
            html = await response.text()

    if not html.strip():
        raise RuntimeError("Sport+ Qazaqstan: получен пустой HTML")
    return html


async def get_sportplus_schedule(
    target_date: date | datetime | str | None = None,
) -> list[dict]:
    html = await fetch_sportplus_html()
    return parse_sportplus_html(html, target_date=target_date)


async def main() -> None:
    schedule = await get_sportplus_schedule()

    print("Sport+ LIVE-событий:", len(schedule))
    print()

    for number, event in enumerate(schedule, start=1):
        print(
            f"{number}. {event['date']} {event['time']} | "
            f"{event['sport']} | {event['title']} | "
            f"{event['channel']}"
        )


if __name__ == "__main__":
    asyncio.run(main())

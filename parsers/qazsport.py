import asyncio
import re
from datetime import datetime, timedelta

import aiohttp
from bs4 import BeautifulSoup

from services.event_contract import build_sport_event
from services.live_evidence import classify_live_evidence, extract_asset_hints


BASE_URL = "https://qazsporttv.kz/ru/program"
CHANNEL = "Qazsport"
SOURCE = "qazsport"
LIVE_ASSET_PATTERNS: tuple[str, ...] = ()

TIME_PATTERN = re.compile(
    r"\b(?:[01]\d|2[0-3]):[0-5]\d\b"
)

DATE_PATTERN = re.compile(
    r"\b(\d{2})\.(\d{2})\.(\d{4})\b"
)


SPORT_PREFIXES = {
    "Футбол": "Футбол",
    "Волейбол": "Волейбол",
    "Баскетбол": "Баскетбол",
    "Хоккей": "Хоккей",
    "Теннис": "Теннис",
    "Бокс": "Бокс",
    "Биатлон": "Биатлон",
    "Дзюдо": "Дзюдо",
    "Judo": "Дзюдо",
    "Шаңғы спорты": "Лыжный спорт",
    "Жеңіл атлетика": "Лёгкая атлетика",
}


RU_REPLACEMENTS = [
    (
        "УЕФА Чемпиондар Лигасы",
        "Лига чемпионов УЕФА",
    ),
    (
        "УЕФА Еуропа Лигасы",
        "Лига Европы УЕФА",
    ),
    (
        "УЕФА Конференциялар Лигасы",
        "Лига конференций УЕФА",
    ),
    (
        "Азия чемпионаты",
        "Чемпионат Азии",
    ),
    (
        "Азия кубогы-2029",
        "Кубок Азии-2029",
    ),
    (
        "іріктеу турнирі",
        "Квалификационный турнир",
    ),
    (
        "Іріктеу турнирі",
        "Квалификационный турнир",
    ),
    (
        "Плей-офф кезеңі",
        "Плей-офф",
    ),
    (
        "жалпы кезеңнің жеребе тарту рәсімі",
        "Жеребьёвка общего этапа",
    ),
    (
        "жалпы кезеңнің жеребе тартуы",
        "Жеребьёвка общего этапа",
    ),
    (
        "жеребе тарту рәсімі",
        "Жеребьёвка",
    ),
    (
        "жеребе тартуы",
        "Жеребьёвка",
    ),
    (
        "(Әйелдер)",
        ". Женщины",
    ),
    (
        "(әйелдер)",
        ". Женщины",
    ),
    (
        "Студиялық бағдарлама",
        "Студийная программа",
    ),
    (
        "Матч қарсаңында",
        "Перед матчем",
    ),
    (
        "Оңтүстік Корея",
        "Южная Корея",
    ),
    (
        "Қазақстан",
        "Казахстан",
    ),
    (
        "Казахстан чемпионаты",
        "Чемпионат Казахстана",
    ),
    (
        "Қазақстан чемпионаты",
        "Чемпионат Казахстана",
    ),
    (
        "Іріктеу",
        "Отборочный этап",
    ),
    (
        "іріктеу",
        "Отборочный этап",
    ),
    (
        "1/2 финал",
        "1/2 финала",
    ),
    (
        "1/4 финал",
        "1/4 финала",
    ),
    (
        "Мальдив аралдары",
        "Мальдивы",
    ),
    (
        "Қырғызстан",
        "Кыргызстан",
    ),
    (
        "Тайланд",
        "Таиланд",
    ),
    (
        "Қайрат",
        "Кайрат",
    ),
    (
        "Жапония",
        "Япония",
    ),
    (
        "Үндістан",
        "Индия",
    ),
    (
        "Жазғы Азия ойындары",
        "Летние Азиатские игры",
    ),
]


def normalize_russian_text(text):
    if not text:
        return ""

    result = (
        text
        .replace("1\\4", "1/4")
        .replace("1\\2", "1/2")
    )

    for source, replacement in RU_REPLACEMENTS:
        result = result.replace(
            source,
            replacement,
        )

    result = re.sub(
        r"\s+",
        " ",
        result,
    )

    result = result.replace(
        " .",
        ".",
    )

    result = result.replace(
        "..",
        ".",
    )

    return result.strip(
        " .-–—"
    )


def extract_sport_and_remainder(
    raw_title,
):
    text = raw_title.strip()

    for source_name, russian_name in sorted(
        SPORT_PREFIXES.items(),
        key=lambda item: len(item[0]),
        reverse=True,
    ):
        pattern = re.compile(
            rf"^{re.escape(source_name)}"
            rf"(?:\s*\.\s*|\s+)",
            flags=re.IGNORECASE,
        )

        match = pattern.match(
            text
        )

        if match:
            remainder = text[
                match.end():
            ].strip(
                " .-–—"
            )

            return (
                source_name,
                russian_name,
                remainder,
            )

    return "", "", text


def extract_trailing_sport(raw_title):
    text = raw_title.strip()

    for source_name, russian_name in sorted(
        SPORT_PREFIXES.items(),
        key=lambda item: len(item[0]),
        reverse=True,
    ):
        pattern = re.compile(
            rf"^(.*?)\s+{re.escape(source_name)}$",
            flags=re.IGNORECASE,
        )
        match = pattern.match(text)
        if match:
            return (
                source_name,
                russian_name,
                match.group(1).strip(" .-–—"),
            )

    return "", "", text


def extract_embedded_sport(raw_title):
    text = raw_title.strip()
    for source_name, russian_name in sorted(
        SPORT_PREFIXES.items(), key=lambda item: len(item[0]), reverse=True
    ):
        pattern = re.compile(
            rf"^(?P<tournament>.+?)\.\s*{re.escape(source_name)}\s+(?P<title>.+)$",
            flags=re.IGNORECASE,
        )
        match = pattern.match(text)
        if match:
            return (
                source_name,
                russian_name,
                match.group("tournament").strip(" .-–—"),
                match.group("title").strip(" .-–—"),
            )
    return "", "", "", text


def build_url(target_date=None):
    if target_date is None:
        return BASE_URL

    if isinstance(
        target_date,
        str,
    ):
        date_text = target_date
    else:
        date_text = target_date.strftime(
            "%Y-%m-%d"
        )

    return (
        f"{BASE_URL}/{date_text}"
    )


def time_to_minutes(time_text):
    hour, minute = map(
        int,
        time_text.split(":"),
    )

    return hour * 60 + minute


def clean_title(
    text,
    time_text,
):
    title = re.sub(
        r"\bLIVE\b",
        "",
        text,
        flags=re.IGNORECASE,
    )

    title = title.replace(
        time_text,
        "",
        1,
    )

    title = " ".join(
        title.split()
    )

    return title.strip(
        " -–—"
    )


def get_page_date(soup):
    if not soup.title:
        return None

    page_title = soup.title.get_text(
        strip=True
    )

    match = DATE_PATTERN.search(
        page_title
    )

    if not match:
        return None

    day, month, year = (
        match.groups()
    )

    return datetime.strptime(
        f"{year}-{month}-{day}",
        "%Y-%m-%d",
    ).date()


def parse_qazsport_title(
    raw_title,
):
    (
        raw_sport,
        sport,
        remainder,
    ) = extract_sport_and_remainder(
        raw_title
    )

    if not sport:
        (
            trailing_raw_sport,
            trailing_sport,
            trailing_remainder,
        ) = extract_trailing_sport(raw_title)

        if trailing_sport:
            raw_sport = trailing_raw_sport
            sport = trailing_sport
            remainder = trailing_remainder

    if not sport:
        embedded_raw_sport, embedded_sport, embedded_tournament, embedded_title = extract_embedded_sport(raw_title)
        if embedded_sport:
            return {
                "sport": embedded_sport,
                "tournament": normalize_russian_text(embedded_tournament),
                "title": normalize_russian_text(embedded_title),
                "raw_sport": embedded_raw_sport,
                "raw_tournament": embedded_tournament,
                "raw_event_title": embedded_title,
            }

    remainder_lower = (
        remainder.casefold()
    )

    # Лига чемпионов УЕФА — жеребьёвка
    if (
        re.search(
            r"чемпиондар\s+лигас",
            remainder_lower,
        )
        and "жеребе"
        in remainder_lower
    ):
        event_title = (
            "Жеребьёвка общего этапа"
            if "жалпы кезең"
            in remainder_lower
            else "Жеребьёвка"
        )

        return {
            "sport": sport or "Футбол",
            "tournament": (
                "Лига чемпионов УЕФА"
            ),
            "title": event_title,
            "raw_sport": raw_sport,
            "raw_tournament": (
                "УЕФА Чемпиондар Лигасы"
            ),
            "raw_event_title": remainder,
        }

    # Лига Европы УЕФА — жеребьёвка
    if (
        re.search(
            r"еуропа\s+лигас",
            remainder_lower,
        )
        and "жеребе"
        in remainder_lower
    ):
        event_title = (
            "Жеребьёвка общего этапа"
            if "жалпы кезең"
            in remainder_lower
            else "Жеребьёвка"
        )

        return {
            "sport": sport or "Футбол",
            "tournament": (
                "Лига Европы УЕФА"
            ),
            "title": event_title,
            "raw_sport": raw_sport,
            "raw_tournament": (
                "УЕФА Еуропа Лигасы"
            ),
            "raw_event_title": remainder,
        }

    # Лига конференций УЕФА — жеребьёвка
    if (
        "конферен"
        in remainder_lower
        and re.search(
            r"лигас",
            remainder_lower,
        )
        and "жеребе"
        in remainder_lower
    ):
        event_title = (
            "Жеребьёвка общего этапа"
            if "жалпы кезең"
            in remainder_lower
            else "Жеребьёвка"
        )

        return {
            "sport": sport or "Футбол",
            "tournament": (
                "Лига конференций УЕФА"
            ),
            "title": event_title,
            "raw_sport": raw_sport,
            "raw_tournament": (
                "УЕФА Конференциялар Лигасы"
            ),
            "raw_event_title": remainder,
        }

    normalized_remainder = normalize_russian_text(remainder)

    if normalized_remainder.casefold().startswith(
        "чемпионат казахстана"
    ):
        parts = [
            part.strip()
            for part in normalized_remainder.split(".")
            if part.strip()
        ]
        tournament = parts[0] if parts else "Чемпионат Казахстана"
        title = ". ".join(parts[1:]).strip() or tournament

        return {
            "sport": sport,
            "tournament": tournament,
            "title": title,
            "raw_sport": raw_sport,
            "raw_tournament": remainder,
            "raw_event_title": remainder,
        }

    # Дзюдо — Grand Slam
    if (
        "grand slam" in remainder_lower
        and (
            sport == "Дзюдо"
            or "дзюдо" in remainder_lower
            or "judo" in remainder_lower
        )
    ):
        raw_event_title = re.sub(
            r"(?i)\b(?:дзюдо|judo)\b",
            "",
            remainder,
        ).strip(" .-–—")

        normalized_title = normalize_russian_text(
            raw_event_title
        )

        return {
            "sport": "Дзюдо",
            "tournament": "Grand Slam",
            "title": normalized_title or "Grand Slam",
            "raw_sport": raw_sport or "Дзюдо",
            "raw_tournament": "Grand Slam",
            "raw_event_title": remainder,
        }

    # Студийная программа
    if remainder_lower.startswith(
        "студиялық бағдарлама"
    ):
        return {
            "sport": "",
            "tournament": "",
            "title": (
                normalize_russian_text(
                    remainder
                )
            ),
            "raw_sport": raw_sport,
            "raw_tournament": "",
            "raw_event_title": remainder,
        }

    # QJ League
    if remainder_lower.startswith(
        "qj league"
    ):
        raw_event_title = remainder[
            len("qj league"):
        ].strip(
            " .-–—"
        )

        return {
            "sport": sport,
            "tournament": "QJ League",
            "title": (
                normalize_russian_text(
                    raw_event_title
                )
            ),
            "raw_sport": raw_sport,
            "raw_tournament": (
                "QJ League"
            ),
            "raw_event_title": (
                raw_event_title
            ),
        }

    tournament_markers = [
        "Плей-офф кезеңі",
        "іріктеу турнирі",
        "Іріктеу турнирі",
        "Финал",
        "(Әйелдер)",
        "(әйелдер)",
    ]

    for marker in tournament_markers:
        marker_position = (
            remainder.find(
                marker
            )
        )

        if marker_position == -1:
            continue

        tournament_end = (
            marker_position
            + len(marker)
        )

        raw_tournament = remainder[
            :tournament_end
        ].strip(
            " ."
        )

        raw_event_title = remainder[
            tournament_end:
        ].strip(
            " .-–—"
        )

        return {
            "sport": sport,
            "tournament": (
                normalize_russian_text(
                    raw_tournament
                )
            ),
            "title": (
                normalize_russian_text(
                    raw_event_title
                )
            ),
            "raw_sport": raw_sport,
            "raw_tournament": (
                raw_tournament
            ),
            "raw_event_title": (
                raw_event_title
            ),
        }

    return {
        "sport": sport,
        "tournament": "",
        "title": (
            normalize_russian_text(
                remainder
            )
        ),
        "raw_sport": raw_sport,
        "raw_tournament": "",
        "raw_event_title": remainder,
    }


def find_current_live(soup):
    page_text = " ".join(
        soup.stripped_strings
    )

    pattern = re.compile(
        r"(?:СЕЙЧАС\s+В\s+ЭФИРЕ|в\s+эфире)"
        r"\s+LIVE\s+"
        r"(\d{2}:\d{2})\s+"
        r"(.+?)\s+"
        r"Смотреть\s+онлайн",
        flags=re.IGNORECASE,
    )

    match = pattern.search(
        page_text
    )

    if not match:
        return None

    return {
        "time": match.group(1),
        "raw_title": " ".join(
            match.group(2).split()
        ),
        "is_live": True,
    }


async def get_qazsport_schedule(
    target_date=None,
    include_current_live=True,
):
    url = build_url(
        target_date
    )

    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    timeout = aiohttp.ClientTimeout(
        total=20
    )

    async with aiohttp.ClientSession(
        headers=headers,
        timeout=timeout,
    ) as session:

        async with session.get(
            url
        ) as response:

            response.raise_for_status()

            html = await response.text()

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    base_date = get_page_date(
        soup
    )

    if base_date is None:
        raise RuntimeError(
            "Не удалось определить "
            "дату страницы Qazsport"
        )

    schedule = []
    seen = set()

    day_offset = 0
    previous_minutes = None

    for element in soup.find_all(
        "a"
    ):
        text = " ".join(
            element.stripped_strings
        )

        time_match = (
            TIME_PATTERN.search(
                text
            )
        )

        if not time_match:
            continue

        time_text = (
            time_match.group(0)
        )

        current_minutes = (
            time_to_minutes(
                time_text
            )
        )

        if (
            previous_minutes
            is not None
            and current_minutes
            < previous_minutes
        ):
            day_offset += 1

        event_date = (
            base_date
            + timedelta(
                days=day_offset
            )
        )

        raw_title = clean_title(
            text,
            time_text,
        )

        live_evidence = classify_live_evidence(
            text,
            asset_hints=extract_asset_hints(element),
            live_asset_patterns=LIVE_ASSET_PATTERNS,
        )
        is_live = live_evidence.is_live

        event_key = (
            event_date.isoformat(),
            time_text,
            raw_title,
        )

        if event_key in seen:
            previous_minutes = (
                current_minutes
            )
            continue

        seen.add(
            event_key
        )

        schedule.append(
            {
                "date": (
                    event_date.isoformat()
                ),
                "time": time_text,
                "channel": CHANNEL,
                "is_live": is_live,
                "live_state": live_evidence.state,
                "live_evidence_method": live_evidence.method,
                "live_evidence_value": live_evidence.value,
                "live_evidence_confidence": live_evidence.confidence,
                "raw_title": raw_title,
                "schedule_offset": (
                    current_minutes
                    + day_offset * 1440
                ),
            }
        )

        previous_minutes = (
            current_minutes
        )

    if not schedule:
        raise RuntimeError(
            "Qazsport: дата страницы определена, но строки телепрограммы не распознаны"
        )

    if include_current_live:
        current_live = (
            find_current_live(
                soup
            )
        )
    else:
        current_live = None

    if current_live:
        current_minutes = (
            time_to_minutes(
                current_live["time"]
            )
        )

        if schedule:
            minimum_offset = min(
                event[
                    "schedule_offset"
                ]
                for event in schedule
            )

            maximum_offset = max(
                event[
                    "schedule_offset"
                ]
                for event in schedule
            )

            candidates = [
                current_minutes,
                current_minutes + 1440,
                current_minutes + 2880,
            ]

            inside_range = [
                value
                for value in candidates
                if (
                    minimum_offset
                    <= value
                    <= maximum_offset
                )
            ]

            if inside_range:
                current_offset = min(
                    inside_range
                )
            else:
                current_offset = min(
                    candidates,
                    key=lambda value: min(
                        abs(
                            value
                            - minimum_offset
                        ),
                        abs(
                            value
                            - maximum_offset
                        ),
                    ),
                )
        else:
            current_offset = (
                current_minutes
            )

        current_day_offset = (
            current_offset // 1440
        )

        current_date = (
            base_date
            + timedelta(
                days=current_day_offset
            )
        )

        event_key = (
            current_date.isoformat(),
            current_live["time"],
            current_live["raw_title"],
        )

        if event_key not in seen:
            schedule.append(
                {
                    "date": (
                        current_date
                        .isoformat()
                    ),
                    "time": (
                        current_live[
                            "time"
                        ]
                    ),
                    "channel": CHANNEL,
                    "is_live": True,
                    "live_state": "live",
                    "live_evidence_method": "official_current_live_banner",
                    "live_evidence_value": "LIVE",
                    "live_evidence_confidence": "high",
                    "raw_title": (
                        current_live[
                            "raw_title"
                        ]
                    ),
                    "schedule_offset": (
                        current_offset
                    ),
                }
            )

    schedule.sort(
        key=lambda event:
        event["schedule_offset"]
    )

    for index, event in enumerate(
        schedule
    ):
        if (
            index + 1
            < len(schedule)
        ):
            next_event = schedule[
                index + 1
            ]

            event[
                "estimated_broadcast_end_date"
            ] = next_event["date"]

            event[
                "estimated_broadcast_end"
            ] = next_event["time"]

            event[
                "end_estimation_method"
            ] = "next_program"

            event[
                "end_confidence"
            ] = "high"

        else:
            event[
                "estimated_broadcast_end_date"
            ] = None

            event[
                "estimated_broadcast_end"
            ] = None

            event[
                "end_estimation_method"
            ] = None

            event[
                "end_confidence"
            ] = "unknown"

    for event in schedule:
        if not event["is_live"]:
            continue

        parsed = parse_qazsport_title(
            event["raw_title"]
        )

        event["raw_sport"] = parsed.get(
            "raw_sport",
            "",
        )

        event[
            "raw_tournament"
        ] = parsed.get(
            "raw_tournament",
            "",
        )

        event[
            "raw_event_title"
        ] = parsed.get(
            "raw_event_title",
            event["raw_title"],
        )

        event["sport"] = parsed[
            "sport"
        ]

        event["tournament"] = parsed[
            "tournament"
        ]

        event["title"] = parsed[
            "title"
        ]

    return [
        build_sport_event(
            event,
            source=SOURCE,
            source_url=url,
        )
        for event in schedule
    ]


async def main():
    schedule = await get_qazsport_schedule()

    live_events = [
        event
        for event in schedule
        if event["is_live"]
    ]

    print(
        "LIVE-событий:",
        len(live_events)
    )

    print()

    for number, event in enumerate(
        live_events,
        start=1
    ):
        print(
            f"{number}.\n"
            f"Дата: {event['date']}\n"
            f"Начало эфира: "
            f"{event['time']}\n"
            f"Окончание эфира: "
            f"{event['estimated_broadcast_end_date']} "
            f"{event['estimated_broadcast_end']}\n"
            f"Вид спорта: "
            f"{event['sport']}\n"
            f"Турнир: "
            f"{event['tournament']}\n"
            f"Событие: "
            f"{event['title']}\n"
            f"Исходник: "
            f"{event['raw_title']}\n"
            f"Канал: "
            f"{event['channel']}\n"
            f"LIVE: "
            f"{event['is_live']}\n"
        )


if __name__ == "__main__":
    asyncio.run(main())
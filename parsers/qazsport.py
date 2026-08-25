import asyncio
import re
from datetime import datetime, timedelta

import aiohttp
from bs4 import BeautifulSoup


URL = "https://qazsporttv.kz/ru/program"
CHANNEL = "Qazsport"

TIME_PATTERN = re.compile(
    r"\b(?:[01]\d|2[0-3]):[0-5]\d\b"
)

DATE_PATTERN = re.compile(
    r"\b(\d{2})\.(\d{2})\.(\d{4})\b"
)


def time_to_minutes(time_text):
    hour, minute = map(
        int,
        time_text.split(":")
    )

    return hour * 60 + minute


def clean_title(text, time_text):
    title = re.sub(
        r"\bLIVE\b",
        "",
        text,
        flags=re.IGNORECASE
    )

    title = title.replace(
        time_text,
        "",
        1
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

    day, month, year = match.groups()

    return datetime.strptime(
        f"{year}-{month}-{day}",
        "%Y-%m-%d"
    ).date()


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
        flags=re.IGNORECASE
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


async def get_qazsport_schedule():
    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    timeout = aiohttp.ClientTimeout(
        total=20
    )

    async with aiohttp.ClientSession(
        headers=headers,
        timeout=timeout
    ) as session:

        async with session.get(URL) as response:
            html = await response.text()

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    base_date = get_page_date(soup)

    if base_date is None:
        raise RuntimeError(
            "Не удалось определить дату страницы Qazsport"
        )

    schedule = []
    seen = set()

    day_offset = 0
    previous_minutes = None

    # Сначала собираем ВСЮ обычную сетку Qazsport
    for element in soup.find_all("a"):
        text = " ".join(
            element.stripped_strings
        )

        time_match = TIME_PATTERN.search(
            text
        )

        if not time_match:
            continue

        time_text = time_match.group(0)

        current_minutes = time_to_minutes(
            time_text
        )

        # Если после 23:xx пошло 00:xx / 01:xx / 02:xx,
        # значит наступил следующий календарный день.
        if (
            previous_minutes is not None
            and current_minutes < previous_minutes
        ):
            day_offset += 1

        event_date = (
            base_date
            + timedelta(days=day_offset)
        )

        raw_title = clean_title(
            text,
            time_text
        )

        is_live = (
            "LIVE" in text.upper()
        )

        event_key = (
            event_date.isoformat(),
            time_text,
            raw_title
        )

        if event_key in seen:
            previous_minutes = current_minutes
            continue

        seen.add(event_key)

        schedule.append(
            {
                "date": event_date.isoformat(),
                "time": time_text,
                "channel": CHANNEL,
                "is_live": is_live,
                "raw_title": raw_title,

                # Внутреннее число,
                # чтобы правильно сортировать после полуночи.
                "schedule_offset": (
                    current_minutes
                    + day_offset * 1440
                ),
            }
        )

        previous_minutes = current_minutes

    # Отдельно добавляем блок "СЕЙЧАС В ЭФИРЕ",
    # потому что Qazsport оформляет его иначе.
    current_live = find_current_live(
        soup
    )

    if current_live:
        current_minutes = time_to_minutes(
            current_live["time"]
        )

        if schedule:
            minimum_offset = min(
                event["schedule_offset"]
                for event in schedule
            )

            maximum_offset = max(
                event["schedule_offset"]
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
                if minimum_offset
                <= value
                <= maximum_offset
            ]

            if inside_range:
                current_offset = min(
                    inside_range
                )
            else:
                current_offset = min(
                    candidates,
                    key=lambda value: min(
                        abs(value - minimum_offset),
                        abs(value - maximum_offset),
                    ),
                )
        else:
            current_offset = current_minutes

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
                    "date": current_date.isoformat(),
                    "time": current_live["time"],
                    "channel": CHANNEL,
                    "is_live": True,
                    "raw_title": current_live[
                        "raw_title"
                    ],
                    "schedule_offset": current_offset,
                }
            )

    # Теперь ставим "СЕЙЧАС В ЭФИРЕ"
    # на правильное место между остальными передачами.
    schedule.sort(
        key=lambda event:
        event["schedule_offset"]
    )

    # Для каждого элемента берём начало
    # следующей программы как ориентир
    # окончания текущего эфирного слота.
    for index, event in enumerate(
        schedule
    ):
        if index + 1 < len(schedule):
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

    return schedule


async def main():
    schedule = await get_qazsport_schedule()

    live_events = [
        event
        for event in schedule
        if event["is_live"]
    ]

    print(
        "Всего строк в сетке:",
        len(schedule)
    )

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
            f"Начало эфира: {event['time']}\n"
            f"Ориентир окончания эфира: "
            f"{event['estimated_broadcast_end_date']} "
            f"{event['estimated_broadcast_end']}\n"
            f"Канал: {event['channel']}\n"
            f"LIVE: {event['is_live']}\n"
            f"Название: {event['raw_title']}\n"
            f"Метод окончания: "
            f"{event['end_estimation_method']}\n"
        )


if __name__ == "__main__":
    asyncio.run(main())
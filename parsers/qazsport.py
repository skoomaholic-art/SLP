import asyncio
import re

import aiohttp
from bs4 import BeautifulSoup


URL = "https://qazsporttv.kz/ru/program"

TIME_PATTERN = re.compile(
    r"\b(?:[01]\d|2[0-3]):[0-5]\d\b"
)


def clean_title(text, time_text):
    title = re.sub(
        r"\bLIVE\b",
        "",
        text,
        count=1,
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


async def get_qazsport_live_events():
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

    live_events = []
    seen = set()

    # 1. Обычные LIVE-события расписания
    for element in soup.find_all("a"):
        text = " ".join(
            element.stripped_strings
        )

        if "LIVE" not in text.upper():
            continue

        time_match = TIME_PATTERN.search(text)

        if not time_match:
            continue

        time_text = time_match.group(0)

        title = clean_title(
            text,
            time_text
        )

        event_key = (
            time_text,
            title
        )

        if event_key in seen:
            continue

        seen.add(event_key)

        live_events.append(
            {
                "time": time_text,
                "title": title,
            }
        )

    # 2. Отдельно ищем событие,
    # которое Qazsport показывает как
    # "СЕЙЧАС В ЭФИРЕ / LIVE"
    page_text = " ".join(
        soup.stripped_strings
    )

    current_live_pattern = re.compile(
        r"(?:СЕЙЧАС\s+В\s+ЭФИРЕ|в\s+эфире)"
        r"\s+LIVE\s+"
        r"(\d{2}:\d{2})\s+"
        r"(.+?)\s+"
        r"Смотреть\s+онлайн",
        flags=re.IGNORECASE
    )

    current_match = current_live_pattern.search(
        page_text
    )

    if current_match:
        time_text = current_match.group(1)

        title = " ".join(
            current_match
            .group(2)
            .split()
        )

        event_key = (
            time_text,
            title
        )

        if event_key not in seen:
            seen.add(event_key)

            live_events.append(
                {
                    "time": time_text,
                    "title": title,
                }
            )

    # Сортируем события по времени
    live_events.sort(
        key=lambda event: tuple(
            map(
                int,
                event["time"].split(":")
            )
        )
    )

    return live_events


async def main():
    events = await get_qazsport_live_events()

    print(
        "Найдено LIVE-событий:",
        len(events)
    )
    print()

    for number, event in enumerate(
        events,
        start=1
    ):
        print(
            f"{number}. "
            f"{event['time']} — "
            f"{event['title']}"
        )


if __name__ == "__main__":
    asyncio.run(main())
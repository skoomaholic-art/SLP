import asyncio
import re

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


def get_page_date(soup):
    if soup.title:
        page_title = soup.title.get_text(
            strip=True
        )

        match = DATE_PATTERN.search(
            page_title
        )

        if match:
            day, month, year = match.groups()

            return (
                f"{year}-{month}-{day}"
            )

    return None


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

    page_date = get_page_date(soup)

    live_events = []
    seen = set()

    # Обычные LIVE-события
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
                "date": page_date,
                "time": time_text,
                "channel": CHANNEL,
                "is_live": True,
                "raw_title": title,
            }
        )

    # Событие "СЕЙЧАС В ЭФИРЕ"
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
                    "date": page_date,
                    "time": time_text,
                    "channel": CHANNEL,
                    "is_live": True,
                    "raw_title": title,
                }
            )

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
            f"{number}.\n"
            f"Дата: {event['date']}\n"
            f"Время: {event['time']}\n"
            f"Канал: {event['channel']}\n"
            f"LIVE: {event['is_live']}\n"
            f"Название: {event['raw_title']}\n"
        )


if __name__ == "__main__":
    asyncio.run(main())
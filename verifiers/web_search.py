import re
from collections import Counter
from datetime import datetime
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from ddgs import DDGS


MOSCOW_TIMEZONE = ZoneInfo(
    "Europe/Moscow"
)

KZ_TIMEZONE = ZoneInfo(
    "Asia/Almaty"
)


def search_event(
    query,
    max_results=10
):
    search = DDGS(
        timeout=15
    )

    return search.text(
        query,
        region="ru-ru",
        safesearch="off",
        max_results=max_results,
    )


def get_domain(url):
    try:
        return urlparse(
            url
        ).netloc.replace(
            "www.",
            ""
        )
    except Exception:
        return ""


def extract_moscow_times(
    results
):
    pattern = re.compile(
        r"(\d{1,2}:\d{2})"
        r"\s+по\s+московскому\s+времени",
        flags=re.IGNORECASE
    )

    found = []

    for result in results:
        title = result.get(
            "title",
            ""
        )

        body = result.get(
            "body",
            ""
        )

        url = result.get(
            "href",
            ""
        )

        combined_text = (
            title
            + " "
            + body
        )

        matches = pattern.findall(
            combined_text
        )

        for time_text in matches:
            found.append(
                {
                    "time": time_text,
                    "domain": get_domain(
                        url
                    ),
                    "title": title,
                    "url": url,
                }
            )

    return found


def find_time_consensus(
    found_times
):
    if not found_times:
        return None

    # Один домен считаем только один раз,
    # чтобы один сайт не дал ложное большинство.
    unique_sources = {}

    for item in found_times:
        key = (
            item["domain"],
            item["time"]
        )

        unique_sources[key] = item

    unique_items = list(
        unique_sources.values()
    )

    time_counter = Counter(
        item["time"]
        for item in unique_items
    )

    most_common_time, count = (
        time_counter.most_common(1)[0]
    )

    supporting_sources = [
        item
        for item in unique_items
        if item["time"]
        == most_common_time
    ]

    return {
        "time": most_common_time,
        "source_count": count,
        "sources": supporting_sources,
    }


def convert_moscow_to_kz(
    date_text,
    time_text
):
    moscow_datetime = (
        datetime.strptime(
            f"{date_text} {time_text}",
            "%Y-%m-%d %H:%M"
        )
        .replace(
            tzinfo=MOSCOW_TIMEZONE
        )
    )

    return moscow_datetime.astimezone(
        KZ_TIMEZONE
    )


def calculate_difference(
    broadcast_datetime,
    event_datetime
):
    difference = (
        event_datetime
        - broadcast_datetime
    )

    return int(
        difference.total_seconds()
        / 60
    )


def get_time_status(
    difference_minutes,
    source_count
):
    absolute_difference = abs(
        difference_minutes
    )

    if source_count < 2:
        return (
            "🟡 Предварительные данные\n"
            "Время найдено только в одном "
            "независимом источнике."
        )

    if absolute_difference <= 10:
        return (
            "✅ Предварительно подтверждено\n"
            "Несколько независимых источников "
            "указывают одинаковое время.\n"
            "Расхождение с эфиром допустимое — "
            "вероятный предэфир телеканала."
        )

    if absolute_difference <= 20:
        return (
            "🟡 Требуется дополнительная проверка\n"
            "Источники согласны между собой, "
            "но расхождение с эфиром "
            "больше 10 минут."
        )

    return (
        "⚠️ Существенное расхождение\n"
        "Необходимо дополнительно проверить "
        "время и соответствие события."
    )


def main():
    query = (
        "АЕК Левски "
        "26 августа 2026 "
        "футбол время матча"
    )

    print(
        "Поисковый запрос:"
    )

    print(query)
    print()

    results = search_event(
        query,
        max_results=10
    )

    print(
        "Всего результатов поиска:",
        len(results)
    )

    print()

    found_times = (
        extract_moscow_times(
            results
        )
    )

    if not found_times:
        print(
            "❌ Время в поисковых "
            "результатах не найдено."
        )
        return

    print(
        "Найденные варианты времени:"
    )

    print()

    for item in found_times:
        print(
            f"• {item['time']} МСК"
        )

        print(
            f"  Источник: "
            f"{item['domain']}"
        )

        print()

    consensus = (
        find_time_consensus(
            found_times
        )
    )

    if not consensus:
        print(
            "❌ Не удалось определить "
            "наиболее вероятное время."
        )
        return

    print(
        "Наиболее вероятное время:",
        consensus["time"],
        "МСК"
    )

    print(
        "Независимых источников:",
        consensus["source_count"]
    )

    print()

    print(
        "Источники, подтверждающие время:"
    )

    for source in consensus[
        "sources"
    ]:
        print(
            "•",
            source["domain"]
        )

    print()

    external_datetime = (
        convert_moscow_to_kz(
            "2026-08-26",
            consensus["time"]
        )
    )

    broadcast_datetime = (
        datetime.strptime(
            "2026-08-26 23:50",
            "%Y-%m-%d %H:%M"
        )
        .replace(
            tzinfo=KZ_TIMEZONE
        )
    )

    difference_minutes = (
        calculate_difference(
            broadcast_datetime,
            external_datetime
        )
    )

    print(
        "Qazsport — начало эфира:",
        broadcast_datetime.strftime(
            "%Y-%m-%d %H:%M"
        )
    )

    print(
        "Время события по внешним источникам:",
        external_datetime.strftime(
            "%Y-%m-%d %H:%M"
        )
    )

    print(
        "Разница:",
        difference_minutes,
        "минут"
    )

    print()

    print(
        get_time_status(
            difference_minutes,
            consensus[
                "source_count"
            ]
        )
    )


if __name__ == "__main__":
    main()
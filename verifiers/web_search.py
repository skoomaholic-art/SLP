import re
from collections import defaultdict
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


# --------------------------------
# ГРУППЫ И УРОВНИ ДОВЕРИЯ
# --------------------------------

SOURCE_GROUPS = {
    "uefa": {
        "domains": {
            "uefa.com",
        },
        "label": "UEFA",
        "category": "official",
        "trust_label": "🥇 Официальный источник",
        "weight": 100,
    },

    "fifa": {
        "domains": {
            "fifa.com",
        },
        "label": "FIFA",
        "category": "official",
        "trust_label": "🥇 Официальный источник",
        "weight": 100,
    },

    "afc": {
        "domains": {
            "the-afc.com",
        },
        "label": "AFC",
        "category": "official",
        "trust_label": "🥇 Официальный источник",
        "weight": 100,
    },

    "kff": {
        "domains": {
            "kff.kz",
        },
        "label": "КФФ",
        "category": "official",
        "trust_label": "🥇 Официальный источник",
        "weight": 100,
    },

    "sport_express": {
        "domains": {
            "sport-express.ru",
            "sport-express.net",
        },
        "label": "Спорт-Экспресс",
        "category": "major_media",
        "trust_label": "🥈 Крупное спортивное СМИ",
        "weight": 70,
    },

    "championat": {
        "domains": {
            "championat.com",
        },
        "label": "Чемпионат",
        "category": "major_media",
        "trust_label": "🥈 Крупное спортивное СМИ",
        "weight": 70,
    },

    "sport24": {
        "domains": {
            "sport24.ru",
        },
        "label": "Sport24",
        "category": "major_media",
        "trust_label": "🥈 Крупное спортивное СМИ",
        "weight": 70,
    },

    "sports_ru": {
        "domains": {
            "sports.ru",
        },
        "label": "Sports.ru",
        "category": "major_media",
        "trust_label": "🥈 Крупное спортивное СМИ",
        "weight": 70,
    },

    "soccerway": {
        "domains": {
            "soccerway.com",
        },
        "label": "Soccerway",
        "category": "database",
        "trust_label": "🥉 Спортивная база / агрегатор",
        "weight": 50,
    },

    "flashscore": {
        "domains": {
            "flashscore.com",
        },
        "label": "Flashscore",
        "category": "database",
        "trust_label": "🥉 Спортивная база / агрегатор",
        "weight": 50,
    },

    "bookmaker_ratings": {
        "domains": {
            "bookmaker-ratings.ru",
        },
        "label": "Рейтинг Букмекеров",
        "category": "low",
        "trust_label": "⚪ Прогнозный / букмекерский источник",
        "weight": 20,
    },

    "metaratings": {
        "domains": {
            "metaratings.ru",
        },
        "label": "Metaratings",
        "category": "low",
        "trust_label": "⚪ Прогнозный / букмекерский источник",
        "weight": 20,
    },

    "online_bookmakers": {
        "domains": {
            "online-bookmakers.com",
        },
        "label": "Online Bookmakers",
        "category": "low",
        "trust_label": "⚪ Прогнозный / букмекерский источник",
        "weight": 20,
    },

    "betonmobile": {
        "domains": {
            "betonmobile.ru",
        },
        "label": "BetOnMobile",
        "category": "low",
        "trust_label": "⚪ Прогнозный / букмекерский источник",
        "weight": 20,
    },
}


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
        domain = urlparse(
            url
        ).netloc.lower()

        if domain.startswith("www."):
            domain = domain[4:]

        return domain

    except Exception:
        return ""


def domain_matches(
    domain,
    known_domain
):
    return (
        domain == known_domain
        or domain.endswith(
            "." + known_domain
        )
    )


def classify_source(domain):
    for group_id, group in (
        SOURCE_GROUPS.items()
    ):
        for known_domain in group[
            "domains"
        ]:
            if domain_matches(
                domain,
                known_domain
            ):
                return {
                    "group_id": group_id,
                    "source_name": group[
                        "label"
                    ],
                    "category": group[
                        "category"
                    ],
                    "trust_label": group[
                        "trust_label"
                    ],
                    "weight": group[
                        "weight"
                    ],
                }

    return {
        "group_id": domain,
        "source_name": domain,
        "category": "unknown",
        "trust_label":
            "⚪ Прочий интернет-источник",
        "weight": 30,
    }


def extract_moscow_times(
    results
):
    pattern = re.compile(
        r"(\d{1,2}:\d{2})"
        r"\s*"
        r"(?:"
        r"по\s+московскому\s+времени"
        r"|мск"
        r")",
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

        domain = get_domain(
            url
        )

        source = classify_source(
            domain
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
                    "domain": domain,
                    "group_id": source[
                        "group_id"
                    ],
                    "source_name": source[
                        "source_name"
                    ],
                    "category": source[
                        "category"
                    ],
                    "trust_label": source[
                        "trust_label"
                    ],
                    "weight": source[
                        "weight"
                    ],
                    "title": title,
                    "url": url,
                }
            )

    return found


def remove_duplicate_sources(
    found_times
):
    unique = {}

    for item in found_times:
        # Главное изменение:
        # считаем одну ОРГАНИЗАЦИЮ
        # только один раз для одного времени.
        key = (
            item["group_id"],
            item["time"]
        )

        existing = unique.get(
            key
        )

        if existing is None:
            unique[key] = item

    return list(
        unique.values()
    )


def find_weighted_consensus(
    found_times
):
    unique_items = (
        remove_duplicate_sources(
            found_times
        )
    )

    if not unique_items:
        return None

    scores = defaultdict(int)
    counts = defaultdict(int)

    for item in unique_items:
        time_text = item["time"]

        scores[
            time_text
        ] += item["weight"]

        counts[
            time_text
        ] += 1

    best_time = max(
        scores,
        key=lambda time_text: (
            scores[time_text],
            counts[time_text],
        )
    )

    supporting_sources = [
        item
        for item in unique_items
        if item["time"] == best_time
    ]

    return {
        "time": best_time,
        "weighted_score":
            scores[best_time],
        "source_count":
            counts[best_time],
        "sources":
            supporting_sources,
    }


def get_confidence_level(
    consensus
):
    sources = consensus[
        "sources"
    ]

    official_count = sum(
        1
        for item in sources
        if item["category"]
        == "official"
    )

    major_media_count = sum(
        1
        for item in sources
        if item["category"]
        == "major_media"
    )

    database_count = sum(
        1
        for item in sources
        if item["category"]
        == "database"
    )

    source_count = consensus[
        "source_count"
    ]

    if official_count >= 1:
        return (
            "🟢 Высокая уверенность",
            "Время подтверждает "
            "официальный источник."
        )

    if major_media_count >= 2:
        return (
            "🟢 Высокая уверенность",
            "Время независимо подтверждают "
            "несколько крупных спортивных СМИ."
        )

    if (
        major_media_count >= 1
        and (
            database_count >= 1
            or source_count >= 3
        )
    ):
        return (
            "🟡 Средняя уверенность",
            "Есть крупное спортивное СМИ "
            "и дополнительные источники."
        )

    if source_count >= 2:
        return (
            "🟡 Средняя уверенность",
            "Несколько независимых источников "
            "указывают одинаковое время, "
            "но официального подтверждения нет."
        )

    return (
        "🔴 Низкая уверенность",
        "Найден только один "
        "независимый источник."
    )


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
    difference_minutes
):
    absolute_difference = abs(
        difference_minutes
    )

    if absolute_difference <= 10:
        return (
            "✅ Время согласуется\n"
            "Расхождение 0–10 минут "
            "допустимо.\n"
            "Вероятно, телеканал начинает "
            "предэфир заранее."
        )

    if absolute_difference <= 20:
        return (
            "🟡 Требуется дополнительная проверка\n"
            "Расхождение с эфиром "
            "больше 10 минут."
        )

    return (
        "⚠️ Существенное расхождение\n"
        "Необходимо проверить "
        "соответствие события и время."
    )


def main():
    query = (
        "АЕК Левски "
        "26 августа 2026 "
        "футбол время матча"
    )

    print("Поисковый запрос:")
    print(query)
    print()

    results = search_event(
        query,
        max_results=10
    )

    found_times = (
        extract_moscow_times(
            results
        )
    )

    unique_sources = (
        remove_duplicate_sources(
            found_times
        )
    )

    if not unique_sources:
        print(
            "❌ Время события "
            "не найдено."
        )
        return

    print(
        "Независимые источники:"
    )
    print()

    for item in unique_sources:
        print(
            f"• {item['time']} МСК"
        )

        print(
            f"  Источник: "
            f"{item['source_name']}"
        )

        print(
            f"  Домен: "
            f"{item['domain']}"
        )

        print(
            f"  {item['trust_label']}"
        )

        print(
            f"  Вес: {item['weight']}"
        )

        print()

    consensus = (
        find_weighted_consensus(
            found_times
        )
    )

    if not consensus:
        print(
            "❌ Консенсус не найден."
        )
        return

    print(
        "Наиболее вероятное время:",
        consensus["time"],
        "МСК"
    )

    print(
        "Независимых организаций:",
        consensus["source_count"]
    )

    print(
        "Суммарный вес доверия:",
        consensus["weighted_score"]
    )

    print()

    confidence_label, confidence_reason = (
        get_confidence_level(
            consensus
        )
    )

    print("Уровень доверия:")
    print(confidence_label)
    print(confidence_reason)
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
            difference_minutes
        )
    )


if __name__ == "__main__":
    main()
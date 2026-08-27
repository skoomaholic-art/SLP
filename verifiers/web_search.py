import re
from collections import defaultdict
from datetime import datetime
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from ddgs import DDGS


MOSCOW_TIMEZONE = ZoneInfo("Europe/Moscow")
KZ_TIMEZONE = ZoneInfo("Asia/Almaty")


# ---------------------------------------------------------
# ИСТОЧНИКИ И ИХ ВЕС
# ---------------------------------------------------------

SOURCE_GROUPS = [
    # Официальные источники
    {
        "group_id": "uefa",
        "name": "UEFA",
        "domains": ["uefa.com"],
        "category": "official",
        "weight": 100,
    },
    {
        "group_id": "fifa",
        "name": "FIFA",
        "domains": ["fifa.com"],
        "category": "official",
        "weight": 100,
    },
    {
        "group_id": "afc",
        "name": "AFC",
        "domains": ["the-afc.com"],
        "category": "official",
        "weight": 100,
    },
    {
        "group_id": "kff",
        "name": "KFF",
        "domains": ["kff.kz"],
        "category": "official",
        "weight": 100,
    },

    # Крупные спортивные СМИ
    {
        "group_id": "sport_express",
        "name": "Спорт-Экспресс",
        "domains": ["sport-express.ru", "sport-express.net"],
        "category": "major_media",
        "weight": 70,
    },
    {
        "group_id": "championat",
        "name": "Чемпионат",
        "domains": ["championat.com"],
        "category": "major_media",
        "weight": 70,
    },
    {
        "group_id": "sport24",
        "name": "Sport24",
        "domains": ["sport24.ru"],
        "category": "major_media",
        "weight": 70,
    },
    {
        "group_id": "sports_ru",
        "name": "Sports.ru",
        "domains": ["sports.ru"],
        "category": "major_media",
        "weight": 70,
    },

    # Спортивные базы / агрегаторы
    {
        "group_id": "soccerway",
        "name": "Soccerway",
        "domains": ["soccerway.com"],
        "category": "database",
        "weight": 50,
    },
    {
        "group_id": "flashscore",
        "name": "Flashscore",
        "domains": ["flashscore.com", "flashscore.ru"],
        "category": "database",
        "weight": 50,
    },
    {
        "group_id": "livesport",
        "name": "Livesport",
        "domains": ["livesport.com"],
        "category": "database",
        "weight": 50,
    },

    # Букмекерские / прогнозные сайты
    {
        "group_id": "bookmaker_ratings",
        "name": "Рейтинг Букмекеров",
        "domains": ["bookmaker-ratings.ru"],
        "category": "low_trust",
        "weight": 20,
    },
    {
        "group_id": "metaratings",
        "name": "Metaratings",
        "domains": ["metaratings.ru"],
        "category": "low_trust",
        "weight": 20,
    },
    {
        "group_id": "online_bookmakers",
        "name": "Online Bookmakers",
        "domains": ["online-bookmakers.com"],
        "category": "low_trust",
        "weight": 20,
    },
    {
        "group_id": "betonmobile",
        "name": "Betonmobile",
        "domains": ["betonmobile.ru"],
        "category": "low_trust",
        "weight": 20,
    },
]


# ---------------------------------------------------------
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ---------------------------------------------------------

def normalize_text(text: str) -> str:
    if not text:
        return ""

    text = text.lower()
    text = text.replace("ё", "е")

    text = re.sub(
        r"[^a-zа-я0-9әіңғүұқөһё\s\-–—]",
        " ",
        text,
        flags=re.IGNORECASE,
    )

    text = re.sub(r"\s+", " ", text)

    return text.strip()


def get_domain(url: str) -> str:
    try:
        domain = urlparse(url).netloc.lower()

        if domain.startswith("www."):
            domain = domain[4:]

        return domain
    except Exception:
        return ""


def domain_matches(domain: str, source_domain: str) -> bool:
    return (
        domain == source_domain
        or domain.endswith("." + source_domain)
    )


def classify_source(url: str) -> dict:
    domain = get_domain(url)

    for source in SOURCE_GROUPS:
        for source_domain in source["domains"]:
            if domain_matches(domain, source_domain):
                return {
                    "group_id": source["group_id"],
                    "source_name": source["name"],
                    "category": source["category"],
                    "weight": source["weight"],
                    "domain": domain,
                }

    return {
        "group_id": domain or "unknown",
        "source_name": domain or "Неизвестный источник",
        "category": "unknown",
        "weight": 30,
        "domain": domain,
    }


# ---------------------------------------------------------
# УЧАСТНИКИ СОБЫТИЯ
# ---------------------------------------------------------

def clean_participant_name(text: str) -> str:
    if not text:
        return ""

    # Убираем страны в скобках:
    # АЕК (Греция) -> АЕК
    text = re.sub(r"\([^)]*\)", "", text)

    return text.strip()


def extract_participants(event_title: str) -> list[str]:
    if not event_title:
        return []

    separators = [
        " - ",
        " – ",
        " — ",
        " vs ",
        " VS ",
        " versus ",
    ]

    for separator in separators:
        if separator in event_title:
            parts = event_title.split(separator)

            if len(parts) >= 2:
                return [
                    clean_participant_name(parts[0]),
                    clean_participant_name(parts[1]),
                ]

    return []


def meaningful_tokens(text: str) -> list[str]:
    text = normalize_text(text)

    ignored_words = {
        "fc",
        "fk",
        "фк",
        "club",
        "football",
        "клуб",
        "the",
    }

    tokens = []

    for token in text.split():
        if token in ignored_words:
            continue

        if len(token) < 2:
            continue

        tokens.append(token)

    return tokens


def participant_matches(participant: str, text: str) -> bool:
    participant_tokens = meaningful_tokens(participant)
    normalized_text = normalize_text(text)

    if not participant_tokens:
        return False

    # Достаточно совпадения хотя бы одного значимого слова
    # из названия команды / участника.
    for token in participant_tokens:
        if token in normalized_text:
            return True

    return False


def event_identity_matches(event: dict, search_result: dict) -> bool:
    event_title = event.get("title", "")

    participants = extract_participants(event_title)

    result_text = (
        f"{search_result.get('title', '')} "
        f"{search_result.get('body', '')}"
    )

    # Если это матч с двумя участниками,
    # желательно увидеть обоих в результате поиска.
    if len(participants) == 2:
        first_matches = participant_matches(
            participants[0],
            result_text,
        )

        second_matches = participant_matches(
            participants[1],
            result_text,
        )

        return first_matches and second_matches

    # Если участников выделить нельзя,
    # используем более мягкую проверку.
    event_tokens = meaningful_tokens(event_title)

    if not event_tokens:
        return True

    normalized_result = normalize_text(result_text)

    matches = sum(
        1
        for token in event_tokens
        if token in normalized_result
    )

    required_matches = max(
        1,
        min(2, len(event_tokens)),
    )

    return matches >= required_matches


# ---------------------------------------------------------
# ПОИСК
# ---------------------------------------------------------

def build_search_query(event: dict) -> str:
    title = event.get("title", "")
    sport = event.get("sport", "")
    tournament = event.get("tournament", "")
    date = event.get("date", "")

    date_text = ""

    try:
        parsed_date = datetime.strptime(
            date,
            "%Y-%m-%d",
        )

        date_text = parsed_date.strftime(
            "%d %B %Y"
        )

    except Exception:
        date_text = date

    query_parts = [
        title,
        tournament,
        sport,
        date_text,
        "время матча",
    ]

    return " ".join(
        part
        for part in query_parts
        if part
    )


def search_event(
    event: dict,
    max_results: int = 10,
) -> list[dict]:

    query = build_search_query(event)

    results = []

    try:
        with DDGS() as ddgs:
            search_results = ddgs.text(
                query,
                max_results=max_results,
            )

            for item in search_results:
                result = {
                    "title": item.get("title", ""),
                    "body": item.get("body", ""),
                    "href": item.get("href", ""),
                }

                result["identity_match"] = (
                    event_identity_matches(
                        event,
                        result,
                    )
                )

                results.append(result)

    except Exception as error:
        print(
            f"Ошибка интернет-поиска: {error}"
        )

    return results


# ---------------------------------------------------------
# ИЗВЛЕЧЕНИЕ ВРЕМЕНИ
# ---------------------------------------------------------

MOSCOW_TIME_PATTERNS = [
    r"\b(\d{1,2}:\d{2})\s*"
    r"(?:по\s+московскому\s+времени|мск)\b",

    r"\b(\d{1,2}:\d{2})\s*"
    r"(?:московского\s+времени)\b",
]


def extract_moscow_times(
    search_results: list[dict],
) -> list[dict]:

    evidence = []

    for result in search_results:

        # Результаты, которые не похожи
        # на нужное событие, не используем.
        if not result.get("identity_match"):
            continue

        full_text = (
            f"{result.get('title', '')} "
            f"{result.get('body', '')}"
        )

        found_times = set()

        for pattern in MOSCOW_TIME_PATTERNS:
            matches = re.findall(
                pattern,
                full_text,
                flags=re.IGNORECASE,
            )

            for time_text in matches:
                found_times.add(time_text)

        source_info = classify_source(
            result.get("href", "")
        )

        for time_text in found_times:
            evidence.append(
                {
                    "time": time_text,
                    "url": result.get("href", ""),
                    "title": result.get("title", ""),
                    **source_info,
                }
            )

    return evidence


# ---------------------------------------------------------
# УДАЛЕНИЕ ДУБЛЕЙ И КОНСЕНСУС
# ---------------------------------------------------------

def remove_duplicate_sources(
    evidence: list[dict],
) -> list[dict]:

    unique = {}

    for item in evidence:

        key = (
            item["group_id"],
            item["time"],
        )

        if key not in unique:
            unique[key] = item

    return list(unique.values())


def find_weighted_consensus(
    evidence: list[dict],
):

    if not evidence:
        return None

    grouped = defaultdict(
        lambda: {
            "weight": 0,
            "sources": [],
        }
    )

    for item in evidence:
        time_text = item["time"]

        grouped[time_text]["weight"] += (
            item["weight"]
        )

        grouped[time_text]["sources"].append(
            item
        )

    best_time = max(
        grouped.keys(),
        key=lambda time_text: (
            grouped[time_text]["weight"],
            len(
                grouped[time_text]["sources"]
            ),
        ),
    )

    return {
        "time": best_time,
        "total_weight": grouped[
            best_time
        ]["weight"],
        "sources": grouped[
            best_time
        ]["sources"],
    }


# ---------------------------------------------------------
# УРОВЕНЬ УВЕРЕННОСТИ
# ---------------------------------------------------------

def get_confidence_level(
    sources: list[dict],
) -> dict:

    if not sources:
        return {
            "level": "low",
            "label": "🔴 Низкая",
            "reason": (
                "Недостаточно независимых "
                "источников."
            ),
        }

    categories = [
        source["category"]
        for source in sources
    ]

    official_count = categories.count(
        "official"
    )

    major_count = categories.count(
        "major_media"
    )

    database_count = categories.count(
        "database"
    )

    source_count = len(sources)

    if official_count >= 1:
        return {
            "level": "high",
            "label": "🟢 Высокая",
            "reason": (
                "Время подтверждается "
                "официальным источником."
            ),
        }

    if major_count >= 2:
        return {
            "level": "high",
            "label": "🟢 Высокая",
            "reason": (
                "Время совпадает у нескольких "
                "крупных спортивных СМИ."
            ),
        }

    if (
        major_count >= 1
        and (
            database_count >= 1
            or source_count >= 3
        )
    ):
        return {
            "level": "medium",
            "label": "🟡 Средняя",
            "reason": (
                "Есть крупное спортивное СМИ "
                "и дополнительные независимые "
                "источники."
            ),
        }

    if source_count >= 2:
        return {
            "level": "medium",
            "label": "🟡 Средняя",
            "reason": (
                "Время совпадает минимум "
                "у двух независимых источников."
            ),
        }

    return {
        "level": "low",
        "label": "🔴 Низкая",
        "reason": (
            "Найден только один "
            "подходящий источник."
        ),
    }


# ---------------------------------------------------------
# ПЕРЕВОД MSK -> ASIA/ALMATY
# ---------------------------------------------------------

def convert_moscow_to_kz(
    event_date: str,
    moscow_time: str,
):

    date_object = datetime.strptime(
        event_date,
        "%Y-%m-%d",
    ).date()

    hour, minute = map(
        int,
        moscow_time.split(":"),
    )

    moscow_datetime = datetime(
        year=date_object.year,
        month=date_object.month,
        day=date_object.day,
        hour=hour,
        minute=minute,
        tzinfo=MOSCOW_TIMEZONE,
    )

    return moscow_datetime.astimezone(
        KZ_TIMEZONE
    )


# ---------------------------------------------------------
# СРАВНЕНИЕ С ЭФИРОМ
# ---------------------------------------------------------

def get_broadcast_datetime(
    event: dict,
):

    event_date = event.get("date")
    event_time = (
        event.get("time")
        or event.get("broadcast_start")
    )

    if not event_date or not event_time:
        return None

    try:
        return datetime.strptime(
            f"{event_date} {event_time}",
            "%Y-%m-%d %H:%M",
        ).replace(
            tzinfo=KZ_TIMEZONE
        )

    except Exception:
        return None


def calculate_difference_minutes(
    broadcast_datetime,
    external_datetime,
):

    difference = (
        external_datetime
        - broadcast_datetime
    )

    return int(
        difference.total_seconds() / 60
    )


def get_time_status(
    difference_minutes: int,
) -> dict:

    absolute_difference = abs(
        difference_minutes
    )

    if absolute_difference <= 10:
        return {
            "status": "match",
            "label": "✅ Время совпадает",
            "description": (
                "Расхождение 0–10 минут "
                "считаем нормальным. "
                "Вероятно, телеканал начинает "
                "предэфир до официального старта."
            ),
        }

    if absolute_difference <= 20:
        return {
            "status": "check",
            "label": (
                "🟡 Требуется дополнительная "
                "проверка"
            ),
            "description": (
                "Расхождение составляет "
                "11–20 минут."
            ),
        }

    return {
        "status": "warning",
        "label": (
            "🔴 Значительное расхождение"
        ),
        "description": (
            "Расхождение превышает "
            "20 минут."
        ),
    }


# ---------------------------------------------------------
# ГЛАВНАЯ УНИВЕРСАЛЬНАЯ ФУНКЦИЯ
# ---------------------------------------------------------

def verify_event(
    event: dict,
    max_results: int = 10,
) -> dict:

    query = build_search_query(event)

    search_results = search_event(
        event,
        max_results=max_results,
    )

    matching_results = [
        result
        for result in search_results
        if result.get("identity_match")
    ]

    evidence = extract_moscow_times(
        search_results
    )

    unique_evidence = (
        remove_duplicate_sources(
            evidence
        )
    )

    consensus = find_weighted_consensus(
        unique_evidence
    )

    result = {
        "event": event,
        "query": query,
        "search_results_count": len(
            search_results
        ),
        "matching_results_count": len(
            matching_results
        ),
        "found": False,
        "sources": [],
        "confidence": {
            "level": "low",
            "label": "🔴 Низкая",
            "reason": (
                "Не удалось получить "
                "достаточно данных."
            ),
        },
    }

    if consensus is None:
        result["message"] = (
            "Не удалось найти подтверждённое "
            "время события во внешних источниках."
        )

        return result

    external_datetime = (
        convert_moscow_to_kz(
            event["date"],
            consensus["time"],
        )
    )

    broadcast_datetime = (
        get_broadcast_datetime(
            event
        )
    )

    confidence = get_confidence_level(
        consensus["sources"]
    )

    result.update(
        {
            "found": True,
            "external_time_msk": (
                consensus["time"]
            ),
            "external_datetime_kz": (
                external_datetime
            ),
            "external_date_kz": (
                external_datetime
                .strftime("%Y-%m-%d")
            ),
            "external_time_kz": (
                external_datetime
                .strftime("%H:%M")
            ),
            "source_count": len(
                consensus["sources"]
            ),
            "total_weight": (
                consensus["total_weight"]
            ),
            "sources": (
                consensus["sources"]
            ),
            "confidence": confidence,
        }
    )

    if broadcast_datetime is not None:

        difference_minutes = (
            calculate_difference_minutes(
                broadcast_datetime,
                external_datetime,
            )
        )

        result[
            "difference_minutes"
        ] = difference_minutes

        result[
            "time_status"
        ] = get_time_status(
            difference_minutes
        )

    return result


# ---------------------------------------------------------
# КРАСИВЫЙ ВЫВОД ДЛЯ ТЕСТА
# ---------------------------------------------------------

def print_verification_result(
    result: dict,
):

    print()
    print("=" * 60)
    print("ПРОВЕРКА СОБЫТИЯ")
    print("=" * 60)

    event = result["event"]

    print(
        f"Событие: {event.get('title')}"
    )

    print(
        f"Дата: {event.get('date')}"
    )

    print(
        "Начало эфира: "
        f"{event.get('time')}"
    )

    print(
        f"Поисковый запрос: "
        f"{result['query']}"
    )

    print(
        "Результатов поиска: "
        f"{result['search_results_count']}"
    )

    print(
        "Подходящих по событию: "
        f"{result['matching_results_count']}"
    )

    print()

    if not result["found"]:
        print("❌ Время не найдено")
        print(result.get("message", ""))
        return

    print(
        "Наиболее вероятное время:"
    )

    print(
        f"  MSK: "
        f"{result['external_time_msk']}"
    )

    print(
        f"  Казахстан: "
        f"{result['external_date_kz']} "
        f"{result['external_time_kz']}"
    )

    print()

    print(
        "Независимых организаций: "
        f"{result['source_count']}"
    )

    print(
        "Суммарный вес доверия: "
        f"{result['total_weight']}"
    )

    print(
        "Уверенность: "
        f"{result['confidence']['label']}"
    )

    print(
        result["confidence"]["reason"]
    )

    if "difference_minutes" in result:

        print()

        print(
            "Разница с эфиром Qazsport: "
            f"{abs(result['difference_minutes'])} мин."
        )

        time_status = result[
            "time_status"
        ]

        print(
            time_status["label"]
        )

        print(
            time_status["description"]
        )

    print()
    print("Использованные источники:")

    for source in result["sources"]:

        print(
            f"- {source['source_name']} | "
            f"{source['time']} MSK | "
            f"вес {source['weight']}"
        )


# ---------------------------------------------------------
# ВРЕМЕННЫЙ ТЕСТ
# ---------------------------------------------------------

def main():

    # Это только тестовый объект.
    # Сама функция verify_event уже универсальная.
    # На следующем шаге передадим сюда событие
    # прямо из парсера Qazsport.

    test_event = {
        "date": "2026-08-26",
        "time": "23:50",
        "sport": "Футбол",
        "tournament": (
            "УЕФА Чемпиондар Лигасы. "
            "Плей-офф кезеңі"
        ),
        "title": (
            "АЕК (Грекия) - "
            "Левски (Болгария)"
        ),
        "channel": "Qazsport",
        "is_live": True,
    }

    result = verify_event(
        test_event
    )

    print_verification_result(
        result
    )


if __name__ == "__main__":
    main()
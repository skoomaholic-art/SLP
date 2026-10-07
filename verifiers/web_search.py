from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from zoneinfo import ZoneInfo


# =========================================================
# НАСТРОЙКИ
# =========================================================

KZ_TIMEZONE = ZoneInfo("Asia/Almaty")
MSK_TIMEZONE = ZoneInfo("Europe/Moscow")

OPENSERP_HOST = "127.0.0.1"
OPENSERP_PORT = 7000
OPENSERP_BASE_URL = (os.getenv("OPENSERP_BASE_URL") or "").strip().rstrip("/")

OPENSERP_BIN = (
    os.getenv("OPENSERP_BIN")
    or shutil.which("openserp")
    or "/go/bin/openserp"
)

PRIMARY_SEARCH_ENGINES = (
    "bing",
    "duckduckgo",
)

FALLBACK_SEARCH_ENGINES = (
    "baidu",
)

OPENSERP_PRIMARY_TIMEOUT = 35
OPENSERP_FALLBACK_TIMEOUT = 25
OPENSERP_RESULT_LIMIT = 10
OPENSERP_EXTRACT_LIMIT = 5
OPENSERP_EXTRACT_TIMEOUT = 45

START_LOCK = threading.Lock()
OPENSERP_TOKEN_LOCK = threading.Lock()
_OPENSERP_ID_TOKEN = ""
_OPENSERP_ID_TOKEN_EXPIRES_AT = 0.0


def _openserp_headers(*, json_body: bool = False) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    if json_body:
        headers["Content-Type"] = "application/json"

    audience = (os.getenv("OPENSERP_AUTH_AUDIENCE") or "").strip()
    if not audience:
        return headers

    global _OPENSERP_ID_TOKEN, _OPENSERP_ID_TOKEN_EXPIRES_AT
    now = time.time()
    if not _OPENSERP_ID_TOKEN or _OPENSERP_ID_TOKEN_EXPIRES_AT <= now + 60:
        with OPENSERP_TOKEN_LOCK:
            now = time.time()
            if (
                not _OPENSERP_ID_TOKEN
                or _OPENSERP_ID_TOKEN_EXPIRES_AT <= now + 60
            ):
                try:
                    from google.auth.transport.requests import Request as GoogleRequest
                    from google.oauth2 import id_token as google_id_token
                    token = google_id_token.fetch_id_token(
                        GoogleRequest(),
                        audience,
                    )
                except Exception as exc:
                    raise RuntimeError(
                        "Не удалось получить Google OIDC-токен для OpenSERP"
                    ) from exc
                _OPENSERP_ID_TOKEN = token
                # Cloud Run identity tokens are normally valid for one hour.
                # Refresh conservatively rather than parsing unverified JWT data.
                _OPENSERP_ID_TOKEN_EXPIRES_AT = now + 45 * 60
    headers["Authorization"] = "Bearer " + _OPENSERP_ID_TOKEN
    return headers


# =========================================================
# АЛИАСЫ
# =========================================================

ALIASES = {
    # Страны
    "таиланд": "Thailand",
    "южная корея": "South Korea",
    "корея": "Korea",
    "казахстан": "Kazakhstan",
    "кыргызстан": "Kyrgyzstan",
    "узбекистан": "Uzbekistan",
    "китай": "China",
    "япония": "Japan",
    "вьетнам": "Vietnam",
    "индонезия": "Indonesia",
    "индия": "India",
    "иран": "Iran",
    "турция": "Turkey",
    "азербайджан": "Azerbaijan",
    "армения": "Armenia",
    "грузия": "Georgia",
    "россия": "Russia",
    "украина": "Ukraine",
    "англия": "England",
    "испания": "Spain",
    "италия": "Italy",
    "германия": "Germany",
    "франция": "France",
    "португалия": "Portugal",
    "нидерланды": "Netherlands",
    "бельгия": "Belgium",
    "сша": "USA",
    "канада": "Canada",
    "бразилия": "Brazil",
    "аргентина": "Argentina",

    # Клубы / имена
    "қайрат": "Kairat",
    "кайрат": "Kairat",
    "андерлехт": "Anderlecht",

    # Виды спорта
    "дзюдо": "Judo",

    # Турниры
    "лига чемпионов уефа": "UEFA Champions League",
    "лига европы уефа": "UEFA Europa League",
    "лига конференций уефа": "UEFA Conference League",
    "чемпионат азии": "Asian Championship",
    "кубок азии": "Asian Cup",
    "чемпионат мира": "World Championship",
    "кубок мира": "World Cup",
    "английская премьер-лига": "Premier League",
    "бундеслига": "Bundesliga",
    "серия а": "Serie A",
    "лига 1": "Ligue 1",

    # Стадии
    "1/4 финал": "quarterfinal",
    "1/2 финал": "semifinal",
    "полуфинал": "semifinal",
    "финал": "final",
    "плей-офф": "playoff",
    "жеребьёвка": "draw",
    "жеребьевка": "draw",

    # Пол
    "женщины": "Women",
    "мужчины": "Men",
}

KAZAKH_TO_RUSSIAN_CHARS = str.maketrans({
    "Қ": "К",
    "қ": "к",
    "Ғ": "Г",
    "ғ": "г",
    "Ң": "Н",
    "ң": "н",
    "Ө": "О",
    "ө": "о",
    "Ұ": "У",
    "ұ": "у",
    "Ү": "У",
    "ү": "у",
    "І": "И",
    "і": "и",
    "Һ": "Х",
    "һ": "х",
    "Ә": "А",
    "ә": "а",
})


# =========================================================
# ДОВЕРИЕ К ИСТОЧНИКАМ
# =========================================================

OFFICIAL_DOMAINS = {
    "fivb.com",
    "asianvolleyball.net",
    "uefa.com",
    "fifa.com",
    "the-afc.com",
    "atptour.com",
    "wtatennis.com",
    "formula1.com",
    "ufc.com",
    "pflmma.com",
    "nba.com",
    "nhl.com",
}


MAJOR_MEDIA = {
    "espn.com",
    "bbc.com",
    "bbc.co.uk",
    "reuters.com",
    "apnews.com",
    "sports.ru",
    "championat.com",
    "sport24.ru",
    "matchtv.ru",
    "khaosodenglish.com",
    "chosun.com",
    "sbs.co.kr",
    "xinhuanet.com",
}


SPORT_DATABASES = {
    "sofascore.com",
    "flashscore.com",
    "livesport.com",
    "soccerway.com",
    "worldfootball.net",
}


# =========================================================
# ЧАСОВЫЕ ПОЯСА ИСТОЧНИКОВ
# =========================================================

DOMAIN_TIMEZONES = {
    "baidu.com": timezone(timedelta(hours=8)),
    "sohu.com": timezone(timedelta(hours=8)),
    "qq.com": timezone(timedelta(hours=8)),
    "xinhuanet.com": timezone(timedelta(hours=8)),

    "thairath.co.th": timezone(timedelta(hours=7)),
    "thailandedition.com": timezone(timedelta(hours=7)),
    "bangkokpost.com": timezone(timedelta(hours=7)),
    "vietnam.vn": timezone(timedelta(hours=7)),

    "chosun.com": timezone(timedelta(hours=9)),
    "sbs.co.kr": timezone(timedelta(hours=9)),

    "sports.ru": MSK_TIMEZONE,
    "championat.com": MSK_TIMEZONE,
    "sport24.ru": MSK_TIMEZONE,
    "matchtv.ru": MSK_TIMEZONE,
}


# =========================================================
# ОБЩИЕ ФУНКЦИИ
# =========================================================

def normalize(text):
    text = str(text or "").casefold()
    text = text.replace("ё", "е")

    text = re.sub(
        r"[^\w\s]+",
        " ",
        text,
        flags=re.UNICODE,
    )

    return re.sub(
        r"\s+",
        " ",
        text,
    ).strip()


def englishize(text):
    result = str(text or "")

    for source, target in sorted(
        ALIASES.items(),
        key=lambda item: len(item[0]),
        reverse=True,
    ):
        result = re.sub(
            re.escape(source),
            target,
            result,
            flags=re.IGNORECASE,
        )

    return re.sub(
        r"\s+",
        " ",
        result,
    ).strip()


def get_domain(url):
    try:
        domain = urlparse(
            url
        ).netloc.casefold()

        domain = domain.split(":")[0]

        if domain.startswith("www."):
            domain = domain[4:]

        return domain

    except Exception:
        return ""


def get_base_domain(domain):
    if not domain:
        return "unknown"

    special = (
        ".co.uk",
        ".co.kr",
        ".co.th",
        ".com.au",
        ".com.cn",
    )

    for suffix in special:
        if domain.endswith(suffix):
            pieces = domain.split(".")
            suffix_parts = (
                suffix
                .lstrip(".")
                .split(".")
            )

            count = len(
                suffix_parts
            ) + 1

            if len(pieces) >= count:
                return ".".join(
                    pieces[-count:]
                )

    pieces = domain.split(".")

    if len(pieces) >= 2:
        return ".".join(
            pieces[-2:]
        )

    return domain


def get_source_weight(url):
    domain = get_base_domain(
        get_domain(url)
    )

    if domain in OFFICIAL_DOMAINS:
        return 100

    if domain in MAJOR_MEDIA:
        return 70

    if domain in SPORT_DATABASES:
        return 60

    if any(
        marker in domain
        for marker in (
            "bet",
            "odds",
            "prediction",
            "bookmaker",
        )
    ):
        return 20

    return 40


# =========================================================
# OPENSERP
# =========================================================

def openserp_is_running():
    try:
        with socket.create_connection(
            (
                OPENSERP_HOST,
                OPENSERP_PORT,
            ),
            timeout=0.5,
        ):
            return True

    except OSError:
        return False


def _openserp_endpoint(path: str) -> str:
    if OPENSERP_BASE_URL:
        return f"{OPENSERP_BASE_URL}{path}"
    return f"http://{OPENSERP_HOST}:{OPENSERP_PORT}{path}"


def ensure_openserp():
    if OPENSERP_BASE_URL:
        return
    if openserp_is_running():
        return

    with START_LOCK:
        if openserp_is_running():
            return

        if not os.path.exists(
            OPENSERP_BIN
        ):
            raise RuntimeError(
                "OpenSERP не найден"
            )

        subprocess.Popen(
            [
                OPENSERP_BIN,
                "serve",
                "-a",
                OPENSERP_HOST,
                "-p",
                str(OPENSERP_PORT),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

        deadline = (
            time.time()
            + 15
        )

        while time.time() < deadline:
            if openserp_is_running():
                return

            time.sleep(
                0.25
            )

        raise RuntimeError(
            "OpenSERP не запустился"
        )


def openserp_search(
    query,
    engines=None,
    timeout=None,
):
    ensure_openserp()

    selected_engines = (
        tuple(engines)
        if engines
        else PRIMARY_SEARCH_ENGINES
    )

    request_timeout = (
        timeout
        if timeout is not None
        else OPENSERP_PRIMARY_TIMEOUT
    )

    params = {
        "text": query,
        "engines": ",".join(
            selected_engines
        ),
        "mode": "balanced",
        "dedupe": "true",
        "merge": "true",
        "limit": str(
            OPENSERP_RESULT_LIMIT
        ),
    }

    url = (
        _openserp_endpoint("/mega/search")
        + "?"
        + urllib.parse.urlencode(params)
    )

    request = urllib.request.Request(
        url,
        headers=_openserp_headers(),
    )

    with urllib.request.urlopen(
        request,
        timeout=request_timeout,
    ) as response:
        data = json.load(
            response
        )

    results = data.get(
        "results",
        [],
    )

    meta = data.get(
        "meta",
        {},
    )

    if not isinstance(
        results,
        list,
    ):
        results = []

    if not isinstance(
        meta,
        dict,
    ):
        meta = {}

    meta = {
        **meta,
        "slp_engines": list(
            selected_engines
        ),
        "slp_timeout": request_timeout,
    }

    return results, meta


def _post_json(url, payload, timeout):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=_openserp_headers(json_body=True),
        method="POST",
    )

    with urllib.request.urlopen(
        request,
        timeout=timeout,
    ) as response:
        return json.load(response)


def openserp_extract_url(url):
    ensure_openserp()

    endpoint = _openserp_endpoint("/extract")

    data = _post_json(
        endpoint,
        {
            "url": url,
            "mode": "fast",
            "clean": False,
        },
        OPENSERP_EXTRACT_TIMEOUT,
    )

    if not isinstance(data, dict):
        return {
            "page_content": "",
            "metadata": {
                "source": url,
                "error": "invalid_response",
            },
        }

    return data


def openserp_extract_batch(urls):
    ensure_openserp()

    unique_urls = []
    seen = set()

    for url in urls:
        url = str(url or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        unique_urls.append(url)
        if len(unique_urls) >= OPENSERP_EXTRACT_LIMIT:
            break

    if not unique_urls:
        return []

    endpoint = _openserp_endpoint("/extract/batch")

    payload = {
        "urls": unique_urls,
        "mode": "fast",
        "clean": False,
    }

    try:
        data = _post_json(
            endpoint,
            payload,
            OPENSERP_EXTRACT_TIMEOUT,
        )

        if isinstance(data, list):
            return data

    except urllib.error.HTTPError as error:
        if error.code not in (404, 405):
            raise

    # Совместимость со старыми версиями OpenSERP:
    # если batch endpoint отсутствует, используем нативный /extract.
    results = []

    for url in unique_urls:
        try:
            item = openserp_extract_url(url)
        except Exception as error:
            item = {
                "page_content": "",
                "metadata": {
                    "source": url,
                    "error": type(error).__name__,
                },
            }
        results.append(item)

    return results


# =========================================================
# ПОИСКОВЫЕ ЗАПРОСЫ
# =========================================================

def build_queries(event):
    title = (
        event.get("title")
        or event.get(
            "raw_event_title"
        )
        or event.get(
            "raw_title"
        )
        or ""
    )

    tournament = event.get(
        "tournament",
        "",
    )

    sport = event.get(
        "sport",
        "",
    )

    date_value = datetime.strptime(
        event["date"],
        "%Y-%m-%d",
    )

    english_date = (
        date_value.strftime(
            "%B %d %Y"
        )
    )

    original = (
        f"{title} "
        f"{tournament} "
        f"{sport} "
        f"{english_date}"
    )

    translated = englishize(
        original
    )

    time_query = (
        f"{translated} "
        f"start time schedule"
    )

    return [
        translated.strip(),
        time_query.strip(),
    ]


# =========================================================
# ОПРЕДЕЛЕНИЕ УЧАСТНИКОВ
# =========================================================

def clean_event_title(title):
    title = re.sub(
        r"^\s*"
        r"(?:1[\\/]4|1[\\/]2|¼|½)"
        r"\s*финал(?:а)?"
        r"\s*[:.\-–—]?\s*",
        "",
        str(title or ""),
        flags=re.IGNORECASE,
    )

    return title.strip()


def kazakh_to_russian_chars(text):
    return str(text or "").translate(
        KAZAKH_TO_RUSSIAN_CHARS
    )


def extract_participants(title):
    title = clean_event_title(
        title
    )

    parts = re.split(
        r"\s+(?:-|–|—|vs\.?|v\.)\s+",
        title,
        maxsplit=1,
        flags=re.IGNORECASE,
    )

    if len(parts) != 2:
        return []

    result = []

    for part in parts:
        part = re.sub(
            r"\s*\([^)]*\)",
            "",
            part,
        ).strip()

        if part:
            result.append(
                part
            )

    if len(result) == 2:
        return result

    return []


def participant_aliases(name):
    variants = {
        str(name or "").strip(),
        kazakh_to_russian_chars(name),
        englishize(name),
        englishize(
            kazakh_to_russian_chars(name)
        ),
    }

    return {
        normalize(value)
        for value in variants
        if value
    }


def get_event_title_text(event):
    return (
        event.get("title")
        or event.get("raw_event_title")
        or event.get("raw_title")
        or ""
    )


def event_kind(event):
    title = get_event_title_text(event)

    if len(extract_participants(title)) == 2:
        return "MATCH"

    normalized = normalize(
        englishize(title)
    )

    if (
        "жереб" in normalize(title)
        or "draw" in normalized
    ):
        return "DRAW"

    return "GENERIC"


GENERIC_TOURNAMENT_WORDS = {
    "uefa",
    "fifa",
    "league",
    "лига",
    "cup",
    "кубок",
    "championship",
    "чемпионат",
    "tournament",
    "турнир",
    "qualifying",
    "квалификационный",
    "playoff",
    "плей",
    "women",
    "женщины",
    "men",
    "мужчины",
}


def significant_tokens(text):
    normalized = normalize(
        englishize(text)
    )

    return {
        token
        for token in normalized.split()
        if (
            len(token) >= 4
            and token not in GENERIC_TOURNAMENT_WORDS
        )
    }


def tournament_matches_text(event, text):
    tournament = str(
        event.get("tournament")
        or ""
    ).strip()

    if not tournament:
        return False

    normalized_text = normalize(text)
    variants = {
        normalize(tournament),
        normalize(englishize(tournament)),
    }

    if any(
        variant
        and variant in normalized_text
        for variant in variants
    ):
        return True

    tokens = significant_tokens(
        tournament
    )

    if not tokens:
        return False

    return all(
        token in normalized_text
        for token in tokens
    )


# =========================================================
# СОВПАДЕНИЕ СОБЫТИЯ
# =========================================================

def result_text(result):
    return " ".join(
        str(
            result.get(key)
            or ""
        )
        for key in (
            "title",
            "snippet",
            "description",
            "content",
        )
    )


def matches_event(
    result,
    event,
):
    raw_text = result_text(result)
    text = normalize(raw_text)
    title = get_event_title_text(event)
    kind = event_kind(event)

    if kind == "MATCH":
        participants = extract_participants(
            title
        )

        for participant in participants:
            aliases = participant_aliases(
                participant
            )

            if not any(
                alias
                and alias in text
                for alias in aliases
            ):
                return False

        return True

    if kind == "DRAW":
        draw_present = (
            "draw" in text
            or "жереб" in text
        )

        return (
            draw_present
            and tournament_matches_text(
                event,
                raw_text,
            )
        )

    expected = " ".join(
        value
        for value in (
            title,
            event.get("tournament", ""),
            event.get("sport", ""),
        )
        if value
    )

    tokens = significant_tokens(
        expected
    )

    if not tokens:
        tokens = {
            token
            for token in normalize(
                englishize(expected)
            ).split()
            if len(token) >= 4
        }

    matched = sum(
        token in text
        for token in tokens
    )

    required = min(
        2,
        len(tokens),
    )

    return (
        required > 0
        and matched >= required
    )


def select_results_for_extraction(
    results,
    event,
    limit=OPENSERP_EXTRACT_LIMIT,
):
    matching = [
        result
        for result in results
        if matches_event(
            result,
            event,
        )
    ]

    matching.sort(
        key=lambda result: (
            -get_source_weight(
                result.get("url")
                or result.get("link")
                or ""
            ),
            int(result.get("rank") or 9999),
        )
    )

    selected = []
    organizations = set()

    for result in matching:
        url = (
            result.get("url")
            or result.get("link")
            or ""
        )

        if not url:
            continue

        organization = get_base_domain(
            get_domain(url)
        )

        if organization in organizations:
            continue

        organizations.add(
            organization
        )
        selected.append(result)

        if len(selected) >= limit:
            break

    return selected


def merge_extracted_pages(
    selected_results,
    extracted_items,
):
    pages = []

    for index, result in enumerate(
        selected_results
    ):
        item = (
            extracted_items[index]
            if index < len(extracted_items)
            else {}
        )

        if not isinstance(item, dict):
            continue

        content = str(
            item.get("page_content")
            or ""
        ).strip()

        metadata = item.get(
            "metadata"
        )

        if not isinstance(metadata, dict):
            metadata = {}

        if not content:
            continue

        url = (
            metadata.get("source")
            or result.get("url")
            or result.get("link")
            or ""
        )

        pages.append(
            {
                **result,
                "url": url,
                "title": (
                    metadata.get("title")
                    or result.get("title")
                    or ""
                ),
                "content": content,
                "extracted": True,
            }
        )

    return pages


# =========================================================
# ЧАСОВОЙ ПОЯС ИСТОЧНИКА
# =========================================================

def timezone_for_url(url):
    domain = get_domain(
        url
    )

    base = get_base_domain(
        domain
    )

    if domain in DOMAIN_TIMEZONES:
        return DOMAIN_TIMEZONES[
            domain
        ]

    if base in DOMAIN_TIMEZONES:
        return DOMAIN_TIMEZONES[
            base
        ]

    if domain.endswith(".cn"):
        return timezone(
            timedelta(hours=8)
        )

    if domain.endswith(".th"):
        return timezone(
            timedelta(hours=7)
        )

    if domain.endswith(".vn"):
        return timezone(
            timedelta(hours=7)
        )

    if domain.endswith(".kr"):
        return timezone(
            timedelta(hours=9)
        )

    if domain.endswith(".ru"):
        return MSK_TIMEZONE

    if domain.endswith(".kz"):
        return KZ_TIMEZONE

    return None


# =========================================================
# ВРЕМЯ
# =========================================================

def broadcast_datetime(event):
    return datetime.strptime(
        (
            f"{event['date']} "
            f"{event['time']}"
        ),
        "%Y-%m-%d %H:%M",
    ).replace(
        tzinfo=KZ_TIMEZONE
    )


def parse_hour(
    hour,
    minute,
    ampm=None,
):
    try:
        hour = int(hour)
        minute = int(
            minute or 0
        )

    except ValueError:
        return None

    if not (
        0 <= minute <= 59
    ):
        return None

    if ampm:
        ampm = (
            ampm.casefold()
        )

        if not (
            1 <= hour <= 12
        ):
            return None

        if ampm == "am":
            if hour == 12:
                hour = 0

        elif ampm == "pm":
            if hour != 12:
                hour += 12

    if not (
        0 <= hour <= 23
    ):
        return None

    return hour, minute


def source_datetime_for_event_date(
    event,
    hour,
    minute,
    source_timezone,
):
    event_date = datetime.strptime(
        event["date"],
        "%Y-%m-%d",
    ).date()

    candidates = []

    for shift in (
        -1,
        0,
        1,
    ):
        source_date = (
            event_date
            + timedelta(days=shift)
        )

        source_dt = datetime(
            source_date.year,
            source_date.month,
            source_date.day,
            hour,
            minute,
            tzinfo=source_timezone,
        )

        kz_dt = source_dt.astimezone(
            KZ_TIMEZONE
        )

        date_distance = abs(
            (
                kz_dt.date()
                - event_date
            ).days
        )

        candidates.append(
            (
                date_distance,
                abs(shift),
                kz_dt,
            )
        )

    return min(
        candidates,
        key=lambda item: (
            item[0],
            item[1],
        ),
    )[2]


def event_anchor_terms(event):
    title = get_event_title_text(event)
    kind = event_kind(event)
    terms = set()

    if kind == "MATCH":
        for participant in extract_participants(
            title
        ):
            terms.update(
                {
                    participant,
                    kazakh_to_russian_chars(
                        participant
                    ),
                    englishize(participant),
                    englishize(
                        kazakh_to_russian_chars(
                            participant
                        )
                    ),
                }
            )

    elif kind == "DRAW":
        tournament = str(
            event.get("tournament")
            or ""
        )
        terms.update(
            {
                tournament,
                englishize(tournament),
                "draw",
                "жереб",
            }
        )

    else:
        for value in (
            title,
            event.get("tournament", ""),
        ):
            if value:
                terms.add(str(value))
                terms.add(
                    englishize(value)
                )

    return {
        term.strip()
        for term in terms
        if term and len(term.strip()) >= 4
    }


def context_windows(
    text,
    event,
    radius=500,
):
    text = str(text or "")

    if not text:
        return []

    folded = text.casefold()
    windows = []
    seen = set()

    for term in event_anchor_terms(
        event
    ):
        term_folded = term.casefold()
        start_at = 0

        while True:
            position = folded.find(
                term_folded,
                start_at,
            )

            if position == -1:
                break

            left = max(
                0,
                position - radius,
            )
            right = min(
                len(text),
                position + len(term) + radius,
            )

            key = (
                left // 100,
                right // 100,
            )

            if key not in seen:
                seen.add(key)
                windows.append(
                    text[left:right]
                )

            start_at = (
                position
                + len(term_folded)
            )

    if windows:
        return windows

    # Если страница уже прошла matches_event, но точная фраза
    # не нашлась, ограничиваемся началом страницы вместо
    # бесконтрольного разбора всего документа.
    return [text[:6000]]


STRONG_TIME_CUE_PATTERN = re.compile(
    r"(?:kick[\s-]?off|start(?:s|ing)?|begins?|"
    r"начал[оа]?|старт|начн[её]тся)",
    flags=re.IGNORECASE,
)

WEAK_TIME_CUE_PATTERN = re.compile(
    r"(?:scheduled|schedule|time|время|эфир)",
    flags=re.IGNORECASE,
)


def candidate_context_score(
    window,
    match_start,
    match_end,
    explicit,
):
    score = 100 if explicit else 0

    def nearest_bonus(pattern, maximum, radius):
        distances = []

        for cue in pattern.finditer(window):
            distance = min(
                abs(match_start - cue.end()),
                abs(match_end - cue.start()),
            )

            if distance <= radius:
                distances.append(distance)

        if not distances:
            return 0

        return max(
            0,
            maximum - min(distances),
        )

    strong_bonus = nearest_bonus(
        STRONG_TIME_CUE_PATTERN,
        180,
        120,
    )

    weak_bonus = nearest_bonus(
        WEAK_TIME_CUE_PATTERN,
        60,
        80,
    )

    score += max(
        strong_bonus,
        weak_bonus,
    )

    return score


def extract_times(
    result,
    event,
):
    text = result_text(
        result
    )

    url = (
        result.get("url")
        or result.get("link")
        or ""
    )

    local_timezone = timezone_for_url(
        url
    )

    candidates = []

    utc_pattern = re.compile(
        r"(?<!\d)"
        r"(\d{1,2})"
        r":(\d{2})"
        r"(?::\d{2})?"
        r"\s*(am|pm)?"
        r"\s*(?:UTC|GMT)"
        r"(?!\w)",
        flags=re.IGNORECASE,
    )

    msk_pattern = re.compile(
        r"(?<!\d)"
        r"(\d{1,2})"
        r":(\d{2})"
        r"\s*(?:MSK|МСК|Moscow time)",
        flags=re.IGNORECASE,
    )

    local_patterns = [
        re.compile(
            r"(?<![\d:])"
            r"(\d{1,2})"
            r":(\d{2})"
            r"(?::\d{2})?"
            r"\s*(am|pm)?",
            flags=re.IGNORECASE,
        ),
        re.compile(
            r"(?<![\d:])"
            r"(\d{1,2})"
            r"\s*(am|pm)"
            r"(?!\w)",
            flags=re.IGNORECASE,
        ),
    ]

    for window in context_windows(
        text,
        event,
    ):
        for match in utc_pattern.finditer(
            window
        ):
            parsed = parse_hour(
                match.group(1),
                match.group(2),
                match.group(3),
            )

            if not parsed:
                continue

            hour, minute = parsed
            kz_dt = source_datetime_for_event_date(
                event,
                hour,
                minute,
                timezone.utc,
            )

            candidates.append(
                {
                    "dt_kz": kz_dt,
                    "original_time": (
                        f"{hour:02d}:"
                        f"{minute:02d} UTC"
                    ),
                    "explicit": True,
                    "context_score": candidate_context_score(
                        window,
                        match.start(),
                        match.end(),
                        True,
                    ),
                }
            )

        for match in msk_pattern.finditer(
            window
        ):
            parsed = parse_hour(
                match.group(1),
                match.group(2),
            )

            if not parsed:
                continue

            hour, minute = parsed
            kz_dt = source_datetime_for_event_date(
                event,
                hour,
                minute,
                MSK_TIMEZONE,
            )

            candidates.append(
                {
                    "dt_kz": kz_dt,
                    "original_time": (
                        f"{hour:02d}:"
                        f"{minute:02d} MSK"
                    ),
                    "explicit": True,
                    "context_score": candidate_context_score(
                        window,
                        match.start(),
                        match.end(),
                        True,
                    ),
                }
            )

        if local_timezone:
            for pattern in local_patterns:
                for match in pattern.finditer(
                    window
                ):
                    # Не дублируем время, уже помеченное UTC/GMT/MSK.
                    suffix = window[
                        match.end():
                        min(
                            len(window),
                            match.end() + 15,
                        )
                    ]

                    if re.match(
                        r"\s*(?:UTC|GMT|MSK|МСК|Moscow time)",
                        suffix,
                        flags=re.IGNORECASE,
                    ):
                        continue

                    if len(
                        match.groups()
                    ) == 3:
                        parsed = parse_hour(
                            match.group(1),
                            match.group(2),
                            match.group(3),
                        )
                    else:
                        parsed = parse_hour(
                            match.group(1),
                            0,
                            match.group(2),
                        )

                    if not parsed:
                        continue

                    hour, minute = parsed
                    kz_dt = source_datetime_for_event_date(
                        event,
                        hour,
                        minute,
                        local_timezone,
                    )

                    candidates.append(
                        {
                            "dt_kz": kz_dt,
                            "original_time": (
                                f"{hour:02d}:"
                                f"{minute:02d} local"
                            ),
                            "explicit": False,
                            "context_score": candidate_context_score(
                                window,
                                match.start(),
                                match.end(),
                                False,
                            ),
                        }
                    )

    unique = {}

    for candidate in candidates:
        key = (
            candidate["dt_kz"],
            candidate["original_time"],
        )

        current = unique.get(key)

        if (
            current is None
            or candidate["context_score"]
            > current["context_score"]
        ):
            unique[key] = candidate

    return list(
        unique.values()
    )


def best_time_for_result(
    result,
    event,
):
    candidates = extract_times(
        result,
        event,
    )

    if not candidates:
        return None

    # Ключевой принцип: выбор времени не зависит от времени Qazsport.
    # Сначала выбираем время из контекста самой страницы, и только
    # после этого сравниваем его с эфирным расписанием.
    return max(
        candidates,
        key=lambda item: (
            item["explicit"],
            item["context_score"],
        ),
    )


# =========================================================
# ДЕДУПЛИКАЦИЯ
# =========================================================

def dedupe_results(results):
    unique = []
    seen = set()

    for result in results:
        url = str(
            result.get("url")
            or result.get("link")
            or ""
        )

        title = str(
            result.get("title")
            or ""
        )

        key = (
            url.casefold(),
            title.casefold(),
        )

        if key in seen:
            continue

        seen.add(
            key
        )

        unique.append(
            result
        )

    return unique


# =========================================================
# КАНДИДАТЫ
# =========================================================

def collect_candidates(
    results,
    event,
):
    candidates = []
    matching_count = 0

    for result in results:
        if not matches_event(
            result,
            event,
        ):
            continue

        matching_count += 1

        time_data = (
            best_time_for_result(
                result,
                event,
            )
        )

        if not time_data:
            continue

        url = (
            result.get("url")
            or result.get("link")
            or ""
        )

        domain = get_domain(
            url
        )

        time_data.update(
            {
                "url": url,
                "source_name": (
                    domain
                    or "Неизвестный источник"
                ),
                "organization": (
                    get_base_domain(
                        domain
                    )
                ),
                "weight": (
                    get_source_weight(
                        url
                    )
                ),
                "engine": (
                    result.get(
                        "engine",
                        "",
                    )
                ),
            }
        )

        candidates.append(
            time_data
        )

    return (
        candidates,
        matching_count,
    )


# =========================================================
# КОНСЕНСУС
# =========================================================

def create_clusters(
    candidates,
):
    clusters = []

    for candidate in sorted(
        candidates,
        key=lambda item: (
            item["dt_kz"]
        ),
    ):
        added = False

        for cluster in clusters:
            reference = (
                cluster[0][
                    "dt_kz"
                ]
            )

            difference = abs(
                (
                    candidate["dt_kz"]
                    - reference
                ).total_seconds()
            ) / 60

            if difference <= 10:
                cluster.append(
                    candidate
                )
                added = True
                break

        if not added:
            clusters.append(
                [candidate]
            )

    return clusters


def cluster_score(cluster):
    best_sources = {}

    for candidate in cluster:
        organization = candidate[
            "organization"
        ]

        current = (
            best_sources.get(
                organization
            )
        )

        if (
            current is None
            or candidate["weight"]
            > current["weight"]
        ):
            best_sources[
                organization
            ] = candidate

    total_weight = sum(
        candidate["weight"]
        for candidate
        in best_sources.values()
    )

    return (
        total_weight,
        len(best_sources),
        len(cluster),
    )


def choose_cluster(
    candidates,
):
    clusters = create_clusters(
        candidates
    )

    if not clusters:
        return []

    return max(
        clusters,
        key=cluster_score,
    )


def unique_sources(cluster):
    organizations = {}

    for candidate in cluster:
        organization = candidate[
            "organization"
        ]

        current = (
            organizations.get(
                organization
            )
        )

        if (
            current is None
            or candidate["weight"]
            > current["weight"]
        ):
            organizations[
                organization
            ] = candidate

    result = []

    for candidate in sorted(
        organizations.values(),
        key=lambda item: (
            -item["weight"]
        ),
    ):
        kz_datetime = (
            candidate["dt_kz"]
        )

        msk_datetime = (
            kz_datetime.astimezone(
                MSK_TIMEZONE
            )
        )

        result.append(
            {
                "source_name": (
                    candidate[
                        "source_name"
                    ]
                ),
                "url": (
                    candidate["url"]
                ),
                "engine": (
                    candidate[
                        "engine"
                    ]
                ),

                # Совместимость с текущим main.py
                "time": (
                    msk_datetime.strftime(
                        "%H:%M"
                    )
                ),

                "display_time": (
                    candidate[
                        "original_time"
                    ]
                ),

                "external_time_kz": (
                    kz_datetime.strftime(
                        "%H:%M"
                    )
                ),

                "external_date_kz": (
                    kz_datetime.strftime(
                        "%Y-%m-%d"
                    )
                ),

                "adjusted_weight": (
                    candidate[
                        "weight"
                    ]
                ),
            }
        )

    return result


# =========================================================
# ОЦЕНКА УВЕРЕННОСТИ
# =========================================================

def confidence_result(
    source_count,
    total_weight,
):
    if (
        source_count >= 2
        and total_weight >= 120
    ):
        return {
            "label": "Высокая",
            "reason": (
                "Время подтверждается "
                "несколькими независимыми "
                "источниками."
            ),
        }

    if (
        source_count >= 2
        or total_weight >= 70
    ):
        return {
            "label": "Средняя",
            "reason": (
                "Время найдено, но "
                "независимых подтверждений "
                "пока недостаточно для "
                "высокой уверенности."
            ),
        }

    return {
        "label": "Низкая",
        "reason": (
            "Время найдено только "
            "в ограниченном числе "
            "источников."
        ),
    }


# =========================================================
# СРАВНЕНИЕ С ЭФИРОМ
# =========================================================

MAX_ACCEPTABLE_EXTERNAL_DIFF_MINUTES = 30


def is_plausible_external_time_difference(difference_minutes):
    return abs(difference_minutes) <= MAX_ACCEPTABLE_EXTERNAL_DIFF_MINUTES


def time_status(
    difference_minutes,
):
    difference = abs(
        difference_minutes
    )

    if difference <= 10:
        return {
            "label": "Время совпадает",
            "description": (
                "Расхождение 0–10 минут. "
                "Это нормально для ТВ: "
                "возможен предэфир "
                "или студия."
            ),
        }

    if difference <= 20:
        return {
            "label": (
                "Событие требуется проверить"
            ),
            "description": (
                "Расхождение 11–20 минут."
            ),
        }

    return {
        "label": (
            "Есть расхождение по времени"
        ),
        "description": (
            "Расхождение больше "
            "20 минут."
        ),
    }


# =========================================================
# ГЛАВНАЯ ФУНКЦИЯ
# =========================================================

def verify_event(event):
    queries = build_queries(
        event
    )

    all_results = []
    search_meta = []
    search_errors = []

    # Основной быстрый поиск: Bing + DuckDuckGo.
    try:
        results, meta = openserp_search(
            queries[0],
            engines=PRIMARY_SEARCH_ENGINES,
            timeout=OPENSERP_PRIMARY_TIMEOUT,
        )
        all_results.extend(results)
        search_meta.append(
            {
                **meta,
                "slp_tier": "primary",
            }
        )
    except Exception as error:
        search_errors.append(f"primary:{type(error).__name__}:{error}")
        print(
            "OpenSERP search error:",
            repr(error),
        )

    all_results = dedupe_results(
        all_results
    )

    snippet_candidates, matching_count = (
        collect_candidates(
            all_results,
            event,
        )
    )

    # Baidu только как резерв, когда основные движки
    # вообще не нашли релевантных страниц события.
    if (
        not snippet_candidates
        and matching_count == 0
    ):
        try:
            results, meta = openserp_search(
                queries[1],
                engines=FALLBACK_SEARCH_ENGINES,
                timeout=OPENSERP_FALLBACK_TIMEOUT,
            )
            all_results.extend(results)
            search_meta.append(
                {
                    **meta,
                    "slp_tier": "fallback",
                }
            )
        except Exception as error:
            search_errors.append(f"fallback:{type(error).__name__}:{error}")
            print(
                "OpenSERP fallback error:",
                repr(error),
            )

        all_results = dedupe_results(
            all_results
        )

        snippet_candidates, matching_count = (
            collect_candidates(
                all_results,
                event,
            )
        )

    selected_results = select_results_for_extraction(
        all_results,
        event,
    )

    extracted_pages = []
    extraction_error = None

    if selected_results:
        try:
            extracted_items = openserp_extract_batch(
                [
                    result.get("url")
                    or result.get("link")
                    or ""
                    for result in selected_results
                ]
            )

            extracted_pages = merge_extracted_pages(
                selected_results,
                extracted_items,
            )

        except Exception as error:
            extraction_error = repr(error)
            print(
                "OpenSERP extract error:",
                extraction_error,
            )

    page_candidates = []
    page_matching_count = 0

    if extracted_pages:
        page_candidates, page_matching_count = (
            collect_candidates(
                extracted_pages,
                event,
            )
        )

    # Извлечённые страницы имеют приоритет.
    # Сниппет остаётся только fallback при ошибке extraction.
    if page_candidates:
        candidates = page_candidates
        verification_method = "page_extract"
    else:
        candidates = snippet_candidates
        verification_method = (
            "search_snippet"
            if snippet_candidates
            else "none"
        )

    base_result = {
        "queries": queries,
        "event_kind": event_kind(event),
        "search_results_count": len(all_results),
        "matching_results_count": matching_count,
        "search_meta": search_meta,
        "search_errors": search_errors,
        "extraction_selected_count": len(
            selected_results
        ),
        "extraction_success_count": len(
            extracted_pages
        ),
        "extraction_matching_count": (
            page_matching_count
        ),
        "verification_method": (
            verification_method
        ),
    }

    if extraction_error:
        base_result[
            "extraction_error"
        ] = extraction_error

    if not candidates:
        unavailable = bool(search_errors and not all_results)
        return {
            **base_result,
            "found": False,
            "verification_unavailable": unavailable,
            "message": (
                "Сервис внешней проверки сейчас недоступен."
                if unavailable
                else (
                    "Событие найдено, но надёжно определить время не удалось."
                    if matching_count
                    else "Подходящих результатов по событию не найдено."
                )
            ),
            "source_count": 0,
            "total_weight": 0,
            "sources": [],
        }

    cluster = choose_cluster(
        candidates
    )

    sources = unique_sources(
        cluster
    )

    representative = max(
        cluster,
        key=lambda item: (
            item["weight"],
            item.get(
                "context_score",
                0,
            ),
        ),
    )

    external_datetime = representative[
        "dt_kz"
    ]

    # Только здесь, после независимого выбора внешнего времени,
    # сравниваем его с эфирным расписанием источника.
    broadcast = broadcast_datetime(
        event
    )

    difference_minutes = int(
        (
            external_datetime
            - broadcast
        ).total_seconds()
        / 60
    )

    # Внешнее время с расхождением больше 30 минут
    # считаем нерелевантным кандидатом. Оно не должно
    # участвовать в доверии и не показывается пользователю
    # как подтверждение времени конкретного эфира.
    if not is_plausible_external_time_difference(
        difference_minutes
    ):
        return {
            **base_result,
            "found": False,
            "time_rejected": True,
            "rejected_external_date_kz": (
                external_datetime.strftime("%Y-%m-%d")
            ),
            "rejected_external_time_kz": (
                external_datetime.strftime("%H:%M")
            ),
            "rejected_difference_minutes": difference_minutes,
            "message": (
                "Событие найдено, но найденное внешнее время "
                "отброшено из-за расхождения более 30 минут."
            ),
            "source_count": 0,
            "total_weight": 0,
            "sources": [],
        }

    source_count = len(
        sources
    )

    total_weight = sum(
        source[
            "adjusted_weight"
        ]
        for source in sources
    )

    return {
        **base_result,
        "found": True,
        "external_date_kz": (
            external_datetime.strftime(
                "%Y-%m-%d"
            )
        ),
        "external_time_kz": (
            external_datetime.strftime(
                "%H:%M"
            )
        ),
        "difference_minutes": (
            difference_minutes
        ),
        "time_status": time_status(
            difference_minutes
        ),
        "source_count": source_count,
        "total_weight": total_weight,
        "confidence": confidence_result(
            source_count,
            total_weight,
        ),
        "sources": sources,
    }


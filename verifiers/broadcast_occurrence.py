from __future__ import annotations

import json
import logging
import re
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from services.time_logic import KZ_TIMEZONE
from verifiers.web_search import (
    OPENSERP_BASE_URL,
    build_queries,
    dedupe_results,
    englishize,
    get_base_domain,
    get_domain,
    get_source_weight,
    matches_event,
    normalize,
    openserp_extract_batch,
    openserp_search,
    result_text,
)

logger = logging.getLogger(__name__)
FAST_TIMEOUT_SECONDS = 25
MAX_RESULTS = 12
MAX_EXTRACT_RESULTS = 3
MAX_CONFIRM_DIFF_MINUTES = 30

SEARCH_ENGINE_GROUPS = (
    ("yandex", "baidu", "ecosia"),
    ("google", "bing", "duckduckgo"),
)

OFFICIAL_EVENT_DOMAINS = {
    "ufc.com", "pflmma.com", "formula1.com", "fia.com", "fiawec.com", "wrc.com",
    "motogp.com", "khl.ru", "nhl.com", "nba.com", "fiba.basketball", "ijf.org",
    "uefa.com", "fifa.com", "the-afc.com", "premierleague.com", "laliga.com",
    "bundesliga.com", "legaseriea.it", "ligue1.com", "kff.kz", "kffleague.kz",
    "atptour.com", "wtatennis.com", "itftennis.com", "wst.tv", "olympics.com",
    "worldathletics.org", "worldaquatics.com", "fivb.com", "asianvolleyball.net",
}

MONTHS = {
    "january": 1, "jan": 1, "января": 1, "январь": 1,
    "february": 2, "feb": 2, "февраля": 2, "февраль": 2,
    "march": 3, "mar": 3, "марта": 3, "март": 3,
    "april": 4, "apr": 4, "апреля": 4, "апрель": 4,
    "may": 5, "мая": 5, "май": 5,
    "june": 6, "jun": 6, "июня": 6, "июнь": 6,
    "july": 7, "jul": 7, "июля": 7, "июль": 7,
    "august": 8, "aug": 8, "августа": 8, "август": 8,
    "september": 9, "sep": 9, "sept": 9, "сентября": 9, "сентябрь": 9,
    "october": 10, "oct": 10, "октября": 10, "октябрь": 10,
    "november": 11, "nov": 11, "ноября": 11, "ноябрь": 11,
    "december": 12, "dec": 12, "декабря": 12, "декабрь": 12,
}
MONTH_TOKEN = "|".join(sorted((re.escape(k) for k in MONTHS), key=len, reverse=True))
DATE_PATTERNS = (
    re.compile(r"(?<!\d)(20\d{2})-(\d{1,2})-(\d{1,2})(?!\d)"),
    re.compile(r"(?<!\d)(\d{1,2})[./](\d{1,2})[./](20\d{2})(?!\d)"),
    re.compile(rf"\b({MONTH_TOKEN})\s+(\d{{1,2}})(?:st|nd|rd|th)?[,]?\s+(20\d{{2}})\b", re.I),
    re.compile(rf"\b(\d{{1,2}})\s+({MONTH_TOKEN})[,]?\s+(20\d{{2}})\b", re.I),
)
TZ_PATTERNS = (
    (re.compile(r"(?<!\d)(\d{1,2}):(\d{2})\s*(am|pm)?\s*(?:UTC|GMT)\b", re.I), timezone.utc, True),
    (re.compile(r"(?<!\d)(\d{1,2}):(\d{2})\s*(am|pm)?\s*(?:MSK|МСК)\b", re.I), ZoneInfo("Europe/Moscow"), True),
    (re.compile(r"(?<!\d)(\d{1,2})(?::(\d{2}))?\s*(am|pm)\s*(?:EDT|ET)\b", re.I), ZoneInfo("America/New_York"), True),
    (re.compile(r"(?<!\d)(\d{1,2})(?::(\d{2}))?\s*(am|pm)\s*EST\b", re.I), timezone(timedelta(hours=-5)), True),
    (re.compile(r"(?<!\d)(\d{1,2})(?::(\d{2}))?\s*(am|pm)\s*(?:PDT|PT)\b", re.I), ZoneInfo("America/Los_Angeles"), True),
    (re.compile(r"(?<!\d)(\d{1,2})(?::(\d{2}))?\s*(am|pm)\s*PST\b", re.I), timezone(timedelta(hours=-8)), True),
    (re.compile(r"(?<!\d)(\d{1,2}):(\d{2})\s*CEST\b", re.I), timezone(timedelta(hours=2)), True),
    (re.compile(r"(?<!\d)(\d{1,2}):(\d{2})\s*CET\b", re.I), timezone(timedelta(hours=1)), True),
)
DOMAIN_TIMEZONES = {
    "ufc.com": ZoneInfo("America/New_York"),
    "khl.ru": ZoneInfo("Europe/Moscow"),
    "sports.ru": ZoneInfo("Europe/Moscow"),
    "championat.com": ZoneInfo("Europe/Moscow"),
    "sport24.ru": ZoneInfo("Europe/Moscow"),
    "matchtv.ru": ZoneInfo("Europe/Moscow"),
    "sports.kz": KZ_TIMEZONE,
    "qazsporttv.kz": KZ_TIMEZONE,
}
LOCAL_TIME_PATTERN = re.compile(r"(?<![\d:])(\d{1,2})(?::(\d{2}))?\s*(am|pm)?(?!\w)", re.I)
STRONG_CUE = re.compile(r"(?:kick[\s-]?off|start(?:s|ing)?|begins?|main card|prelims|начал[оа]?|старт|начн[её]тся|время|эфир)", re.I)
STOP_TOKENS = {
    "match", "матч", "final", "финал", "round", "тур", "season", "сезон",
    "championship", "чемпионат", "league", "лига", "cup", "кубок", "live",
    "main", "card", "grand", "prix", "sport", "sports",
}


def _parse_hour(hour: str, minute: str | None, ampm: str | None) -> tuple[int, int] | None:
    h = int(hour)
    m = int(minute or 0)
    if not 0 <= m <= 59:
        return None
    if ampm:
        marker = ampm.casefold()
        if not 1 <= h <= 12:
            return None
        if marker == "am" and h == 12:
            h = 0
        elif marker == "pm" and h != 12:
            h += 12
    return (h, m) if 0 <= h <= 23 else None


def extract_explicit_dates(text: str) -> list[tuple[date, int]]:
    value = str(text or "")
    found: list[tuple[date, int]] = []
    for index, pattern in enumerate(DATE_PATTERNS):
        for match in pattern.finditer(value):
            try:
                if index == 0:
                    year, month, day = map(int, match.groups())
                elif index == 1:
                    day, month, year = map(int, match.groups())
                elif index == 2:
                    month_name, day_text, year_text = match.groups()
                    year = int(year_text)
                    month = MONTHS[month_name.casefold()]
                    day = int(day_text)
                else:
                    day_text, month_name, year_text = match.groups()
                    year = int(year_text)
                    month = MONTHS[month_name.casefold()]
                    day = int(day_text)
                found.append((date(year, month, day), match.start()))
            except (ValueError, KeyError):
                continue
    unique: dict[date, int] = {}
    for parsed, position in found:
        unique.setdefault(parsed, position)
    return [(parsed, unique[parsed]) for parsed in sorted(unique)]


def _nearest_date(dates: list[tuple[date, int]], position: int) -> date | None:
    return min(dates, key=lambda item: abs(item[1] - position))[0] if dates else None


def _domain_timezone(url: str):
    domain = get_base_domain(get_domain(url))
    if domain in DOMAIN_TIMEZONES:
        return DOMAIN_TIMEZONES[domain]
    if domain.endswith(".kz"):
        return KZ_TIMEZONE
    if domain.endswith(".ru"):
        return ZoneInfo("Europe/Moscow")
    return None


def _source_weight(url: str) -> int:
    domain = get_base_domain(get_domain(url))
    if domain in OFFICIAL_EVENT_DOMAINS:
        return max(100, get_source_weight(url))
    return get_source_weight(url)


def _context_score(text: str, start: int, end: int, explicit_tz: bool) -> int:
    score = 100 if explicit_tz else 0
    if STRONG_CUE.search(text[max(0, start - 100):min(len(text), end + 100)]):
        score += 80
    return score


def _relaxed_match(result: dict, event: dict) -> bool:
    if matches_event(result, event):
        return True
    haystack = normalize(englishize(result_text(result)))
    tournament = str(event.get("tournament") or "").strip()
    if tournament:
        needle = normalize(englishize(tournament))
        if len(needle) >= 5 and needle in haystack:
            return True
    expected = " ".join(
        str(event.get(key) or "") for key in ("title", "raw_title", "tournament", "sport")
    )
    tokens = [
        token for token in normalize(englishize(expected)).split()
        if len(token) >= 5 and token not in STOP_TOKENS and not token.isdigit()
    ]
    unique = list(dict.fromkeys(tokens))
    matched = sum(token in haystack for token in unique)
    return matched >= min(2, len(unique)) if unique else False


def _dated_candidates(result: dict) -> tuple[list[dict], list[date]]:
    text = result_text(result)
    url = str(result.get("url") or result.get("link") or "")
    dates_with_pos = extract_explicit_dates(text)
    explicit_dates = [item[0] for item in dates_with_pos]
    candidates: list[dict] = []

    def add(match, source_tz, explicit_tz):
        groups = match.groups()
        parsed = _parse_hour(
            groups[0],
            groups[1] if len(groups) > 1 else None,
            groups[2] if len(groups) > 2 else None,
        )
        source_date = _nearest_date(dates_with_pos, match.start())
        if not parsed or source_date is None:
            return
        hour, minute = parsed
        dt = datetime(
            source_date.year,
            source_date.month,
            source_date.day,
            hour,
            minute,
            tzinfo=source_tz,
        ).astimezone(KZ_TIMEZONE)
        candidates.append({
            "dt_kz": dt,
            "url": url,
            "weight": _source_weight(url),
            "organization": get_base_domain(get_domain(url)),
            "source_name": get_domain(url) or "Источник",
            "context_score": _context_score(text, match.start(), match.end(), explicit_tz),
        })

    for pattern, source_tz, explicit_tz in TZ_PATTERNS:
        for match in pattern.finditer(text):
            add(match, source_tz, explicit_tz)

    local_tz = _domain_timezone(url)
    if local_tz and dates_with_pos:
        for match in LOCAL_TIME_PATTERN.finditer(text):
            suffix = text[match.end():match.end() + 12]
            if re.match(r"\s*(?:UTC|GMT|MSK|МСК|EDT|EST|ET|PDT|PST|PT|CET|CEST)\b", suffix, re.I):
                continue
            add(match, local_tz, False)

    return candidates, explicit_dates


def _openserp_search_any(query: str, engines: tuple[str, ...], timeout: int):
    """Use OpenSERP mode=any in Railway so one healthy engine can return early.

    Local/CI runs keep using the existing helper, which makes tests independent
    of a running OpenSERP service.
    """
    if not OPENSERP_BASE_URL:
        return openserp_search(query, engines=engines, timeout=timeout)

    params = {
        "text": query,
        "engines": ",".join(engines),
        "mode": "any",
        "dedupe": "true",
        "merge": "true",
        "limit": str(MAX_RESULTS),
    }
    url = f"{OPENSERP_BASE_URL}/mega/search?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.load(response)

    results = data.get("results", []) if isinstance(data, dict) else []
    meta = data.get("meta", {}) if isinstance(data, dict) else {}
    if not isinstance(results, list):
        results = []
    if not isinstance(meta, dict):
        meta = {}
    return results, {
        **meta,
        "slp_engines": list(engines),
        "slp_timeout": timeout,
        "slp_mode": "any",
    }


def _search_resilient(event: dict) -> tuple[list[dict], list[dict], list[str]]:
    all_results: list[dict] = []
    meta: list[dict] = []
    errors: list[str] = []

    for query_index, query in enumerate(build_queries(event)):
        got_results = False
        for engines in SEARCH_ENGINE_GROUPS:
            try:
                results, search_meta = _openserp_search_any(
                    query,
                    engines=engines,
                    timeout=FAST_TIMEOUT_SECONDS,
                )
                meta.append({
                    **search_meta,
                    "query_index": query_index,
                    "engine_group": list(engines),
                })
                if results:
                    all_results.extend(results)
                    got_results = True
                    break
            except Exception as error:
                errors.append(
                    f"{','.join(engines)}:{type(error).__name__}:{error}"
                )

        all_results = dedupe_results(all_results)[:MAX_RESULTS]
        if any(_relaxed_match(result, event) for result in all_results):
            break
        if not got_results:
            continue

    return all_results, meta, errors


def _extract_matching_pages(matching: list[dict]) -> list[dict]:
    selected: list[dict] = []
    seen_domains: set[str] = set()
    for result in sorted(
        matching,
        key=lambda item: (
            -_source_weight(str(item.get("url") or item.get("link") or "")),
            int(item.get("rank") or 9999),
        ),
    ):
        url = str(result.get("url") or result.get("link") or "")
        if not url:
            continue
        domain = get_base_domain(get_domain(url))
        if domain in seen_domains:
            continue
        seen_domains.add(domain)
        selected.append(result)
        if len(selected) >= MAX_EXTRACT_RESULTS:
            break

    if not selected:
        return []

    try:
        extracted = openserp_extract_batch([
            str(item.get("url") or item.get("link") or "") for item in selected
        ])
    except Exception:
        return []

    pages: list[dict] = []
    for result, item in zip(selected, extracted):
        if not isinstance(item, dict):
            continue
        content = str(item.get("page_content") or "").strip()
        if not content:
            continue
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        url = str(metadata.get("source") or result.get("url") or result.get("link") or "")
        pages.append({
            **result,
            "url": url,
            "title": str(metadata.get("title") or result.get("title") or ""),
            "content": content,
            "extracted": True,
        })
    return pages


def verify_broadcast_occurrence(event: dict) -> dict:
    results, search_meta, search_errors = _search_resilient(event)
    matching = [result for result in results if _relaxed_match(result, event)]
    broadcast = datetime.strptime(
        f"{event['date']} {event['time']}", "%Y-%m-%d %H:%M"
    ).replace(tzinfo=KZ_TIMEZONE)

    candidates: list[dict] = []
    seen_dates: set[date] = set()
    for result in matching:
        extracted, dates = _dated_candidates(result)
        candidates.extend(extracted)
        seen_dates.update(dates)

    if not candidates and matching:
        for page in _extract_matching_pages(matching):
            if not _relaxed_match(page, event):
                continue
            extracted, dates = _dated_candidates(page)
            candidates.extend(extracted)
            seen_dates.update(dates)

    plausible: list[dict] = []
    for candidate in candidates:
        difference = int((candidate["dt_kz"] - broadcast).total_seconds() / 60)
        enriched = {**candidate, "difference_minutes": difference}
        if abs(difference) <= MAX_CONFIRM_DIFF_MINUTES:
            plausible.append(enriched)

    if plausible:
        by_org: dict[str, dict] = {}
        for candidate in sorted(
            plausible,
            key=lambda item: (-item["weight"], -item["context_score"]),
        ):
            by_org.setdefault(candidate["organization"], candidate)
        sources = list(by_org.values())
        total_weight = sum(item["weight"] for item in sources)
        best = max(sources, key=lambda item: (item["weight"], item["context_score"]))
        if any(item["weight"] >= 100 for item in sources) or (
            len(sources) >= 2 and total_weight >= 100
        ):
            logger.info(
                "occurrence confirmed channel=%s scheduled=%s %s external=%s diff=%d source=%s",
                event.get("channel"), event.get("date"), event.get("time"),
                best["dt_kz"].isoformat(), best["difference_minutes"], best["source_name"],
            )
            return {
                "state": "confirmed_direct",
                "difference_minutes": best["difference_minutes"],
                "external_time_kz": best["dt_kz"].isoformat(),
                "sources": [
                    {
                        "source_name": item["source_name"],
                        "url": item["url"],
                        "difference_minutes": item["difference_minutes"],
                    }
                    for item in sources[:5]
                ],
                "search_meta": search_meta,
                "search_errors": search_errors,
            }

    if candidates:
        closest = min(
            candidates,
            key=lambda item: abs((item["dt_kz"] - broadcast).total_seconds()),
        )
        difference = int((closest["dt_kz"] - broadcast).total_seconds() / 60)
        logger.info(
            "occurrence mismatch channel=%s scheduled=%s %s external=%s diff=%d source=%s",
            event.get("channel"), event.get("date"), event.get("time"),
            closest["dt_kz"].isoformat(), difference, closest["source_name"],
        )
        return {
            "state": "mismatch",
            "difference_minutes": difference,
            "external_time_kz": closest["dt_kz"].isoformat(),
            "sources": [{
                "source_name": closest["source_name"],
                "url": closest["url"],
                "difference_minutes": difference,
            }],
            "search_meta": search_meta,
            "search_errors": search_errors,
        }

    if seen_dates and all(abs((found - broadcast.date()).days) > 1 for found in seen_dates):
        return {
            "state": "mismatch",
            "reason": "external_event_date_mismatch",
            "external_dates": sorted(value.isoformat() for value in seen_dates),
            "sources": [],
            "search_meta": search_meta,
            "search_errors": search_errors,
        }

    unavailable = bool(search_errors and not results)
    return {
        "state": "unavailable" if unavailable else "unknown",
        "matching_results_count": len(matching),
        "search_results_count": len(results),
        "sources": [],
        "search_meta": search_meta,
        "search_errors": search_errors,
    }

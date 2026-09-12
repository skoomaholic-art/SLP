from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from services.time_logic import KZ_TIMEZONE
from verifiers.web_search import (
    PRIMARY_SEARCH_ENGINES,
    build_queries,
    dedupe_results,
    get_base_domain,
    get_domain,
    get_source_weight,
    matches_event,
    openserp_search,
    result_text,
)

FAST_TIMEOUT_SECONDS = 12
MAX_RESULTS = 10
MAX_CONFIRM_DIFF_MINUTES = 30

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
    (re.compile(r"(?<!\d)(\d{1,2}):(\d{2})\s*(?:CEST)\b", re.I), timezone(timedelta(hours=2)), True),
    (re.compile(r"(?<!\d)(\d{1,2}):(\d{2})\s*(?:CET)\b", re.I), timezone(timedelta(hours=1)), True),
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
    if not 0 <= h <= 23:
        return None
    return h, m


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
                    year = int(year_text); month = MONTHS[month_name.casefold()]; day = int(day_text)
                else:
                    day_text, month_name, year_text = match.groups()
                    year = int(year_text); month = MONTHS[month_name.casefold()]; day = int(day_text)
                found.append((date(year, month, day), match.start()))
            except (ValueError, KeyError):
                continue
    unique: dict[date, int] = {}
    for parsed, position in found:
        unique.setdefault(parsed, position)
    return [(key, unique[key]) for key in sorted(unique)]


def _nearest_date(dates: list[tuple[date, int]], position: int) -> date | None:
    if not dates:
        return None
    parsed, _ = min(dates, key=lambda item: abs(item[1] - position))
    return parsed


def _domain_timezone(url: str):
    domain = get_base_domain(get_domain(url))
    if domain in DOMAIN_TIMEZONES:
        return DOMAIN_TIMEZONES[domain]
    if domain.endswith(".kz"):
        return KZ_TIMEZONE
    if domain.endswith(".ru"):
        return ZoneInfo("Europe/Moscow")
    return None


def _context_score(text: str, start: int, end: int, *, explicit_tz: bool) -> int:
    left = max(0, start - 100)
    right = min(len(text), end + 100)
    score = 100 if explicit_tz else 0
    if STRONG_CUE.search(text[left:right]):
        score += 80
    return score


def _dated_candidates(result: dict, event: dict) -> tuple[list[dict], list[date]]:
    text = result_text(result)
    url = str(result.get("url") or result.get("link") or "")
    dates_with_pos = extract_explicit_dates(text)
    explicit_dates = [item[0] for item in dates_with_pos]
    candidates: list[dict] = []

    for pattern, source_tz, explicit_tz in TZ_PATTERNS:
        for match in pattern.finditer(text):
            parsed = _parse_hour(match.group(1), match.group(2), match.group(3) if len(match.groups()) >= 3 else None)
            if not parsed:
                continue
            source_date = _nearest_date(dates_with_pos, match.start())
            if source_date is None:
                continue
            hour, minute = parsed
            source_dt = datetime(source_date.year, source_date.month, source_date.day, hour, minute, tzinfo=source_tz)
            candidates.append({
                "dt_kz": source_dt.astimezone(KZ_TIMEZONE),
                "url": url,
                "weight": get_source_weight(url),
                "organization": get_base_domain(get_domain(url)),
                "source_name": get_domain(url) or "Источник",
                "context_score": _context_score(text, match.start(), match.end(), explicit_tz=explicit_tz),
            })

    local_tz = _domain_timezone(url)
    if local_tz and dates_with_pos:
        for match in LOCAL_TIME_PATTERN.finditer(text):
            suffix = text[match.end():match.end() + 12]
            if re.match(r"\s*(?:UTC|GMT|MSK|МСК|EDT|EST|ET|PDT|PST|PT|CET|CEST)\b", suffix, re.I):
                continue
            parsed = _parse_hour(match.group(1), match.group(2), match.group(3))
            if not parsed:
                continue
            source_date = _nearest_date(dates_with_pos, match.start())
            if source_date is None:
                continue
            hour, minute = parsed
            source_dt = datetime(source_date.year, source_date.month, source_date.day, hour, minute, tzinfo=local_tz)
            candidates.append({
                "dt_kz": source_dt.astimezone(KZ_TIMEZONE),
                "url": url,
                "weight": get_source_weight(url),
                "organization": get_base_domain(get_domain(url)),
                "source_name": get_domain(url) or "Источник",
                "context_score": _context_score(text, match.start(), match.end(), explicit_tz=False),
            })

    return candidates, explicit_dates


def verify_broadcast_occurrence(event: dict) -> dict:
    query = build_queries(event)[0]
    try:
        results, meta = openserp_search(query, engines=PRIMARY_SEARCH_ENGINES, timeout=FAST_TIMEOUT_SECONDS)
    except Exception as error:
        return {"state": "unavailable", "error": f"{type(error).__name__}: {error}", "sources": []}

    results = dedupe_results(results)[:MAX_RESULTS]
    matching = [result for result in results if matches_event(result, event)]
    broadcast = datetime.strptime(
        f"{event['date']} {event['time']}", "%Y-%m-%d %H:%M"
    ).replace(tzinfo=KZ_TIMEZONE)

    candidates: list[dict] = []
    seen_dates: set[date] = set()
    for result in matching:
        extracted, dates = _dated_candidates(result, event)
        candidates.extend(extracted)
        seen_dates.update(dates)

    plausible = []
    for candidate in candidates:
        difference = int((candidate["dt_kz"] - broadcast).total_seconds() / 60)
        candidate = {**candidate, "difference_minutes": difference}
        if abs(difference) <= MAX_CONFIRM_DIFF_MINUTES:
            plausible.append(candidate)

    if plausible:
        by_org: dict[str, dict] = {}
        for candidate in sorted(plausible, key=lambda item: (-item["weight"], -item["context_score"])):
            by_org.setdefault(candidate["organization"], candidate)
        sources = list(by_org.values())
        total_weight = sum(item["weight"] for item in sources)
        best = max(sources, key=lambda item: (item["weight"], item["context_score"]))
        official = any(item["weight"] >= 100 for item in sources)
        confirmed = official or (len(sources) >= 2 and total_weight >= 100)
        if confirmed:
            return {
                "state": "confirmed_direct",
                "difference_minutes": best["difference_minutes"],
                "external_time_kz": best["dt_kz"].isoformat(),
                "sources": [
                    {"source_name": item["source_name"], "url": item["url"], "difference_minutes": item["difference_minutes"]}
                    for item in sources[:5]
                ],
                "search_meta": meta,
            }

    if candidates:
        closest = min(candidates, key=lambda item: abs((item["dt_kz"] - broadcast).total_seconds()))
        difference = int((closest["dt_kz"] - broadcast).total_seconds() / 60)
        return {
            "state": "mismatch",
            "difference_minutes": difference,
            "external_time_kz": closest["dt_kz"].isoformat(),
            "sources": [{"source_name": closest["source_name"], "url": closest["url"], "difference_minutes": difference}],
        }

    event_date = broadcast.date()
    if seen_dates and all(abs((found - event_date).days) > 1 for found in seen_dates):
        return {
            "state": "mismatch",
            "reason": "external_event_date_mismatch",
            "external_dates": sorted(value.isoformat() for value in seen_dates),
            "sources": [],
        }

    return {
        "state": "unknown",
        "matching_results_count": len(matching),
        "search_results_count": len(results),
        "sources": [],
    }

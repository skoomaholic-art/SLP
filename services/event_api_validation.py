"""Cross-check the assembled SLP schedule against public sports APIs.

The validator never changes the schedule itself. It may only return a proposed
editorial patch that a human editor can inspect and apply from Notifications.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
import asyncio
import os

import aiohttp

from services.event_text_ru import to_russian_text
from services.schedule_merge import normalize_match_text
from services.time_logic import KZ_TIMEZONE

THESPORTSDB_KEY = os.getenv("THESPORTSDB_API_KEY", "3").strip() or "3"
TIMEOUT_SECONDS = 12

# Leagues and cups carried by the SLP channel line-up. The list follows the
# ESPN public scoreboard slugs used by the sports-api project; every slug was
# checked against the live endpoint (Saudi Pro League is "ksa.1", not "sau.1").
ESPN_FEEDS = (
    ("soccer", "eng.1"),
    ("soccer", "esp.1"),
    ("soccer", "ita.1"),
    ("soccer", "ger.1"),
    ("soccer", "fra.1"),
    ("soccer", "por.1"),
    ("soccer", "ned.1"),
    ("soccer", "sco.1"),
    ("soccer", "usa.1"),
    ("soccer", "ksa.1"),
    ("soccer", "eng.fa"),
    ("soccer", "eng.league_cup"),
    ("soccer", "esp.copa_del_rey"),
    ("soccer", "ita.coppa_italia"),
    ("soccer", "ger.dfb_pokal"),
    ("soccer", "fra.coupe_de_france"),
    ("soccer", "uefa.champions"),
    ("soccer", "uefa.europa"),
    ("soccer", "uefa.europa.conf"),
    ("soccer", "uefa.super_cup"),
    ("soccer", "uefa.nations"),
    ("soccer", "fifa.worldq.uefa"),
    ("soccer", "fifa.cwc"),
    ("basketball", "nba"),
    ("hockey", "nhl"),
    ("racing", "f1"),
)

# Which ESPN sport a Russian SLP sport label belongs to. A feed is requested
# only for days that actually carry a candidate of that sport.
ESPN_SPORT_MARKERS = {
    "soccer": ("футбол",),
    "basketball": ("баскетбол",),
    "hockey": ("хоккей",),
    "racing": ("формула", "автоспорт", "мотоспорт", "гонк"),
}
ESPN_MAX_DAYS = 8
ESPN_CONCURRENCY = 8

CANCELLED_MARKERS = ("cancelled", "canceled", "отмен")
POSTPONED_MARKERS = ("postponed", "suspended", "перенес", "перенос", "отлож")


def _parse_iso(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def _candidate_text(event: dict) -> str:
    return normalize_match_text(to_russian_text(
        str(event.get("title") or event.get("raw_title") or "")
    ))


def _external_text(home: str, away: str) -> str:
    return normalize_match_text(to_russian_text(f"{home} - {away}"))


def _identity_score(candidate: dict, external: dict) -> float:
    left = _candidate_text(candidate)
    right = _external_text(external.get("home", ""), external.get("away", ""))
    if not left or not right:
        return 0.0
    home = normalize_match_text(to_russian_text(str(external.get("home") or "")))
    away = normalize_match_text(to_russian_text(str(external.get("away") or "")))
    if home and away and home in left and away in left:
        return 1.0
    if home and away and away in left and home in left:
        return 0.98
    return SequenceMatcher(None, left, right).ratio()


def _match_score(candidate: dict, external: dict) -> float:
    identity = _identity_score(candidate, external)
    if identity <= 0:
        return 0.0
    start = _parse_iso(str(candidate.get("start_at") or ""))
    ext_start = _parse_iso(str(external.get("start_at") or ""))
    if start and ext_start:
        if start.tzinfo is None:
            start = start.replace(tzinfo=KZ_TIMEZONE)
        if ext_start.tzinfo is None:
            ext_start = ext_start.replace(tzinfo=KZ_TIMEZONE)
        difference = abs((start - ext_start).total_seconds()) / 60
        if difference > 30:
            return 0.0
    return identity


def _candidate_storage_ids(candidate: dict) -> list[str]:
    values: list[str] = []
    direct = str(candidate.get("source_record_id") or "")
    if direct:
        values.append(direct)
    for item in candidate.get("broadcasts") or []:
        storage_id = str(item.get("source_record_id") or "")
        if storage_id and storage_id not in values:
            values.append(storage_id)
    return values


def _candidate_storage_id(candidate: dict) -> str:
    values = _candidate_storage_ids(candidate)
    return values[0] if values else ""


def _provider_label(provider: str) -> str:
    return {
        "thesportsdb": "TheSportsDB",
        "espn_public": "ESPN",
    }.get(provider, provider)


def _status_text(event: dict) -> str:
    return " ".join(
        str(event.get(key) or "")
        for key in ("status", "status_detail")
    ).strip()


def _has_marker(text: str, markers: tuple[str, ...]) -> bool:
    lowered = str(text or "").casefold()
    return any(marker in lowered for marker in markers)


async def _get_json(session: aiohttp.ClientSession, url: str, params=None):
    try:
        async with session.get(url, params=params, allow_redirects=True) as response:
            if response.status != 200:
                return None
            return await response.json(content_type=None)
    except Exception:
        return None


async def _thesportsdb_events(session, dates: list[str]) -> list[dict]:
    result = []
    base = f"https://www.thesportsdb.com/api/v1/json/{THESPORTSDB_KEY}/eventsday.php"
    for day in dates[:9]:
        data = await _get_json(session, base, {"d": day})
        for raw in (data or {}).get("events") or []:
            event_id = str(raw.get("idEvent") or "")
            start = raw.get("strTimestamp")
            if not start and raw.get("dateEvent"):
                start = f"{raw.get('dateEvent')}T{raw.get('strTime') or '00:00:00'}+00:00"
            result.append({
                "provider": "thesportsdb",
                "id": event_id,
                "home": raw.get("strHomeTeam") or "",
                "away": raw.get("strAwayTeam") or "",
                "start_at": start or "",
                "status": raw.get("strStatus") or "",
                "status_detail": raw.get("strPostponed") or "",
                "league": raw.get("strLeague") or "",
                "source_url": (
                    f"https://www.thesportsdb.com/event/{event_id}"
                    if event_id else ""
                ),
            })
    return result


def _espn_sport_for(label: str) -> str:
    lowered = str(label or "").casefold()
    for sport, markers in ESPN_SPORT_MARKERS.items():
        if any(marker in lowered for marker in markers):
            return sport
    return "other"


def _espn_sports_by_date(candidates: list[dict]) -> dict[str, set[str]]:
    """Days and sports to request, including the previous calendar day.

    ESPN files a fixture under its local (US) date, so a match that starts
    after midnight in Almaty is listed one day earlier there.
    """
    result: dict[str, set[str]] = {}
    for event in candidates:
        try:
            day = date.fromisoformat(str(event.get("date") or ""))
        except ValueError:
            continue
        label = str(event.get("sport") or "").strip()
        sport = _espn_sport_for(label) if label else ""
        for target in (day - timedelta(days=1), day):
            result.setdefault(target.isoformat(), set()).add(sport)
    return result


def _espn_requests(
    dates: list[str], sports_by_date: dict[str, set[str]] | None,
) -> list[tuple[str, str, str]]:
    """(day, sport, league) triples worth asking ESPN about.

    ``sports_by_date`` maps a day to the ESPN sports present in the SLP
    schedule that day; an empty string in the set means "sport unknown, ask
    every feed". Without the mapping every feed is requested for every day.
    """
    requests = []
    for day in dates[:ESPN_MAX_DAYS]:
        wanted = None if sports_by_date is None else sports_by_date.get(day, set())
        for sport, league in ESPN_FEEDS:
            if wanted is None or sport in wanted or "" in wanted:
                requests.append((day, sport, league))
    return requests


async def _espn_events(
    session, dates: list[str],
    sports_by_date: dict[str, set[str]] | None = None,
) -> list[dict]:
    limiter = asyncio.Semaphore(ESPN_CONCURRENCY)

    async def fetch(day: str, sport: str, league: str) -> list[dict]:
        compact = day.replace("-", "")
        url = f"https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/scoreboard"
        async with limiter:
            data = await _get_json(session, url, {"dates": compact})
        result = []
        for raw in (data or {}).get("events") or []:
            competitions = raw.get("competitions") or [{}]
            competitors = (competitions[0] or {}).get("competitors") or []
            home = next(
                (item for item in competitors if item.get("homeAway") == "home"),
                competitors[0] if competitors else {},
            )
            away = next(
                (item for item in competitors if item.get("homeAway") == "away"),
                competitors[1] if len(competitors) > 1 else {},
            )
            status = (raw.get("status") or {}).get("type") or {}
            links = raw.get("links") or []
            source_url = next(
                (
                    str(link.get("href") or "")
                    for link in links
                    if str(link.get("href") or "").startswith("http")
                ),
                "",
            )
            if not source_url:
                source_url = f"{url}?dates={compact}"
            result.append({
                "provider": "espn_public",
                "id": str(raw.get("id") or ""),
                "home": (home.get("team") or {}).get("displayName") or "",
                "away": (away.get("team") or {}).get("displayName") or "",
                "start_at": raw.get("date") or "",
                "status": status.get("state") or "",
                "status_detail": " ".join(
                    str(status.get(key) or "")
                    for key in ("name", "detail", "shortDetail")
                ).strip(),
                "league": league,
                "source_url": source_url,
            })
        return result

    batches = await asyncio.gather(*(
        fetch(day, sport, league)
        for day, sport, league in _espn_requests(dates, sports_by_date)
    ))
    seen: set[tuple[str, str]] = set()
    result = []
    for batch in batches:
        for item in batch:
            identity = (item["league"], item["id"])
            if item["id"] and identity in seen:
                continue
            seen.add(identity)
            result.append(item)
    return result


def _expanded_dates(values: list[str]) -> list[str]:
    result: set[str] = set()
    for value in values:
        try:
            day = date.fromisoformat(value)
        except ValueError:
            continue
        for offset in (-1, 0, 1):
            result.add((day + timedelta(days=offset)).isoformat())
    return sorted(result)


def _build_discrepancy(candidate: dict, external: dict, identity_score: float) -> dict | None:
    storage_ids = _candidate_storage_ids(candidate)
    storage_id = storage_ids[0] if storage_ids else ""
    if not storage_id:
        return None

    current = _parse_iso(str(candidate.get("start_at") or ""))
    external_start = _parse_iso(str(external.get("start_at") or ""))
    if current and current.tzinfo is None:
        current = current.replace(tzinfo=KZ_TIMEZONE)
    if external_start and external_start.tzinfo is None:
        external_start = external_start.replace(tzinfo=KZ_TIMEZONE)

    status_text = _status_text(external)
    cancelled = _has_marker(status_text, CANCELLED_MARKERS)
    postponed = _has_marker(status_text, POSTPONED_MARKERS)
    source_name = _provider_label(str(external.get("provider") or ""))
    source_url = str(external.get("source_url") or "")
    patch: dict[str, str] = {}
    kind = ""
    level = "attention"
    message = ""

    if cancelled:
        kind = "event_cancelled"
        level = "critical"
        patch = {"cancelled": "true"}
        message = (
            f"{source_name} отмечает событие как отменённое. "
            "В SLP оно пока остаётся в расписании."
        )
    elif current and external_start:
        external_kz = external_start.astimezone(KZ_TIMEZONE)
        difference = int((external_kz - current.astimezone(KZ_TIMEZONE)).total_seconds() / 60)
        if abs(difference) <= 10:
            return None
        if abs(difference) > 36 * 60:
            return None
        patch = {
            "date": external_kz.date().isoformat(),
            "time": external_kz.strftime("%H:%M"),
        }
        kind = "event_postponed" if postponed else "time_mismatch"
        old_value = current.astimezone(KZ_TIMEZONE).strftime("%d.%m %H:%M")
        new_value = external_kz.strftime("%d.%m %H:%M")
        if postponed:
            message = (
                f"{source_name} показывает перенос события: "
                f"{old_value} → {new_value}."
            )
        else:
            message = (
                f"В SLP стоит {old_value}, а {source_name} показывает "
                f"{new_value}. Расхождение {abs(difference)} мин."
            )
    elif postponed:
        kind = "event_postponed"
        message = (
            f"{source_name} отмечает событие как перенесённое, "
            "но новое время пока не удалось подтвердить."
        )
    else:
        return None

    return {
        "kind": kind,
        "level": level,
        "title": str(candidate.get("title") or "Событие"),
        "channel": str(candidate.get("channel") or ""),
        "date": str(candidate.get("date") or ""),
        "time": str(candidate.get("time") or ""),
        "storage_id": storage_id,
        "source_name": source_name,
        "source_url": source_url,
        "external_id": str(external.get("id") or ""),
        "external_status": status_text,
        "patch": patch,
        "message": message,
        "evidence": {
            "provider": external.get("provider"),
            "identity_score": round(identity_score, 3),
            "external_id": external.get("id"),
            "external_league": external.get("league"),
            "external_start_at": external.get("start_at"),
            "external_status": status_text,
            "storage_ids": storage_ids,
        },
    }


async def validate_events_with_public_apis(events: list[dict]) -> dict:
    candidates = [
        event for event in events
        if event.get("date") and event.get("title")
    ][:200]
    dates = sorted({str(event["date"]) for event in candidates})
    if not candidates or not dates:
        return {
            "checked": 0,
            "confirmed": 0,
            "providers": {},
            "matches": [],
            "discrepancies": [],
        }

    expanded = _expanded_dates(dates)
    timeout = aiohttp.ClientTimeout(total=TIMEOUT_SECONDS)
    headers = {"User-Agent": "Mozilla/5.0 SLP/2.0", "Accept": "application/json"}
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        thesportsdb = await _thesportsdb_events(session, expanded)
        sports_by_date = _espn_sports_by_date(candidates)
        espn = await _espn_events(
            session, sorted(sports_by_date), sports_by_date
        )

    external = thesportsdb + espn
    matches = []
    discrepancies = []

    for candidate in candidates:
        scored = sorted(
            (
                (_identity_score(candidate, item), item)
                for item in external
            ),
            key=lambda pair: pair[0],
            reverse=True,
        )
        best_score, best = scored[0] if scored else (0.0, None)
        if best is None or best_score < 0.86:
            continue

        conservative = _match_score(candidate, best)
        if conservative >= 0.78:
            matches.append({
                "title": candidate.get("title"),
                "channel": candidate.get("channel"),
                "date": candidate.get("date"),
                "time": candidate.get("time"),
                "provider": best["provider"],
                "external_id": best["id"],
                "score": round(conservative, 3),
                "external_status": _status_text(best),
            })

        discrepancy = _build_discrepancy(candidate, best, best_score)
        if discrepancy:
            discrepancies.append(discrepancy)

    return {
        "checked": len(candidates),
        "confirmed": len(matches),
        "providers": {
            "thesportsdb": len(thesportsdb),
            "espn_public": len(espn),
        },
        "matches": matches[:100],
        "discrepancies": discrepancies[:100],
    }

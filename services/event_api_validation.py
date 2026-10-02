"""Best-effort event validation using public sports APIs.

These providers never create, delete or retime SLP broadcasts. They only
cross-check already confirmed EPG candidates and return diagnostics.
"""
from __future__ import annotations

from datetime import datetime
from difflib import SequenceMatcher
import os

import aiohttp

from services.event_text_ru import to_russian_text
from services.schedule_merge import normalize_match_text
from services.time_logic import KZ_TIMEZONE

THESPORTSDB_KEY = os.getenv("THESPORTSDB_API_KEY", "3").strip() or "3"
TIMEOUT_SECONDS = 12

ESPN_FEEDS = (
    ("soccer", "eng.1"),
    ("soccer", "uefa.champions"),
    ("soccer", "uefa.europa"),
    ("soccer", "ger.1"),
    ("soccer", "esp.1"),
    ("soccer", "ita.1"),
    ("soccer", "fra.1"),
    ("basketball", "nba"),
    ("hockey", "nhl"),
    ("racing", "f1"),
)


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


def _match_score(candidate: dict, external: dict) -> float:
    left = _candidate_text(candidate)
    right = _external_text(external.get("home", ""), external.get("away", ""))
    if not left or not right:
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

    home = normalize_match_text(to_russian_text(str(external.get("home") or "")))
    away = normalize_match_text(to_russian_text(str(external.get("away") or "")))
    if home and away and home in left and away in left:
        return 1.0
    return SequenceMatcher(None, left, right).ratio()


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
    for day in dates[:7]:
        data = await _get_json(session, base, {"d": day})
        for raw in (data or {}).get("events") or []:
            start = raw.get("strTimestamp")
            if not start and raw.get("dateEvent"):
                start = f"{raw.get('dateEvent')}T{raw.get('strTime') or '00:00:00'}+00:00"
            result.append({
                "provider": "thesportsdb",
                "id": str(raw.get("idEvent") or ""),
                "home": raw.get("strHomeTeam") or "",
                "away": raw.get("strAwayTeam") or "",
                "start_at": start or "",
                "status": raw.get("strStatus") or "",
                "league": raw.get("strLeague") or "",
            })
    return result


async def _espn_events(session, dates: list[str]) -> list[dict]:
    result = []
    # Keep request volume bounded: ESPN validates today/nearest schedule days,
    # while TheSportsDB provides the wider daily cross-check.
    for day in dates[:2]:
        compact = day.replace("-", "")
        for sport, league in ESPN_FEEDS:
            url = f"https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/scoreboard"
            data = await _get_json(session, url, {"dates": compact})
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
                result.append({
                    "provider": "espn_public",
                    "id": str(raw.get("id") or ""),
                    "home": (home.get("team") or {}).get("displayName") or "",
                    "away": (away.get("team") or {}).get("displayName") or "",
                    "start_at": raw.get("date") or "",
                    "status": ((raw.get("status") or {}).get("type") or {}).get("state") or "",
                    "league": league,
                })
    return result


async def validate_events_with_public_apis(events: list[dict]) -> dict:
    candidates = [
        event for event in events
        if event.get("date") and event.get("title")
    ][:200]
    dates = sorted({str(event["date"]) for event in candidates})
    if not candidates or not dates:
        return {"checked": 0, "confirmed": 0, "providers": {}, "matches": []}

    timeout = aiohttp.ClientTimeout(total=TIMEOUT_SECONDS)
    headers = {"User-Agent": "Mozilla/5.0 SLP/2.0", "Accept": "application/json"}
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        thesportsdb = await _thesportsdb_events(session, dates)
        espn = await _espn_events(session, dates)

    external = thesportsdb + espn
    matches = []
    for candidate in candidates:
        best = None
        best_score = 0.0
        for item in external:
            score = _match_score(candidate, item)
            if score > best_score:
                best_score = score
                best = item
        if best and best_score >= 0.78:
            matches.append({
                "title": candidate.get("title"),
                "channel": candidate.get("channel"),
                "date": candidate.get("date"),
                "time": candidate.get("time"),
                "provider": best["provider"],
                "external_id": best["id"],
                "score": round(best_score, 3),
                "external_status": best.get("status", ""),
            })

    return {
        "checked": len(candidates),
        "confirmed": len(matches),
        "providers": {
            "thesportsdb": len(thesportsdb),
            "espn_public": len(espn),
        },
        "matches": matches[:100],
    }

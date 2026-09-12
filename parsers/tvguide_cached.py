from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import date, datetime, timedelta

from parsers.tvplus import fetch_tvplus_schedules
from services.time_logic import KZ_TIMEZONE

logger = logging.getLogger(__name__)
SOURCE = "tvguide"
LOOKAHEAD_DAYS = 7
CACHE_TTL_SECONDS = 180.0

_REPLAY_MARKERS = (
    "повтор", "replay", "rerun", "re-run", "архив", "archive", "catch-up",
    "обзор", "highlights", "лучшее", "итоги", "дневник", "журнал", "классика",
    "документальный", "новости", "студия", "ток-шоу", "программа",
)
_EVENT_MARKERS = (
    "чемпионат", "лига", "кубок", "турнир", "гран-при", "grand prix", "ufc",
    "khl", "кхл", "финал", "полуфинал", "четвертьфинал", "1/4", "1/2",
    "теннис", "снукер", "футбол", "хоккей", "баскетбол", "волейбол", "бокс",
    "mma", "мма", "мотоспорт", "формула-1", "formula 1", "гонк", "дзюдо",
    "борьб", "биатлон", "атлетик", "гимнастик",
)
_SPORT_HINTS = (
    ("футбол", "Футбол"), ("хоккей", "Хоккей"), ("кхл", "Хоккей"),
    ("теннис", "Теннис"), ("снукер", "Снукер"), ("баскетбол", "Баскетбол"),
    ("волейбол", "Волейбол"), ("ufc", "MMA"), ("mma", "MMA"), ("мма", "MMA"),
    ("бокс", "Бокс"), ("мотоспорт", "Мотоспорт"), ("формула-1", "Автоспорт"),
    ("дзюдо", "Дзюдо"), ("борьб", "Борьба"),
)
_MATCH_RE = re.compile(r"\s(?:-|–|—|vs\.?|v\.)\s", re.I)
_YEAR_RE = re.compile(r"(?<!\d)(20\d{2})(?!\d)")

_cache_lock = asyncio.Lock()
_cache_anchor: date | None = None
_cache_expires_at = 0.0
_cache_by_date: dict[date, list[dict]] = {}


def _text(event: dict) -> str:
    return " ".join(str(event.get(key) or "") for key in ("raw_title", "title", "tournament")).casefold()


def _infer_sport(event: dict) -> None:
    if str(event.get("sport") or "").strip():
        return
    text = _text(event)
    for marker, sport in _SPORT_HINTS:
        if marker in text:
            event["sport"] = sport
            return


def infer_direct_event(event: dict, *, target_date: date, seen: set[tuple[str, str]]) -> dict:
    item = dict(event)
    provider_source = str(item.get("source") or "")
    item["provider_source"] = provider_source
    item["source"] = SOURCE
    _infer_sport(item)

    if bool(item.get("is_live")):
        item["live_evidence_method"] = item.get("live_evidence_method") or "provider_live_text"
        return item

    text = _text(item)
    identity = (str(item.get("channel") or ""), " ".join(text.split()))
    years = {int(value) for value in _YEAR_RE.findall(text)}
    replay = any(marker in text for marker in _REPLAY_MARKERS)
    stale_year = bool(years and target_date.year not in years)
    event_like = bool(_MATCH_RE.search(text) or any(marker in text for marker in _EVENT_MARKERS))
    repeated = identity in seen
    seen.add(identity)

    likely_direct = event_like and not replay and not stale_year and not repeated
    item["is_live"] = likely_direct
    item["live_state"] = "live" if likely_direct else "not_live"
    item["live_evidence_method"] = "provider_scheduled_sport_event" if likely_direct else "provider_epg_only"
    item["live_evidence_value"] = "TV+/Mobikino EPG"
    item["live_evidence_confidence"] = "medium" if likely_direct else "low"
    return item


def _normalize_by_date(raw_by_date: dict[date, list[dict]]) -> dict[date, list[dict]]:
    seen: set[tuple[str, str]] = set()
    result: dict[date, list[dict]] = {}
    for target_date in sorted(raw_by_date):
        events = sorted(raw_by_date[target_date], key=lambda e: (str(e.get("time") or ""), str(e.get("channel") or "")))
        result[target_date] = [infer_direct_event(event, target_date=target_date, seen=seen) for event in events]
    return result


async def _refresh_cache(anchor: date) -> None:
    global _cache_anchor, _cache_by_date, _cache_expires_at
    dates = [anchor + timedelta(days=offset) for offset in range(-1, LOOKAHEAD_DAYS + 1)]
    loaded = await fetch_tvplus_schedules(dates)
    total = sum(len(items) for items in loaded.by_date.values())
    if not total:
        raise RuntimeError("TV+/Mobikino EPG returned no events")
    if loaded.errors:
        logger.warning("tvguide partial errors=%s", loaded.errors[:20])
    _cache_by_date = _normalize_by_date(loaded.by_date)
    _cache_anchor = anchor
    _cache_expires_at = time.monotonic() + CACHE_TTL_SECONDS
    direct = sum(1 for events in _cache_by_date.values() for event in events if event.get("is_live"))
    logger.info("tvguide cache refreshed events=%d direct_candidates=%d dates=%d errors=%d", total, direct, len(dates), len(loaded.errors))


async def get_tvguide_schedule(target_date: date | datetime | str | None = None) -> list[dict]:
    if target_date is None:
        requested = datetime.now(KZ_TIMEZONE).date()
    elif isinstance(target_date, datetime):
        requested = target_date.astimezone(KZ_TIMEZONE).date() if target_date.tzinfo else target_date.date()
    elif isinstance(target_date, date):
        requested = target_date
    else:
        requested = datetime.strptime(str(target_date), "%Y-%m-%d").date()

    anchor = datetime.now(KZ_TIMEZONE).date()
    async with _cache_lock:
        if _cache_anchor != anchor or time.monotonic() >= _cache_expires_at or requested not in _cache_by_date:
            await _refresh_cache(anchor)
        return [dict(event) for event in _cache_by_date.get(requested, [])]

from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import date, datetime, timedelta, timezone

from parsers.tvplus import fetch_tvplus_schedules
from services.time_logic import KZ_TIMEZONE

logger = logging.getLogger(__name__)
SOURCE = "tvguide"
LOOKAHEAD_DAYS = 7
CACHE_TTL_SECONDS = 180.0

_REPLAY_MARKERS = (
    "повтор", "replay", "rerun", "re-run", "архив", "archive", "catch-up",
    "обзор", "review", "highlights", "лучшее", "итоги", "дневник", "журнал",
    "классика", "classic", "превью", "preview", "документальный", "новости",
    "студия", "ток-шоу", "программа", "sport review",
)
_EVENT_MARKERS = (
    "чемпионат", "лига", "кубок", "турнир", "гран-при", "grand prix", "ufc",
    "khl", "кхл", "финал", "полуфинал", "четвертьфинал", "1/4", "1/2",
    "теннис", "снукер", "футбол", "хоккей", "баскетбол", "волейбол", "бокс",
    "mma", "мма", "мотоспорт", "формула-1", "formula 1", "гонк", "дзюдо",
    "борьб", "биатлон", "атлетик", "гимнастик", "wrc", "wec",
)
_SPORT_HINTS = (
    ("футбол", "Футбол"), ("хоккей", "Хоккей"), ("кхл", "Хоккей"),
    ("khl", "Хоккей"), ("теннис", "Теннис"), ("снукер", "Снукер"),
    ("баскетбол", "Баскетбол"), ("волейбол", "Волейбол"), ("ufc", "MMA"),
    ("mma", "MMA"), ("мма", "MMA"), ("бокс", "Бокс"),
    ("мотоспорт", "Мотоспорт"), ("формула-1", "Автоспорт"),
    ("formula 1", "Автоспорт"), ("гран-при", "Автоспорт"),
    ("grand prix", "Автоспорт"), ("wrc", "Автоспорт"), ("wec", "Автоспорт"),
    ("дзюдо", "Дзюдо"), ("борьб", "Борьба"),
)
_MATCH_RE = re.compile(r"\s(?:-|–|—|vs\.?|v\.)\s", re.I)
_YEAR_RE = re.compile(r"(?<!\d)(20\d{2})(?!\d)")

_cache_lock = asyncio.Lock()
_cache_anchor: date | None = None
_cache_expires_at = 0.0
_cache_by_date: dict[date, list[dict]] = {}


def _text(event: dict) -> str:
    return " ".join(
        str(event.get(key) or "")
        for key in ("raw_title", "title", "tournament")
    ).casefold()


def _infer_sport(event: dict) -> None:
    if str(event.get("sport") or "").strip():
        return
    text = _text(event)
    for marker, sport in _SPORT_HINTS:
        if marker in text:
            event["sport"] = sport
            return


def _infer_tournament_and_title(event: dict) -> None:
    if str(event.get("tournament") or "").strip():
        return
    title = " ".join(str(event.get("title") or "").split())
    if ". " not in title:
        return
    parts = [part.strip(" .,-–—") for part in title.split(". ") if part.strip()]
    if len(parts) < 2:
        return
    tail = parts[-1]
    if not _MATCH_RE.search(tail):
        return
    event["tournament"] = ". ".join(parts[:-1]).strip(" .,-–—")
    event["title"] = tail


def _utc_clock_to_kz(date_text: str, time_text: str) -> datetime:
    source_dt = datetime.strptime(
        f"{date_text} {time_text}", "%Y-%m-%d %H:%M"
    ).replace(tzinfo=timezone.utc)
    return source_dt.astimezone(KZ_TIMEZONE)


def normalize_provider_timezone(event: dict) -> dict:
    """Convert TV+/Mobikino Z-suffixed EPG clock from UTC to Asia/Almaty.

    The lower-level adapter historically stripped ``Z`` and treated the clock
    as local. Preserve the original provider timestamp and correct it here
    before status calculation or persistence in the canonical TVGuide feed.
    """
    item = dict(event)
    provider_source = str(item.get("source") or "")
    if provider_source not in {"tvplus", "mobikino"}:
        return item

    source_date = str(item.get("date") or "")
    source_time = str(item.get("time") or "")
    start = _utc_clock_to_kz(source_date, source_time)
    item["source_timezone"] = "UTC"
    item["source_start_at"] = f"{source_date}T{source_time}:00Z"
    item["timezone"] = "Asia/Almaty"
    item["time_normalization"] = "utc_to_asia_almaty"
    item["date"] = start.date().isoformat()
    item["time"] = start.strftime("%H:%M")
    item["schedule_offset"] = start.hour * 60 + start.minute

    end_date = item.get("estimated_broadcast_end_date")
    end_time = item.get("estimated_broadcast_end")
    if end_date and end_time:
        end = _utc_clock_to_kz(str(end_date), str(end_time))
        item["source_end_at"] = f"{end_date}T{end_time}:00Z"
        item["estimated_broadcast_end_date"] = end.date().isoformat()
        item["estimated_broadcast_end"] = end.strftime("%H:%M")

    return item


def infer_direct_event(event: dict, *, target_date: date, seen: set[tuple[str, str]]) -> dict:
    """Normalize one TVGuide programme without inventing LIVE evidence."""
    item = dict(event)
    provider_source = str(item.get("source") or "")
    item["provider_source"] = provider_source
    item["source"] = SOURCE
    _infer_sport(item)
    _infer_tournament_and_title(item)

    text = _text(item)
    identity = (str(item.get("channel") or ""), " ".join(text.split()))
    years = {int(value) for value in _YEAR_RE.findall(text)}
    replay = any(marker in text for marker in _REPLAY_MARKERS)
    stale_year = bool(years and target_date.year not in years)
    event_like = bool(_MATCH_RE.search(text) or any(marker in text for marker in _EVENT_MARKERS))
    repeated = identity in seen
    seen.add(identity)

    method = str(item.get("live_evidence_method") or "")
    explicit_live = bool(
        item.get("is_live_broadcast", item.get("is_live", False))
        and item.get("live_state") == "live"
        and method in {
            "provider_live_text", "provider_live_asset",
            "official_live_text", "official_live_asset",
        }
    )

    if explicit_live and not replay and not stale_year:
        item["is_live_broadcast"] = True
        item["is_live"] = True
        item["is_sport_event"] = True
        item["live_evidence_confidence"] = item.get("live_evidence_confidence") or "high"
        return item

    item["is_live_broadcast"] = False
    item["is_live"] = False
    item["is_sport_event"] = bool(event_like and not replay and not stale_year)

    if replay:
        item["live_state"] = "not_live"
        item["live_evidence_method"] = "provider_replay_or_editorial"
        item["live_evidence_confidence"] = "high"
    elif stale_year:
        item["live_state"] = "not_live"
        item["live_evidence_method"] = "provider_stale_year"
        item["live_evidence_confidence"] = "high"
    else:
        item["live_state"] = "unknown"
        item["live_evidence_method"] = (
            "provider_repeat_epg" if repeated
            else "provider_epg_sport_candidate" if event_like
            else "provider_epg_only"
        )
        item["live_evidence_confidence"] = "low"
    item["live_evidence_value"] = "TV+/Mobikino EPG"
    return item


def _normalize_by_date(raw_by_date: dict[date, list[dict]]) -> dict[date, list[dict]]:
    seen: set[tuple[str, str]] = set()
    result: dict[date, list[dict]] = {}
    for source_date in sorted(raw_by_date):
        events = sorted(
            raw_by_date[source_date],
            key=lambda e: (str(e.get("time") or ""), str(e.get("channel") or "")),
        )
        for event in events:
            corrected = normalize_provider_timezone(event)
            local_date = date.fromisoformat(str(corrected["date"]))
            normalized = infer_direct_event(
                corrected,
                target_date=local_date,
                seen=seen,
            )
            result.setdefault(local_date, []).append(normalized)

    for target_date in result:
        result[target_date].sort(
            key=lambda event: (
                str(event.get("time") or ""),
                str(event.get("channel") or ""),
                str(event.get("title") or ""),
            )
        )
    return result


async def _refresh_cache(anchor: date) -> None:
    global _cache_anchor, _cache_by_date, _cache_expires_at
    # Local Kazakhstan day starts on the previous UTC date, hence -2 coverage.
    dates = [
        anchor + timedelta(days=offset)
        for offset in range(-2, LOOKAHEAD_DAYS + 1)
    ]
    loaded = await fetch_tvplus_schedules(dates)
    total = sum(len(items) for items in loaded.by_date.values())
    if not total:
        raise RuntimeError("TV+/Mobikino EPG returned no events")
    if loaded.errors:
        logger.warning("tvguide partial errors=%s", loaded.errors[:20])
    _cache_by_date = _normalize_by_date(loaded.by_date)
    _cache_anchor = anchor
    _cache_expires_at = time.monotonic() + CACHE_TTL_SECONDS
    direct = sum(
        1
        for events in _cache_by_date.values()
        for event in events
        if event.get("is_live_broadcast")
    )
    candidates = sum(
        1
        for events in _cache_by_date.values()
        for event in events
        if event.get("is_sport_event")
    )
    logger.info(
        "tvguide cache refreshed events=%d sport_candidates=%d "
        "confirmed_direct=%d dates=%d errors=%d timezone=Asia/Almaty",
        total,
        candidates,
        direct,
        len(dates),
        len(loaded.errors),
    )


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
        if (
            _cache_anchor != anchor
            or time.monotonic() >= _cache_expires_at
            or requested not in _cache_by_date
        ):
            await _refresh_cache(anchor)
        return [dict(event) for event in _cache_by_date.get(requested, [])]

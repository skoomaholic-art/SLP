from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import date, datetime, timedelta, timezone

from parsers.tvplus import fetch_tvplus_schedules
from services.time_logic import KZ_TIMEZONE, get_scheduled_datetimes
from verifiers.broadcast_occurrence import verify_broadcast_occurrence

logger = logging.getLogger(__name__)
SOURCE = "tvguide"
LOOKAHEAD_DAYS = 7
CACHE_TTL_SECONDS = 180.0
RECONCILE_LOOKAHEAD_HOURS = 36
RECONCILE_MAX_NEW_PER_REFRESH = 80
RECONCILE_CONCURRENCY = 6

_REPLAY_MARKERS = (
    "повтор", "replay", "rerun", "re-run", "архив", "archive", "catch-up",
    "обзор", "review", "highlights", "лучшее", "итоги", "дневник", "журнал",
    "классика", "classic", "превью", "preview", "документальный", "новости",
    "студия", "ток-шоу", "программа", "sport review", "подробно", "трансферы",
    "best of", "road to", "история", "легенды",
)
_EVENT_MARKERS = (
    "чемпионат", "лига", "кубок", "турнир", "гран-при", "grand prix", "ufc",
    "khl", "кхл", "финал", "полуфинал", "четвертьфинал", "1/4", "1/2",
    "теннис", "снукер", "футбол", "хоккей", "баскетбол", "волейбол", "бокс",
    "mma", "мма", "мотоспорт", "формула-1", "formula 1", "гонк", "дзюдо",
    "борьб", "биатлон", "атлетик", "гимнастик", "wrc", "wec", "бейсбол",
)
_SPORT_HINTS = (
    ("футбол", "Футбол"), ("хоккей", "Хоккей"), ("кхл", "Хоккей"),
    ("khl", "Хоккей"), ("теннис", "Теннис"), ("снукер", "Снукер"),
    ("баскетбол", "Баскетбол"), ("волейбол", "Волейбол"), ("ufc", "MMA"),
    ("mma", "MMA"), ("мма", "MMA"), ("бокс", "Бокс"),
    ("мотоспорт", "Мотоспорт"), ("формула-1", "Автоспорт"),
    ("formula 1", "Автоспорт"), ("гран-при", "Автоспорт"),
    ("grand prix", "Автоспорт"), ("wrc", "Автоспорт"), ("wec", "Автоспорт"),
    ("дзюдо", "Дзюдо"), ("борьб", "Борьба"), ("бейсбол", "Бейсбол"),
)
_MATCH_RE = re.compile(r"\s(?:-|–|—|vs\.?|v\.)\s", re.I)
_YEAR_RE = re.compile(r"(?<!\d)(20\d{2})(?!\d)")

_cache_lock = asyncio.Lock()
_cache_anchor: date | None = None
_cache_expires_at = 0.0
_cache_by_date: dict[date, list[dict]] = {}
_verification_cache: dict[str, tuple[float, dict]] = {}


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
        item["reconciliation_state"] = "provider_confirmed"
        item["live_evidence_confidence"] = item.get("live_evidence_confidence") or "high"
        return item

    item["is_live_broadcast"] = False
    item["is_live"] = False
    item["is_sport_event"] = bool(event_like and not replay and not stale_year and not repeated)

    if replay:
        item["live_state"] = "not_live"
        item["live_evidence_method"] = "provider_replay_or_editorial"
        item["live_evidence_confidence"] = "high"
        item["reconciliation_state"] = "replay"
    elif stale_year:
        item["live_state"] = "not_live"
        item["live_evidence_method"] = "provider_stale_year"
        item["live_evidence_confidence"] = "high"
        item["reconciliation_state"] = "replay"
    elif repeated:
        item["live_state"] = "not_live"
        item["live_evidence_method"] = "provider_repeat_epg"
        item["live_evidence_confidence"] = "medium"
        item["reconciliation_state"] = "replay"
    else:
        item["live_state"] = "unknown"
        item["live_evidence_method"] = (
            "provider_epg_sport_candidate" if event_like else "provider_epg_only"
        )
        item["live_evidence_confidence"] = "low"
        item["reconciliation_state"] = "unverified"
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


def _verification_key(event: dict) -> str:
    return "|".join(
        (
            str(event.get("channel") or ""),
            str(event.get("date") or ""),
            str(event.get("time") or ""),
            " ".join(str(event.get("raw_title") or event.get("title") or "").casefold().split()),
        )
    )


def _cache_ttl(result: dict) -> float:
    state = str(result.get("state") or "")
    if state in {"confirmed_direct", "mismatch"}:
        return 12 * 60 * 60
    if state == "unknown":
        return 30 * 60
    return 5 * 60


def apply_reconciliation_result(event: dict, result: dict) -> dict:
    item = dict(event)
    state = str(result.get("state") or "unknown")
    item["reconciliation_state"] = state
    item["reconciliation_checked_at"] = datetime.now(KZ_TIMEZONE).isoformat(timespec="seconds")
    item["reconciliation_sources"] = result.get("sources") or []
    if result.get("external_time_kz"):
        item["reconciliation_external_time"] = result["external_time_kz"]
    if result.get("difference_minutes") is not None:
        item["reconciliation_difference_minutes"] = int(result["difference_minutes"])

    if state == "confirmed_direct":
        item["is_live_broadcast"] = True
        item["is_live"] = True
        item["is_sport_event"] = True
        item["live_state"] = "live"
        item["live_evidence_method"] = "external_schedule_consensus"
        item["live_evidence_value"] = "real-event schedule matched TV slot"
        item["live_evidence_confidence"] = "high"
    elif state == "mismatch":
        item["is_live_broadcast"] = False
        item["is_live"] = False
        item["is_sport_event"] = False
        item["live_state"] = "not_live"
        item["live_evidence_method"] = "external_schedule_mismatch"
        item["live_evidence_value"] = str(result.get("reason") or "real-event time/date differs from TV slot")
        item["live_evidence_confidence"] = "high"
    else:
        # Fail closed: EPG alone is not enough to publish a LIVE event.
        item["is_live_broadcast"] = False
        item["is_live"] = False
        item["is_sport_event"] = False
        item["live_state"] = "unknown"
        item["live_evidence_method"] = "external_schedule_unconfirmed"
        item["live_evidence_confidence"] = "low"
    return item


async def _reconcile_external(by_date: dict[date, list[dict]]) -> dict[date, list[dict]]:
    now = datetime.now(KZ_TIMEZONE)
    start_window = datetime.combine(now.date(), datetime.min.time(), tzinfo=KZ_TIMEZONE)
    end_window = now + timedelta(hours=RECONCILE_LOOKAHEAD_HOURS)
    semaphore = asyncio.Semaphore(RECONCILE_CONCURRENCY)
    new_budget = RECONCILE_MAX_NEW_PER_REFRESH
    new_candidates: list[tuple[date, int, dict, str]] = []

    for scope_date in sorted(by_date):
        for index, event in enumerate(by_date[scope_date]):
            if event.get("is_live_broadcast"):
                continue
            if not event.get("is_sport_event"):
                continue
            start, _ = get_scheduled_datetimes(event)
            if start < start_window or start > end_window:
                event["is_sport_event"] = False
                event["reconciliation_state"] = "pending_window"
                continue

            key = _verification_key(event)
            cached = _verification_cache.get(key)
            if cached and cached[0] > time.monotonic():
                by_date[scope_date][index] = apply_reconciliation_result(event, cached[1])
                continue
            new_candidates.append((scope_date, index, event, key))

    async def verify_one(entry: tuple[date, int, dict, str]):
        scope_date, index, event, key = entry
        async with semaphore:
            result = await asyncio.to_thread(verify_broadcast_occurrence, event)
        _verification_cache[key] = (time.monotonic() + _cache_ttl(result), result)
        return scope_date, index, apply_reconciliation_result(event, result)

    selected = new_candidates[:new_budget]
    if selected:
        verified = await asyncio.gather(*(verify_one(entry) for entry in selected))
        for scope_date, index, event in verified:
            by_date[scope_date][index] = event

    for scope_date, index, event, _ in new_candidates[new_budget:]:
        item = dict(event)
        item["is_sport_event"] = False
        item["reconciliation_state"] = "pending_verification"
        by_date[scope_date][index] = item

    confirmed = sum(
        1 for events in by_date.values() for event in events
        if event.get("reconciliation_state") in {"confirmed_direct", "provider_confirmed"}
    )
    replays = sum(
        1 for events in by_date.values() for event in events
        if event.get("reconciliation_state") in {"replay", "mismatch"}
    )
    pending = sum(
        1 for events in by_date.values() for event in events
        if str(event.get("reconciliation_state") or "").startswith("pending")
    )
    logger.info(
        "tvguide reconciliation checked_new=%d confirmed=%d replay_or_mismatch=%d pending=%d window_hours=%d",
        len(selected), confirmed, replays, pending, RECONCILE_LOOKAHEAD_HOURS,
    )
    return by_date


async def _refresh_cache(anchor: date) -> None:
    global _cache_anchor, _cache_by_date, _cache_expires_at
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
    _cache_by_date = await _reconcile_external(_cache_by_date)
    _cache_anchor = anchor
    _cache_expires_at = time.monotonic() + CACHE_TTL_SECONDS
    direct = sum(
        1 for events in _cache_by_date.values() for event in events
        if event.get("is_live_broadcast")
    )
    candidates = sum(
        1 for events in _cache_by_date.values() for event in events
        if event.get("is_sport_event")
    )
    logger.info(
        "tvguide cache refreshed events=%d publishable_live_events=%d confirmed_direct=%d dates=%d errors=%d timezone=Asia/Almaty",
        total, candidates, direct, len(dates), len(loaded.errors),
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

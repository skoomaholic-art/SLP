from __future__ import annotations

from datetime import datetime

from services.time_logic import KZ_TIMEZONE
from verifiers.broadcast_occurrence import OFFICIAL_EVENT_DOMAINS, verify_broadcast_occurrence
from verifiers.web_search import (
    get_base_domain,
    get_domain,
    matches_event,
    normalize,
)

MAX_PRE_SHOW_MINUTES = 45
MAX_LATE_JOIN_MINUTES = 10


def _event_result(row: dict) -> dict:
    return {
        "title": str(row.get("title") or ""),
        "snippet": str(row.get("raw_title") or row.get("search_text") or ""),
        "url": str(row.get("source_url") or ""),
    }


def _broadcast_datetime(event: dict) -> datetime:
    return datetime.strptime(
        f"{event['date']} {event['time']}",
        "%Y-%m-%d %H:%M",
    ).replace(tzinfo=KZ_TIMEZONE)


def _calendar_datetime(row: dict) -> datetime | None:
    value = str(row.get("start_at_kz") or "").strip()
    if value:
        try:
            parsed = datetime.fromisoformat(value)
            return parsed.astimezone(KZ_TIMEZONE) if parsed.tzinfo else parsed.replace(tzinfo=KZ_TIMEZONE)
        except ValueError:
            pass
    date_text = str(row.get("date") or "").strip()
    time_text = str(row.get("time") or "").strip()
    if not date_text or not time_text:
        return None
    try:
        return datetime.strptime(
            f"{date_text} {time_text}",
            "%Y-%m-%d %H:%M",
        ).replace(tzinfo=KZ_TIMEZONE)
    except ValueError:
        return None


def _sport_compatible(event: dict, row: dict) -> bool:
    left = normalize(str(event.get("sport") or ""))
    right = normalize(str(row.get("sport") or ""))
    if not left or not right or right == "прочее":
        return True
    return left == right or left in right or right in left


def _plausible_difference(minutes: int) -> bool:
    return -MAX_LATE_JOIN_MINUTES <= minutes <= MAX_PRE_SHOW_MINUTES


def match_championat_calendar(event: dict, calendar_events: list[dict]) -> dict:
    """Match one TV EPG row against an already-fetched Championat calendar.

    This function performs no network I/O. Championat is the reference for the
    real occurrence; TV EPG remains only evidence that a channel plans to show it.
    """
    broadcast = _broadcast_datetime(event)
    matches: list[dict] = []

    for row in calendar_events:
        if not _sport_compatible(event, row):
            continue
        if not matches_event(_event_result(row), event):
            continue
        start = _calendar_datetime(row)
        if start is None:
            continue
        difference = int((start - broadcast).total_seconds() / 60)
        matches.append(
            {
                "row": row,
                "start": start,
                "difference_minutes": difference,
            }
        )

    if not matches:
        return {
            "state": "unknown",
            "verification_source": "championat_calendar",
            "reason": "championat_calendar_no_match",
            "sources": [],
        }

    plausible = [
        item for item in matches
        if _plausible_difference(int(item["difference_minutes"]))
    ]
    if plausible:
        best = min(
            plausible,
            key=lambda item: abs(int(item["difference_minutes"])),
        )
        row = best["row"]
        return {
            "state": "confirmed_direct",
            "verification_source": "championat_calendar",
            "difference_minutes": best["difference_minutes"],
            "external_time_kz": best["start"].isoformat(),
            "sources": [
                {
                    "source_name": "championat.com",
                    "url": str(row.get("source_url") or ""),
                    "difference_minutes": best["difference_minutes"],
                }
            ],
        }

    closest = min(
        matches,
        key=lambda item: abs(int(item["difference_minutes"])),
    )
    row = closest["row"]
    return {
        "state": "mismatch",
        "verification_source": "championat_calendar",
        "reason": "championat_calendar_time_mismatch",
        "difference_minutes": closest["difference_minutes"],
        "external_time_kz": closest["start"].isoformat(),
        "sources": [
            {
                "source_name": "championat.com",
                "url": str(row.get("source_url") or ""),
                "difference_minutes": closest["difference_minutes"],
            }
        ],
    }


def _official_sources(result: dict) -> list[dict]:
    sources: list[dict] = []
    for source in result.get("sources") or []:
        domain = get_base_domain(get_domain(str(source.get("url") or "")))
        if domain in OFFICIAL_EVENT_DOMAINS:
            sources.append(source)
    return sources


def verify_official_fallback(event: dict) -> dict:
    """Allow a niche/local event missing on Championat to be confirmed only by an official source."""
    result = verify_broadcast_occurrence(event)
    official = _official_sources(result)

    if result.get("state") == "confirmed_direct" and official:
        return {
            **result,
            "verification_source": "official_fallback",
            "sources": official,
        }

    if result.get("state") == "mismatch" and official:
        return {
            **result,
            "verification_source": "official_fallback",
            "sources": official,
        }

    return {
        "state": "unavailable" if result.get("state") == "unavailable" else "unknown",
        "verification_source": "official_fallback",
        "reason": "official_source_not_confirmed",
        "sources": [],
        "search_errors": result.get("search_errors") or [],
    }

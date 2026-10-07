"""Authoritative supplier-mail overlay for the published schedule.

Accepted supplier EPG never destroys scraped source history. Instead, the
published/current schedule treats active email_epg_* rows as the final
authority for the same channel/fixture. If a supplier event has no scraped
counterpart, it supplements the schedule.
"""
from __future__ import annotations

from datetime import datetime
from typing import Iterable

from services.schedule_merge import normalize_match_text


SUPPLIER_SOURCE_PREFIX = "email_epg_"


def is_supplier_event(event: dict) -> bool:
    return str(event.get("source") or "").startswith(SUPPLIER_SOURCE_PREFIX)


def _event_title(event: dict) -> str:
    return str(
        event.get("title")
        or event.get("raw_event_title")
        or event.get("raw_title")
        or ""
    )


def _event_start(event: dict) -> datetime | None:
    value = event.get("start")
    if isinstance(value, datetime):
        return value
    date_value = str(event.get("date") or "")
    time_value = str(event.get("time") or "")
    if not date_value or not time_value:
        return None
    try:
        return datetime.strptime(
            f"{date_value} {time_value}",
            "%Y-%m-%d %H:%M",
        )
    except ValueError:
        return None


def same_channel_fixture(first: dict, second: dict) -> bool:
    """Match one fixture across scraped and supplier data.

    Time may differ because supplier mail is precisely the correction source.
    Tournament text is intentionally not required to match: suppliers often
    carry a newer/canonical competition label while the actual fixture title
    stays the same.
    """
    if normalize_match_text(str(first.get("channel") or "")) != normalize_match_text(
        str(second.get("channel") or "")
    ):
        return False

    first_title = normalize_match_text(_event_title(first))
    second_title = normalize_match_text(_event_title(second))
    if not first_title or first_title != second_title:
        return False

    first_sport = normalize_match_text(str(first.get("sport") or ""))
    second_sport = normalize_match_text(str(second.get("sport") or ""))
    if first_sport and second_sport and first_sport != second_sport:
        return False

    first_start = _event_start(first)
    second_start = _event_start(second)
    if first_start is None or second_start is None:
        return str(first.get("date") or "") == str(second.get("date") or "")

    # Same-day corrections are the common case. A 12-hour allowance also
    # covers a supplier correction around midnight without conflating
    # repeated daily sessions of the same tournament.
    return abs((first_start - second_start).total_seconds()) <= 12 * 3600


def apply_supplier_overlay(events: Iterable[dict]) -> list[dict]:
    """Return the current published list with accepted mail taking priority.

    Scraped rows remain in SQLite and the archive. Only the current schedule
    view/export hides a matched scraped row when an active supplier row exists.
    """
    items = list(events)
    supplier = [event for event in items if is_supplier_event(event)]
    if not supplier:
        return items

    result: list[dict] = []
    for event in items:
        if is_supplier_event(event):
            result.append(event)
            continue
        if any(same_channel_fixture(event, approved) for approved in supplier):
            continue
        result.append(event)
    return result

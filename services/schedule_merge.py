from __future__ import annotations

import re
from datetime import datetime
from typing import Iterable

from services.event_status import KZ_TIMEZONE


KAZAKH_MATCH_REPLACEMENTS = str.maketrans(
    {
        "қ": "к",
        "ғ": "г",
        "ә": "а",
        "ң": "н",
        "ө": "о",
        "ұ": "у",
        "ү": "у",
        "і": "и",
        "һ": "х",
        "ё": "е",
    }
)

MATCH_ALIASES = {
    "kairat": "кайрат",
}


def normalize_match_text(value: str) -> str:
    """Normalize event text only for matching/deduplication."""
    text = str(value or "").casefold().translate(KAZAKH_MATCH_REPLACEMENTS)
    text = re.sub(r"\([^)]*\)", " ", text)
    text = text.replace("–", "-").replace("—", "-")
    text = re.sub(r"[^\w\s-]+", " ", text, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text).strip(" -")

    for source, replacement in MATCH_ALIASES.items():
        text = re.sub(
            rf"\b{re.escape(source)}\b",
            replacement,
            text,
        )

    return text


def _event_title(event: dict) -> str:
    return str(
        event.get("title")
        or event.get("raw_event_title")
        or event.get("raw_title")
        or ""
    )


def _event_datetime(event: dict) -> datetime:
    value = event.get("start_time")
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("SportEvent start_time must be timezone-aware")
        return value.astimezone(KZ_TIMEZONE)

    # Compatibility path for old test fixtures/adapters only.
    return datetime.strptime(
        f"{event.get('date', '')} {event.get('time', '')}",
        "%Y-%m-%d %H:%M",
    ).replace(tzinfo=KZ_TIMEZONE)


def exact_broadcast_key(event: dict) -> tuple[str, str, str]:
    """Identity of one TV broadcast, not of the sporting event itself."""
    return (
        _event_datetime(event).isoformat(),
        normalize_match_text(str(event.get("channel") or "")),
        normalize_match_text(_event_title(event)),
    )


def deduplicate_broadcasts(events: Iterable[dict]) -> list[dict]:
    """Remove only true duplicate broadcasts.

    The same match on two different TV channels is deliberately preserved.
    """
    result: list[dict] = []
    seen: set[tuple[str, str, str]] = set()

    for event in events:
        key = exact_broadcast_key(event)
        if key in seen:
            continue
        seen.add(key)
        result.append(event)

    return result


def sort_schedule_events(events: Iterable[dict]) -> list[dict]:
    return sorted(
        events,
        key=lambda event: (
            _event_datetime(event),
            str(event.get("channel") or ""),
        ),
    )


def same_sporting_event(
    first: dict,
    second: dict,
    *,
    max_start_difference_minutes: int = 30,
) -> bool:
    """Return True when two broadcasts look like the same sports event."""
    if str(first.get("channel") or "") == str(second.get("channel") or ""):
        return False

    first_title = normalize_match_text(_event_title(first))
    second_title = normalize_match_text(_event_title(second))

    if not first_title or first_title != second_title:
        return False

    first_sport = normalize_match_text(str(first.get("sport") or ""))
    second_sport = normalize_match_text(str(second.get("sport") or ""))

    if first_sport and second_sport and first_sport != second_sport:
        return False

    first_tournament = normalize_match_text(
        str(first.get("tournament") or "")
    )
    second_tournament = normalize_match_text(
        str(second.get("tournament") or "")
    )

    if (
        first_tournament
        and second_tournament
        and first_tournament != second_tournament
    ):
        return False

    difference = abs(
        int(
            (
                _event_datetime(first)
                - _event_datetime(second)
            ).total_seconds()
            / 60
        )
    )

    return difference <= max_start_difference_minutes


def group_simulcasts(
    events: Iterable[dict],
    *,
    max_start_difference_minutes: int = 30,
) -> list[list[dict]]:
    """Group same-event broadcasts for compact Telegram presentation.

    No SportEvent is destroyed or merged. Grouping is a view-layer operation.
    """
    ordered = sort_schedule_events(events)
    groups: list[list[dict]] = []

    for event in ordered:
        placed = False

        for group in groups:
            if any(
                same_sporting_event(
                    event,
                    existing,
                    max_start_difference_minutes=max_start_difference_minutes,
                )
                for existing in group
            ):
                group.append(event)
                group.sort(
                    key=lambda item: (
                        _event_datetime(item),
                        str(item.get("channel") or ""),
                    )
                )
                placed = True
                break

        if not placed:
            groups.append([event])

    groups.sort(key=lambda group: _event_datetime(group[0]))
    return groups


def merge_source_schedules(*schedules: Iterable[dict]) -> list[dict]:
    """Flatten source schedules into one deduplicated aware-datetime list."""
    combined: list[dict] = []

    for schedule in schedules:
        combined.extend(schedule)

    return sort_schedule_events(
        deduplicate_broadcasts(combined)
    )


def unique_channels(events: Iterable[dict]) -> list[str]:
    return sorted(
        {
            str(event.get("channel") or "").strip()
            for event in events
            if str(event.get("channel") or "").strip()
        }
    )

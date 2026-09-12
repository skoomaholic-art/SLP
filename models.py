from __future__ import annotations

from typing import NotRequired, TypedDict


class SportEvent(TypedDict):
    """Единый формат эфирного события внутри SLP.

    ``is_live_broadcast`` is source evidence. ``status`` is computed from the
    timezone-aware broadcast window and must never be used as source evidence.
    """

    source: str
    source_url: str
    channel: str
    date: str
    time: str
    timezone: str
    start_time: str
    end_time: str
    sport: str
    tournament: str
    title: str
    raw_title: str
    is_live_broadcast: bool

    estimated_broadcast_end_date: str | None
    estimated_broadcast_end: str | None
    end_estimation_method: str | None
    end_confidence: str

    # Migration compatibility for snapshots produced before the LIVE split.
    is_live: NotRequired[bool]
    status: NotRequired[str]
    raw_sport: NotRequired[str]
    raw_tournament: NotRequired[str]
    raw_event_title: NotRequired[str]
    schedule_offset: NotRequired[int]

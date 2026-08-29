from __future__ import annotations

from typing import NotRequired, TypedDict


class SportEvent(TypedDict):
    """Единый формат спортивного события внутри SLP."""

    source: str
    source_url: str
    channel: str
    date: str
    time: str
    sport: str
    tournament: str
    title: str
    is_live: bool
    raw_title: str

    estimated_broadcast_end_date: str | None
    estimated_broadcast_end: str | None
    end_estimation_method: str | None
    end_confidence: str

    raw_sport: NotRequired[str]
    raw_tournament: NotRequired[str]
    raw_event_title: NotRequired[str]
    schedule_offset: NotRequired[int]
from __future__ import annotations

from datetime import datetime
from typing import NotRequired, TypedDict


class SportEvent(TypedDict):
    """Universal SLP event contract.

    ``is_live_broadcast`` describes source metadata only: the source explicitly
    marks the programme as a direct/live broadcast. Whether it is happening
    right now is always calculated from ``start_time``/``end_time`` by
    ``services.event_status``.
    """

    event_key: str
    source: str
    source_url: str
    channel: str

    raw_title: str
    normalized_title: str
    title: str
    sport: str
    tournament: str

    timezone: str
    start_time: datetime
    end_time: datetime | None
    is_live_broadcast: bool
    updated_at: datetime

    end_estimation_method: str | None
    end_confidence: str

    # Transitional display/parser fields retained so existing adapters do not
    # need a flag-day migration. New business logic must use the aware datetime
    # fields above and ``is_live_broadcast``.
    date: NotRequired[str]
    time: NotRequired[str]
    estimated_broadcast_end_date: NotRequired[str | None]
    estimated_broadcast_end: NotRequired[str | None]
    is_live: NotRequired[bool]

    raw_sport: NotRequired[str]
    raw_tournament: NotRequired[str]
    raw_event_title: NotRequired[str]
    schedule_offset: NotRequired[int]

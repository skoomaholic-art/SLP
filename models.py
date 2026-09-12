from __future__ import annotations

from typing import NotRequired, TypedDict


class BroadcastEvidence(TypedDict):
    method: str
    source: str
    value: str
    confidence: str
    on_air_now: NotRequired[bool]


class SportEvent(TypedDict):
    """Единый формат спортивного события внутри SLP.

    ``is_live`` сохранён как совместимый alias для старых адаптеров.
    Каноническое поле прямой трансляции — ``is_live_broadcast``.
    Текущее положение во времени вычисляется динамически через time_logic.
    """

    source: str
    source_url: str
    channel: str
    date: str
    time: str
    timezone: str
    sport: str
    tournament: str
    title: str
    is_live: bool
    is_live_broadcast: bool
    raw_title: str

    estimated_broadcast_end_date: str | None
    estimated_broadcast_end: str | None
    end_estimation_method: str | None
    end_confidence: str

    raw_sport: NotRequired[str]
    raw_tournament: NotRequired[str]
    raw_event_title: NotRequired[str]
    schedule_offset: NotRequired[int]
    source_timezone: NotRequired[str]
    source_start_at: NotRequired[str]
    source_end_at: NotRequired[str]
    time_normalization: NotRequired[str]
    is_sport_event: NotRequired[bool]
    live_state: NotRequired[str]
    live_evidence_method: NotRequired[str]
    live_evidence_value: NotRequired[str]
    live_evidence_confidence: NotRequired[str]
    provider_source: NotRequired[str]
    broadcast_evidence: NotRequired[list[BroadcastEvidence]]
    provider_on_air_now: NotRequired[bool]
    provider_claimed_live: NotRequired[bool]
    third_party_claimed_live: NotRequired[bool]
    provider_live_evidence_strength: NotRequired[str]

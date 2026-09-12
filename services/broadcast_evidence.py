from __future__ import annotations

from typing import Iterable


OFFICIAL_LIVE_METHODS = {
    "official_live_badge",
    "official_live_text",
    "official_live_asset",
    "official_live_page_marker",
}

THIRD_PARTY_LIVE_METHODS = {
    "third_party_live_badge",
    "third_party_live_text",
}

ON_AIR_METHODS = {
    "official_on_air_marker",
}


def add_broadcast_evidence(
    event: dict,
    *,
    method: str,
    source: str,
    value: str = "",
    confidence: str = "medium",
    on_air_now: bool | None = None,
) -> dict:
    """Append one normalized broadcast-evidence record to an event.

    Evidence is additive. A provider marker says what a channel/guide claims;
    it does not automatically turn a non-sport programme into a sports LIVE.
    In particular, ``official_on_air_marker`` only means the programme is the
    channel's current slot; it is not evidence of a direct sports broadcast.
    """
    item = dict(event)
    evidence = list(item.get("broadcast_evidence") or [])
    record = {
        "method": str(method),
        "source": str(source),
        "value": str(value),
        "confidence": str(confidence),
    }
    if on_air_now is not None:
        record["on_air_now"] = bool(on_air_now)
    if record not in evidence:
        evidence.append(record)
    item["broadcast_evidence"] = evidence

    if on_air_now is not None or method in ON_AIR_METHODS:
        item["provider_on_air_now"] = bool(
            True if on_air_now is None else on_air_now
        )

    if method in OFFICIAL_LIVE_METHODS:
        item["provider_claimed_live"] = True
        item["provider_live_evidence_strength"] = "high"
    elif method in THIRD_PARTY_LIVE_METHODS:
        item["third_party_claimed_live"] = True
        item.setdefault("provider_live_evidence_strength", "medium")
    return item


def has_evidence_method(event: dict, methods: Iterable[str]) -> bool:
    accepted = set(methods)
    return any(
        str(row.get("method") or "") in accepted
        for row in event.get("broadcast_evidence") or []
    )


def official_live_claim(event: dict) -> bool:
    return has_evidence_method(event, OFFICIAL_LIVE_METHODS)


def third_party_live_claim(event: dict) -> bool:
    return has_evidence_method(event, THIRD_PARTY_LIVE_METHODS)

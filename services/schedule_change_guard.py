from __future__ import annotations

from datetime import datetime


DAY_SHIFT_MINUTES = 24 * 60
MIN_SYSTEMIC_SHIFT_EVENTS = 3


def _delta_minutes(old_value: str, new_value: str) -> int:
    old_dt = datetime.fromisoformat(old_value)
    new_dt = datetime.fromisoformat(new_value)
    return int((new_dt - old_dt).total_seconds() // 60)


def systemic_one_day_shift(changes: list[dict]) -> dict | None:
    """Detect a parser/source-wide ±1 day remap masquerading as many updates.

    Real schedule edits normally affect one or a few events and often change the
    clock time. When several events on the same channel all move by exactly one
    calendar day while keeping their clock times and durations, treat the batch
    as a source-date remapping correction rather than user-facing schedule news.
    """
    if len(changes) < MIN_SYSTEMIC_SHIFT_EVENTS:
        return None

    deltas: list[int] = []
    channels: set[str] = set()
    affected = 0

    for change in changes:
        if change.get("type") != "updated":
            return None

        time_changes = [
            item for item in change.get("changes", [])
            if item.get("field") == "time"
        ]
        end_changes = [
            item for item in change.get("changes", [])
            if item.get("field") == "end"
        ]
        other_changes = [
            item for item in change.get("changes", [])
            if item.get("field") not in {"time", "end"}
        ]

        if len(time_changes) != 1 or other_changes:
            return None

        time_change = time_changes[0]
        delta = _delta_minutes(time_change["old"], time_change["new"])
        if abs(delta) != DAY_SHIFT_MINUTES:
            return None

        for end_change in end_changes:
            if _delta_minutes(end_change["old"], end_change["new"]) != delta:
                return None

        channel = str(time_change.get("channel") or "")
        if not channel:
            return None

        channels.add(channel)
        deltas.append(delta)
        affected += 1

    if affected < MIN_SYSTEMIC_SHIFT_EVENTS:
        return None
    if len(channels) != 1 or len(set(deltas)) != 1:
        return None

    return {
        "channel": next(iter(channels)),
        "delta_minutes": deltas[0],
        "event_count": affected,
    }

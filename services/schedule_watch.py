from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Iterable

from services.schedule_merge import group_simulcasts, normalize_match_text
from services.time_logic import KZ_TIMEZONE, get_scheduled_datetimes


STATE_VERSION = 1
DEFAULT_STATE_PATH = Path(__file__).resolve().parents[1] / "slp_state.json"
END_CHANGE_THRESHOLD_MINUTES = 15


def _event_title(event: dict) -> str:
    return str(
        event.get("title")
        or event.get("raw_event_title")
        or event.get("raw_title")
        or "Без названия"
    )


def stable_event_identity(event: dict) -> str:
    """Stable sports-event identity that deliberately ignores TV time/channel.

    This lets SLP recognise a rescheduled event as the same event instead of
    reporting it as one deletion plus one addition.
    """
    parts = (
        normalize_match_text(str(event.get("sport") or "")),
        normalize_match_text(str(event.get("tournament") or "")),
        normalize_match_text(_event_title(event)),
    )
    raw = "|".join(parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _broadcast_snapshot(event: dict) -> dict:
    start, end = get_scheduled_datetimes(event)
    return {
        "channel": str(event.get("channel") or "Канал не указан"),
        "start": start.isoformat(),
        "end": end.isoformat(),
    }


def build_schedule_snapshot(events: Iterable[dict]) -> dict:
    snapshot_events = []

    for group in group_simulcasts(events):
        first = group[0]
        broadcasts = sorted(
            (_broadcast_snapshot(event) for event in group),
            key=lambda item: (item["start"], item["channel"]),
        )
        snapshot_events.append(
            {
                "identity": stable_event_identity(first),
                "title": _event_title(first),
                "sport": str(first.get("sport") or ""),
                "tournament": str(first.get("tournament") or ""),
                "broadcasts": broadcasts,
            }
        )

    snapshot_events.sort(
        key=lambda item: (
            item["broadcasts"][0]["start"] if item["broadcasts"] else "",
            item["identity"],
        )
    )

    return {
        "version": STATE_VERSION,
        "generated_at": datetime.now(KZ_TIMEZONE).isoformat(),
        "events": snapshot_events,
    }


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _anchor_start(event_snapshot: dict) -> datetime:
    broadcasts = event_snapshot.get("broadcasts") or []
    if not broadcasts:
        return datetime.min.replace(tzinfo=KZ_TIMEZONE)
    return min(_parse_dt(item["start"]) for item in broadcasts)


def _bucket_by_identity(snapshot: dict) -> dict[str, list[dict]]:
    result: dict[str, list[dict]] = {}
    for event in snapshot.get("events", []):
        result.setdefault(str(event.get("identity") or ""), []).append(event)
    for bucket in result.values():
        bucket.sort(key=_anchor_start)
    return result


def _pair_same_identity(old_events: list[dict], new_events: list[dict]) -> tuple[list[tuple[dict, dict]], list[dict], list[dict]]:
    remaining_new = list(new_events)
    pairs: list[tuple[dict, dict]] = []
    removed: list[dict] = []

    for old in old_events:
        if not remaining_new:
            removed.append(old)
            continue

        old_start = _anchor_start(old)
        best_index = min(
            range(len(remaining_new)),
            key=lambda index: abs(
                (_anchor_start(remaining_new[index]) - old_start).total_seconds()
            ),
        )
        pairs.append((old, remaining_new.pop(best_index)))

    return pairs, removed, remaining_new


def _by_channel(event_snapshot: dict) -> dict[str, dict]:
    return {
        str(item.get("channel") or ""): item
        for item in event_snapshot.get("broadcasts", [])
    }


def _format_iso(value: str) -> str:
    dt = _parse_dt(value)
    return dt.strftime("%d.%m %H:%M")


def _minutes_between(first: str, second: str) -> int:
    return int(abs((_parse_dt(first) - _parse_dt(second)).total_seconds()) // 60)


def _updated_change(old: dict, new: dict, *, end_threshold_minutes: int) -> dict | None:
    changes = []
    old_channels = _by_channel(old)
    new_channels = _by_channel(new)

    old_set = set(old_channels)
    new_set = set(new_channels)

    if old_set != new_set:
        changes.append(
            {
                "field": "channels",
                "old": sorted(old_set),
                "new": sorted(new_set),
            }
        )

    common_channels = sorted(old_set & new_set)

    if not common_channels and old_channels and new_channels:
        old_broadcast = min(old_channels.values(), key=lambda item: item["start"])
        new_broadcast = min(new_channels.values(), key=lambda item: item["start"])

        if old_broadcast["start"] != new_broadcast["start"]:
            changes.append(
                {
                    "field": "time",
                    "channel": "Эфир",
                    "old": old_broadcast["start"],
                    "new": new_broadcast["start"],
                }
            )

        if (
            old_broadcast["end"] != new_broadcast["end"]
            and _minutes_between(old_broadcast["end"], new_broadcast["end"])
            >= end_threshold_minutes
        ):
            changes.append(
                {
                    "field": "end",
                    "channel": "Эфир",
                    "old": old_broadcast["end"],
                    "new": new_broadcast["end"],
                }
            )

    for channel in common_channels:
        old_broadcast = old_channels[channel]
        new_broadcast = new_channels[channel]

        if old_broadcast["start"] != new_broadcast["start"]:
            changes.append(
                {
                    "field": "time",
                    "channel": channel,
                    "old": old_broadcast["start"],
                    "new": new_broadcast["start"],
                }
            )

        if (
            old_broadcast["end"] != new_broadcast["end"]
            and _minutes_between(old_broadcast["end"], new_broadcast["end"])
            >= end_threshold_minutes
        ):
            changes.append(
                {
                    "field": "end",
                    "channel": channel,
                    "old": old_broadcast["end"],
                    "new": new_broadcast["end"],
                }
            )

    if not changes:
        return None

    return {
        "type": "updated",
        "identity": new.get("identity"),
        "title": new.get("title") or old.get("title") or "Без названия",
        "changes": changes,
    }


def _snapshot_event_is_still_relevant(
    event_snapshot: dict,
    now: datetime,
) -> bool:
    broadcasts = event_snapshot.get("broadcasts") or []
    if not broadcasts:
        return False
    return any(_parse_dt(item["end"]) > now for item in broadcasts)


def diff_schedule_snapshots(
    old_snapshot: dict,
    new_snapshot: dict,
    *,
    end_threshold_minutes: int = END_CHANGE_THRESHOLD_MINUTES,
    now: datetime | None = None,
) -> list[dict]:
    if now is None:
        now = datetime.now(KZ_TIMEZONE)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=KZ_TIMEZONE)
    else:
        now = now.astimezone(KZ_TIMEZONE)

    old_buckets = _bucket_by_identity(old_snapshot)
    new_buckets = _bucket_by_identity(new_snapshot)
    identities = sorted(set(old_buckets) | set(new_buckets))
    changes: list[dict] = []

    for identity in identities:
        old_events = old_buckets.get(identity, [])
        new_events = new_buckets.get(identity, [])
        pairs, removed, added = _pair_same_identity(old_events, new_events)

        for old, new in pairs:
            updated = _updated_change(
                old,
                new,
                end_threshold_minutes=end_threshold_minutes,
            )
            if updated:
                changes.append(updated)

        for event in removed:
            if not _snapshot_event_is_still_relevant(event, now):
                continue
            changes.append(
                {
                    "type": "removed",
                    "identity": identity,
                    "title": event.get("title") or "Без названия",
                    "broadcasts": event.get("broadcasts", []),
                }
            )

        for event in added:
            changes.append(
                {
                    "type": "added",
                    "identity": identity,
                    "title": event.get("title") or "Без названия",
                    "broadcasts": event.get("broadcasts", []),
                }
            )

    return changes


def format_schedule_change(change: dict) -> str:
    change_type = change.get("type")
    title = str(change.get("title") or "Без названия")

    if change_type == "added":
        lines = ["➕ Новое LIVE-событие", title]
        for item in change.get("broadcasts", []):
            lines.append(
                f"🕐 {_format_iso(item['start'])} · 📺 {item['channel']}"
            )
        return "\n".join(lines)

    if change_type == "removed":
        lines = ["➖ Событие исчезло из расписания", title]
        for item in change.get("broadcasts", []):
            lines.append(
                f"Было: {_format_iso(item['start'])} · {item['channel']}"
            )
        return "\n".join(lines)

    lines = ["🔔 Изменение расписания", title]

    for item in change.get("changes", []):
        field = item.get("field")
        if field == "channels":
            old = ", ".join(item.get("old", [])) or "нет"
            new = ", ".join(item.get("new", [])) or "нет"
            lines.append(f"📺 Канал: {old} → {new}")
        elif field == "time":
            lines.append(
                f"🕐 {item.get('channel')}: "
                f"{_format_iso(item['old'])} → {_format_iso(item['new'])}"
            )
        elif field == "end":
            lines.append(
                f"🏁 {item.get('channel')}: "
                f"{_format_iso(item['old'])} → {_format_iso(item['new'])}"
            )

    return "\n".join(lines)


def build_change_messages(changes: Iterable[dict], limit: int = 3900) -> list[str]:
    blocks = [format_schedule_change(change) for change in changes]
    if not blocks:
        return []

    messages: list[str] = []
    current = ""

    for block in blocks:
        candidate = block if not current else f"{current}\n\n{block}"
        if current and len(candidate) > limit:
            messages.append(current)
            current = block
        else:
            current = candidate

    if current:
        messages.append(current)

    return messages


def empty_runtime_state() -> dict:
    return {
        "version": STATE_VERSION,
        "subscribers": [],
        "snapshot": None,
    }


def load_runtime_state(path: str | Path = DEFAULT_STATE_PATH) -> dict:
    target = Path(path)
    if not target.exists():
        return empty_runtime_state()

    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty_runtime_state()

    if not isinstance(data, dict):
        return empty_runtime_state()

    state = empty_runtime_state()
    state.update(data)
    state["subscribers"] = [
        int(value)
        for value in state.get("subscribers", [])
        if str(value).lstrip("-").isdigit()
    ]
    return state


def save_runtime_state(state: dict, path: str | Path = DEFAULT_STATE_PATH) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".tmp")
    temp.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temp.replace(target)


def is_subscribed(chat_id: int, path: str | Path = DEFAULT_STATE_PATH) -> bool:
    state = load_runtime_state(path)
    return int(chat_id) in set(state.get("subscribers", []))


def set_subscription(
    chat_id: int,
    enabled: bool,
    path: str | Path = DEFAULT_STATE_PATH,
) -> bool:
    state = load_runtime_state(path)
    subscribers = set(state.get("subscribers", []))

    if enabled:
        subscribers.add(int(chat_id))
    else:
        subscribers.discard(int(chat_id))

    state["subscribers"] = sorted(subscribers)
    save_runtime_state(state, path)
    return enabled


def update_snapshot(
    snapshot: dict,
    path: str | Path = DEFAULT_STATE_PATH,
) -> None:
    state = load_runtime_state(path)
    state["snapshot"] = snapshot
    save_runtime_state(state, path)


def get_previous_snapshot(path: str | Path = DEFAULT_STATE_PATH) -> dict | None:
    state = load_runtime_state(path)
    snapshot = state.get("snapshot")
    return snapshot if isinstance(snapshot, dict) else None


def get_subscribers(path: str | Path = DEFAULT_STATE_PATH) -> list[int]:
    return list(load_runtime_state(path).get("subscribers", []))

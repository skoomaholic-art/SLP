from __future__ import annotations

from dataclasses import dataclass, field

from services.event_contract import validate_sport_event
from services.event_status import is_live_broadcast
from services.schedule_merge import exact_broadcast_key
from services.time_logic import get_scheduled_datetimes


@dataclass
class QAResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    duplicate_count: int = 0


class ParserQAAgent:
    """Deterministic guardrail between parsers and persistent storage."""

    def validate_batch(self, events: list[dict], *, expected_source: str) -> QAResult:
        errors: list[str] = []
        warnings: list[str] = []
        seen: set[tuple[str, str, str, str]] = set()
        duplicate_count = 0

        for index, event in enumerate(events):
            prefix = f"event[{index}]"

            try:
                validate_sport_event(event)
            except Exception as error:
                errors.append(f"{prefix}: contract: {error}")
                continue

            if str(event.get("source") or "") != expected_source:
                errors.append(
                    f"{prefix}: source={event.get('source')!r}, expected={expected_source!r}"
                )

            try:
                start, end = get_scheduled_datetimes(event)
            except Exception as error:
                errors.append(f"{prefix}: time_logic: {error}")
                continue

            if start.tzinfo is None or end.tzinfo is None:
                errors.append(f"{prefix}: naive_datetime")
            if end <= start:
                errors.append(f"{prefix}: end_not_after_start")

            if not str(event.get("channel") or "").strip():
                errors.append(f"{prefix}: empty_channel")

            if not str(event.get("raw_title") or "").strip():
                errors.append(f"{prefix}: empty_raw_title")

            if is_live_broadcast(event):
                if not str(event.get("sport") or "").strip():
                    warnings.append(f"{prefix}: live_event_without_sport")
                if not str(event.get("title") or "").strip():
                    warnings.append(f"{prefix}: live_event_without_normalized_title")

            key = exact_broadcast_key(event)
            if key in seen:
                duplicate_count += 1
            else:
                seen.add(key)

        if duplicate_count:
            warnings.append(f"duplicate_broadcasts={duplicate_count}")

        return QAResult(
            ok=not errors,
            errors=errors,
            warnings=warnings,
            duplicate_count=duplicate_count,
        )

"""Internal consistency checks for the assembled SLP schedule.

The public-API validator compares SLP with outside sports data. This module
looks only at the schedule itself and flags rows that are unlikely to be a
correct broadcast: an impossible duration, two broadcasts colliding on one
linear channel, the same fixture at two different times, a leftover test row,
a tournament year that cannot be right, or a date whose day and month look
swapped.

The checker never changes the schedule. Every finding is a hint for a human
editor, with the reason spelled out in Russian.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from hashlib import sha256
import re

from services.schedule_merge import normalize_match_text
from services.time_logic import KZ_TIMEZONE

LEVEL_ORDER = {"error": 0, "warning": 1, "attention": 2}

# Broadcast windows include studio time, so limits are deliberately loose.
# Long-format sports can legitimately fill most of a day part.
LONG_FORMAT_SPORTS = (
    "теннис", "снукер", "гольф", "крикет", "велоспорт", "дартс",
    "мотоспорт", "автоспорт", "лёгкая атлетика", "легкая атлетика",
    "бокс", "единоборства", "мма", "киберспорт", "шахматы",
    "плавание", "гимнастика",
)
LONG_FORMAT_MAX_MINUTES = 10 * 60
DEFAULT_MAX_MINUTES = 4 * 60 + 30
MIN_MINUTES = 20

SAME_FIXTURE_MIN_GAP_MINUTES = 31
SAME_FIXTURE_MAX_GAP_MINUTES = 36 * 60
NEAR_SIMULTANEOUS_MINUTES = 45

YEAR_AHEAD_LIMIT = 4
YEAR_BEHIND_LIMIT = 1
SWAP_FAR_DAYS = 21
SWAP_NEAR_DAYS_AHEAD = 14
SWAP_NEAR_DAYS_BEHIND = 1

_TEST_RE = re.compile(
    r"(?<![\w-])(?:тест|test)(?![\w]|[\s-]*(?:матч|match|серия|series))",
    re.IGNORECASE,
)
_YEAR_RE = re.compile(r"(?<!\d)(20\d{2})(?!\d)")
_PARTICIPANT_SPLIT_RE = re.compile(r"\s[-–—]\s")


def _parse(value) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value or ""))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=KZ_TIMEZONE)
    return parsed.astimezone(KZ_TIMEZONE)


def _clock(value: datetime) -> str:
    return value.strftime("%H:%M")


def _day(value: datetime) -> str:
    return value.strftime("%d.%m")


def _duration_text(minutes: int) -> str:
    hours, rest = divmod(minutes, 60)
    if hours and rest:
        return f"{hours} ч {rest} мин"
    if hours:
        return f"{hours} ч"
    return f"{rest} мин"


def _max_minutes(sport: str) -> int:
    lowered = str(sport or "").casefold()
    if any(marker in lowered for marker in LONG_FORMAT_SPORTS):
        return LONG_FORMAT_MAX_MINUTES
    return DEFAULT_MAX_MINUTES


def _has_two_participants(title: str) -> bool:
    return bool(_PARTICIPANT_SPLIT_RE.search(str(title or "")))


def _titles_look_alike(first: str, second: str) -> bool:
    if not first or not second:
        return False
    if first in second or second in first:
        return True
    return SequenceMatcher(None, first, second).ratio() >= 0.72


def _broadcasts(event: dict) -> list[dict]:
    """Flatten one merged schedule entry into per-channel broadcasts."""
    rows = []
    listed = event.get("broadcasts") or []
    if not listed:
        listed = [{
            "channel": event.get("channel", ""),
            "start_at": event.get("start_at"),
            "end_at": event.get("end_at"),
            "source": event.get("source", ""),
            "source_record_id": event.get("source_record_id", ""),
            "active": event.get("active", True),
        }]
    for item in listed:
        if item.get("active") is False:
            continue
        start = _parse(item.get("start_at"))
        end = _parse(item.get("end_at"))
        channel = str(item.get("channel") or "").strip()
        if start is None or not channel:
            continue
        rows.append({
            "channel": channel,
            "start": start,
            "end": end,
            "end_known": bool(event.get("end_known")) and end is not None,
            "title": str(event.get("title") or "").strip(),
            "sport": str(event.get("sport") or "").strip(),
            "tournament": str(event.get("tournament") or "").strip(),
            "source": str(item.get("source") or event.get("source") or ""),
            "storage_id": str(
                item.get("source_record_id")
                or event.get("source_record_id") or ""
            ),
            "event_id": str(event.get("id") or ""),
        })
    return rows


def _finding(kind: str, level: str, row: dict, message: str, **details) -> dict:
    fingerprint = "|".join((
        kind, row["channel"], row["start"].isoformat(),
        normalize_match_text(row["title"]),
        str(details.get("other_channel") or ""),
        str(details.get("other_start_at") or ""),
    ))
    return {
        "key": sha256(fingerprint.encode("utf-8")).hexdigest()[:24],
        "kind": kind,
        "level": level,
        "title": row["title"],
        "sport": row["sport"],
        "tournament": row["tournament"],
        "channel": row["channel"],
        "date": row["start"].date().isoformat(),
        "time": _clock(row["start"]),
        "storage_id": row["storage_id"],
        "event_id": row["event_id"],
        "source": row["source"],
        "message": message,
        "details": details,
    }


def _check_duration(row: dict) -> dict | None:
    if not row["end_known"]:
        return None
    minutes = int((row["end"] - row["start"]).total_seconds() // 60)
    limit = _max_minutes(row["sport"])
    if minutes > limit:
        return _finding(
            "duration_too_long", "error", row,
            f"Эфир длится {_duration_text(minutes)}: с {_clock(row['start'])} "
            f"до {_clock(row['end'])} ({_day(row['end'])}). Для этого вида "
            f"спорта ожидается не больше {_duration_text(limit)}. Проверьте "
            "время начала и окончания: возможна опечатка в часах.",
            duration_minutes=minutes, limit_minutes=limit,
            end_at=row["end"].isoformat(),
        )
    if 0 <= minutes < MIN_MINUTES:
        return _finding(
            "duration_too_short", "attention", row,
            f"Эфир длится всего {_duration_text(minutes)}: с "
            f"{_clock(row['start'])} до {_clock(row['end'])}. Для прямой "
            "трансляции это слишком мало.",
            duration_minutes=minutes, limit_minutes=MIN_MINUTES,
            end_at=row["end"].isoformat(),
        )
    return None


def _check_test_label(row: dict) -> dict | None:
    text = f"{row['title']} {row['tournament']}"
    if not _TEST_RE.search(text):
        return None
    return _finding(
        "test_label", "warning", row,
        "В названии или турнире есть пометка «тест». Похоже на тестовую "
        "запись, которая не должна попасть в расписание.",
    )


def _check_year(row: dict) -> dict | None:
    event_year = row["start"].year
    text = f"{row['title']} {row['tournament']}"
    for match in _YEAR_RE.finditer(text):
        year = int(match.group(1))
        if year > event_year + YEAR_AHEAD_LIMIT:
            return _finding(
                "impossible_year", "warning", row,
                f"В названии указан {year} год, а эфир стоит на "
                f"{event_year}-й. Турнир с таким годом не может идти сейчас.",
                year=year, event_year=event_year,
            )
        if year < event_year - YEAR_BEHIND_LIMIT:
            return _finding(
                "impossible_year", "attention", row,
                f"В названии указан {year} год, а эфир стоит на "
                f"{event_year}-й. Возможно, это повтор или старая запись, "
                "а не прямой эфир.",
                year=year, event_year=event_year,
            )
    return None


def _check_date_swap(row: dict, today: date) -> dict | None:
    """04.07 entered as 07.04: the row lands months away from its real day."""
    start_day = row["start"].date()
    if start_day.day > 12 or start_day.day == start_day.month:
        return None
    if abs((start_day - today).days) < SWAP_FAR_DAYS:
        return None
    try:
        swapped = start_day.replace(month=start_day.day, day=start_day.month)
    except ValueError:
        return None
    nearest = min(
        (swapped.replace(year=swapped.year + shift) for shift in (-1, 0, 1)),
        key=lambda candidate: abs((candidate - today).days),
    )
    offset = (nearest - today).days
    if not -SWAP_NEAR_DAYS_BEHIND <= offset <= SWAP_NEAR_DAYS_AHEAD:
        return None
    return _finding(
        "date_swap_suspect", "error", row,
        f"Эфир стоит на {start_day.strftime('%d.%m.%Y')}, далеко от текущей "
        f"сетки. Если поменять день и месяц местами, получится "
        f"{nearest.strftime('%d.%m.%Y')} — это рядом с сегодняшней датой. "
        "Проверьте, не перепутаны ли день и месяц.",
        swapped_date=nearest.isoformat(),
    )


def _check_channel_collisions(rows: list[dict]) -> list[dict]:
    findings = []
    by_channel: dict[str, list[dict]] = {}
    for row in rows:
        by_channel.setdefault(row["channel"], []).append(row)
    for channel_rows in by_channel.values():
        channel_rows.sort(key=lambda item: item["start"])
        for index, first in enumerate(channel_rows):
            for second in channel_rows[index + 1:]:
                gap = int(
                    (second["start"] - first["start"]).total_seconds() // 60
                )
                if first["end_known"]:
                    if second["start"] >= first["end"]:
                        break
                elif gap >= NEAR_SIMULTANEOUS_MINUTES:
                    break
                first_title = normalize_match_text(first["title"])
                second_title = normalize_match_text(second["title"])
                if first_title == second_title:
                    # Same fixture twice is reported by the fixture check.
                    continue
                if _titles_look_alike(first_title, second_title):
                    findings.append(_finding(
                        "duplicate_slot", "attention", second,
                        f"На канале {second['channel']} почти одновременно "
                        f"стоят «{first['title']}» ({_clock(first['start'])})"
                        f" и «{second['title']}» ({_clock(second['start'])})."
                        " Похоже на один эфир, пришедший из двух источников "
                        "с разным временем или написанием.",
                        other_title=first["title"],
                        other_channel=first["channel"],
                        other_start_at=first["start"].isoformat(),
                        other_storage_id=first["storage_id"],
                    ))
                    continue
                until = (
                    f"идёт до {_clock(first['end'])}"
                    if first["end_known"] else "ещё не мог закончиться"
                )
                findings.append(_finding(
                    "channel_overlap", "warning", second,
                    f"На канале {second['channel']} два эфира накладываются: "
                    f"«{first['title']}» начинается в {_clock(first['start'])}"
                    f" и {until}, а «{second['title']}» стартует в "
                    f"{_clock(second['start'])}. Один линейный канал не "
                    "может показывать оба.",
                    other_title=first["title"],
                    other_channel=first["channel"],
                    other_start_at=first["start"].isoformat(),
                    other_storage_id=first["storage_id"],
                ))
    return findings


def _check_same_fixture(rows: list[dict]) -> list[dict]:
    findings = []
    by_fixture: dict[tuple, list[dict]] = {}
    for row in rows:
        if not _has_two_participants(row["title"]):
            continue
        key = (
            normalize_match_text(row["title"]),
            normalize_match_text(row["sport"]),
        )
        by_fixture.setdefault(key, []).append(row)
    for fixture_rows in by_fixture.values():
        fixture_rows.sort(key=lambda item: (item["start"], item["channel"]))
        for index, first in enumerate(fixture_rows):
            for second in fixture_rows[index + 1:]:
                gap = int(
                    (second["start"] - first["start"]).total_seconds() // 60
                )
                if gap > SAME_FIXTURE_MAX_GAP_MINUTES:
                    break
                if gap < SAME_FIXTURE_MIN_GAP_MINUTES:
                    continue
                first_tournament = normalize_match_text(first["tournament"])
                second_tournament = normalize_match_text(second["tournament"])
                if (first_tournament and second_tournament
                        and first_tournament != second_tournament):
                    continue
                same_channel = first["channel"] == second["channel"]
                where = (
                    f"на канале {first['channel']} дважды"
                    if same_channel else
                    f"на {first['channel']} и {second['channel']}"
                )
                findings.append(_finding(
                    "same_fixture_different_time", "warning", second,
                    f"«{second['title']}» стоит как прямой эфир {where} в "
                    f"разное время: {_day(first['start'])} "
                    f"{_clock(first['start'])} и {_day(second['start'])} "
                    f"{_clock(second['start'])}. Матч начинается один раз: "
                    "одно из времён ошибочно либо это повтор.",
                    other_channel=first["channel"],
                    other_start_at=first["start"].isoformat(),
                    other_storage_id=first["storage_id"],
                    gap_minutes=gap,
                ))
    return findings


def find_schedule_anomalies(
    events: list[dict], *, now: datetime | None = None,
) -> list[dict]:
    """Return editor-facing findings for merged schedule entries.

    ``events`` is the list produced for the web UI: one entry per sporting
    event with a ``broadcasts`` list of per-channel slots.
    """
    reference = _parse(now) if now is not None else datetime.now(KZ_TIMEZONE)
    today = reference.date()
    rows: list[dict] = []
    for event in events or []:
        if isinstance(event, dict):
            rows.extend(_broadcasts(event))

    findings: list[dict] = []
    for row in rows:
        for check in (_check_duration, _check_test_label, _check_year):
            found = check(row)
            if found:
                findings.append(found)
        found = _check_date_swap(row, today)
        if found:
            findings.append(found)
    findings.extend(_check_channel_collisions(rows))
    findings.extend(_check_same_fixture(rows))

    unique: dict[str, dict] = {}
    for item in findings:
        unique.setdefault(item["key"], item)
    return sorted(
        unique.values(),
        key=lambda item: (
            LEVEL_ORDER.get(item["level"], 9), item["date"], item["time"],
            item["channel"], item["kind"],
        ),
    )


def summarize_anomalies(findings: list[dict]) -> dict:
    counts = {"error": 0, "warning": 0, "attention": 0}
    kinds: dict[str, int] = {}
    for item in findings:
        counts[item["level"]] = counts.get(item["level"], 0) + 1
        kinds[item["kind"]] = kinds.get(item["kind"], 0) + 1
    return {"total": len(findings), "levels": counts, "kinds": kinds}

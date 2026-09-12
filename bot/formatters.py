from __future__ import annotations

from datetime import datetime

from services.schedule_merge import group_simulcasts, unique_channels
from services.time_logic import KZ_TIMEZONE, MONTHS, get_event_status, get_time_window_text


def event_title(event: dict) -> str:
    return str(event.get("title") or event.get("raw_title") or "Без названия")


def display_title(event: dict) -> str:
    title = " ".join(event_title(event).split()).replace("Grand slam", "Grand Slam")
    sport = " ".join(str(event.get("sport") or "").split())
    tournament = " ".join(str(event.get("tournament") or "").split())
    if any(separator in title for separator in (" – ", " - ", " — ")):
        return title
    if tournament and title.casefold().startswith(tournament.casefold()):
        return title
    parts: list[str] = []
    for part in (sport, tournament, title):
        if part and not any(part.casefold() == existing.casefold() for existing in parts):
            parts.append(part)
    return ". ".join(parts) if parts else "Без названия"


def status_icon(event: dict) -> str:
    return {"live": "🔴", "upcoming": "🟡", "finished": "⚪"}.get(
        get_event_status(event), "⚪"
    )


def compact_event_line(event: dict) -> str:
    channel = str(event.get("channel") or "Канал не указан")
    return (
        f"{status_icon(event)} {get_time_window_text(event)} · "
        f"{display_title(event)} | {channel}"
    )


def schedule_blocks(events: list[dict]) -> list[str]:
    blocks: list[str] = []
    for group in group_simulcasts(events):
        if len(group) == 1:
            blocks.append(compact_event_line(group[0]))
            continue
        title = display_title(group[0])
        lines = [f"📡 {title}"]
        for event in group:
            channel = str(event.get("channel") or "Канал не указан")
            lines.append(f"{status_icon(event)} {get_time_window_text(event)} | {channel}")
        blocks.append("\n".join(lines))
    return blocks


def split_blocks(header: str, blocks: list[str], footer: str = "", limit: int = 3900) -> list[str]:
    if not blocks:
        return [f"{header}\n\nСобытий нет.{footer}"]
    messages: list[str] = []
    current = header
    for block in blocks:
        candidate = f"{current}\n\n{block}"
        if len(candidate) > limit and current != header:
            messages.append(current)
            current = f"{header}\n\n{block}"
        else:
            current = candidate
    if footer and len(current) + len(footer) <= limit:
        current += footer
    messages.append(current)
    return messages


def build_schedule_messages(events: list[dict]) -> list[str]:
    now = datetime.now(KZ_TIMEZONE)
    header = (
        "📅 SLP · Расписание\n"
        f"{now.day} {MONTHS[now.month]} {now.year} · "
        f"событий: {len(events)} · каналов: {len(unique_channels(events))}"
    )
    return split_blocks(header, schedule_blocks(events), "\n\n🔴 LIVE · 🟡 SOON · ⚪ OVER")


def build_live_messages(events: list[dict]) -> list[str]:
    now = datetime.now(KZ_TIMEZONE)
    header = f"🔴 Сейчас LIVE\n{now:%d.%m.%Y %H:%M} · UTC+5"
    if not events:
        return [f"{header}\n\nСейчас прямых трансляций нет."]
    return split_blocks(header, schedule_blocks(events))

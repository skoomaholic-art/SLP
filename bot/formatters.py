from __future__ import annotations

import re
from datetime import datetime

from services.schedule_merge import group_simulcasts, unique_channels
from services.time_logic import KZ_TIMEZONE, MONTHS, get_event_status, get_time_window_text


# Presentation-only normalization. Raw/provider values stay untouched for matching,
# reconciliation, exports and diagnostics; only Telegram-facing text is normalized.
_PHRASE_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("УЕФА Чемпиондар Лигасы", "Лига чемпионов УЕФА"),
    ("Чемпиондар Лигасы", "Лига чемпионов"),
    ("Қазақстан чемпионаты", "Чемпионат Казахстана"),
    ("Қазақстан Премьер-Лигасы", "Премьер-лига Казахстана"),
    ("Азия чемпионаты", "Чемпионат Азии"),
    ("Жалпы кезең", "Общий этап"),
    ("Тікелей эфир", "Прямая трансляция"),
    ("Grand Slam", "Большой шлем"),
    ("Формула 1", "Формула-1"),
)

_NAME_ALIASES: tuple[tuple[str, str], ...] = (
    ("шахтер", "Шахтёр"),
    ("шахтёр", "Шахтёр"),
    ("окжетпес", "Окжетпес"),
    ("кайрат", "Кайрат"),
    ("тобыл", "Тобол"),
    ("тобол", "Тобол"),
    ("ертис", "Иртыш"),
    ("иртыш", "Иртыш"),
    ("актобе", "Актобе"),
    ("жетису", "Жетысу"),
    ("жетысу", "Жетысу"),
    ("елимай", "Елимай"),
    ("кызылжар", "Кызылжар"),
    ("улытау", "Улытау"),
    ("туран", "Туран"),
    ("хан-тенгири", "Хан-Тенгри"),
    ("хан-тенгри", "Хан-Тенгри"),
)

_KAZAKH_TO_RUSSIAN = str.maketrans(
    {
        "Ә": "А",
        "ә": "а",
        "Ғ": "Г",
        "ғ": "г",
        "Қ": "К",
        "қ": "к",
        "Ң": "Н",
        "ң": "н",
        "Ө": "О",
        "ө": "о",
        "Ұ": "У",
        "ұ": "у",
        "Ү": "У",
        "ү": "у",
        "Һ": "Х",
        "һ": "х",
        "І": "И",
        "і": "и",
    }
)

_KEEP_UPPER = {
    "ATP",
    "ERC",
    "F1",
    "F2",
    "F3",
    "FIFA",
    "HD",
    "KHL",
    "LMB",
    "MMA",
    "PFL",
    "UFC",
    "UEFA",
    "WRC",
    "WTA",
    "КХЛ",
    "КПЛ",
    "ЛЧ",
    "НХЛ",
    "РПЛ",
    "УЕФА",
    "ФИФА",
}
_TOKEN_RE = re.compile(r"[A-Za-zА-Яа-яЁё]+(?:-[A-Za-zА-Яа-яЁё]+)*")


def _replace_ci(value: str, source: str, replacement: str) -> str:
    return re.sub(re.escape(source), replacement, value, flags=re.IGNORECASE)


def _normal_case_token(match: re.Match[str]) -> str:
    token = match.group(0)
    if token.upper() in _KEEP_UPPER:
        return token.upper()
    letters = [char for char in token if char.isalpha()]
    if len(letters) >= 2 and all(char.isupper() for char in letters):
        return token.capitalize()
    return token


def normalize_display_text(value: str) -> str:
    """Return a compact Russian-facing label without changing source data."""
    text = " ".join(str(value or "").replace("\xa0", " ").split())
    if not text:
        return ""

    for source, replacement in _PHRASE_REPLACEMENTS:
        text = _replace_ci(text, source, replacement)

    # Any still-untranslated Kazakh-specific letters are transliterated so the
    # user-facing label uses a consistent Russian Cyrillic alphabet.
    text = text.translate(_KAZAKH_TO_RUSSIAN)

    for source, replacement in _NAME_ALIASES:
        text = re.sub(
            rf"(?<![\w-]){re.escape(source)}(?![\w-])",
            replacement,
            text,
            flags=re.IGNORECASE,
        )

    text = _TOKEN_RE.sub(_normal_case_token, text)
    text = re.sub(r"\s+[–—-]\s+", " – ", text)
    text = re.sub(r"(?<=\w)[–—](?=\w)", " – ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def event_title(event: dict) -> str:
    return str(event.get("title") or event.get("raw_title") or "Без названия")


def display_title(event: dict) -> str:
    title = normalize_display_text(event_title(event))
    sport = normalize_display_text(str(event.get("sport") or ""))
    tournament = normalize_display_text(str(event.get("tournament") or ""))
    if any(separator in title for separator in (" – ", " — ", " - ")):
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


def build_schedule_messages(
    events: list[dict],
    *,
    total_count: int | None = None,
) -> list[str]:
    now = datetime.now(KZ_TIMEZONE)
    shown_count = len(events)
    effective_total = shown_count if total_count is None else max(int(total_count), shown_count)
    count_text = f"событий: {effective_total}"
    if effective_total > shown_count:
        count_text += f" · показано: {shown_count}"
    header = (
        "📅 SLP · Расписание\n"
        f"{now.day} {MONTHS[now.month]} {now.year} · "
        f"{count_text} · каналов в выдаче: {len(unique_channels(events))}"
    )
    footer = "\n\n🔴 LIVE · 🟡 SOON · ⚪ OVER"
    if effective_total > shown_count:
        footer += "\n📥 Полный горизонт расписания доступен через XLSX."
    return split_blocks(header, schedule_blocks(events), footer)


def build_live_messages(events: list[dict]) -> list[str]:
    now = datetime.now(KZ_TIMEZONE)
    header = f"🔴 Сейчас LIVE\n{now:%d.%m.%Y %H:%M} · UTC+5"
    if not events:
        return [f"{header}\n\nСейчас прямых трансляций нет."]
    return split_blocks(header, schedule_blocks(events))

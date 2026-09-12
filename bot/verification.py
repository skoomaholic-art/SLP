from __future__ import annotations

from bot.formatters import display_title
from services.time_logic import get_end_full_text, get_start_full_text


def build_verification_text(event: dict, verification: dict) -> str:
    found = bool(verification.get("found", False))
    difference = verification.get("difference_minutes")

    if verification.get("time_rejected", False):
        verdict = "⚠️ Внешнее время отброшено как ненадёжное"
    elif not found:
        verdict = "⚪ Надёжного внешнего подтверждения времени нет"
    elif difference is None:
        verdict = "🟡 Событие найдено, время требует ручной проверки"
    elif abs(difference) <= 10:
        verdict = "✅ Время подтверждено"
    elif abs(difference) <= 20:
        verdict = "🟡 Мягкое расхождение — нужна дополнительная проверка"
    else:
        verdict = "⚠️ Существенное расхождение времени"

    lines = [
        "🌐 Проверка события",
        display_title(event),
        "",
        f"📺 Канал: {event.get('channel') or 'не указан'}",
        f"🕐 Эфир: {get_start_full_text(event)}",
        f"🏁 Окончание эфира: {get_end_full_text(event)}",
        f"📡 Источник: {event.get('source') or 'не указан'}",
        "",
        verdict,
    ]

    external_time = verification.get("external_time_kz")
    if external_time:
        lines.append(f"Внешнее время: {external_time} UTC+5")
    if difference is not None:
        lines.append(f"Расхождение: {abs(int(difference))} мин.")

    lines.append(f"Подтвердили время: {verification.get('source_count', 0)} источник(а/ов)")
    lines.append(f"Релевантных результатов: {verification.get('matching_results_count', 0)}")

    sources = verification.get("sources") or []
    if sources:
        lines.extend(("", "Источники проверки:"))
        for source in sources[:5]:
            name = source.get("source_name") or source.get("domain") or "Источник"
            source_time = source.get("external_time_kz")
            suffix = f" · {source_time} UTC+5" if source_time else ""
            lines.append(f"• {name}{suffix}")

    lines.extend(("", "Эфирное время телеканала автоматически не переписывается."))
    return "\n".join(lines)

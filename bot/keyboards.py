from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


MAIN_KEYBOARD = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(text="📅 Расписание", callback_data="schedule"),
            InlineKeyboardButton(text="🔴 Сейчас LIVE", callback_data="live"),
        ],
        [
            InlineKeyboardButton(text="🌐 Проверить событие", callback_data="check"),
            InlineKeyboardButton(text="📊 Система", callback_data="status"),
        ],
        [
            InlineKeyboardButton(text="🔔 Уведомления", callback_data="notifications"),
            InlineKeyboardButton(text="📥 XLSX", callback_data="export_schedule"),
        ],
    ]
)


def event_check_keyboard(events: list[dict], *, limit: int = 20) -> InlineKeyboardMarkup:
    rows = []
    for index, event in enumerate(events[:limit]):
        time_text = str(event.get("time") or "--:--")
        title = str(event.get("title") or event.get("raw_title") or "Без названия")
        if len(title) > 42:
            title = title[:39].rstrip() + "…"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{time_text} · {title}",
                    callback_data=f"check_event:{index}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="↩️ Меню", callback_data="menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


BACK_TO_MENU = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="↩️ Меню", callback_data="menu")],
    ]
)

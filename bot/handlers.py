from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from agents.health import build_health_text
from bot.formatters import build_live_messages, build_schedule_messages
from bot.keyboards import BACK_TO_MENU, MAIN_KEYBOARD, event_check_keyboard
from bot.verification import build_verification_text
from config import Settings
from services.export_xlsx import build_schedule_xlsx
from services.schedule_service import ScheduleService
from services.schedule_watch import is_subscribed, set_subscription
from services.time_logic import KZ_TIMEZONE
from verifiers.web_search import verify_event


logger = logging.getLogger(__name__)
router = Router(name="slp")
_check_views: dict[int, list[dict]] = {}


def _admin_allowed(settings: Settings, user_id: int) -> bool:
    return settings.is_admin(user_id)


async def _send_messages(message: Message, chunks: list[str]) -> None:
    for chunk in chunks:
        await message.answer(chunk)


@router.message(Command("start"))
async def start_command(message: Message) -> None:
    await message.answer(
        "SLP · Skoomaholic Live Parser ✅\n\n"
        "Данные обновляются в фоне и сохраняются в SQLite. Выберите действие:",
        reply_markup=MAIN_KEYBOARD,
    )


@router.callback_query(F.data == "menu")
async def menu_callback(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.answer("Главное меню:", reply_markup=MAIN_KEYBOARD)


@router.message(Command("today"))
async def today_command(message: Message, schedule_service: ScheduleService) -> None:
    await _send_messages(
        message,
        build_schedule_messages(schedule_service.get_events()),
    )


@router.callback_query(F.data == "schedule")
async def schedule_callback(
    callback: CallbackQuery,
    schedule_service: ScheduleService,
) -> None:
    await callback.answer()
    await _send_messages(
        callback.message,
        build_schedule_messages(schedule_service.get_events()),
    )


@router.message(Command("live"))
async def live_command(message: Message, schedule_service: ScheduleService) -> None:
    await _send_messages(
        message,
        build_live_messages(schedule_service.get_live_events()),
    )


@router.callback_query(F.data == "live")
async def live_callback(
    callback: CallbackQuery,
    schedule_service: ScheduleService,
) -> None:
    await callback.answer()
    await _send_messages(
        callback.message,
        build_live_messages(schedule_service.get_live_events()),
    )


@router.message(Command("health", "status"))
async def health_command(
    message: Message,
    schedule_service: ScheduleService,
) -> None:
    await message.answer(build_health_text(schedule_service.database))


@router.callback_query(F.data == "status")
async def status_callback(
    callback: CallbackQuery,
    schedule_service: ScheduleService,
) -> None:
    await callback.answer()
    await callback.message.answer(
        build_health_text(schedule_service.database),
        reply_markup=BACK_TO_MENU,
    )


@router.message(Command("check"))
async def check_command(
    message: Message,
    schedule_service: ScheduleService,
) -> None:
    events = schedule_service.get_events()
    _check_views[message.from_user.id] = events
    if not events:
        await message.answer("Событий для проверки пока нет.")
        return
    await message.answer(
        "🌐 Выберите событие для независимой проверки:",
        reply_markup=event_check_keyboard(events),
    )


@router.callback_query(F.data == "check")
async def check_callback(
    callback: CallbackQuery,
    schedule_service: ScheduleService,
) -> None:
    await callback.answer()
    events = schedule_service.get_events()
    _check_views[callback.from_user.id] = events
    if not events:
        await callback.message.answer("Событий для проверки пока нет.")
        return
    await callback.message.answer(
        "🌐 Выберите событие для независимой проверки:",
        reply_markup=event_check_keyboard(events),
    )


@router.callback_query(F.data.startswith("check_event:"))
async def check_event_callback(callback: CallbackQuery) -> None:
    await callback.answer("Проверяю…")
    events = _check_views.get(callback.from_user.id, [])
    try:
        index = int(str(callback.data).split(":", 1)[1])
        event = events[index]
    except (ValueError, IndexError):
        await callback.message.answer(
            "Список устарел. Откройте «Проверить событие» заново."
        )
        return

    loading = await callback.message.answer(
        "⏳ Сверяю событие с внешними источниками…"
    )
    try:
        verification = await asyncio.to_thread(verify_event, event)
        await loading.edit_text(
            build_verification_text(event, verification),
            reply_markup=BACK_TO_MENU,
        )
    except Exception as error:
        logger.exception(
            "internet verification failed source=%s date=%s time=%s title=%r",
            event.get("source"),
            event.get("date"),
            event.get("time"),
            event.get("title") or event.get("raw_title"),
        )
        await loading.edit_text(
            f"❌ Интернет-проверка недоступна: {type(error).__name__}",
            reply_markup=BACK_TO_MENU,
        )


@router.callback_query(F.data == "notifications")
async def notifications_callback(callback: CallbackQuery) -> None:
    chat_id = callback.message.chat.id
    enabled = not is_subscribed(chat_id)
    set_subscription(chat_id, enabled)
    await callback.answer("Включены" if enabled else "Выключены")
    text = (
        "🔔 Уведомления об изменениях расписания включены."
        if enabled
        else "🔕 Уведомления выключены."
    )
    await callback.message.answer(text, reply_markup=BACK_TO_MENU)


@router.callback_query(F.data == "export_schedule")
async def export_callback(
    callback: CallbackQuery,
    schedule_service: ScheduleService,
) -> None:
    await callback.answer()
    events = schedule_service.get_events()
    payload = build_schedule_xlsx(events)
    filename = f"SLP_{datetime.now(KZ_TIMEZONE):%Y-%m-%d_%H-%M}.xlsx"
    await callback.message.answer_document(
        BufferedInputFile(payload, filename=filename),
        caption=f"📥 Событий: {len(events)}",
    )


@router.message(Command("refresh"))
async def refresh_command(
    message: Message,
    schedule_service: ScheduleService,
    settings: Settings,
) -> None:
    if not _admin_allowed(settings, message.from_user.id):
        await message.answer("⛔ Команда доступна администратору.")
        return
    loading = await message.answer("🔄 Обновляю источники…")
    result = await schedule_service.refresh()
    status = "с ошибками" if result.source_errors else "успешно"
    await loading.edit_text(
        f"✅ Обновление завершено {status}.\n"
        f"Run: {result.run_id}\n"
        f"Событий: {len(result.events)}"
    )


@router.message(Command("errors"))
async def errors_command(
    message: Message,
    schedule_service: ScheduleService,
    settings: Settings,
) -> None:
    if not _admin_allowed(settings, message.from_user.id):
        await message.answer("⛔ Команда доступна администратору.")
        return
    incidents = schedule_service.database.unresolved_incidents(limit=10)
    if not incidents:
        await message.answer("✅ Открытых инцидентов нет.")
        return
    lines = ["⚠️ Последние инциденты"]
    for item in incidents:
        lines.append(
            f"• {item.get('source')} · {item.get('incident_type')} · "
            f"{item.get('message')}"
        )
    await message.answer("\n".join(lines))

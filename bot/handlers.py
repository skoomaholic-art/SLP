from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from agents.health import build_health_text
from bot.formatters import build_live_messages, build_schedule_messages
from bot.keyboards import (
    BACK_TO_MENU,
    MAIN_KEYBOARD,
    event_check_keyboard,
    schedule_navigation_keyboard,
)
from bot.verification import build_verification_text
from config import Settings
from services.export_xlsx import (
    EXPORT_MODE_FINISH_UPCOMING,
    EXPORT_MODE_LIVE,
    build_schedule_xlsx,
    filter_export_events,
)
from services.schedule_service import ScheduleService
from services.schedule_watch import is_subscribed, set_subscription
from services.time_logic import KZ_TIMEZONE
from verifiers.web_search import verify_event


logger = logging.getLogger(__name__)
router = Router(name="slp")
_check_views: dict[int, list[dict]] = {}
TELEGRAM_SCHEDULE_EVENT_LIMIT = 60


def _admin_allowed(settings: Settings, user_id: int) -> bool:
    return settings.is_admin(user_id)


def _schedule_view_events(
    schedule_service: ScheduleService,
    *,
    limit: int | None = TELEGRAM_SCHEDULE_EVENT_LIMIT,
    target_date: date | datetime | str | None = None,
) -> tuple[list[dict], int]:
    if target_date is None:
        events = schedule_service.get_events()
    else:
        events = schedule_service.get_events(target_date=target_date)
    if limit is None:
        return events, len(events)
    safe_limit = max(int(limit), 0)
    return events[:safe_limit], len(events)


async def _send_messages(message: Message, chunks: list[str], *, reply_markup=None) -> None:
    logger.info("telegram send chunks=%d", len(chunks))
    for index, chunk in enumerate(chunks):
        markup = reply_markup if index == len(chunks) - 1 else None
        await message.answer(chunk, reply_markup=markup)


async def _send_schedule_view(
    message: Message,
    schedule_service: ScheduleService,
    *,
    target_date: date | datetime | str | None = None,
) -> None:
    now = datetime.now(KZ_TIMEZONE)
    if target_date is None:
        selected_date = now.date()
    elif isinstance(target_date, datetime):
        selected_date = (
            target_date.astimezone(KZ_TIMEZONE).date()
            if target_date.tzinfo
            else target_date.date()
        )
    elif isinstance(target_date, date):
        selected_date = target_date
    else:
        selected_date = date.fromisoformat(str(target_date))

    events, total_count = _schedule_view_events(
        schedule_service,
        target_date=selected_date,
        limit=None,
    )
    dates = schedule_service.get_schedule_dates(now=now)
    first_date = dates[0] if dates else selected_date
    last_date = dates[-1] if dates else selected_date
    chunks = build_schedule_messages(
        events,
        total_count=total_count,
        target_date=selected_date,
    )
    logger.info(
        "telegram schedule view date=%s total=%d shown=%d chunks=%d last_date=%s",
        selected_date.isoformat(),
        total_count,
        len(events),
        len(chunks),
        last_date.isoformat(),
    )
    await _send_messages(
        message,
        chunks,
        reply_markup=schedule_navigation_keyboard(
            selected_date,
            first_date=first_date,
            last_date=last_date,
        ),
    )


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
    await _send_schedule_view(message, schedule_service)


@router.callback_query(F.data == "schedule")
async def schedule_callback(
    callback: CallbackQuery,
    schedule_service: ScheduleService,
) -> None:
    await callback.answer()
    await _send_schedule_view(callback.message, schedule_service)


@router.callback_query(F.data.startswith("schedule:"))
async def schedule_date_callback(
    callback: CallbackQuery,
    schedule_service: ScheduleService,
) -> None:
    await callback.answer()
    try:
        selected_date = date.fromisoformat(str(callback.data).split(":", 1)[1])
    except (TypeError, ValueError):
        await callback.message.answer(
            "Список устарел. Откройте «Расписание» заново.",
            reply_markup=MAIN_KEYBOARD,
        )
        return
    await _send_schedule_view(
        callback.message,
        schedule_service,
        target_date=selected_date,
    )


@router.message(Command("live"))
async def live_command(message: Message, schedule_service: ScheduleService) -> None:
    events = schedule_service.get_live_events()
    logger.info("telegram live view events=%d", len(events))
    await _send_messages(
        message,
        build_live_messages(events),
        reply_markup=MAIN_KEYBOARD,
    )


@router.callback_query(F.data == "live")
async def live_callback(
    callback: CallbackQuery,
    schedule_service: ScheduleService,
) -> None:
    await callback.answer()
    events = schedule_service.get_live_events()
    logger.info("telegram live callback events=%d", len(events))
    await _send_messages(
        callback.message,
        build_live_messages(events),
        reply_markup=MAIN_KEYBOARD,
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
    now = datetime.now(KZ_TIMEZONE)
    events = schedule_service.get_events(now=now)
    stamp = f"{now:%Y-%m-%d_%H-%M}"

    live_events = filter_export_events(events, mode=EXPORT_MODE_LIVE, now=now)
    live_payload = build_schedule_xlsx(events, mode=EXPORT_MODE_LIVE, now=now)
    await callback.message.answer_document(
        BufferedInputFile(
            live_payload,
            filename=f"SLP_{stamp}_(live).xlsx",
        ),
        caption=f"📥 LIVE сейчас: {len(live_events)}",
    )

    other_events = filter_export_events(
        events,
        mode=EXPORT_MODE_FINISH_UPCOMING,
        now=now,
    )
    other_payload = build_schedule_xlsx(
        events,
        mode=EXPORT_MODE_FINISH_UPCOMING,
        now=now,
    )
    await callback.message.answer_document(
        BufferedInputFile(
            other_payload,
            filename=f"SLP_{stamp}_(finish-upcoming).xlsx",
        ),
        caption=f"📥 FINISHED + UPCOMING: {len(other_events)}",
        reply_markup=MAIN_KEYBOARD,
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

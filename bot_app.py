from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from services.event_status import is_live_now, log_live_candidates
from services.event_store import get_store_diagnostics
from services.schedule_loader import refresh_schedule
from services.schedule_merge import group_simulcasts, merge_source_schedules, unique_channels
from services.schedule_watch import (
    build_change_messages,
    build_schedule_snapshot,
    diff_schedule_snapshots,
    get_previous_snapshot,
    get_subscribers,
    is_subscribed,
    set_subscription,
    update_snapshot,
)
from services.time_logic import (
    KZ_TIMEZONE,
    get_end_full_text,
    get_event_status,
    get_start_full_text,
    get_time_window_text,
)
from verifiers.web_search import verify_event


logging.basicConfig(
    level=os.getenv("SLP_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
LOGGER = logging.getLogger("slp.bot")

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не найден в переменных окружения")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

verification_cache: dict[str, dict] = {}
verification_tasks: dict[str, asyncio.Task] = {}
view_cache: dict[tuple[int, str], list[dict]] = {}
schedule_cache = {
    "time": 0.0,
    "events": [],
    "all_events": [],
    "source_errors": [],
    "horizon": None,
}

SCHEDULE_CACHE_TTL = 180
NOTIFICATION_CHECK_INTERVAL = 180
verification_semaphore = asyncio.Semaphore(2)

MONTHS = {
    1: "января", 2: "февраля", 3: "марта", 4: "апреля",
    5: "мая", 6: "июня", 7: "июля", 8: "августа",
    9: "сентября", 10: "октября", 11: "ноября", 12: "декабря",
}

main_keyboard = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(text="📅 Расписание", callback_data="schedule"),
            InlineKeyboardButton(text="🔴 LIVE", callback_data="live"),
        ],
        [
            InlineKeyboardButton(text="🔔 Уведомления", callback_data="notifications"),
            InlineKeyboardButton(text="📊 Статус", callback_data="status"),
        ],
        [InlineKeyboardButton(text="📥 Выгрузить", callback_data="export_schedule")],
    ]
)


def get_event_title(event: dict) -> str:
    return str(event.get("title") or event.get("raw_title") or "Без названия")


def _normalize_compact_part(value: object) -> str:
    text = str(value or "").strip()
    text = text.replace("Grand slam", "Grand Slam")
    text = text.replace(". Женщины", ", женщины")
    return " ".join(text.split())


def _looks_like_match_title(title: str) -> bool:
    return any(separator in title for separator in (" – ", " - ", " — "))


def get_schedule_display_title(event: dict) -> str:
    title = _normalize_compact_part(get_event_title(event))
    sport = _normalize_compact_part(event.get("sport"))
    tournament = _normalize_compact_part(event.get("tournament"))

    if _looks_like_match_title(title):
        return title
    if tournament and title.casefold().startswith(tournament.casefold()):
        return title

    parts: list[str] = []
    for part in (sport, tournament, title):
        if part and not any(part.casefold() == item.casefold() for item in parts):
            parts.append(part)
    return ". ".join(parts) if parts else "Без названия"


def get_event_id(event: dict) -> str:
    return str(event.get("event_key") or "")


def is_user_event(event: dict) -> bool:
    title = get_event_title(event).casefold()
    if (
        any(marker in title for marker in (
            "студийная программа", "студиялық бағдарлама",
            "перед матчем", "матч қарсаңында",
        ))
        and not str(event.get("sport") or "").strip()
        and not str(event.get("tournament") or "").strip()
    ):
        return False
    return True


def get_status_icon(event: dict) -> str:
    status = get_event_status(event)
    if status == "live_now":
        return "🔴"
    if status == "upcoming":
        return "🟡"
    if status == "on_air":
        return "🔵"
    return "⚪"


def get_status_name(event: dict) -> str:
    return {
        "live_now": "LIVE",
        "upcoming": "SOON",
        "on_air": "ON AIR",
        "finished": "OVER",
    }[get_event_status(event)]


def get_broadcast_end_detail(event: dict) -> str:
    end_text = get_end_full_text(event)
    if event.get("end_time") is not None or (
        event.get("estimated_broadcast_end_date")
        and event.get("estimated_broadcast_end")
    ):
        return end_text
    return f"≈ {end_text} (оценка по виду спорта)"


def get_live_evidence_text(event: dict) -> str:
    source = event.get("source", "")
    raw_title = str(event.get("raw_title") or "").upper()
    if source == "sportplus" and any(
        marker in raw_title
        for marker in ("ПРЯМАЯ ТРАНСЛЯЦИЯ", "ПРЯМОЙ ЭФИР", "ТІКЕЛЕЙ ЭФИР", "ТIКЕЛЕЙ ЭФИР")
    ):
        return "официальная пометка прямого эфира Sport+"
    if source == "qazsport":
        return "LIVE-пометка в расписании Qazsport"
    return "официальный LIVE-признак источника"


def get_accuracy(event: dict) -> dict:
    verification = verification_cache.get(get_event_id(event))
    if not verification:
        return {"text": "Нет данных о времени", "difference": None, "needs_attention": False}
    if verification.get("time_rejected", False):
        return {
            "text": "Нуждается в проверке: внешнее время отброшено",
            "difference": abs(verification.get("rejected_difference_minutes", 0)),
            "needs_attention": True,
        }
    if not verification.get("found", False) or verification.get("difference_minutes") is None:
        return {"text": "Нет данных о времени", "difference": None, "needs_attention": False}
    difference = abs(verification["difference_minutes"])
    if difference <= 10:
        return {"text": "Время совпадает", "difference": difference, "needs_attention": False}
    if difference <= 20:
        return {"text": "Событие требуется проверить", "difference": difference, "needs_attention": True}
    return {"text": "Есть расхождение по времени", "difference": difference, "needs_attention": True}


def get_schedule_status_badge(event: dict) -> str:
    attention = "⚠️" if get_accuracy(event).get("needs_attention") else ""
    return f"{get_status_icon(event)}{attention}"


def compact_event_line(event: dict) -> str:
    channel = event.get("channel") or "Канал не указан"
    return (
        f"{get_schedule_status_badge(event)} {get_time_window_text(event)} · "
        f"{get_schedule_display_title(event)} | {channel}"
    )


def compact_simulcast_block(group: list[dict]) -> str:
    if len(group) == 1:
        return compact_event_line(group[0])
    title = get_schedule_display_title(group[0])
    lines = [f"📡 {title}"]
    for event in group:
        channel = event.get("channel") or "Канал не указан"
        lines.append(
            f"{get_schedule_status_badge(event)} {get_time_window_text(event)} · {title} | {channel}"
        )
    return "\n".join(lines)


def build_schedule_text(events: list[dict]) -> str:
    now = datetime.now(KZ_TIMEZONE)
    channels = unique_channels(events)
    count = len(channels)
    label = "1 канал" if count == 1 else f"{count} канала" if 2 <= count <= 4 else f"{count} каналов"
    horizon = schedule_cache.get("horizon")
    horizon_text = f" · до {horizon.day} {MONTHS[horizon.month]}" if horizon else ""
    groups = group_simulcasts(events)
    body = "\n\n".join(compact_simulcast_block(group) for group in groups) if groups else "Событий нет."
    return (
        f"📅 Расписание\n{now.day} {MONTHS[now.month]} {now.year} · {label}{horizon_text}\n\n"
        f"{body}\n\nСтатус: 🔴 LIVE · 🟡 SOON · ⚪ OVER · ⚠️ Нуждается в проверке"
    )


def build_live_text(events: list[dict]) -> str:
    now = datetime.now(KZ_TIMEZONE)
    header = f"🔴 LIVE\nСейчас: {now.strftime('%H:%M')} (Asia/Almaty, UTC+5)"
    groups = group_simulcasts(events)
    if not groups:
        return f"{header}\n\nСейчас прямых трансляций нет."
    return f"{header}\n\n" + "\n\n".join(compact_simulcast_block(group) for group in groups)


def build_schedule_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔎 Подробнее", callback_data="details_schedule")],
        [InlineKeyboardButton(text="📥 Выгрузить", callback_data="export_schedule")],
    ])


def build_live_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔎 Подробнее", callback_data="details_live")]
    ])


async def load_schedule_events(*, force_refresh: bool = False, return_errors: bool = False):
    now_timestamp = time.time()
    if (
        not force_refresh
        and schedule_cache["events"]
        and now_timestamp - schedule_cache["time"] < SCHEDULE_CACHE_TTL
    ):
        result = schedule_cache["events"]
        errors = list(schedule_cache["source_errors"])
        return (result, errors) if return_errors else result

    all_events, errors, horizon = await refresh_schedule()
    live_broadcasts = [
        event for event in all_events
        if bool(event.get("is_live_broadcast", False)) and is_user_event(event)
    ]
    # Keep using the existing merge service as the single dedupe/sort layer.
    events = merge_source_schedules(live_broadcasts)
    schedule_cache.update(
        time=now_timestamp,
        events=events,
        all_events=all_events,
        source_errors=list(errors),
        horizon=horizon,
    )
    return (events, errors) if return_errors else events


async def run_verification(event: dict) -> dict:
    event_id = get_event_id(event)
    async with verification_semaphore:
        try:
            result = await asyncio.to_thread(verify_event, event)
        except Exception as error:
            LOGGER.exception("OpenSERP failed for %s", event_id, exc_info=error)
            result = {
                "found": False, "search_results_count": 0,
                "matching_results_count": 0, "source_count": 0,
                "total_weight": 0, "sources": [],
            }
    verification_cache[event_id] = result
    return result


async def ensure_event_verification(event: dict) -> dict:
    event_id = get_event_id(event)
    if event_id in verification_cache:
        return verification_cache[event_id]
    task = verification_tasks.get(event_id)
    if task:
        return await task
    task = asyncio.create_task(run_verification(event))
    verification_tasks[event_id] = task
    try:
        return await task
    finally:
        if verification_tasks.get(event_id) is task:
            verification_tasks.pop(event_id, None)


async def verify_events(events: list[dict]) -> None:
    await asyncio.gather(*(ensure_event_verification(event) for event in events), return_exceptions=True)


async def background_verify_and_refresh(events: list[dict], message: Message, view_type: str) -> None:
    await verify_events(events)
    try:
        if view_type == "schedule":
            text, keyboard = build_schedule_text(events), build_schedule_keyboard()
        else:
            current = [event for event in events if is_live_now(event)]
            text, keyboard = build_live_text(current), build_live_keyboard()
        await message.edit_text(text, reply_markup=keyboard)
    except Exception as error:
        LOGGER.warning("Telegram background edit failed: %r", error)


async def send_schedule_changes(changes: list[dict]) -> None:
    messages = build_change_messages(changes)
    for chat_id in get_subscribers():
        for message_text in messages:
            try:
                await bot.send_message(chat_id, message_text)
            except Exception as error:
                LOGGER.warning("Notification failed for %s: %r", chat_id, error)


async def check_schedule_changes_once() -> list[dict]:
    events, source_errors = await load_schedule_events(force_refresh=True, return_errors=True)
    if source_errors:
        LOGGER.warning("Snapshot diff skipped due to source errors: %s", "; ".join(source_errors))
        return []
    current = build_schedule_snapshot(events)
    previous = get_previous_snapshot()
    if previous is None:
        update_snapshot(current)
        return []
    changes = diff_schedule_snapshots(previous, current)
    update_snapshot(current)
    if changes:
        await send_schedule_changes(changes)
    return changes


async def notification_watch_loop() -> None:
    while True:
        try:
            await check_schedule_changes_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("Schedule watcher failed")
        await asyncio.sleep(NOTIFICATION_CHECK_INTERVAL)


def build_source_text(verification: dict) -> str:
    lines: list[str] = []
    for source in verification.get("sources", [])[:3]:
        name = source.get("source_name", "Источник")
        external_time = source.get("external_time_kz", "")
        lines.append(f"• {name} · {external_time} UTC+5" if external_time else f"• {name}")
    return "\nИсточники:\n" + "\n".join(lines) if lines else ""


def build_detailed_event(event: dict, number: int) -> str:
    verification = verification_cache.get(get_event_id(event), {})
    accuracy = get_accuracy(event)
    lines = [
        f"{number}. {get_event_title(event)}",
        f"📌 Статус: {get_status_icon(event)} {get_status_name(event)}",
        f"🕐 Начало эфира: {get_start_full_text(event)}",
        f"🏁 Окончание эфира: {get_broadcast_end_detail(event)}",
        f"🏅 Вид спорта: {event.get('sport') or 'не определён'}",
        f"🏆 Турнир: {event.get('tournament') or 'не определён'}",
        f"📺 Канал: {event.get('channel') or 'не указан'}",
        f"📡 Основание LIVE: {get_live_evidence_text(event)}",
        "", "🌐 Интернет-проверка", accuracy["text"],
    ]
    if verification.get("found", False):
        difference = verification.get("difference_minutes")
        difference_text = "не определено" if difference is None else f"{abs(difference)} мин."
        confidence = verification.get("confidence", {}).get("label", "не определён")
        lines.extend([
            f"🕐 Внешнее время: {verification.get('external_time_kz', '?')} (UTC+5)",
            f"⏱ Расхождение: {difference_text}",
            f"⚖️ Вес доверия: {verification.get('total_weight', 0)}",
            f"📊 Уровень доверия: {confidence}",
            f"📚 Время подтвердили: {verification.get('source_count', 0)} независимых источника(ов)",
            f"🔎 Найдено результатов: {verification.get('search_results_count', 0)}",
            f"🎯 Релевантных результатов: {verification.get('matching_results_count', 0)}",
        ])
        source_text = build_source_text(verification)
        if source_text:
            lines.append(source_text)
    else:
        lines.extend([
            "⚖️ Вес доверия: 0",
            "📊 Уровень доверия: Не рассчитывается",
            "📚 Время подтвердили: 0 независимых источников",
            f"🔎 Найдено результатов: {verification.get('search_results_count', 0)}",
            f"🎯 Релевантных результатов: {verification.get('matching_results_count', 0)}",
        ])
        if verification.get("time_rejected", False):
            lines.append("ℹ️ Найденное внешнее время отброшено: расхождение больше 30 минут.")
    return "\n".join(lines)


def split_messages(blocks: list[str], title: str, limit: int = 3900) -> list[str]:
    messages: list[str] = []
    current = title
    for block in blocks:
        candidate = f"{current}\n\n{block}"
        if len(candidate) > limit:
            messages.append(current)
            current = f"{title}\n\n{block}"
        else:
            current = candidate
    if current:
        messages.append(current)
    return messages


async def show_details(callback: CallbackQuery, events: list[dict], title: str) -> None:
    if not events:
        await callback.message.answer("Событий нет.")
        return
    loading = await callback.message.answer("⏳ Формирую подробный список...")
    await verify_events(events)
    messages = split_messages(
        [build_detailed_event(event, number) for number, event in enumerate(events, start=1)],
        title,
    )
    await loading.edit_text(messages[0])
    for text in messages[1:]:
        await callback.message.answer(text)


@dp.message(Command("start"))
async def start_command(message: Message) -> None:
    await message.answer(
        "SLP [Skoomaholic Live Parser] запущен ✅\n\nВыберите действие:",
        reply_markup=main_keyboard,
    )


@dp.callback_query(F.data == "schedule")
async def schedule_callback(callback: CallbackQuery) -> None:
    await callback.answer()
    loading = await callback.message.answer("⏳ Загружаю расписание...")
    try:
        events = await load_schedule_events()
        view_cache[(callback.from_user.id, "schedule")] = events
        await loading.edit_text(build_schedule_text(events), reply_markup=build_schedule_keyboard())
        asyncio.create_task(background_verify_and_refresh(events, loading, "schedule"))
    except Exception:
        LOGGER.exception("Schedule callback failed")
        await loading.edit_text("❌ Не удалось загрузить расписание.")


@dp.callback_query(F.data == "live")
async def live_callback(callback: CallbackQuery) -> None:
    await callback.answer()
    loading = await callback.message.answer("⏳ Загружаю LIVE...")
    try:
        events = await load_schedule_events()
        now = datetime.now(KZ_TIMEZONE)
        log_live_candidates(events, now=now, logger=LOGGER)
        live_events = [event for event in events if is_live_now(event, now)]
        view_cache[(callback.from_user.id, "live")] = live_events
        await loading.edit_text(build_live_text(live_events), reply_markup=build_live_keyboard())
        asyncio.create_task(background_verify_and_refresh(live_events, loading, "live"))
    except Exception:
        LOGGER.exception("LIVE callback failed")
        await loading.edit_text("❌ Не удалось загрузить LIVE.")


@dp.callback_query(F.data == "details_schedule")
async def details_schedule_callback(callback: CallbackQuery) -> None:
    await callback.answer()
    await show_details(
        callback,
        view_cache.get((callback.from_user.id, "schedule"), []),
        "🔎 Подробное расписание",
    )


@dp.callback_query(F.data == "details_live")
async def details_live_callback(callback: CallbackQuery) -> None:
    await callback.answer()
    await show_details(
        callback,
        view_cache.get((callback.from_user.id, "live"), []),
        "🔎 Подробно LIVE",
    )


@dp.callback_query(F.data == "notifications")
async def notifications_callback(callback: CallbackQuery) -> None:
    chat_id = callback.message.chat.id
    enabled = not is_subscribed(chat_id)
    set_subscription(chat_id, enabled)
    await callback.answer("Уведомления включены" if enabled else "Уведомления выключены")
    await callback.message.answer(
        "🔔 Уведомления включены." if enabled else "🔕 Уведомления выключены."
    )


@dp.callback_query(F.data == "export_schedule")
async def export_schedule_callback(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.answer("📥 Выгрузка расписания\n\nXLSX пока не подключён.")


@dp.callback_query(F.data == "status")
async def status_callback(callback: CallbackQuery) -> None:
    await callback.answer()
    store = get_store_diagnostics()
    horizon = schedule_cache.get("horizon")
    await callback.message.answer(
        "📊 Статус SLP\n\n"
        "✅ Qazsport подключён\n"
        "✅ Sport+ Qazaqstan подключён\n"
        "✅ source LIVE отделён от live_now\n"
        "✅ Все runtime datetime timezone-aware (Asia/Almaty)\n"
        "✅ SQLite-кэш событий подключён\n"
        "✅ LIVE-кандидаты логируются с причиной включения/исключения\n"
        f"📦 БД: {store['path']}\n"
        f"Событий в БД: {store['total']} · source LIVE: {store['live_marked']}\n"
        f"Последнее обновление: {store['last_updated'] or 'нет данных'}\n"
        f"Горизонт: {horizon.isoformat() if horizon else 'ещё не рассчитан'}\n"
        f"Подписчиков: {len(get_subscribers())}"
    )


async def main() -> None:
    LOGGER.info("SLP started; notification interval=%ss", NOTIFICATION_CHECK_INTERVAL)
    watch_task = asyncio.create_task(notification_watch_loop())
    try:
        await dp.start_polling(bot)
    finally:
        watch_task.cancel()
        await asyncio.gather(watch_task, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())

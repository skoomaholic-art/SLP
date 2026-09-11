import asyncio
import hashlib
import os
import time
from datetime import datetime

from aiogram import Bot, Dispatcher, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from services.event_filter import is_user_event
from services.live_evidence import event_is_official_live
from services.schedule_merge import (
    group_simulcasts,
    merge_source_schedules,
    unique_channels,
)
from services.schedule_watch import (
    build_schedule_snapshot,
    diff_schedule_snapshots,
    format_schedule_change,
    get_previous_snapshot,
    get_subscribers,
    is_subscribed,
    set_subscription,
    stable_event_identity,
    update_snapshot,
)
from services.source_loader import load_source_schedules
from services.time_logic import (
    KZ_TIMEZONE,
    MONTHS,
    get_end_full_text,
    get_event_status,
    get_start_full_text,
    get_time_window_text,
)
from services.xlsx_export import build_schedule_workbook
from verifiers.web_search import verify_event


BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не найден в переменных окружения Codespaces")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# =========================================================
# КЭШ
# =========================================================

verification_cache = {}
verification_cache_time = {}
verification_tasks = {}

schedule_cache = {
    "time": 0,
    "events": [],
    "source_errors": [],
}

SCHEDULE_CACHE_TTL = 180
VERIFICATION_CACHE_TTL = 15 * 60
VERIFICATION_ERROR_CACHE_TTL = 60
NOTIFICATION_CHECK_INTERVAL = 180
verification_semaphore = asyncio.Semaphore(2)


# =========================================================
# ГЛАВНОЕ МЕНЮ И НАВИГАЦИЯ
# =========================================================

def _notification_button(chat_id=None):
    enabled = bool(chat_id is not None and is_subscribed(chat_id))
    return InlineKeyboardButton(
        text=(
            "🔕 Выключить уведомления"
            if enabled
            else "🔔 Включить уведомления"
        ),
        callback_data="notifications",
    )


def build_main_keyboard(chat_id=None):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📅 Расписание", callback_data="schedule"),
                InlineKeyboardButton(text="🔴 LIVE", callback_data="live"),
            ],
            [
                InlineKeyboardButton(text="📥 XLSX", callback_data="export_schedule"),
                InlineKeyboardButton(text="📊 Статус", callback_data="status"),
            ],
            [_notification_button(chat_id)],
        ]
    )


def build_aux_keyboard(chat_id=None):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📅 Расписание", callback_data="schedule"),
                InlineKeyboardButton(text="🔴 LIVE", callback_data="live"),
            ],
            [
                InlineKeyboardButton(text="📥 XLSX", callback_data="export_schedule"),
                InlineKeyboardButton(text="📊 Статус", callback_data="status"),
            ],
            [_notification_button(chat_id)],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu")],
        ]
    )


# =========================================================
# СОБЫТИЕ
# =========================================================

def get_event_title(event):
    return (
        event.get("title")
        or event.get("raw_title")
        or "Без названия"
    )


def _normalize_compact_part(value):
    text = str(value or "").strip()
    if not text:
        return ""

    text = text.replace("Grand slam", "Grand Slam")
    text = text.replace(". Женщины", ", женщины")
    return " ".join(text.split())


def _looks_like_match_title(title):
    text = _normalize_compact_part(title)
    return any(separator in text for separator in (" – ", " - ", " — "))


def get_schedule_display_title(event):
    """Короткое, но самодостаточное название для списка расписания."""
    title = _normalize_compact_part(get_event_title(event))
    sport = _normalize_compact_part(event.get("sport"))
    tournament = _normalize_compact_part(event.get("tournament"))

    # Для матчей пара участников сама по себе является понятным названием.
    if _looks_like_match_title(title):
        return title

    # Если адаптер уже собрал полноценное название (например,
    # "Сезон конных скачек 2026. Алматы"), не раздуваем строку.
    if tournament and title.casefold().startswith(tournament.casefold()):
        return title

    parts = []
    for part in (sport, tournament, title):
        if not part:
            continue
        if any(part.casefold() == existing.casefold() for existing in parts):
            continue
        parts.append(part)

    return ". ".join(parts) if parts else "Без названия"


def get_event_id(event):
    raw_title = (
        event.get("raw_title")
        or event.get("title")
        or ""
    )

    value = (
        f"{event.get('date', '')}|"
        f"{event.get('time', '')}|"
        f"{event.get('channel', '')}|"
        f"{raw_title}"
    )

    return hashlib.sha1(
        value.encode("utf-8")
    ).hexdigest()[:12]




# =========================================================
# СТАТУС ТРАНСЛЯЦИИ
# ВАЖНО: статус берётся ТОЛЬКО из эфирного расписания.
# Интернет-проверка не может менять LIVE / SOON / OVER.
# =========================================================

def is_confirmed_direct_event(event):
    return event_is_official_live(event)


def get_confirmed_live_events(events):
    return [
        event for event in events
        if is_confirmed_direct_event(event)
        and get_event_status(event) == "live"
    ]


def get_status_icon(event):
    status = get_event_status(event)

    if is_confirmed_direct_event(event):
        if status == "live":
            return "🔴"
        if status == "upcoming":
            return "🟡"
        return "⚪"

    # Обычная программа EPG: мы знаем, что она стоит в эфирной сетке,
    # но не имеем права объявлять её прямой трансляцией без LIVE evidence.
    if status == "finished":
        return "⚪"
    return "📺"


def get_status_name(event):
    status = get_event_status(event)

    if is_confirmed_direct_event(event):
        if status == "live":
            return "LIVE"
        if status == "upcoming":
            return "SOON"
        return "OVER"

    if status == "live":
        return "ON AIR"
    if status == "upcoming":
        return "SCHEDULED"
    return "OVER"


def get_broadcast_end_detail(event):
    end_text = get_end_full_text(event)

    if (
        event.get("estimated_broadcast_end_date")
        and event.get("estimated_broadcast_end")
    ):
        return end_text

    return f"≈ {end_text} (оценка по виду спорта)"


def get_live_evidence_text(event):
    method = str(event.get("live_evidence_method") or "none")
    value = str(event.get("live_evidence_value") or "").strip()
    confidence = str(event.get("live_evidence_confidence") or "unknown")
    labels = {
        "official_live_text": "официальная LIVE-пометка источника",
        "official_live_asset": "официальный LIVE asset события",
        "official_current_live_banner": "официальный блок «сейчас в эфире»",
        "provider_live_text": "LIVE-пометка в EPG провайдера",
        "provider_live_asset": "LIVE asset в EPG провайдера",
    }
    label = labels.get(method, "LIVE evidence не определено")
    suffix = f" · {value}" if value else ""
    return f"{label}{suffix} · confidence={confidence}"


# =========================================================
# ПРОВЕРКА ТОЧНОСТИ
# =========================================================

def get_accuracy(event):
    verification = get_cached_verification(get_event_id(event))

    if not verification:
        return {
            "text": "Нет данных о времени",
            "difference": None,
            "needs_attention": False,
        }

    if verification.get("verification_error"):
        return {
            "text": "Интернет-проверка временно недоступна",
            "difference": None,
            "needs_attention": False,
        }

    if verification.get("time_rejected", False):
        return {
            "text": "Нуждается в проверке: внешнее время отброшено",
            "difference": abs(
                verification.get("rejected_difference_minutes", 0)
            ),
            "needs_attention": True,
        }

    if not verification.get("found", False):
        return {
            "text": "Нет данных о времени",
            "difference": None,
            "needs_attention": False,
        }

    difference = verification.get("difference_minutes")

    if difference is None:
        return {
            "text": "Нет данных о времени",
            "difference": None,
            "needs_attention": False,
        }

    difference = abs(difference)

    if difference <= 10:
        return {
            "text": "Время совпадает",
            "difference": difference,
            "needs_attention": False,
        }

    if difference <= 20:
        return {
            "text": "Событие требуется проверить",
            "difference": difference,
            "needs_attention": True,
        }

    return {
        "text": "Есть расхождение по времени",
        "difference": difference,
        "needs_attention": True,
    }


# =========================================================
# КОМПАКТНЫЙ СПИСОК
# =========================================================

def get_schedule_status_badge(event):
    accuracy = get_accuracy(event)
    attention = "⚠️" if accuracy.get("needs_attention") else ""
    return f"{get_status_icon(event)}{attention}"


def compact_event_line(event):
    channel = event.get("channel") or "Канал не указан"

    return (
        f"{get_schedule_status_badge(event)} "
        f"{get_time_window_text(event)} · "
        f"{get_schedule_display_title(event)} | {channel}"
    )


def compact_simulcast_block(group):
    if len(group) == 1:
        return compact_event_line(group[0])

    title = get_schedule_display_title(group[0])
    lines = [f"📡 {title}"]

    for event in group:
        channel = event.get("channel") or "Канал не указан"
        lines.append(
            f"{get_schedule_status_badge(event)} "
            f"{get_time_window_text(event)} · "
            f"{title} | {channel}"
        )

    return "\n".join(lines)


def _events_by_channel(events):
    grouped = {}
    for event in events:
        channel = event.get("channel") or "Канал не указан"
        grouped.setdefault(channel, []).append(event)
    for channel_events in grouped.values():
        channel_events.sort(
            key=lambda event: (event.get("date", ""), event.get("time", ""), get_event_title(event))
        )
    return dict(sorted(grouped.items(), key=lambda item: item[0].casefold()))


def _channel_preview(channel_events):
    current = next(
        (event for event in channel_events if get_event_status(event) == "live"),
        None,
    )
    upcoming = next(
        (event for event in channel_events if get_event_status(event) == "upcoming"),
        None,
    )
    return current or upcoming or (channel_events[-1] if channel_events else None)


def build_schedule_text(events):
    now = datetime.now(KZ_TIMEZONE)
    grouped = _events_by_channel(events)
    channel_count = len(grouped)

    if channel_count == 1:
        channel_label = "1 канал"
    elif 2 <= channel_count <= 4:
        channel_label = f"{channel_count} канала"
    else:
        channel_label = f"{channel_count} каналов"

    lines = [
        "📅 Расписание",
        f"{now.day} {MONTHS[now.month]} {now.year} · {channel_label} · {len(events)} спортивных программ",
        "",
    ]

    for channel, channel_events in grouped.items():
        preview = _channel_preview(channel_events)
        lines.append(f"📡 {channel} · {len(channel_events)} программ")
        if preview:
            lines.append(
                f"{get_schedule_status_badge(preview)} {get_time_window_text(preview)} · "
                f"{get_schedule_display_title(preview)}"
            )
        lines.append("")

    lines.extend([
        "Нажмите канал ниже, чтобы открыть его полное расписание.",
        "",
        "Статус: 🔴 подтверждённый LIVE · 🟡 подтверждённый LIVE позже · 📺 программа EPG · ⚪ завершено · ⚠️ проверить время",
    ])
    return "\n".join(lines)


def build_channel_schedule_messages(events, channel):
    selected = [event for event in events if event.get("channel") == channel]
    selected.sort(key=lambda event: (event.get("date", ""), event.get("time", ""), get_event_title(event)))
    title = f"📺 {channel} · {len(selected)} программ"
    if not selected:
        return [f"{title}\n\nСобытий нет."]
    blocks = [
        compact_event_line(event).rsplit(" | ", 1)[0]
        for event in selected
    ]
    messages = split_messages(blocks, title, limit=3800)
    messages[-1] += (
        "\n\nСтатус: 🔴 подтверждённый LIVE · 🟡 подтверждённый LIVE позже "
        "· 📺 программа EPG · ⚪ завершено"
    )
    return messages


def _export_sources_text(verification):
    lines = []
    for source in verification.get("sources", []):
        name = str(source.get("source_name") or "Источник").strip()
        url = str(source.get("url") or source.get("source_url") or "").strip()
        lines.append(f"{name}: {url}" if url else name)
    return "\n".join(lines)


def build_export_rows(events):
    rows = []
    for number, event in enumerate(events, start=1):
        verification = get_cached_verification(get_event_id(event)) or {}
        accuracy = get_accuracy(event)
        if verification.get("time_rejected"):
            verification_text = "Внешнее время отброшено (>30 мин.)"
            rejected = str(verification.get("rejected_external_time_kz") or "").strip()
            external_time = f"{rejected} (отброшено)" if rejected else "Отброшено (>30 мин.)"
            difference = verification.get("rejected_difference_minutes")
            confidence = "Не рассчитывается"
        elif verification.get("found"):
            verification_text = accuracy.get("text") or "Проверено"
            external_time = verification.get("external_time_kz", "")
            difference = verification.get("difference_minutes")
            confidence = verification.get("confidence", {}).get("label", "Не рассчитывается")
        else:
            verification_text = "Не подтверждено независимо"
            external_time = ""
            difference = None
            confidence = "Не рассчитывается"
        end_date = event.get("estimated_broadcast_end_date") or event.get("date") or ""
        end_time = event.get("estimated_broadcast_end") or ""
        rows.append({
            "number": number,
            "date": event.get("date", ""),
            "start": event.get("time", ""),
            "end": f"{end_date} {end_time}".strip() if end_time else "",
            "status": get_status_name(event),
            "verification": verification_text,
            "sport": event.get("sport") or "",
            "tournament": event.get("tournament") or "",
            "title": get_schedule_display_title(event),
            "channel": event.get("channel") or "",
            "source": event.get("source") or "",
            "live_evidence": get_live_evidence_text(event),
            "external_time": external_time,
            "difference_minutes": abs(difference) if difference is not None else "",
            "trust_weight": verification.get("total_weight", 0),
            "confidence": confidence,
            "source_count": verification.get("source_count", 0),
            "matching_results_count": verification.get("matching_results_count", 0),
            "sources": _export_sources_text(verification),
        })
    return rows


def build_live_text(events):
    now = datetime.now(KZ_TIMEZONE)

    header = (
        "🔴 LIVE\n"
        f"Сейчас: {now.strftime('%H:%M')} (UTC+5)"
    )

    groups = group_simulcasts(events)

    if not groups:
        return (
            f"{header}\n\n"
            "Сейчас прямых трансляций нет."
        )

    event_text = "\n\n".join(
        compact_simulcast_block(group)
        for group in groups
    )

    return f"{header}\n\n{event_text}"


def build_schedule_keyboard(events, chat_id=None):
    channels = unique_channels(events)
    rows = []
    for index in range(0, len(channels), 2):
        row = [
            InlineKeyboardButton(
                text=f"📺 {channel}",
                callback_data=f"schedule_ch:{channel}",
            )
            for channel in channels[index:index + 2]
        ]
        rows.append(row)
    rows.extend([
        [
            InlineKeyboardButton(text="🔄 Обновить", callback_data="schedule"),
            InlineKeyboardButton(text="🔴 LIVE", callback_data="live"),
        ],
        [
            InlineKeyboardButton(text="📥 XLSX", callback_data="export_schedule"),
            InlineKeyboardButton(text="📊 Статус", callback_data="status"),
        ],
        [_notification_button(chat_id)],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu")],
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_channel_schedule_keyboard(channel, chat_id=None):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⬅️ Все каналы", callback_data="schedule"),
                InlineKeyboardButton(text="🔄 Обновить", callback_data=f"schedule_ch:{channel}"),
            ],
            [
                InlineKeyboardButton(text="🔴 LIVE", callback_data="live"),
                InlineKeyboardButton(text="📥 XLSX", callback_data="export_schedule"),
            ],
            [_notification_button(chat_id)],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu")],
        ]
    )


def build_live_keyboard(chat_id=None):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔎 Подробнее", callback_data="details_live"),
                InlineKeyboardButton(text="🔄 Обновить", callback_data="live"),
            ],
            [
                InlineKeyboardButton(text="📅 Расписание", callback_data="schedule"),
                InlineKeyboardButton(text="📥 XLSX", callback_data="export_schedule"),
            ],
            [
                _notification_button(chat_id),
                InlineKeyboardButton(text="📊 Статус", callback_data="status"),
            ],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu")],
        ]
    )


@dp.message(Command("menu"))
async def menu_command(message: Message):
    await message.answer(
        "🏠 Главное меню",
        reply_markup=build_main_keyboard(message.chat.id),
    )


@dp.callback_query(F.data == "menu")
async def menu_callback(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer(
        "🏠 Главное меню",
        reply_markup=build_main_keyboard(callback.message.chat.id),
    )


# =========================================================
# РАСПИСАНИЕ
# =========================================================

async def load_schedule_events(
    *,
    force_refresh=False,
    return_errors=False,
):
    now_timestamp = time.time()
    if (
        not force_refresh
        and schedule_cache["events"]
        and now_timestamp - schedule_cache["time"] < SCHEDULE_CACHE_TTL
    ):
        events = schedule_cache["events"]
        errors = list(schedule_cache.get("source_errors", []))
        return (events, errors) if return_errors else events

    loaded = await load_source_schedules()
    today_events = [
        event for event in loaded.today
        if is_user_event(event)
    ]
    yesterday_events = [
        event for event in loaded.yesterday
        if (
            event_is_official_live(event)
            and is_user_event(event)
            and get_event_status(event) == "live"
        )
    ]
    events = merge_source_schedules(yesterday_events, today_events)

    schedule_cache["events"] = events
    schedule_cache["time"] = now_timestamp
    schedule_cache["source_errors"] = list(loaded.errors)

    valid_ids = {get_event_id(event) for event in events}
    for event_id in list(verification_cache):
        if event_id not in valid_ids:
            verification_cache.pop(event_id, None)
            verification_cache_time.pop(event_id, None)

    return (events, list(loaded.errors)) if return_errors else events


# =========================================================
# OPENSERP
# =========================================================

def get_cached_verification(event_id):
    result = verification_cache.get(event_id)
    cached_at = verification_cache_time.get(event_id)
    if result is None or cached_at is None:
        return None
    ttl = (
        VERIFICATION_ERROR_CACHE_TTL
        if result.get("verification_error")
        else VERIFICATION_CACHE_TTL
    )
    if time.monotonic() - cached_at >= ttl:
        verification_cache.pop(event_id, None)
        verification_cache_time.pop(event_id, None)
        return None
    return result


async def run_verification(event):
    event_id = get_event_id(event)

    async with verification_semaphore:
        try:
            result = await asyncio.to_thread(
                verify_event,
                event,
            )
        except Exception as error:
            print(
                "Ошибка OpenSERP:",
                repr(error),
            )
            result = {
                "found": False,
                "verification_error": "search_unavailable",
                "search_results_count": 0,
                "matching_results_count": 0,
                "source_count": 0,
                "total_weight": 0,
                "sources": [],
            }

    verification_cache[event_id] = result
    verification_cache_time[event_id] = time.monotonic()
    return result


async def ensure_event_verification(event):
    event_id = get_event_id(event)

    cached = get_cached_verification(event_id)
    if cached is not None:
        return cached

    existing_task = verification_tasks.get(event_id)

    if existing_task:
        return await existing_task

    task = asyncio.create_task(
        run_verification(event)
    )
    verification_tasks[event_id] = task

    try:
        return await task
    finally:
        if verification_tasks.get(event_id) is task:
            verification_tasks.pop(event_id, None)


async def verify_events(events):
    await asyncio.gather(
        *[
            ensure_event_verification(event)
            for event in events
        ],
        return_exceptions=True,
    )


# =========================================================
# ФОНОВОЕ ОБНОВЛЕНИЕ
# =========================================================

async def background_verify_and_refresh(
    events,
    message,
    view_type,
):
    await verify_events([event for event in events if is_confirmed_direct_event(event)])

    try:
        if view_type == "schedule":
            text = build_schedule_text(events)
            keyboard = build_schedule_keyboard(events, message.chat.id)
        else:
            live_events = get_confirmed_live_events(events)
            text = build_live_text(live_events)
            keyboard = build_live_keyboard(message.chat.id)

        await message.edit_text(
            text,
            reply_markup=keyboard,
        )
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error).casefold():
            print("Ошибка фонового обновления Telegram:", repr(error))
    except Exception as error:
        print(
            "Ошибка фонового обновления Telegram:",
            repr(error),
        )


# =========================================================
# УВЕДОМЛЕНИЯ ОБ ИЗМЕНЕНИЯХ РАСПИСАНИЯ
# =========================================================

def build_change_keyboard(identity):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔎 Подробнее",
                    callback_data=f"watch_detail:{identity}",
                ),
                InlineKeyboardButton(text="📅 Расписание", callback_data="schedule"),
            ],
            [
                InlineKeyboardButton(text="🔴 LIVE", callback_data="live"),
                InlineKeyboardButton(text="🏠 Меню", callback_data="menu"),
            ],
        ]
    )

async def send_schedule_changes(changes):
    subscribers = get_subscribers()
    if not subscribers or not changes:
        return

    for chat_id in subscribers:
        for change in changes:
            try:
                await bot.send_message(
                    chat_id,
                    format_schedule_change(change),
                    reply_markup=build_change_keyboard(change.get("identity", "")),
                )
            except Exception as error:
                print(f"Ошибка уведомления для {chat_id}:", repr(error))


async def check_schedule_changes_once():
    events, source_errors = await load_schedule_events(
        force_refresh=True,
        return_errors=True,
    )

    if source_errors:
        print(
            "Проверка изменений пропущена: "
            "есть ошибки источников:",
            ", ".join(source_errors),
        )
        return []

    watched_events = [event for event in events if is_confirmed_direct_event(event)]
    current_snapshot = build_schedule_snapshot(watched_events)
    previous_snapshot = get_previous_snapshot()

    if previous_snapshot is None:
        update_snapshot(current_snapshot)
        print("SLP notifications: базовый снимок расписания сохранён")
        return []

    changes = diff_schedule_snapshots(
        previous_snapshot,
        current_snapshot,
    )

    update_snapshot(current_snapshot)

    if changes:
        await send_schedule_changes(changes)
        print(
            "SLP notifications: найдено изменений:",
            len(changes),
        )

    return changes


async def notification_watch_loop():
    while True:
        try:
            await check_schedule_changes_once()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            print(
                "Ошибка мониторинга расписания:",
                repr(error),
            )

        await asyncio.sleep(NOTIFICATION_CHECK_INTERVAL)


# =========================================================
# ПОДРОБНЫЙ СПИСОК
# =========================================================

def build_source_text(verification):
    sources = verification.get("sources", [])

    if not sources:
        return ""

    lines = []

    for source in sources[:3]:
        name = source.get("source_name", "Источник")
        external_time = source.get("external_time_kz", "")

        if external_time:
            lines.append(
                f"• {name} · {external_time} UTC+5"
            )
        else:
            lines.append(f"• {name}")

    if not lines:
        return ""

    return "\nИсточники:\n" + "\n".join(lines)


def build_detailed_event(event, number):
    verification = get_cached_verification(get_event_id(event)) or {}
    accuracy = get_accuracy(event)

    sport = event.get("sport") or "не определён"
    tournament = event.get("tournament") or "не определён"

    lines = [
        f"{number}. {get_event_title(event)}",
        (
            f"📌 Статус: "
            f"{get_status_icon(event)} "
            f"{get_status_name(event)}"
        ),
        f"🕐 Начало эфира: {get_start_full_text(event)}",
        f"🏁 Окончание эфира: {get_broadcast_end_detail(event)}",
        f"🏅 Вид спорта: {sport}",
        f"🏆 Турнир: {tournament}",
        f"📺 Канал: {event['channel']}",
        f"📡 Основание LIVE: {get_live_evidence_text(event)}",
        "",
        "🌐 Интернет-проверка",
        accuracy["text"],
    ]

    if verification.get("found", False):
        difference = verification.get("difference_minutes")

        if difference is None:
            difference_text = "не определено"
        else:
            difference_text = f"{abs(difference)} мин."

        confidence = (
            verification.get("confidence", {})
            .get("label", "не определён")
        )

        lines.extend(
            [
                (
                    f"🕐 Внешнее время: "
                    f"{verification.get('external_time_kz', '?')} "
                    f"(UTC+5)"
                ),
                f"⏱ Расхождение: {difference_text}",
                (
                    f"⚖️ Вес доверия: "
                    f"{verification.get('total_weight', 0)}"
                ),
                f"📊 Уровень доверия: {confidence}",
                (
                    f"📚 Время подтвердили: "
                    f"{verification.get('source_count', 0)} "
                    "независимых источника(ов)"
                ),
                (
                    f"🔎 Найдено результатов: "
                    f"{verification.get('search_results_count', 0)}"
                ),
                (
                    f"🎯 Релевантных результатов: "
                    f"{verification.get('matching_results_count', 0)}"
                ),
            ]
        )

        source_text = build_source_text(verification)

        if source_text:
            lines.append(source_text)

    else:
        total = verification.get(
            "search_results_count",
            0,
        )
        matching = verification.get(
            "matching_results_count",
            0,
        )

        lines.extend(
            [
                "⚖️ Вес доверия: 0",
                "📊 Уровень доверия: Не рассчитывается",
                "📚 Время подтвердили: 0 независимых источников",
                f"🔎 Найдено результатов: {total}",
                f"🎯 Релевантных результатов: {matching}",
            ]
        )

        if verification.get("verification_error"):
            lines.append(
                "ℹ️ Интернет-проверка временно недоступна; "
                "эфирное расписание остаётся основным источником."
            )
        elif verification.get("time_rejected", False):
            lines.append(
                "ℹ️ Найденное внешнее время отброшено: "
                "расхождение больше 30 минут."
            )
        elif matching > 0:
            lines.append(
                "ℹ️ Событие найдено, но время надёжно "
                "определить не удалось."
            )

    return "\n".join(lines)


def split_messages(
    blocks,
    title,
    limit=3900,
):
    messages = []
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


async def show_details(
    callback,
    events,
    title,
):
    chat_id = callback.message.chat.id
    keyboard = build_aux_keyboard(chat_id)

    if not events:
        await callback.message.answer(
            "Событий нет.",
            reply_markup=keyboard,
        )
        return

    loading = await callback.message.answer(
        "⏳ Формирую подробный список..."
    )

    await verify_events([event for event in events if is_confirmed_direct_event(event)])

    blocks = [
        build_detailed_event(event, number)
        for number, event in enumerate(events, start=1)
    ]
    messages = split_messages(blocks, title)

    await loading.edit_text(
        messages[0],
        reply_markup=keyboard if len(messages) == 1 else None,
    )

    for index, message_text in enumerate(messages[1:], start=1):
        await callback.message.answer(
            message_text,
            reply_markup=keyboard if index == len(messages) - 1 else None,
        )


# =========================================================
# START
# =========================================================

@dp.message(Command("start"))
async def start_command(message: Message):
    await message.answer(
        "SLP [Skoomaholic Live Parser] запущен ✅\n\n"
        "Выберите действие:",
        reply_markup=build_main_keyboard(message.chat.id),
    )


# =========================================================
# РАСПИСАНИЕ
# =========================================================

@dp.callback_query(F.data == "schedule")
async def schedule_callback(callback: CallbackQuery):
    await callback.answer()

    loading = await callback.message.answer(
        "⏳ Загружаю расписание..."
    )

    try:
        events = await load_schedule_events()

        await loading.edit_text(
            build_schedule_text(events),
            reply_markup=build_schedule_keyboard(events, callback.message.chat.id),
        )

        asyncio.create_task(
            background_verify_and_refresh(
                events,
                loading,
                "schedule",
            )
        )

    except Exception as error:
        print(
            "Ошибка расписания:",
            repr(error),
        )
        await loading.edit_text(
            "❌ Не удалось загрузить расписание.",
            reply_markup=build_aux_keyboard(callback.message.chat.id),
        )


@dp.callback_query(F.data.startswith("schedule_ch:"))
async def schedule_channel_callback(callback: CallbackQuery):
    await callback.answer()
    channel = callback.data.split(":", 1)[1]
    loading = await callback.message.answer("⏳ Загружаю канал...")
    try:
        events = await load_schedule_events()
        messages = build_channel_schedule_messages(events, channel)
        keyboard = build_channel_schedule_keyboard(channel, callback.message.chat.id)
        await loading.edit_text(
            messages[0],
            reply_markup=keyboard if len(messages) == 1 else None,
        )
        for index, text in enumerate(messages[1:], start=1):
            await callback.message.answer(
                text,
                reply_markup=keyboard if index == len(messages) - 1 else None,
            )
    except Exception as error:
        print("Ошибка расписания канала:", repr(error))
        await loading.edit_text(
            "❌ Не удалось загрузить расписание канала.",
            reply_markup=build_aux_keyboard(callback.message.chat.id),
        )


# =========================================================
# LIVE
# =========================================================

@dp.callback_query(F.data == "live")
async def live_callback(callback: CallbackQuery):
    await callback.answer()

    loading = await callback.message.answer(
        "⏳ Загружаю LIVE..."
    )

    try:
        all_events = await load_schedule_events()

        live_events = get_confirmed_live_events(all_events)

        await loading.edit_text(
            build_live_text(live_events),
            reply_markup=build_live_keyboard(callback.message.chat.id),
        )

        asyncio.create_task(
            background_verify_and_refresh(
                live_events,
                loading,
                "live",
            )
        )

    except Exception as error:
        print(
            "Ошибка LIVE:",
            repr(error),
        )
        await loading.edit_text(
            "❌ Не удалось загрузить LIVE.",
            reply_markup=build_aux_keyboard(callback.message.chat.id),
        )


# =========================================================
# ПОДРОБНЕЕ
# =========================================================

@dp.callback_query(F.data == "details_schedule")
async def details_schedule_callback(callback: CallbackQuery):
    await callback.answer()
    events = await load_schedule_events()
    await show_details(callback, events, "🔎 Подробное расписание")


@dp.callback_query(F.data == "details_live")
async def details_live_callback(callback: CallbackQuery):
    await callback.answer()
    events = get_confirmed_live_events(await load_schedule_events())
    await show_details(callback, events, "🔎 Подробно LIVE")


# =========================================================
# УВЕДОМЛЕНИЯ
# =========================================================

@dp.callback_query(F.data.startswith("watch_detail:"))
async def watch_detail_callback(callback: CallbackQuery):
    await callback.answer()
    identity = callback.data.split(":", 1)[1]
    events = [
        event for event in await load_schedule_events(force_refresh=True)
        if is_confirmed_direct_event(event) and stable_event_identity(event) == identity
    ]
    if not events:
        await callback.message.answer(
            "Событие больше не найдено в актуальном расписании.",
            reply_markup=build_aux_keyboard(callback.message.chat.id),
        )
        return
    await show_details(callback, events, "🔎 Изменившееся событие")


@dp.callback_query(F.data == "notifications")
async def notifications_callback(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    enabled = not is_subscribed(chat_id)
    set_subscription(chat_id, enabled)

    if enabled:
        await callback.answer("Уведомления включены")
        await callback.message.answer(
            "🔔 Уведомления включены.\n\n"
            "SLP сообщит об изменении времени, канала, "
            "существенном изменении окончания эфира, "
            "появлении или исчезновении LIVE-события.",
            reply_markup=build_aux_keyboard(chat_id),
        )
    else:
        await callback.answer("Уведомления выключены")
        await callback.message.answer(
            "🔕 Уведомления выключены.",
            reply_markup=build_aux_keyboard(chat_id),
        )


# =========================================================
# ВЫГРУЗКА
# =========================================================

@dp.callback_query(F.data == "export_schedule")
async def export_schedule_callback(callback: CallbackQuery):
    await callback.answer()
    loading = await callback.message.answer("⏳ Формирую XLSX...")
    try:
        events = await load_schedule_events()
        await verify_events([event for event in events if is_confirmed_direct_event(event)])
        payload = build_schedule_workbook(build_export_rows(events))
        stamp = datetime.now(KZ_TIMEZONE).strftime("%Y-%m-%d_%H-%M")
        document = BufferedInputFile(payload, filename=f"SLP_schedule_{stamp}.xlsx")
        await callback.message.answer_document(
            document,
            caption="📥 SLP — расписание и сводка",
            reply_markup=build_aux_keyboard(callback.message.chat.id),
        )
        await loading.delete()
    except Exception as error:
        print("Ошибка XLSX-выгрузки:", repr(error))
        await loading.edit_text(
            "❌ Не удалось сформировать XLSX.",
            reply_markup=build_aux_keyboard(callback.message.chat.id),
        )


# =========================================================
# СТАТУС
# =========================================================

@dp.callback_query(F.data == "status")
async def status_callback(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer(
        "📊 Статус SLP\n\n"
        "✅ QAZSPORT HD — прямой источник\n"
        "✅ Sport+ Qazaqstan — прямой источник\n"
        "✅ 14 дополнительных каналов — provider EPG\n"
        "✅ Eurosport и Eurosport 2 подключены\n"
        "✅ LIVE evidence работает fail-closed\n"
        "✅ UTC+5 и переход через полночь нормализуются\n"
        "✅ Интернет-проверка не меняет LIVE/SOON/OVER\n"
        "✅ Уведомления и XLSX работают\n\n"
        f"Подписчиков на уведомления: {len(get_subscribers())}\n"
        "Текущий этап: Step 75 · Multi-channel + Telegram UX",
        reply_markup=build_aux_keyboard(callback.message.chat.id),
    )


# =========================================================
# ЗАПУСК
# =========================================================

async def main():
    print(
        "SLP запущен. "
        "Ожидаю сообщения в Telegram..."
    )
    print(
        "SLP notifications: проверка каждые "
        f"{NOTIFICATION_CHECK_INTERVAL} сек."
    )

    watch_task = asyncio.create_task(
        notification_watch_loop()
    )

    try:
        await dp.start_polling(bot)
    finally:
        watch_task.cancel()
        await asyncio.gather(
            watch_task,
            return_exceptions=True,
        )


if __name__ == "__main__":
    asyncio.run(main())

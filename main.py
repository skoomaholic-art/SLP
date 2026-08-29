import asyncio
import hashlib
import os
import time
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from parsers.qazsport import get_qazsport_schedule
from parsers.sportplus import get_sportplus_schedule
from services.schedule_merge import (
    group_simulcasts,
    merge_source_schedules,
    unique_channels,
)
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


BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не найден в переменных окружения Codespaces")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# =========================================================
# КЭШ
# =========================================================

verification_cache = {}
verification_tasks = {}
view_cache = {}

schedule_cache = {
    "time": 0,
    "events": [],
    "source_errors": [],
}

SCHEDULE_CACHE_TTL = 180
NOTIFICATION_CHECK_INTERVAL = 180
verification_semaphore = asyncio.Semaphore(2)


# =========================================================
# ГЛАВНОЕ МЕНЮ
# =========================================================

main_keyboard = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(
                text="📅 Расписание",
                callback_data="schedule",
            ),
            InlineKeyboardButton(
                text="🔴 LIVE",
                callback_data="live",
            ),
        ],
        [
            InlineKeyboardButton(
                text="🔔 Уведомления",
                callback_data="notifications",
            ),
            InlineKeyboardButton(
                text="📊 Статус",
                callback_data="status",
            ),
        ],
        [
            InlineKeyboardButton(
                text="📥 Выгрузить",
                callback_data="export_schedule",
            ),
        ],
    ]
)


# =========================================================
# ДАТЫ
# =========================================================

MONTHS = {
    1: "января",
    2: "февраля",
    3: "марта",
    4: "апреля",
    5: "мая",
    6: "июня",
    7: "июля",
    8: "августа",
    9: "сентября",
    10: "октября",
    11: "ноября",
    12: "декабря",
}


def format_date(date_text):
    value = datetime.strptime(
        date_text,
        "%Y-%m-%d",
    )
    return f"{value.day} {MONTHS[value.month]}"


def format_full_date(date_text):
    value = datetime.strptime(
        date_text,
        "%Y-%m-%d",
    )
    return f"{value.day} {MONTHS[value.month]} {value.year}"


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


def is_user_event(event):
    title = get_event_title(event).casefold()
    sport = str(event.get("sport") or "").strip()
    tournament = str(event.get("tournament") or "").strip()

    studio_markers = (
        "студийная программа",
        "студиялық бағдарлама",
        "перед матчем",
        "матч қарсаңында",
    )

    if (
        any(marker in title for marker in studio_markers)
        and not sport
        and not tournament
    ):
        return False

    return True


# =========================================================
# СТАТУС ТРАНСЛЯЦИИ
# ВАЖНО: статус берётся ТОЛЬКО из эфирного расписания.
# Интернет-проверка не может менять LIVE / SOON / OVER.
# =========================================================

def get_status_icon(event):
    status = get_event_status(event)

    if status == "live":
        return "🔴"
    if status == "upcoming":
        return "🟡"
    return "⚪"


def get_status_name(event):
    status = get_event_status(event)

    if status == "live":
        return "LIVE"
    if status == "upcoming":
        return "SOON"
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
    source = event.get("source", "")
    raw_title = str(event.get("raw_title") or "").upper()

    if source == "sportplus":
        if any(
            marker in raw_title
            for marker in (
                "ПРЯМАЯ ТРАНСЛЯЦИЯ",
                "ПРЯМОЙ ЭФИР",
                "ТІКЕЛЕЙ ЭФИР",
                "ТIКЕЛЕЙ ЭФИР",
            )
        ):
            return "официальная пометка прямого эфира Sport+"

    if source == "qazsport":
        return "LIVE-пометка в расписании Qazsport"

    return "официальное расписание канала"


# =========================================================
# ПРОВЕРКА ТОЧНОСТИ
# =========================================================

def get_accuracy(event):
    verification = verification_cache.get(
        get_event_id(event)
    )

    if not verification:
        return {
            "text": "Нет данных о времени",
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


def build_schedule_text(events):
    now = datetime.now(KZ_TIMEZONE)
    channels = unique_channels(events)
    channel_count = len(channels)

    if channel_count == 1:
        channel_label = "1 канал"
    elif 2 <= channel_count <= 4:
        channel_label = f"{channel_count} канала"
    else:
        channel_label = f"{channel_count} каналов"

    header = (
        "📅 Расписание\n"
        f"{now.day} {MONTHS[now.month]} {now.year} · {channel_label}"
    )

    groups = group_simulcasts(events)

    if groups:
        event_text = "\n\n".join(
            compact_simulcast_block(group)
            for group in groups
        )
    else:
        event_text = "Событий нет."

    return (
        f"{header}\n\n"
        f"{event_text}\n\n"
        "Статус: 🔴 LIVE · 🟡 SOON · ⚪ OVER · ⚠️ Нуждается в проверке"
    )


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


def build_schedule_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔎 Подробнее",
                    callback_data="details_schedule",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📥 Выгрузить",
                    callback_data="export_schedule",
                )
            ],
        ]
    )


def build_live_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔎 Подробнее",
                    callback_data="details_live",
                )
            ]
        ]
    )


# =========================================================
# РАСПИСАНИЕ
# =========================================================

async def _safe_source_load(label, coroutine, source_errors):
    try:
        return await coroutine
    except Exception as error:
        source_errors.append(label)
        print(
            f"Ошибка источника {label}:",
            repr(error),
        )
        return []


async def load_schedule_events(
    *,
    force_refresh=False,
    return_errors=False,
):
    now_timestamp = time.time()

    if (
        not force_refresh
        and schedule_cache["events"]
        and (
            now_timestamp
            - schedule_cache["time"]
        ) < SCHEDULE_CACHE_TTL
    ):
        events = schedule_cache["events"]
        errors = list(schedule_cache.get("source_errors", []))
        return (events, errors) if return_errors else events

    now = datetime.now(KZ_TIMEZONE)
    yesterday = now.date() - timedelta(days=1)
    source_errors = []

    (
        qazsport_today,
        sportplus_today,
        qazsport_yesterday,
        sportplus_yesterday,
    ) = await asyncio.gather(
        _safe_source_load(
            "Qazsport сегодня",
            get_qazsport_schedule(
                include_current_live=True,
            ),
            source_errors,
        ),
        _safe_source_load(
            "Sport+ сегодня",
            get_sportplus_schedule(),
            source_errors,
        ),
        _safe_source_load(
            "Qazsport вчера",
            get_qazsport_schedule(
                yesterday,
                include_current_live=True,
            ),
            source_errors,
        ),
        _safe_source_load(
            "Sport+ вчера",
            get_sportplus_schedule(yesterday),
            source_errors,
        ),
    )

    today_schedule = merge_source_schedules(
        qazsport_today,
        sportplus_today,
    )
    yesterday_schedule = merge_source_schedules(
        qazsport_yesterday,
        sportplus_yesterday,
    )

    today_events = [
        event
        for event in today_schedule
        if (
            event.get("is_live", False)
            and is_user_event(event)
        )
    ]

    yesterday_events = [
        event
        for event in yesterday_schedule
        if (
            event.get("is_live", False)
            and is_user_event(event)
            and get_event_status(event) == "live"
        )
    ]

    events = merge_source_schedules(
        yesterday_events,
        today_events,
    )

    schedule_cache["events"] = events
    schedule_cache["time"] = now_timestamp
    schedule_cache["source_errors"] = list(source_errors)

    return (events, source_errors) if return_errors else events


# =========================================================
# OPENSERP
# =========================================================

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
                "search_results_count": 0,
                "matching_results_count": 0,
                "source_count": 0,
                "total_weight": 0,
                "sources": [],
            }

    verification_cache[event_id] = result
    return result


async def ensure_event_verification(event):
    event_id = get_event_id(event)

    if event_id in verification_cache:
        return verification_cache[event_id]

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
    await verify_events(events)

    try:
        if view_type == "schedule":
            text = build_schedule_text(events)
            keyboard = build_schedule_keyboard()
        else:
            live_events = [
                event
                for event in events
                if get_event_status(event) == "live"
            ]
            text = build_live_text(live_events)
            keyboard = build_live_keyboard()

        await message.edit_text(
            text,
            reply_markup=keyboard,
        )
    except Exception as error:
        print(
            "Ошибка фонового обновления Telegram:",
            repr(error),
        )


# =========================================================
# УВЕДОМЛЕНИЯ ОБ ИЗМЕНЕНИЯХ РАСПИСАНИЯ
# =========================================================

async def send_schedule_changes(changes):
    subscribers = get_subscribers()

    if not subscribers or not changes:
        return

    messages = build_change_messages(changes)

    for chat_id in subscribers:
        for message_text in messages:
            try:
                await bot.send_message(
                    chat_id,
                    message_text,
                )
            except Exception as error:
                print(
                    f"Ошибка уведомления для {chat_id}:",
                    repr(error),
                )


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

    current_snapshot = build_schedule_snapshot(events)
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
    verification = verification_cache.get(
        get_event_id(event),
        {},
    )
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

        if verification.get("time_rejected", False):
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
    if not events:
        await callback.message.answer(
            "Событий нет."
        )
        return

    loading = await callback.message.answer(
        "⏳ Формирую подробный список..."
    )

    await verify_events(events)

    blocks = [
        build_detailed_event(event, number)
        for number, event in enumerate(
            events,
            start=1,
        )
    ]

    messages = split_messages(
        blocks,
        title,
    )

    await loading.edit_text(messages[0])

    for message_text in messages[1:]:
        await callback.message.answer(message_text)


# =========================================================
# START
# =========================================================

@dp.message(Command("start"))
async def start_command(message: Message):
    await message.answer(
        "SLP [Skoomaholic Live Parser] запущен ✅\n\n"
        "Выберите действие:",
        reply_markup=main_keyboard,
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

        view_cache[
            (
                callback.from_user.id,
                "schedule",
            )
        ] = events

        await loading.edit_text(
            build_schedule_text(events),
            reply_markup=build_schedule_keyboard(),
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
            "❌ Не удалось загрузить расписание."
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

        live_events = [
            event
            for event in all_events
            if get_event_status(event) == "live"
        ]

        view_cache[
            (
                callback.from_user.id,
                "live",
            )
        ] = live_events

        await loading.edit_text(
            build_live_text(live_events),
            reply_markup=build_live_keyboard(),
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
            "❌ Не удалось загрузить LIVE."
        )


# =========================================================
# ПОДРОБНЕЕ
# =========================================================

@dp.callback_query(F.data == "details_schedule")
async def details_schedule_callback(callback: CallbackQuery):
    await callback.answer()

    events = view_cache.get(
        (
            callback.from_user.id,
            "schedule",
        ),
        [],
    )

    await show_details(
        callback,
        events,
        "🔎 Подробное расписание",
    )


@dp.callback_query(F.data == "details_live")
async def details_live_callback(callback: CallbackQuery):
    await callback.answer()

    events = view_cache.get(
        (
            callback.from_user.id,
            "live",
        ),
        [],
    )

    await show_details(
        callback,
        events,
        "🔎 Подробно LIVE",
    )


# =========================================================
# УВЕДОМЛЕНИЯ
# =========================================================

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
            "появлении или исчезновении LIVE-события."
        )
    else:
        await callback.answer("Уведомления выключены")
        await callback.message.answer(
            "🔕 Уведомления выключены."
        )


# =========================================================
# ВЫГРУЗКА
# =========================================================

@dp.callback_query(F.data == "export_schedule")
async def export_schedule_callback(callback: CallbackQuery):
    await callback.answer()

    await callback.message.answer(
        "📥 Выгрузка расписания\n\n"
        "XLSX подключим после стабилизации Parser v1."
    )


# =========================================================
# СТАТУС
# =========================================================

@dp.callback_query(F.data == "status")
async def status_callback(callback: CallbackQuery):
    await callback.answer()

    await callback.message.answer(
        "📊 Статус SLP\n\n"
        "✅ Qazsport подключён\n"
        "✅ Sport+ Qazaqstan подключён\n"
        "✅ Единый SportEvent используется\n"
        "✅ Расписание работает с несколькими источниками\n"
        "✅ LIVE работает\n"
        "✅ UTC+5 учитывается\n"
        "✅ Переход через полночь нормализуется\n"
        "✅ Последнее событие получает fallback окончания\n"
        "✅ Интернет-время не меняет LIVE/SOON/OVER\n"
        "✅ OpenSERP работает в фоне\n"
        "✅ Мониторинг изменений расписания работает\n"
        "✅ Подписки на Telegram-уведомления сохраняются\n\n"
        f"Подписчиков на уведомления: {len(get_subscribers())}\n"
        "Текущий этап: Step 71.2 · Parser v1 RC"
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

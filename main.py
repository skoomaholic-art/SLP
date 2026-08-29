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
}

SCHEDULE_CACHE_TTL = 180
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
                text="📥 Выгрузить",
                callback_data="export_schedule",
            ),
            InlineKeyboardButton(
                text="📊 Статус",
                callback_data="status",
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
        }

    if not verification.get("found", False):
        return {
            "text": "Нет данных о времени",
            "difference": None,
        }

    difference = verification.get("difference_minutes")

    if difference is None:
        return {
            "text": "Нет данных о времени",
            "difference": None,
        }

    difference = abs(difference)

    if difference <= 10:
        return {
            "text": "Время совпадает",
            "difference": difference,
        }

    if difference <= 20:
        return {
            "text": "Событие требуется проверить",
            "difference": difference,
        }

    return {
        "text": "Есть расхождение по времени",
        "difference": difference,
    }


# =========================================================
# КОМПАКТНЫЙ СПИСОК
# =========================================================

def compact_event_line(event):
    accuracy = get_accuracy(event)

    return (
        f"{get_status_icon(event)} "
        f"{get_time_window_text(event)} · "
        f"{get_event_title(event)}\n"
        f"   {accuracy['text']}"
    )


def compact_simulcast_block(group):
    if len(group) == 1:
        return compact_event_line(group[0])

    title = get_event_title(group[0])
    lines = [f"📡 {title}"]

    for event in group:
        accuracy = get_accuracy(event)
        lines.extend(
            [
                (
                    f"{get_status_icon(event)} "
                    f"{get_time_window_text(event)} · "
                    f"{event.get('channel', 'Канал не указан')}"
                ),
                f"   {accuracy['text']}",
            ]
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
        "Статус: 🔴 LIVE · 🟡 SOON · ⚪ OVER"
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

async def _safe_source_load(label, coroutine):
    try:
        return await coroutine
    except Exception as error:
        print(
            f"Ошибка источника {label}:",
            repr(error),
        )
        return []


async def load_schedule_events():
    now_timestamp = time.time()

    if (
        schedule_cache["events"]
        and (
            now_timestamp
            - schedule_cache["time"]
        ) < SCHEDULE_CACHE_TTL
    ):
        return schedule_cache["events"]

    now = datetime.now(KZ_TIMEZONE)
    yesterday = now.date() - timedelta(days=1)

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
        ),
        _safe_source_load(
            "Sport+ сегодня",
            get_sportplus_schedule(),
        ),
        _safe_source_load(
            "Qazsport вчера",
            get_qazsport_schedule(
                yesterday,
                include_current_live=True,
            ),
        ),
        _safe_source_load(
            "Sport+ вчера",
            get_sportplus_schedule(yesterday),
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

    return events


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
                    f"🎯 По событию: "
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
                f"🔎 Найдено результатов: {total}",
                f"🎯 По событию: {matching}",
                "⏱ Время подтвердили: 0 источников",
            ]
        )

        if matching > 0:
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
        "✅ OpenSERP работает в фоне\n\n"
        "Текущий этап: Step 70.8.1"
    )


# =========================================================
# ЗАПУСК
# =========================================================

async def main():
    print(
        "SLP запущен. "
        "Ожидаю сообщения в Telegram..."
    )

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

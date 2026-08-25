import asyncio
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from events import EVENTS
from parsers.qazsport import get_qazsport_schedule


BOT_TOKEN = os.getenv("BOT_TOKEN")

KZ_TIMEZONE = ZoneInfo("Asia/Almaty")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


main_keyboard = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(
                text="📅 Расписание",
                callback_data="schedule"
            ),
            InlineKeyboardButton(
                text="🔴 Сейчас LIVE",
                callback_data="live"
            ),
        ],
        [
            InlineKeyboardButton(
                text="📊 Статус",
                callback_data="status"
            ),
        ],
        [
            InlineKeyboardButton(
                text="🌐 Проверка в интернете",
                callback_data="internet_check"
            ),
        ],
        [
            InlineKeyboardButton(
                text="👥 Команда",
                callback_data="team"
            ),
        ],
    ]
)


def format_date(date_text):
    months = {
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

    date_value = datetime.strptime(
        date_text,
        "%Y-%m-%d"
    )

    return (
        f"{date_value.day} "
        f"{months[date_value.month]}"
    )


def get_event_datetimes(event):
    end_date = event[
        "estimated_broadcast_end_date"
    ]

    end_time = event[
        "estimated_broadcast_end"
    ]

    start_datetime = datetime.strptime(
        f"{event['date']} {event['time']}",
        "%Y-%m-%d %H:%M"
    ).replace(
        tzinfo=KZ_TIMEZONE
    )

    if not end_date or not end_time:
        return start_datetime, None

    end_datetime = datetime.strptime(
        f"{end_date} {end_time}",
        "%Y-%m-%d %H:%M"
    ).replace(
        tzinfo=KZ_TIMEZONE
    )

    return (
        start_datetime,
        end_datetime
    )


def get_event_status(event):
    start_datetime, end_datetime = (
        get_event_datetimes(event)
    )

    now = datetime.now(
        KZ_TIMEZONE
    )

    if now < start_datetime:
        return "upcoming"

    if (
        end_datetime is not None
        and start_datetime <= now < end_datetime
    ):
        return "live"

    if (
        end_datetime is not None
        and now >= end_datetime
    ):
        return "finished"

    return "unknown"


def get_event_status_text(event):
    status = get_event_status(event)

    if status == "live":
        return "🔴 ИДЁТ СЕЙЧАС"

    if status == "upcoming":
        return "🕒 Предстоит"

    if status == "finished":
        return "✅ Эфирный слот завершён"

    return "⚪ Статус не определён"


def is_event_live_now(event):
    return (
        get_event_status(event)
        == "live"
    )


def format_qazsport_event(event):
    date_text = format_date(
        event["date"]
    )

    end_date = event[
        "estimated_broadcast_end_date"
    ]

    end_time = event[
        "estimated_broadcast_end"
    ]

    if end_date and end_time:
        if end_date == event["date"]:
            end_text = end_time
        else:
            end_text = (
                f"{format_date(end_date)}, "
                f"{end_time}"
            )
    else:
        end_text = "не определено"

    status_text = get_event_status_text(
        event
    )

    return (
        f"{status_text}\n"
        f"{date_text}, {event['time']} – "
        f"{event['sport']}. "
        f"{event['tournament']}. "
        f"{event['title']} | "
        f"{event['channel']}\n"
        f"↳ Ориентировочно до: {end_text}"
    )


def remove_duplicates(events):
    result = []
    seen = set()

    for event in events:
        event_key = (
            event["date"],
            event["time"],
            event.get("title"),
            event["channel"],
        )

        if event_key in seen:
            continue

        seen.add(event_key)
        result.append(event)

    return result


async def get_current_live_events():
    now = datetime.now(
        KZ_TIMEZONE
    )

    today = now.date()

    yesterday = (
        today
        - timedelta(days=1)
    )

    today_schedule = (
        await get_qazsport_schedule(
            today,
            include_current_live=True
        )
    )

    yesterday_schedule = (
        await get_qazsport_schedule(
            yesterday,
            include_current_live=True
        )
    )

    combined_schedule = (
        yesterday_schedule
        + today_schedule
    )

    combined_schedule = remove_duplicates(
        combined_schedule
    )

    current_live_events = [
        event
        for event in combined_schedule
        if (
            event["is_live"]
            and is_event_live_now(event)
        )
    ]

    return current_live_events


def get_verification_result(event):
    difference = event.time_difference_minutes

    if difference is None:
        return (
            "⚪ Не проверено\n"
            "Нет данных для сравнения времени."
        )

    if difference <= 10:
        return (
            "✅ Подтверждено\n"
            "Расхождение допустимое. "
            "Вероятно, телеканал начинает эфир заранее."
        )

    if difference <= 20:
        return (
            "🟡 Требуется дополнительная проверка\n"
            "Время отличается больше чем на 10 минут."
        )

    return (
        "⚠️ Обнаружено существенное расхождение\n"
        "Необходимо проверить дату, время и само событие."
    )


def build_verification_keyboard():
    buttons = []

    for index, event in enumerate(EVENTS):
        buttons.append(
            [
                InlineKeyboardButton(
                    text=(
                        f"{event.broadcast_start} — "
                        f"{event.title}"
                    ),
                    callback_data=f"verify_{index}"
                )
            ]
        )

    return InlineKeyboardMarkup(
        inline_keyboard=buttons
    )


@dp.message(Command("start"))
async def start_command(message: Message):
    await message.answer(
        "SLP [Skoomaholic Live Parser] запущен ✅\n\n"
        "Выберите действие:",
        reply_markup=main_keyboard,
    )


@dp.callback_query(F.data == "schedule")
async def schedule_callback(
    callback: CallbackQuery
):
    await callback.answer()

    loading_message = await callback.message.answer(
        "⏳ Загружаю расписание Qazsport..."
    )

    try:
        schedule = await get_qazsport_schedule()

        live_events = [
            event
            for event in schedule
            if event["is_live"]
        ]

        if not live_events:
            await loading_message.edit_text(
                "📅 Расписание Qazsport\n\n"
                "LIVE-событий не найдено."
            )
            return

        schedule_text = "\n\n".join(
            format_qazsport_event(event)
            for event in live_events
        )

        current_time = datetime.now(
            KZ_TIMEZONE
        ).strftime("%H:%M")

        await loading_message.edit_text(
            "📅 LIVE-расписание Qazsport\n"
            f"Текущее время: {current_time} (UTC+5)\n\n"
            + schedule_text
        )

    except Exception as error:
        print(
            "Ошибка Qazsport:",
            repr(error)
        )

        await loading_message.edit_text(
            "❌ Не удалось загрузить "
            "расписание Qazsport."
        )


@dp.callback_query(F.data == "live")
async def live_callback(
    callback: CallbackQuery
):
    await callback.answer()

    loading_message = await callback.message.answer(
        "⏳ Проверяю, что идёт прямо сейчас..."
    )

    try:
        current_live_events = (
            await get_current_live_events()
        )

        current_time = datetime.now(
            KZ_TIMEZONE
        ).strftime("%H:%M")

        if not current_live_events:
            await loading_message.edit_text(
                "🔴 Сейчас LIVE\n\n"
                f"Текущее время: {current_time} "
                "(UTC+5)\n\n"
                "На Qazsport сейчас "
                "LIVE-событий нет."
            )
            return

        live_text = "\n\n".join(
            format_qazsport_event(event)
            for event in current_live_events
        )

        await loading_message.edit_text(
            "🔴 Сейчас LIVE\n\n"
            f"Текущее время: {current_time} "
            "(UTC+5)\n\n"
            + live_text
        )

    except Exception as error:
        print(
            "Ошибка Qazsport LIVE:",
            repr(error)
        )

        await loading_message.edit_text(
            "❌ Не удалось проверить "
            "текущие LIVE-события Qazsport."
        )


@dp.callback_query(F.data == "status")
async def status_callback(
    callback: CallbackQuery
):
    await callback.answer()

    await callback.message.answer(
        "📊 Статус SLP\n\n"
        "✅ Telegram-бот работает\n"
        "✅ Qazsport подключён\n"
        "✅ LIVE-фильтрация работает\n"
        "✅ UTC+5 учитывается\n"
        "✅ Переход через 00:00 работает\n"
        "✅ Вчерашний эфир после 00:00 учитывается\n"
        "✅ Окончание эфирного слота рассчитывается\n"
        "✅ Статус события рассчитывается\n\n"
        "Стадия: подключение источников 🛠"
    )


@dp.callback_query(
    F.data == "internet_check"
)
async def internet_check_callback(
    callback: CallbackQuery
):
    await callback.answer()

    await callback.message.answer(
        "🌐 Проверка в интернете\n\n"
        "Выберите тестовое событие "
        "для проверки:",
        reply_markup=build_verification_keyboard(),
    )


@dp.callback_query(
    F.data.startswith("verify_")
)
async def verify_event_callback(
    callback: CallbackQuery
):
    await callback.answer()

    event_index = int(
        callback.data.split("_")[1]
    )

    event = EVENTS[event_index]

    verification_result = (
        get_verification_result(event)
    )

    await callback.message.answer(
        "🌐 Проверка события\n\n"
        f"{event.title}\n"
        f"{event.sport}. "
        f"{event.tournament}\n\n"
        f"📺 Канал: {event.channel}\n"
        f"📅 Дата: {event.date}\n"
        f"🕐 Эфир телеканала: "
        f"{event.broadcast_start}\n"
        f"🏁 Официальный старт: "
        f"{event.event_start}\n"
        f"⏱ Разница: "
        f"{event.time_difference_minutes} минут\n\n"
        f"{verification_result}"
    )


@dp.callback_query(F.data == "team")
async def team_callback(
    callback: CallbackQuery
):
    await callback.answer()

    await callback.message.answer(
        "👥 Команда\n\n"
        "Редактор\n"
        "Промо-продюсер\n"
        "Переводчик\n"
        "Дизайнер\n\n"
        "Роли пользователей пока не настроены."
    )


async def main():
    print(
        "SLP запущен. "
        "Ожидаю сообщения в Telegram..."
    )

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
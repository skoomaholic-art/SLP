import asyncio
import os
from datetime import datetime

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
                text="🔴 LIVE",
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

    return (
        f"{date_text}, {event['time']} – "
        f"{event['sport']}. "
        f"{event['tournament']}. "
        f"{event['title']} | "
        f"{event['channel']}\n"
        f"↳ Ориентировочно до: {end_text}"
    )


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

        await loading_message.edit_text(
            "📅 LIVE-расписание Qazsport\n\n"
            + schedule_text
        )

    except Exception as error:
        print(
            "Ошибка Qazsport:",
            repr(error)
        )

        await loading_message.edit_text(
            "❌ Не удалось загрузить "
            "расписание Qazsport.\n\n"
            "Попробуйте ещё раз позже."
        )


@dp.callback_query(F.data == "live")
async def live_callback(
    callback: CallbackQuery
):
    await callback.answer()

    loading_message = await callback.message.answer(
        "⏳ Проверяю LIVE-события Qazsport..."
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
                "🔴 LIVE\n\n"
                "LIVE-событий Qazsport "
                "сейчас не найдено."
            )
            return

        live_text = "\n\n".join(
            format_qazsport_event(event)
            for event in live_events
        )

        await loading_message.edit_text(
            "🔴 LIVE Qazsport\n\n"
            + live_text
        )

    except Exception as error:
        print(
            "Ошибка Qazsport:",
            repr(error)
        )

        await loading_message.edit_text(
            "❌ Не удалось получить "
            "LIVE-события Qazsport."
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
        "✅ Переход через 00:00 работает\n"
        "✅ Окончание эфирного слота рассчитывается\n\n"
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
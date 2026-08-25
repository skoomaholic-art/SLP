import asyncio
import os

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from events import EVENTS


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


def format_event(event):
    return (
        f"{event.date}, {event.broadcast_start} – "
        f"{event.sport}. "
        f"{event.tournament}. "
        f"{event.title} | "
        f"{event.channel}"
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
                    text=f"{event.broadcast_start} — {event.title}",
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
async def schedule_callback(callback: CallbackQuery):
    await callback.answer()

    schedule_text = "\n\n".join(
        format_event(event)
        for event in EVENTS
    )

    await callback.message.answer(
        "📅 Расписание\n\n"
        + schedule_text
    )


@dp.callback_query(F.data == "live")
async def live_callback(callback: CallbackQuery):
    await callback.answer()

    live_events = [
        event
        for event in EVENTS
        if event.is_live
    ]

    if not live_events:
        await callback.message.answer(
            "🔴 LIVE\n\n"
            "Сейчас LIVE-событий нет."
        )
        return

    live_text = "\n\n".join(
        format_event(event)
        for event in live_events
    )

    await callback.message.answer(
        "🔴 LIVE-события\n\n"
        + live_text
    )


@dp.callback_query(F.data == "status")
async def status_callback(callback: CallbackQuery):
    await callback.answer()

    await callback.message.answer(
        "📊 Статус расписания\n\n"
        "Стадия: тестирование системы 🛠\n"
        f"Событий в расписании: {len(EVENTS)}"
    )


@dp.callback_query(F.data == "internet_check")
async def internet_check_callback(callback: CallbackQuery):
    await callback.answer()

    await callback.message.answer(
        "🌐 Проверка в интернете\n\n"
        "Выберите событие для проверки:",
        reply_markup=build_verification_keyboard(),
    )


@dp.callback_query(F.data.startswith("verify_"))
async def verify_event_callback(callback: CallbackQuery):
    await callback.answer()

    event_index = int(
        callback.data.split("_")[1]
    )

    event = EVENTS[event_index]

    verification_result = get_verification_result(event)

    await callback.message.answer(
        "🌐 Проверка события\n\n"
        f"{event.title}\n"
        f"{event.sport}. {event.tournament}\n\n"
        f"📺 Канал: {event.channel}\n"
        f"📅 Дата: {event.date}\n"
        f"🕐 Эфир телеканала: {event.broadcast_start}\n"
        f"🏁 Официальный старт: {event.event_start}\n"
        f"⏱ Разница: {event.time_difference_minutes} минут\n\n"
        f"{verification_result}"
    )


@dp.callback_query(F.data == "team")
async def team_callback(callback: CallbackQuery):
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
    print("SLP запущен. Ожидаю сообщения в Telegram...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
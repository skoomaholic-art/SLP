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

BOT_TOKEN = os.getenv("BOT_TOKEN")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# Пока это тестовые события.
# Позже этот список будет автоматически заполнять парсер.
EVENTS = [
    {
        "date": "25 августа",
        "time": "18:55",
        "sport": "Хоккей",
        "tournament": "Кубок Республики Казахстан. Финал",
        "title": "Торпедо – Сарыарқа",
        "channel": "Qazsport",
        "is_live": True,
    },
    {
        "date": "25 августа",
        "time": "21:00",
        "sport": "Теннис",
        "tournament": "US Open",
        "title": "Квалификация",
        "channel": "Eurosport 1",
        "is_live": False,
    },
    {
        "date": "25 августа",
        "time": "23:00",
        "sport": "Футбол",
        "tournament": "Ла Лига",
        "title": "Валенсия – Бетис",
        "channel": "Setanta Sports 1",
        "is_live": True,
    },
]


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
        f"{event['date']}, {event['time']} – "
        f"{event['sport']}. "
        f"{event['tournament']}. "
        f"{event['title']} | "
        f"{event['channel']}"
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
        if event["is_live"]
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
        "Модуль проверки событий пока не подключён."
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
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


main_keyboard = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(
                text="📊 Статус",
                callback_data="status"
            )
        ],
        [
            InlineKeyboardButton(
                text="🌐 Проверка в интернете",
                callback_data="internet_check"
            )
        ],
    ]
)


@dp.message(Command("start"))
async def start_command(message: Message):
    await message.answer(
        "SLP [Skoomaholic Live Parser] запущен ✅\n\n"
        "Выберите действие:",
        reply_markup=main_keyboard,
    )


@dp.callback_query(F.data == "status")
async def status_callback(callback: CallbackQuery):
    await callback.answer()

    await callback.message.answer(
        "📊 Статус расписания\n\n"
        "Стадия: тестирование системы 🛠"
    )


@dp.callback_query(F.data == "internet_check")
async def internet_check_callback(callback: CallbackQuery):
    await callback.answer()

    await callback.message.answer(
        "🌐 Проверка в интернете\n\n"
        "Модуль проверки событий пока не подключён."
    )


async def main():
    print("SLP запущен. Ожидаю сообщения в Telegram...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
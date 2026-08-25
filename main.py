import asyncio
import os

from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message

BOT_TOKEN = os.getenv("BOT_TOKEN")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


@dp.message(Command("start"))
async def start_command(message: Message):
    await message.answer(
        "SLP [Skoomaholic Live Parser] запущен ✅"
    )


async def main():
    print("SLP запущен. Ожидаю сообщения в Telegram...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
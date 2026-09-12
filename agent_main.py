"""Agent-enabled SLP entrypoint.

Both entrypoints now use the same DB-backed loader from ``main.py``. This file
only adds agent health UI; it no longer creates or monkey-patches a second
ParserOrchestrator instance.
"""

from __future__ import annotations

import asyncio

from aiogram import F
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import main as app
from agents.health import build_health_text


orchestrator = app.orchestrator


# Replace only the menu object. Existing handlers resolve app.main_keyboard at
# runtime, while schedule/LIVE/notifications all keep the shared main loader.
app.main_keyboard = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(text="📅 Расписание", callback_data="schedule"),
            InlineKeyboardButton(text="🔴 LIVE", callback_data="live"),
        ],
        [
            InlineKeyboardButton(text="🔔 Уведомления", callback_data="notifications"),
            InlineKeyboardButton(text="🧠 Health", callback_data="agent_health"),
        ],
        [
            InlineKeyboardButton(text="📥 Выгрузить", callback_data="export_schedule"),
        ],
    ]
)


@app.dp.message(Command("health"))
async def health_command(message: Message):
    await message.answer(build_health_text(orchestrator.database))


@app.dp.callback_query(F.data == "agent_health")
async def health_callback(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer(build_health_text(orchestrator.database))


async def main() -> None:
    print("SLP agent network enabled")
    print(f"SLP SQLite: {orchestrator.database.path}")
    await app.main()


if __name__ == "__main__":
    asyncio.run(main())

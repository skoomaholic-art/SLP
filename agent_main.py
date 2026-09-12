"""Agent-enabled SLP entrypoint.

Run this instead of main.py to enable persistent parser snapshots, source-health
checks, QA guardrails, fallback recovery and /health without rewriting the
stable Telegram UI in main.py.
"""

from __future__ import annotations

import asyncio
import time

from aiogram import F
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import main as app
from agents.health import build_health_text
from agents.orchestrator import ParserOrchestrator


orchestrator = ParserOrchestrator()


async def orchestrated_load_schedule_events(
    *,
    force_refresh: bool = False,
    return_errors: bool = False,
):
    """Drop-in replacement for main.load_schedule_events."""
    now_timestamp = time.time()

    if (
        not force_refresh
        and app.schedule_cache["events"]
        and (now_timestamp - app.schedule_cache["time"]) < app.SCHEDULE_CACHE_TTL
    ):
        events = app.schedule_cache["events"]
        errors = list(app.schedule_cache.get("source_errors", []))
        return (events, errors) if return_errors else events

    try:
        result = await orchestrator.refresh()
    except Exception as error:
        print("SLP orchestrator fatal error:", repr(error))
        cached = list(app.schedule_cache.get("events") or [])
        errors = [f"Orchestrator: {type(error).__name__}: {error}"]
        if cached:
            print("SLP orchestrator: using in-memory cache after fatal error")
            return (cached, errors) if return_errors else cached
        raise

    app.schedule_cache["events"] = result.events
    app.schedule_cache["time"] = now_timestamp
    app.schedule_cache["source_errors"] = list(result.source_errors)

    if result.source_errors:
        print("SLP orchestrator degraded:", " | ".join(result.source_errors))
    if result.source_warnings:
        print("SLP orchestrator warnings:", " | ".join(result.source_warnings))

    return (
        (result.events, result.source_errors)
        if return_errors
        else result.events
    )


# Existing callbacks in main.py resolve this global at runtime, so replacing it
# here upgrades schedule/LIVE/notification flows without duplicating handlers.
app.load_schedule_events = orchestrated_load_schedule_events


# Replace only the menu object. Existing /start reads app.main_keyboard at call
# time, while all original schedule/LIVE/notification callbacks stay intact.
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

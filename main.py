from __future__ import annotations

import asyncio

from aiogram import Bot, Dispatcher

from agents.orchestrator import ParserOrchestrator
from bot.handlers import router
from config import load_settings
from scheduler.jobs import scheduler_loop
from services.schedule_service import ScheduleService
from storage.database import SLPDatabase


async def main() -> None:
    settings = load_settings()
    database = SLPDatabase()
    orchestrator = ParserOrchestrator(database=database)
    schedule_service = ScheduleService(orchestrator)

    bot = Bot(token=settings.bot_token)
    dispatcher = Dispatcher()
    dispatcher.include_router(router)

    print("SLP v2 started")
    print(f"SQLite: {database.path}")
    print(f"Background refresh: every {settings.refresh_interval_seconds} sec")

    scheduler_task = asyncio.create_task(
        scheduler_loop(
            schedule_service,
            bot,
            settings.refresh_interval_seconds,
        )
    )

    try:
        await dispatcher.start_polling(
            bot,
            schedule_service=schedule_service,
            settings=settings,
        )
    finally:
        scheduler_task.cancel()
        await asyncio.gather(scheduler_task, return_exceptions=True)
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())

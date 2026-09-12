from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher

from agents.runtime_orchestrator import RuntimeParserOrchestrator
from bot.handlers import router
from config import load_settings
from scheduler.jobs import scheduler_loop
from services.logging_config import configure_logging
from services.schedule_service import ScheduleService
from storage.database import SLPDatabase


logger = logging.getLogger(__name__)


async def main() -> None:
    configure_logging()
    settings = load_settings()
    database = SLPDatabase()
    orchestrator = RuntimeParserOrchestrator(database=database)
    schedule_service = ScheduleService(orchestrator)

    bot = Bot(token=settings.bot_token)
    dispatcher = Dispatcher()
    dispatcher.include_router(router)

    logger.info(
        "SLP v2 starting sqlite=%s refresh_interval_seconds=%d",
        database.path,
        settings.refresh_interval_seconds,
    )

    try:
        initial = await schedule_service.refresh()
        logger.info(
            "startup refresh run=%s events=%d errors=%d warnings=%d",
            initial.run_id,
            len(initial.events),
            len(initial.source_errors),
            len(initial.source_warnings),
        )
    except Exception:
        logger.exception("startup refresh failed; starting with stored data")

    scheduler_task = asyncio.create_task(
        scheduler_loop(
            schedule_service,
            bot,
            settings.refresh_interval_seconds,
            initial_delay=True,
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
        logger.info("SLP v2 stopped")


if __name__ == "__main__":
    asyncio.run(main())

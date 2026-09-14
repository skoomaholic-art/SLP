from __future__ import annotations

import asyncio
import logging

from aiogram import Bot

from services.schedule_change_guard import systemic_one_day_shift
from services.schedule_service import ScheduleService
from services.schedule_watch import (
    build_change_messages,
    build_schedule_snapshot,
    diff_schedule_snapshots,
    get_previous_snapshot,
    get_subscribers,
    update_snapshot,
)


logger = logging.getLogger(__name__)


async def _send_changes(bot: Bot, changes: list[dict]) -> None:
    if not changes:
        return
    messages = build_change_messages(changes)
    for chat_id in get_subscribers():
        for text in messages:
            try:
                await bot.send_message(chat_id, text)
            except Exception:
                logger.exception(
                    "notification delivery failed chat_id=%s",
                    chat_id,
                )


async def refresh_and_notify(schedule_service: ScheduleService, bot: Bot) -> None:
    previous = get_previous_snapshot()
    result = await schedule_service.refresh()

    if result.source_errors:
        logger.warning(
            "refresh degraded run=%s errors=%s; continuing with healthy/fallback data",
            result.run_id,
            " | ".join(result.source_errors),
        )

    current = build_schedule_snapshot(schedule_service.get_events())
    if previous is None:
        update_snapshot(current)
        logger.info("scheduler baseline snapshot saved")
        return

    changes = diff_schedule_snapshots(previous, current)
    systemic_shift = systemic_one_day_shift(changes)

    update_snapshot(current)

    if systemic_shift:
        logger.warning(
            "scheduler suppressed systemic one-day remap channel=%s delta_minutes=%d events=%d",
            systemic_shift["channel"],
            systemic_shift["delta_minutes"],
            systemic_shift["event_count"],
        )
        return

    await _send_changes(bot, changes)
    if changes:
        logger.info("scheduler schedule_changes=%d", len(changes))


async def scheduler_loop(
    schedule_service: ScheduleService,
    bot: Bot,
    interval_seconds: int,
    *,
    initial_delay: bool = False,
) -> None:
    if initial_delay:
        await asyncio.sleep(interval_seconds)

    while True:
        try:
            await refresh_and_notify(schedule_service, bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("scheduler refresh failed")
        await asyncio.sleep(interval_seconds)

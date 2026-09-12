from __future__ import annotations

import asyncio

from aiogram import Bot

from services.schedule_service import ScheduleService
from services.schedule_watch import (
    build_change_messages,
    build_schedule_snapshot,
    diff_schedule_snapshots,
    get_previous_snapshot,
    get_subscribers,
    update_snapshot,
)


async def _send_changes(bot: Bot, changes: list[dict]) -> None:
    if not changes:
        return
    messages = build_change_messages(changes)
    for chat_id in get_subscribers():
        for text in messages:
            try:
                await bot.send_message(chat_id, text)
            except Exception as error:
                print(f"SLP notification error chat={chat_id}: {error!r}")


async def refresh_and_notify(schedule_service: ScheduleService, bot: Bot) -> None:
    previous = get_previous_snapshot()
    result = await schedule_service.refresh()

    if result.source_errors:
        print("SLP refresh degraded:", " | ".join(result.source_errors))
        return

    current = build_schedule_snapshot(schedule_service.get_events())
    if previous is None:
        update_snapshot(current)
        print("SLP scheduler: baseline snapshot saved")
        return

    changes = diff_schedule_snapshots(previous, current)
    update_snapshot(current)
    await _send_changes(bot, changes)
    if changes:
        print(f"SLP scheduler: {len(changes)} schedule change(s)")


async def scheduler_loop(
    schedule_service: ScheduleService,
    bot: Bot,
    interval_seconds: int,
) -> None:
    while True:
        try:
            await refresh_and_notify(schedule_service, bot)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            print("SLP scheduler error:", repr(error))
        await asyncio.sleep(interval_seconds)

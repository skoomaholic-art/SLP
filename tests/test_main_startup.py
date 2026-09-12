from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import main as app_main


class MainStartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_polling_starts_without_waiting_for_network_refresh(self) -> None:
        scheduler_started = asyncio.Event()
        observed_initial_delay: list[bool] = []

        async def fake_scheduler_loop(
            schedule_service,
            bot,
            interval_seconds: int,
            *,
            initial_delay: bool = False,
        ) -> None:
            observed_initial_delay.append(initial_delay)
            scheduler_started.set()
            await asyncio.Event().wait()

        fake_schedule_service = SimpleNamespace(refresh=AsyncMock())
        fake_bot = SimpleNamespace(
            session=SimpleNamespace(close=AsyncMock()),
        )
        fake_dispatcher = MagicMock()
        fake_dispatcher.include_router = MagicMock()

        async def fake_start_polling(*args, **kwargs) -> None:
            await asyncio.wait_for(scheduler_started.wait(), timeout=0.5)

        fake_dispatcher.start_polling = AsyncMock(side_effect=fake_start_polling)
        fake_settings = SimpleNamespace(
            bot_token="test-token",
            refresh_interval_seconds=240,
        )
        fake_database = SimpleNamespace(path="/tmp/slp-test.db")

        with (
            patch.object(app_main, "configure_logging"),
            patch.object(app_main, "load_settings", return_value=fake_settings),
            patch.object(app_main, "SLPDatabase", return_value=fake_database),
            patch.object(app_main, "RuntimeParserOrchestrator", return_value=object()),
            patch.object(app_main, "ScheduleService", return_value=fake_schedule_service),
            patch.object(app_main, "Bot", return_value=fake_bot),
            patch.object(app_main, "Dispatcher", return_value=fake_dispatcher),
            patch.object(app_main, "scheduler_loop", side_effect=fake_scheduler_loop),
        ):
            await asyncio.wait_for(app_main.main(), timeout=1.0)

        fake_schedule_service.refresh.assert_not_awaited()
        fake_dispatcher.start_polling.assert_awaited_once()
        fake_bot.session.close.assert_awaited_once()
        self.assertEqual(observed_initial_delay, [False])


if __name__ == "__main__":
    unittest.main()

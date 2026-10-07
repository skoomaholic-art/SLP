import asyncio
import tempfile
import unittest
from pathlib import Path

from config import Settings
from parsers.qazsport import LIVE_WORD_PATTERN
from services.schedule_service import ScheduleService
from services.schedule_watch import load_runtime_state, save_runtime_state


def _settings(admin_ids):
    return Settings(
        bot_token="",
        refresh_interval_seconds=240,
        admin_ids=frozenset(admin_ids),
    )


class AdminAccessTests(unittest.TestCase):
    def test_empty_admin_ids_denies_everyone(self):
        self.assertFalse(_settings([]).is_admin(123))

    def test_listed_admin_is_allowed(self):
        settings = _settings([123])
        self.assertTrue(settings.is_admin(123))
        self.assertFalse(settings.is_admin(456))


class QazsportLiveWordTests(unittest.TestCase):
    def test_live_marker_matches_whole_word(self):
        for text in ("LIVE Футбол", "Футбол (live)", "Хоккей. LIVE", "LIVE-трансляция"):
            self.assertTrue(LIVE_WORD_PATTERN.search(text), text)

    def test_words_containing_live_do_not_match(self):
        for text in ("Liverpool - Arsenal", "Oliver Kahn", "Deliver", "Liverpool LIVERPOOL"):
            self.assertFalse(LIVE_WORD_PATTERN.search(text), text)


class _SlowOrchestrator:
    def __init__(self):
        self.database = None
        self.calls = 0

    async def refresh(self):
        self.calls += 1
        await asyncio.sleep(0.05)
        return f"result-{self.calls}"


class RefreshLockTests(unittest.TestCase):
    def test_concurrent_refreshes_run_one_scrape(self):
        orchestrator = _SlowOrchestrator()
        service = ScheduleService(orchestrator)

        async def run():
            return await asyncio.gather(service.refresh(), service.refresh(), service.refresh())

        results = asyncio.run(run())
        self.assertEqual(orchestrator.calls, 1)
        self.assertEqual(results, ["result-1"] * 3)

    def test_sequential_refreshes_each_scrape(self):
        orchestrator = _SlowOrchestrator()
        service = ScheduleService(orchestrator)

        async def run():
            await service.refresh()
            return await service.refresh()

        self.assertEqual(asyncio.run(run()), "result-2")
        self.assertEqual(orchestrator.calls, 2)


class RuntimeStateTests(unittest.TestCase):
    def test_corrupt_state_is_backed_up_before_reset(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "slp_state.json"
            target.write_text('{"subscribers": [1, 2', encoding="utf-8")

            with self.assertLogs("services.schedule_watch", level="ERROR"):
                state = load_runtime_state(target)

            self.assertEqual(state["subscribers"], [])
            backups = list(Path(folder).glob("slp_state.json.corrupt-*"))
            self.assertEqual(len(backups), 1)
            self.assertIn("[1, 2", backups[0].read_text(encoding="utf-8"))

    def test_valid_state_round_trips(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "slp_state.json"
            save_runtime_state({"version": 1, "subscribers": [5], "snapshot": None}, target)
            self.assertEqual(load_runtime_state(target)["subscribers"], [5])


if __name__ == "__main__":
    unittest.main()

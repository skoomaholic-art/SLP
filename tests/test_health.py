import unittest

from agents.health import CHANNEL_SOURCES, build_health_text


class FakeDatabase:
    def latest_source_runs(self):
        return {
            source: {
                "status": "ok",
                "scope_date": "2026-09-13",
                "details": {"reason": "source_output_looks_normal"},
            }
            for source in {source for _, source in CHANNEL_SOURCES}
        }

    def load_active_source_snapshot(self, source, scope_date):
        self.assert_scope_date(scope_date)
        return {
            "qazsport": [{"channel": "Qazsport"}],
            "sportplus": [],
            "tvguide": [
                {"channel": "KHL HD"},
                {"channel": "KHL HD"},
                {"channel": "Eurosport"},
            ],
        }[source]

    @staticmethod
    def assert_scope_date(scope_date):
        if scope_date != "2026-09-13":
            raise AssertionError(scope_date)

    @staticmethod
    def latest_agent_run():
        return None

    @staticmethod
    def unresolved_incidents(limit=5):
        return []

    @staticmethod
    def active_event_count():
        return 4


class HealthTests(unittest.TestCase):
    def test_health_lists_all_14_channels_separately(self):
        text = build_health_text(FakeDatabase())
        channel_names = [name for name, _ in CHANNEL_SOURCES]

        self.assertEqual(len(channel_names), 14)
        self.assertEqual(len(set(channel_names)), 14)
        for name in channel_names:
            with self.subTest(channel=name):
                self.assertIn(f"🟢 {name}\n", text)
        self.assertNotIn("TV+ / Mobikino (14 каналов)", text)

    def test_health_counts_active_events_per_channel(self):
        text = build_health_text(FakeDatabase())

        self.assertIn("🟢 KHL HD\n   2026-09-13 · 2 событий", text)
        self.assertIn("🟢 Eurosport\n   2026-09-13 · 1 событий", text)
        self.assertIn("🟢 Sport+ Qazaqstan\n   2026-09-13 · 0 событий", text)


if __name__ == "__main__":
    unittest.main()

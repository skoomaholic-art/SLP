import tempfile
import unittest
from pathlib import Path

from services import editorial_notifications as notices
from services import editorial_store as editorial
from storage.database import SLPDatabase


class EditorialNotificationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = SLPDatabase(Path(self.tmp.name) / "slp.db")
        notices.init_notifications(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def _event(self):
        return {
            "source": "tvguide",
            "source_url": "https://example.test/epg",
            "date": "2026-10-03",
            "time": "20:00",
            "channel": "SETANTA SPORTS 1",
            "sport": "Футбол",
            "tournament": "Премьер-лига",
            "title": "Арсенал - Челси",
            "raw_title": "Арсенал - Челси",
            "is_live": True,
            "is_live_broadcast": True,
        }

    def test_notification_read_state_is_persistent(self):
        item = notices.create_notification(
            self.db,
            dedupe_key="network:test",
            kind="time_mismatch",
            level="attention",
            title="Арсенал - Челси",
            message="Время отличается",
            source_name="ESPN",
            source_url="https://www.espn.com/",
            patch={"time": "20:30"},
        )
        self.assertFalse(item["read"])
        read = notices.mark_read(self.db, item["id"])
        self.assertTrue(read["read"])
        self.assertEqual(read["status"], "read")
        again = notices.create_notification(
            self.db,
            dedupe_key="network:test",
            kind="time_mismatch",
            level="attention",
            title="Арсенал - Челси",
            message="Время всё ещё отличается",
            source_name="ESPN",
            source_url="https://www.espn.com/",
            patch={"time": "20:30"},
        )
        self.assertEqual(again["status"], "read")

    def test_editorial_patch_supports_date_channel_and_cancellation(self):
        self.db.upsert_source_snapshot(
            run_id="r1", source="tvguide", scope_date="2026-10-03",
            events=[self._event()],
        )
        with self.db._connect() as conn:
            storage_id = conn.execute(
                "SELECT storage_id FROM events LIMIT 1"
            ).fetchone()["storage_id"]
        result = editorial.apply_edit(
            self.db,
            storage_id=storage_id,
            username="Editor",
            values={
                "date": "2026-10-04",
                "time": "21:00",
                "channel": "SETANTA SPORTS 2",
                "cancelled": "true",
            },
        )
        self.assertEqual(result["values"]["date"], "2026-10-04")
        self.assertEqual(result["values"]["channel"], "SETANTA SPORTS 2")
        self.assertEqual(result["values"]["cancelled"], "true")


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

from services.schedule_watch import (
    build_change_messages,
    build_schedule_snapshot,
    diff_schedule_snapshots,
    format_schedule_change,
    get_previous_snapshot,
    get_subscribers,
    is_subscribed,
    set_subscription,
    stable_event_identity,
    update_snapshot,
)


KZ = ZoneInfo("Asia/Almaty")


def event(
    *,
    channel="Qazsport",
    date="2026-08-29",
    time="18:50",
    end="21:00",
    title="Кайрат – Андерлехт",
    sport="Футбол",
    tournament="Лига чемпионов УЕФА",
):
    return {
        "source": channel,
        "source_url": "https://example.com",
        "channel": channel,
        "date": date,
        "time": time,
        "sport": sport,
        "tournament": tournament,
        "title": title,
        "is_live": True,
        "raw_title": title,
        "estimated_broadcast_end_date": date,
        "estimated_broadcast_end": end,
        "end_estimation_method": "next_program",
        "end_confidence": "high",
    }


class ScheduleWatchTests(unittest.TestCase):
    def test_stable_identity_ignores_time_and_channel(self):
        first = event(channel="Qazsport", time="18:50")
        second = event(channel="Sport+ Qazaqstan", time="19:20")
        self.assertEqual(stable_event_identity(first), stable_event_identity(second))

    def test_time_shift_is_update_not_delete_plus_add(self):
        old = build_schedule_snapshot([event(time="18:50", end="21:00")])
        new = build_schedule_snapshot([event(time="19:20", end="21:30")])
        changes = diff_schedule_snapshots(old, new)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["type"], "updated")
        fields = {item["field"] for item in changes[0]["changes"]}
        self.assertIn("time", fields)
        self.assertIn("end", fields)

    def test_channel_change_is_update(self):
        old = build_schedule_snapshot([event(channel="Qazsport")])
        new = build_schedule_snapshot([event(channel="Sport+ Qazaqstan")])
        changes = diff_schedule_snapshots(old, new)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["type"], "updated")
        self.assertEqual(changes[0]["changes"][0]["field"], "channels")

    def test_new_event_is_added(self):
        old = build_schedule_snapshot([])
        new = build_schedule_snapshot([event()])
        changes = diff_schedule_snapshots(old, new)
        self.assertEqual([item["type"] for item in changes], ["added"])

    def test_missing_event_is_removed_while_still_relevant(self):
        old = build_schedule_snapshot([event()])
        new = build_schedule_snapshot([])
        now = datetime(2026, 8, 29, 19, 0, tzinfo=KZ)
        changes = diff_schedule_snapshots(old, new, now=now)
        self.assertEqual([item["type"] for item in changes], ["removed"])

    def test_finished_event_expiration_does_not_send_removed_alert(self):
        old = build_schedule_snapshot([event(time="18:50", end="21:00")])
        new = build_schedule_snapshot([])
        now = datetime(2026, 8, 29, 22, 0, tzinfo=KZ)
        changes = diff_schedule_snapshots(old, new, now=now)
        self.assertEqual(changes, [])

    def test_small_end_change_is_ignored(self):
        old = build_schedule_snapshot([event(end="21:00")])
        new = build_schedule_snapshot([event(end="21:10")])
        self.assertEqual(diff_schedule_snapshots(old, new), [])

    def test_15_minute_end_change_is_reported(self):
        old = build_schedule_snapshot([event(end="21:00")])
        new = build_schedule_snapshot([event(end="21:15")])
        changes = diff_schedule_snapshots(old, new)
        self.assertEqual(changes[0]["changes"][0]["field"], "end")

    def test_simulcast_channels_are_one_snapshot_event(self):
        snapshot = build_schedule_snapshot(
            [
                event(channel="Qazsport", time="18:50"),
                event(channel="Sport+ Qazaqstan", time="19:00"),
            ]
        )
        self.assertEqual(len(snapshot["events"]), 1)
        self.assertEqual(len(snapshot["events"][0]["broadcasts"]), 2)

    def test_notification_text_contains_arrow_for_time_change(self):
        old = build_schedule_snapshot([event(time="18:50")])
        new = build_schedule_snapshot([event(time="19:20")])
        change = diff_schedule_snapshots(old, new)[0]
        text = format_schedule_change(change)
        self.assertIn("🔔 Изменение расписания", text)
        self.assertIn("→", text)
        self.assertIn("Qazsport", text)

    def test_multiple_changes_are_batched(self):
        old = build_schedule_snapshot([])
        new = build_schedule_snapshot(
            [
                event(title="Матч 1"),
                event(title="Матч 2", time="21:00", end="23:00"),
            ]
        )
        changes = diff_schedule_snapshots(old, new)
        messages = build_change_messages(changes)
        self.assertEqual(len(messages), 1)
        self.assertIn("Матч 1", messages[0])
        self.assertIn("Матч 2", messages[0])

    def test_subscription_and_snapshot_persist(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            self.assertFalse(is_subscribed(123, path))
            set_subscription(123, True, path)
            self.assertTrue(is_subscribed(123, path))
            self.assertEqual(get_subscribers(path), [123])

            snapshot = build_schedule_snapshot([event()])
            update_snapshot(snapshot, path)
            self.assertEqual(get_previous_snapshot(path), snapshot)

            set_subscription(123, False, path)
            self.assertFalse(is_subscribed(123, path))


if __name__ == "__main__":
    unittest.main()

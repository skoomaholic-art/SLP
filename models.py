from dataclasses import dataclass


@dataclass
class SportEvent:
    date: str
    broadcast_start: str

    sport: str
    tournament: str
    title: str
    channel: str

    is_live: bool

    event_start: str | None = None
    verification_status: str = "not_checked"

    @property
    def time_difference_minutes(self):
        if self.event_start is None:
            return None

        broadcast_hour, broadcast_minute = map(
            int,
            self.broadcast_start.split(":")
        )

        event_hour, event_minute = map(
            int,
            self.event_start.split(":")
        )

        broadcast_total = (
            broadcast_hour * 60
            + broadcast_minute
        )

        event_total = (
            event_hour * 60
            + event_minute
        )

        difference = abs(
            event_total - broadcast_total
        )

        return min(
            difference,
            1440 - difference
        )
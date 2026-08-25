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
    time_difference_minutes: int | None = None
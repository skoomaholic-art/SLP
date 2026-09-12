from __future__ import annotations

from datetime import datetime, timedelta

from agents.orchestrator import ParserOrchestrator, RefreshResult
from services.schedule_merge import merge_source_schedules
from services.time_logic import KZ_TIMEZONE, get_event_status


SOURCE_KEYS = ("qazsport", "sportplus")


def is_user_event(event: dict) -> bool:
    title = str(event.get("title") or event.get("raw_title") or "").casefold()
    sport = str(event.get("sport") or "").strip()
    tournament = str(event.get("tournament") or "").strip()
    studio_markers = (
        "студийная программа",
        "студиялық бағдарлама",
        "перед матчем",
        "матч қарсаңында",
    )
    return not (
        any(marker in title for marker in studio_markers)
        and not sport
        and not tournament
    )


class ScheduleService:
    """Single application service for parser refreshes and bot reads.

    Parsers write accepted source snapshots to SQLite through ParserOrchestrator.
    Telegram reads those accepted snapshots instead of scraping sites on every click.
    """

    def __init__(self, orchestrator: ParserOrchestrator | None = None):
        self.orchestrator = orchestrator or ParserOrchestrator()
        self.database = self.orchestrator.database
        self.last_refresh: RefreshResult | None = None

    async def refresh(self) -> RefreshResult:
        self.last_refresh = await self.orchestrator.refresh()
        return self.last_refresh

    def _source_snapshot(self, source: str, scope_date: str) -> list[dict]:
        return self.database.load_active_source_snapshot(source, scope_date)

    def get_events(self, *, now: datetime | None = None) -> list[dict]:
        if now is None:
            now = datetime.now(KZ_TIMEZONE)
        elif now.tzinfo is None:
            now = now.replace(tzinfo=KZ_TIMEZONE)
        else:
            now = now.astimezone(KZ_TIMEZONE)

        today = now.date()
        yesterday = today - timedelta(days=1)

        today_schedule = merge_source_schedules(
            *[
                self._source_snapshot(source, today.isoformat())
                for source in SOURCE_KEYS
            ]
        )
        yesterday_schedule = merge_source_schedules(
            *[
                self._source_snapshot(source, yesterday.isoformat())
                for source in SOURCE_KEYS
            ]
        )

        today_events = [
            event
            for event in today_schedule
            if event.get("is_live", False) and is_user_event(event)
        ]
        yesterday_events = [
            event
            for event in yesterday_schedule
            if (
                event.get("is_live", False)
                and is_user_event(event)
                and get_event_status(event, now=now) == "live"
            )
        ]

        return merge_source_schedules(yesterday_events, today_events)

    def get_live_events(self, *, now: datetime | None = None) -> list[dict]:
        if now is None:
            now = datetime.now(KZ_TIMEZONE)
        return [
            event
            for event in self.get_events(now=now)
            if get_event_status(event, now=now) == "live"
        ]

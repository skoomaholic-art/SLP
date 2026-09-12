from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from agents.orchestrator import ParserOrchestrator, RefreshResult
from services.live_evidence import event_is_live_broadcast, event_is_schedule_candidate
from services.schedule_merge import merge_source_schedules
from services.time_logic import KZ_TIMEZONE, get_event_status


logger = logging.getLogger(__name__)
SCHEDULE_LOOKAHEAD_DAYS = 14


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


def _date_range(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


class ScheduleService:
    """Application service for confirmed LIVE-broadcast schedule reads."""

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
        first_scope = today - timedelta(days=1)
        last_scope = today + timedelta(days=SCHEDULE_LOOKAHEAD_DAYS)

        snapshots: list[list[dict]] = []
        for scope_date in _date_range(first_scope, last_scope):
            scope_text = scope_date.isoformat()
            for source in self.orchestrator.loaders:
                snapshots.append(self._source_snapshot(source, scope_text))

        merged = merge_source_schedules(*snapshots)
        result: list[dict] = []
        skipped = {
            "not_confirmed_direct": 0,
            "not_sport_candidate": 0,
            "non_sport_studio": 0,
            "past_not_on_air": 0,
        }

        for event in merged:
            # SLP is a LIVE-broadcast parser, not a generic EPG browser. Unknown
            # TV programmes and catch-up/replay rows stay in source snapshots for
            # diagnostics but never enter the public schedule/export.
            if not event_is_live_broadcast(event):
                skipped["not_confirmed_direct"] += 1
                continue

            if not event_is_schedule_candidate(event):
                skipped["not_sport_candidate"] += 1
                continue

            if not is_user_event(event):
                skipped["non_sport_studio"] += 1
                continue

            status = get_event_status(event, now=now)
            if str(event.get("date") or "") < today.isoformat() and status != "live":
                skipped["past_not_on_air"] += 1
                continue

            result.append(event)

        live_count = sum(
            1 for event in result if get_event_status(event, now=now) == "live"
        )
        upcoming_count = sum(
            1 for event in result if get_event_status(event, now=now) == "upcoming"
        )
        finished_count = len(result) - live_count - upcoming_count

        logger.info(
            "schedule read candidates=%d returned=%d direct_live=%d upcoming=%d "
            "finished=%d skipped=%s now=%s timezone=Asia/Almaty horizon=%s",
            len(merged),
            len(result),
            live_count,
            upcoming_count,
            finished_count,
            skipped,
            now.isoformat(timespec="seconds"),
            last_scope.isoformat(),
        )
        return result

    def get_live_events(self, *, now: datetime | None = None) -> list[dict]:
        if now is None:
            now = datetime.now(KZ_TIMEZONE)
        elif now.tzinfo is None:
            now = now.replace(tzinfo=KZ_TIMEZONE)
        else:
            now = now.astimezone(KZ_TIMEZONE)

        events = [
            event
            for event in self.get_events(now=now)
            if get_event_status(event, now=now) == "live"
        ]
        logger.info(
            "direct live query count=%d now=%s timezone=Asia/Almaty",
            len(events),
            now.isoformat(timespec="seconds"),
        )
        return events

    def get_upcoming_events(
        self,
        *,
        now: datetime | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        if now is None:
            now = datetime.now(KZ_TIMEZONE)
        elif now.tzinfo is None:
            now = now.replace(tzinfo=KZ_TIMEZONE)
        else:
            now = now.astimezone(KZ_TIMEZONE)

        events = [
            event
            for event in self.get_events(now=now)
            if get_event_status(event, now=now) == "upcoming"
        ]
        if limit is not None:
            return events[: max(int(limit), 0)]
        return events

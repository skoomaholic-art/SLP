from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Awaitable, Callable

from agents.qa_agent import ParserQAAgent, QAResult
from agents.source_agent import SourceAssessment, SourceHealthAgent
from parsers.qazsport import get_qazsport_schedule
from parsers.sportplus_cached import get_sportplus_schedule_cached
from services.schedule_merge import merge_source_schedules
from services.time_logic import KZ_TIMEZONE, get_event_status
from storage.database import SLPDatabase


logger = logging.getLogger(__name__)
SourceLoader = Callable[[date], Awaitable[list[dict]]]

SOURCE_LABELS = {
    "qazsport": "Qazsport",
    "sportplus": "Sport+ Qazaqstan",
}
SPORTPLUS_LOOKAHEAD_DAYS = 14


def _qazsport_lookahead_days(today: date) -> int:
    """Cover the current TV week and at least the nearest Monday."""
    days_to_sunday = (6 - today.weekday()) % 7
    days_to_monday = (7 - today.weekday()) % 7
    return max(days_to_sunday, days_to_monday)


def source_lookahead_days(source: str, today: date) -> int:
    if source == "qazsport":
        return _qazsport_lookahead_days(today)
    if source == "sportplus":
        return SPORTPLUS_LOOKAHEAD_DAYS
    return 7


@dataclass
class SourceRunResult:
    source: str
    scope_date: str
    status: str
    events: list[dict]
    fresh_event_count: int
    previous_count: int | None
    reason: str
    required: bool = True
    used_fallback: bool = False
    qa_errors: list[str] = field(default_factory=list)
    qa_warnings: list[str] = field(default_factory=list)


@dataclass
class RefreshResult:
    run_id: str
    events: list[dict]
    source_errors: list[str]
    source_warnings: list[str]
    source_runs: list[SourceRunResult]


async def _default_qazsport_loader(target_date: date) -> list[dict]:
    today = datetime.now(KZ_TIMEZONE).date()
    events = await get_qazsport_schedule(
        target_date,
        include_current_live=(target_date == today),
    )
    if events and not any(
        str(event.get("date") or "") == target_date.isoformat()
        for event in events
    ):
        raise RuntimeError(
            f"Qazsport returned a different schedule date for {target_date.isoformat()}"
        )
    return events


async def _default_sportplus_loader(target_date: date) -> list[dict]:
    return await get_sportplus_schedule_cached(target_date)


def _is_user_event(event: dict) -> bool:
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


def _event_is_relevant(event: dict, *, now: datetime) -> bool:
    if not event.get("is_live", False):
        logger.debug(
            "[%s] skip reason=not_direct date=%s time=%s title=%r",
            event.get("source"),
            event.get("date"),
            event.get("time"),
            event.get("raw_title"),
        )
        return False
    if not _is_user_event(event):
        logger.debug(
            "[%s] skip reason=non_sport_studio date=%s time=%s title=%r",
            event.get("source"),
            event.get("date"),
            event.get("time"),
            event.get("raw_title"),
        )
        return False

    event_date = str(event.get("date") or "")
    if event_date < now.date().isoformat() and get_event_status(event, now=now) != "live":
        logger.debug(
            "[%s] skip reason=past_not_live date=%s time=%s title=%r",
            event.get("source"),
            event.get("date"),
            event.get("time"),
            event.get("raw_title"),
        )
        return False
    return True


class ParserOrchestrator:
    """Coordinates isolated source loading, QA, fallback and persistence."""

    def __init__(
        self,
        database: SLPDatabase | None = None,
        loaders: dict[str, SourceLoader] | None = None,
    ):
        self.database = database or SLPDatabase()
        self.source_agent = SourceHealthAgent(self.database)
        self.qa_agent = ParserQAAgent()
        self.loaders: dict[str, SourceLoader] = loaders or {
            "qazsport": _default_qazsport_loader,
            "sportplus": _default_sportplus_loader,
        }

    async def _call_loader(
        self,
        loader: SourceLoader,
        target_date: date,
        *,
        required: bool,
    ) -> list[dict]:
        attempts = 2 if required else 1
        for attempt in range(1, attempts + 1):
            try:
                return list(await loader(target_date))
            except Exception:
                if attempt >= attempts:
                    raise
                logger.warning(
                    "source retry date=%s attempt=%d/%d",
                    target_date.isoformat(),
                    attempt,
                    attempts,
                    exc_info=True,
                )
                await asyncio.sleep(0.5 * attempt)
        return []

    async def _load_one(
        self,
        *,
        run_id: str,
        source: str,
        target_date: date,
        required: bool,
    ) -> SourceRunResult:
        scope_date = target_date.isoformat()
        loader = self.loaders[source]
        error: Exception | None = None

        try:
            fresh_events = await self._call_loader(
                loader,
                target_date,
                required=required,
            )
        except Exception as caught:
            error = caught
            fresh_events = []
            logger.exception(
                "[%s] source load failed date=%s required=%s",
                source.upper(),
                scope_date,
                required,
            )

        previous = self.database.latest_successful_count(source, scope_date)

        # A future page may simply not be published yet. Keep a stored snapshot if
        # one exists, but do not degrade the entire current feed for an optional date.
        if error is not None and not required:
            selected_events = self.database.load_active_source_snapshot(source, scope_date)
            used_fallback = bool(selected_events)
            status = "warning" if used_fallback else "unavailable"
            reason = (
                "future_source_error_using_fallback"
                if used_fallback
                else f"future_schedule_unavailable: {type(error).__name__}"
            )
            self.database.record_parser_run(
                run_id=run_id,
                source=source,
                scope_date=scope_date,
                status=status,
                event_count=0,
                previous_count=previous,
                error=str(error),
                details={
                    "reason": reason,
                    "used_fallback": used_fallback,
                    "selected_count": len(selected_events),
                },
            )
            logger.info(
                "[%s] date=%s fetched=0 accepted=%d status=%s reason=%s",
                source.upper(),
                scope_date,
                len(selected_events),
                status,
                reason,
            )
            return SourceRunResult(
                source=source,
                scope_date=scope_date,
                status=status,
                events=selected_events,
                fresh_event_count=0,
                previous_count=previous,
                reason=reason,
                required=False,
                used_fallback=used_fallback,
            )

        # Qazsport returns the full TV grid, so an empty current/yesterday grid is
        # never a legitimate "no live sport today" answer.
        if (
            error is None
            and required
            and source == "qazsport"
            and not fresh_events
        ):
            error = RuntimeError("Qazsport returned an empty TV schedule")

        assessment: SourceAssessment = self.source_agent.assess(
            source=source,
            scope_date=scope_date,
            current_count=len(fresh_events),
            error=error,
        )

        qa_result = QAResult(ok=True)
        if error is None:
            qa_result = self.qa_agent.validate_batch(
                fresh_events,
                expected_source=source,
            )

        final_status = assessment.status
        reason = assessment.reason
        if not qa_result.ok:
            final_status = "blocked"
            reason = "qa_failed"

        publish_allowed = assessment.publish_allowed and qa_result.ok
        used_fallback = False

        if publish_allowed:
            selected_events = fresh_events
            self.database.upsert_source_snapshot(
                run_id=run_id,
                source=source,
                scope_date=scope_date,
                events=fresh_events,
            )
        else:
            selected_events = self.database.load_active_source_snapshot(
                source,
                scope_date,
            )
            used_fallback = bool(selected_events)

            if error is not None:
                incident_type = "source_error"
                message = f"{SOURCE_LABELS.get(source, source)}: {error}"
            elif not qa_result.ok:
                incident_type = "qa_failed"
                message = "; ".join(qa_result.errors[:5]) or "QA failed"
            else:
                incident_type = "source_anomaly"
                message = assessment.reason

            self.database.record_incident(
                run_id=run_id,
                source=source,
                scope_date=scope_date,
                incident_type=incident_type,
                severity=assessment.severity or "critical",
                message=message,
                details={
                    "current_count": len(fresh_events),
                    "previous_count": assessment.previous_count,
                    "used_fallback": used_fallback,
                    "qa_errors": qa_result.errors[:20],
                    "qa_warnings": qa_result.warnings[:20],
                },
            )

        self.database.record_parser_run(
            run_id=run_id,
            source=source,
            scope_date=scope_date,
            status=final_status,
            event_count=len(fresh_events),
            previous_count=assessment.previous_count,
            error=str(error) if error else None,
            details={
                "reason": reason,
                "used_fallback": used_fallback,
                "selected_count": len(selected_events),
                "qa_errors": qa_result.errors[:20],
                "qa_warnings": qa_result.warnings[:20],
            },
        )

        status_counts = {"live": 0, "upcoming": 0, "finished": 0}
        for event in selected_events:
            if not event.get("is_live", False):
                continue
            status = get_event_status(event)
            status_counts[status] = status_counts.get(status, 0) + 1

        logger.info(
            "[%s] date=%s fetched=%d accepted=%d live=%d upcoming=%d "
            "finished=%d status=%s reason=%s now=%s timezone=Asia/Almaty",
            source.upper(),
            scope_date,
            len(fresh_events),
            len(selected_events),
            status_counts.get("live", 0),
            status_counts.get("upcoming", 0),
            status_counts.get("finished", 0),
            final_status,
            reason,
            datetime.now(KZ_TIMEZONE).isoformat(timespec="seconds"),
        )

        return SourceRunResult(
            source=source,
            scope_date=scope_date,
            status=final_status,
            events=selected_events,
            fresh_event_count=len(fresh_events),
            previous_count=assessment.previous_count,
            reason=reason,
            required=required,
            used_fallback=used_fallback,
            qa_errors=qa_result.errors,
            qa_warnings=qa_result.warnings,
        )

    async def refresh(self, *, now: datetime | None = None) -> RefreshResult:
        if now is None:
            now = datetime.now(KZ_TIMEZONE)
        elif now.tzinfo is None:
            now = now.replace(tzinfo=KZ_TIMEZONE)
        else:
            now = now.astimezone(KZ_TIMEZONE)

        run_id = uuid.uuid4().hex[:12]
        self.database.start_agent_run(run_id)

        today = now.date()
        yesterday = today - timedelta(days=1)
        specs: list[tuple[str, date, bool]] = []

        for source in self.loaders:
            lookahead = source_lookahead_days(source, today)
            for offset in range(-1, lookahead + 1):
                target_date = today + timedelta(days=offset)
                specs.append(
                    (
                        source,
                        target_date,
                        target_date in {yesterday, today},
                    )
                )

        source_runs = await asyncio.gather(
            *[
                self._load_one(
                    run_id=run_id,
                    source=source,
                    target_date=target_date,
                    required=required,
                )
                for source, target_date, required in specs
            ]
        )

        all_events = merge_source_schedules(
            *[result.events for result in source_runs]
        )
        events = [
            event
            for event in all_events
            if _event_is_relevant(event, now=now)
        ]

        source_errors: list[str] = []
        source_warnings: list[str] = []

        for result in source_runs:
            label = f"{SOURCE_LABELS.get(result.source, result.source)} {result.scope_date}"
            if result.required and result.status in {"blocked", "error"}:
                suffix = " (fallback)" if result.used_fallback else ""
                source_errors.append(f"{label}: {result.reason}{suffix}")
            elif result.status == "warning" or result.qa_warnings:
                reason = result.reason
                if result.qa_warnings:
                    reason += "; " + "; ".join(result.qa_warnings[:3])
                source_warnings.append(f"{label}: {reason}")

        run_status = "degraded" if source_errors else "ok"
        summary = {
            "event_count": len(events),
            "source_errors": source_errors,
            "source_warnings": source_warnings,
            "now": now.isoformat(),
            "timezone": "Asia/Almaty",
            "horizon": max(
                (
                    result.scope_date
                    for result in source_runs
                    if result.events
                ),
                default=today.isoformat(),
            ),
        }
        self.database.finish_agent_run(run_id, run_status, summary)

        logger.info(
            "orchestrator run=%s status=%s events=%d source_errors=%d "
            "source_warnings=%d horizon=%s now=%s timezone=Asia/Almaty",
            run_id,
            run_status,
            len(events),
            len(source_errors),
            len(source_warnings),
            summary["horizon"],
            now.isoformat(timespec="seconds"),
        )

        return RefreshResult(
            run_id=run_id,
            events=events,
            source_errors=source_errors,
            source_warnings=source_warnings,
            source_runs=source_runs,
        )

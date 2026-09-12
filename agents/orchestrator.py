from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Awaitable, Callable

from agents.qa_agent import ParserQAAgent, QAResult
from agents.source_agent import SourceAssessment, SourceHealthAgent
from parsers.qazsport import get_qazsport_schedule
from parsers.sportplus import get_sportplus_schedule
from services.schedule_merge import merge_source_schedules
from services.time_logic import KZ_TIMEZONE, get_event_status
from storage.database import SLPDatabase


SourceLoader = Callable[[date], Awaitable[list[dict]]]

SOURCE_LABELS = {
    "qazsport": "Qazsport",
    "sportplus": "Sport+ Qazaqstan",
}


@dataclass
class SourceRunResult:
    source: str
    scope_date: str
    status: str
    events: list[dict]
    fresh_event_count: int
    previous_count: int | None
    reason: str
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
    return await get_qazsport_schedule(
        target_date,
        include_current_live=True,
    )


async def _default_sportplus_loader(target_date: date) -> list[dict]:
    return await get_sportplus_schedule(target_date)


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


class ParserOrchestrator:
    """Coordinates source loading, anomaly detection, QA and persistence."""

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

    async def _load_one(
        self,
        *,
        run_id: str,
        source: str,
        target_date: date,
    ) -> SourceRunResult:
        scope_date = target_date.isoformat()
        loader = self.loaders[source]
        error: Exception | None = None

        try:
            fresh_events = list(await loader(target_date))
        except Exception as caught:
            error = caught
            fresh_events = []

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

        return SourceRunResult(
            source=source,
            scope_date=scope_date,
            status=final_status,
            events=selected_events,
            fresh_event_count=len(fresh_events),
            previous_count=assessment.previous_count,
            reason=reason,
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
        specs = [
            (source, target_date)
            for target_date in (today, yesterday)
            for source in ("qazsport", "sportplus")
        ]

        source_runs = await asyncio.gather(
            *[
                self._load_one(
                    run_id=run_id,
                    source=source,
                    target_date=target_date,
                )
                for source, target_date in specs
            ]
        )

        by_key = {
            (result.source, result.scope_date): result
            for result in source_runs
        }

        today_schedule = merge_source_schedules(
            by_key[("qazsport", today.isoformat())].events,
            by_key[("sportplus", today.isoformat())].events,
        )
        yesterday_schedule = merge_source_schedules(
            by_key[("qazsport", yesterday.isoformat())].events,
            by_key[("sportplus", yesterday.isoformat())].events,
        )

        today_events = [
            event
            for event in today_schedule
            if event.get("is_live", False) and _is_user_event(event)
        ]
        yesterday_events = [
            event
            for event in yesterday_schedule
            if (
                event.get("is_live", False)
                and _is_user_event(event)
                and get_event_status(event, now=now) == "live"
            )
        ]

        events = merge_source_schedules(yesterday_events, today_events)
        source_errors: list[str] = []
        source_warnings: list[str] = []

        for result in source_runs:
            label = f"{SOURCE_LABELS.get(result.source, result.source)} {result.scope_date}"
            if result.status in {"blocked", "error"}:
                suffix = " (fallback)" if result.used_fallback else ""
                source_errors.append(f"{label}: {result.reason}{suffix}")
            elif result.status == "warning" or result.qa_warnings:
                reason = result.reason
                if result.qa_warnings:
                    reason += "; " + "; ".join(result.qa_warnings[:3])
                source_warnings.append(f"{label}: {reason}")

        run_status = "degraded" if source_errors else "ok"
        self.database.finish_agent_run(
            run_id,
            run_status,
            {
                "event_count": len(events),
                "source_errors": source_errors,
                "source_warnings": source_warnings,
            },
        )

        return RefreshResult(
            run_id=run_id,
            events=events,
            source_errors=source_errors,
            source_warnings=source_warnings,
            source_runs=source_runs,
        )

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Awaitable, Callable

from agents.qa_agent import ParserQAAgent, QAResult
from agents.source_agent import SourceAssessment, SourceHealthAgent
from parsers.qazsport import get_qazsport_schedule
from parsers.sportplus_epg import get_sportplus_epg_schedule
from services.event_status import KZ_TIMEZONE, get_event_status, is_live_broadcast, normalize_now
from services.source_horizon import HorizonDiscovery, discover_source_dates, nearest_monday
from storage.database import SLPDatabase


SourceLoader = Callable[[date], Awaitable[list[dict]]]
DateDiscoverer = Callable[[date], Awaitable[HorizonDiscovery]]

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
    minimum_horizon: str
    actual_horizon: str


async def _default_qazsport_loader(target_date: date) -> list[dict]:
    return await get_qazsport_schedule(target_date, include_current_live=True)


async def _default_sportplus_loader(target_date: date) -> list[dict]:
    return await get_sportplus_epg_schedule(target_date)


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


def _date_range(start: date, end: date) -> set[date]:
    if end < start:
        return set()
    return {start + timedelta(days=offset) for offset in range((end - start).days + 1)}


class ParserOrchestrator:
    """Coordinates source loading, QA, persistence and the published horizon."""

    def __init__(
        self,
        database: SLPDatabase | None = None,
        loaders: dict[str, SourceLoader] | None = None,
        date_discoverer: DateDiscoverer | None = None,
    ):
        self.database = database or SLPDatabase()
        self.source_agent = SourceHealthAgent(self.database)
        self.qa_agent = ParserQAAgent()
        self.loaders: dict[str, SourceLoader] = loaders or {
            "qazsport": _default_qazsport_loader,
            "sportplus": _default_sportplus_loader,
        }
        self.date_discoverer = date_discoverer or discover_source_dates

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
            selected_events = self.database.load_active_source_snapshot(source, scope_date)
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
        current = normalize_now(now)
        run_id = uuid.uuid4().hex[:12]
        self.database.start_agent_run(run_id)

        today = current.date()
        yesterday = today - timedelta(days=1)
        minimum_horizon = nearest_monday(today)
        discovery_warnings: list[str] = []
        try:
            discovery = await self.date_discoverer(today)
        except Exception as error:
            discovery = HorizonDiscovery(
                dates_by_source={source: {today} for source in self.loaders},
                warnings=[],
            )
            discovery_warnings.append(
                f"Horizon discovery: {type(error).__name__}: {error}"
            )

        discovery_warnings.extend(discovery.warnings)
        minimum_dates = _date_range(today, minimum_horizon)
        specs: list[tuple[str, date]] = []
        for source in self.loaders:
            source_dates = set(discovery.dates_by_source.get(source, set()))
            source_dates.update(minimum_dates)
            source_dates.add(today)
            source_dates.add(yesterday)
            for target_date in sorted(value for value in source_dates if value >= yesterday):
                specs.append((source, target_date))

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

        selected_events = [event for result in source_runs for event in result.events]
        future_live_dates: list[date] = []
        for event in selected_events:
            if not is_live_broadcast(event):
                continue
            try:
                event_date = date.fromisoformat(str(event.get("date") or ""))
            except ValueError:
                continue
            if event_date >= today:
                future_live_dates.append(event_date)

        furthest_live = max(future_live_dates) if future_live_dates else today
        actual_horizon = max(minimum_horizon, furthest_live)

        # Telegram reads the same normalized rows that were just persisted.
        persisted_live = self.database.load_active_events(
            start_date=yesterday.isoformat(),
            end_date=actual_horizon.isoformat(),
            live_broadcast_only=True,
        )
        events = [
            event
            for event in persisted_live
            if _is_user_event(event)
            and (
                str(event.get("date") or "") >= today.isoformat()
                or get_event_status(event, now=current) == "live_now"
            )
        ]

        source_errors: list[str] = []
        source_warnings: list[str] = list(discovery_warnings)
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
                "minimum_horizon": minimum_horizon.isoformat(),
                "actual_horizon": actual_horizon.isoformat(),
                "database_path": str(self.database.path),
            },
        )

        return RefreshResult(
            run_id=run_id,
            events=events,
            source_errors=source_errors,
            source_warnings=source_warnings,
            source_runs=source_runs,
            minimum_horizon=minimum_horizon.isoformat(),
            actual_horizon=actual_horizon.isoformat(),
        )

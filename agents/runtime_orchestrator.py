from __future__ import annotations

from datetime import date, datetime

from agents.orchestrator import ParserOrchestrator
from parsers.qazsport_complete import get_qazsport_schedule_complete
from parsers.sportplus_cached import get_sportplus_schedule_cached
from parsers.tvguide_broadcast import get_tvguide_schedule_with_evidence
from services.time_logic import KZ_TIMEZONE
from storage.database import SLPDatabase


async def _qazsport_loader(target_date: date) -> list[dict]:
    today = datetime.now(KZ_TIMEZONE).date()
    events = await get_qazsport_schedule_complete(
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


async def _sportplus_loader(target_date: date) -> list[dict]:
    return await get_sportplus_schedule_cached(target_date)


async def _tvguide_loader(target_date: date) -> list[dict]:
    return await get_tvguide_schedule_with_evidence(target_date)


class RuntimeParserOrchestrator(ParserOrchestrator):
    """Production orchestrator with source-completeness/evidence wrappers enabled."""

    def __init__(
        self,
        database: SLPDatabase | None = None,
        loaders=None,
    ):
        super().__init__(
            database=database,
            loaders=loaders
            or {
                "qazsport": _qazsport_loader,
                "sportplus": _sportplus_loader,
                "tvguide": _tvguide_loader,
            },
        )

from __future__ import annotations

import asyncio
from datetime import datetime

from agents.runtime_orchestrator import RuntimeParserOrchestrator
from services.live_evidence import event_is_live_broadcast
from services.logging_config import configure_logging
from services.schedule_service import ScheduleService
from services.time_logic import KZ_TIMEZONE, get_event_status, get_scheduled_datetimes
from storage.database import SLPDatabase


def _title(event: dict) -> str:
    return str(event.get("title") or event.get("raw_title") or "")


def _print_table(events: list[dict], *, now: datetime) -> None:
    headers = ("CHANNEL", "DATE", "START", "END", "STATUS", "DIRECT", "EVENT", "SOURCE")
    rows = []
    for event in events:
        start, end = get_scheduled_datetimes(event)
        rows.append(
            (
                str(event.get("channel") or ""),
                start.strftime("%Y-%m-%d"),
                start.strftime("%H:%M"),
                end.strftime("%Y-%m-%d %H:%M"),
                get_event_status(event, now=now),
                "yes" if event_is_live_broadcast(event) else "no",
                _title(event),
                str(event.get("source") or ""),
            )
        )

    widths = [
        max(len(headers[index]), *(len(row[index]) for row in rows))
        if rows else len(headers[index])
        for index in range(len(headers))
    ]
    print(" | ".join(headers[index].ljust(widths[index]) for index in range(len(headers))))
    print("-+-".join("-" * width for width in widths))
    for row in rows:
        print(" | ".join(row[index].ljust(widths[index]) for index in range(len(headers))))


async def main() -> None:
    configure_logging()
    now = datetime.now(KZ_TIMEZONE)
    database = SLPDatabase()
    service = ScheduleService(RuntimeParserOrchestrator(database=database))
    refresh = await service.refresh()
    events = service.get_events(now=now)

    print(f"NOW={now.isoformat()} timezone=Asia/Almaty")
    print(f"RUN={refresh.run_id} errors={len(refresh.source_errors)} warnings={len(refresh.source_warnings)}")
    print()
    _print_table(events, now=now)

    live = service.get_live_events(now=now)
    print("\n=== CONFIRMED DIRECT LIVE NOW ===")
    if live:
        _print_table(live, now=now)
    else:
        print("No confirmed direct LIVE events.")

    upcoming = service.get_upcoming_events(now=now, limit=10)
    print("\n=== NEXT SCHEDULE EVENTS ===")
    if upcoming:
        _print_table(upcoming, now=now)
    else:
        print("No upcoming events.")

    if refresh.source_errors:
        print("\n=== SOURCE ERRORS ===")
        for item in refresh.source_errors:
            print(f"- {item}")

    if refresh.source_warnings:
        print("\n=== SOURCE WARNINGS ===")
        for item in refresh.source_warnings:
            print(f"- {item}")


if __name__ == "__main__":
    asyncio.run(main())

from __future__ import annotations

import asyncio
from datetime import datetime

from parsers.qazsport import get_qazsport_schedule
from parsers.sportplus_cached import (
    get_sportplus_available_dates,
    get_sportplus_schedule_cached,
)
from services.event_contract import validate_sport_event
from services.time_logic import KZ_TIMEZONE, get_event_status


async def main() -> None:
    now = datetime.now(KZ_TIMEZONE)
    today = now.date()

    qazsport, sportplus, sportplus_dates = await asyncio.gather(
        get_qazsport_schedule(today, include_current_live=True),
        get_sportplus_schedule_cached(today),
        get_sportplus_available_dates(today),
    )

    if not qazsport:
        raise RuntimeError("Qazsport: parser returned an empty TV grid")
    if today not in sportplus_dates:
        raise RuntimeError(
            "Sport+ Qazaqstan: current date is missing from the published TV guide"
        )

    sources = {
        "Qazsport": qazsport,
        "Sport+ Qazaqstan": sportplus,
    }

    for name, events in sources.items():
        for event in events:
            validate_sport_event(event)
        live_now = sum(
            1
            for event in events
            if event.get("is_live", False)
            and get_event_status(event, now=now) == "live"
        )
        upcoming = sum(
            1
            for event in events
            if event.get("is_live", False)
            and get_event_status(event, now=now) == "upcoming"
        )
        print(
            f"{name}: entries={len(events)} live_now={live_now} "
            f"upcoming={upcoming} now={now.isoformat()} timezone=Asia/Almaty"
        )


if __name__ == "__main__":
    asyncio.run(main())

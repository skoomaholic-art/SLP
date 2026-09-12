from __future__ import annotations

import asyncio
from datetime import datetime

from parsers.championat import get_championat_calendar
from parsers.qazsport_complete import get_qazsport_schedule_complete
from parsers.sportplus_cached import (
    get_sportplus_available_dates,
    get_sportplus_schedule_cached,
)
from services.event_contract import validate_sport_event
from services.live_evidence import event_is_live_broadcast
from services.time_logic import KZ_TIMEZONE, get_event_status


async def main() -> None:
    now = datetime.now(KZ_TIMEZONE)
    today = now.date()

    qazsport, sportplus, sportplus_dates, championat = await asyncio.gather(
        get_qazsport_schedule_complete(today, include_current_live=True),
        get_sportplus_schedule_cached(today),
        get_sportplus_available_dates(today),
        get_championat_calendar(
            today,
            lookback_days=0,
            lookahead_days=1,
            force_refresh=True,
        ),
    )

    if not qazsport:
        raise RuntimeError("Qazsport: parser returned an empty TV grid")
    if today not in sportplus_dates:
        raise RuntimeError(
            "Sport+ Qazaqstan: current date is missing from the published TV guide"
        )
    if not championat.events:
        raise RuntimeError(
            f"Championat match center returned no events; errors={championat.errors[:5]}"
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
            if event_is_live_broadcast(event)
            and get_event_status(event, now=now) == "live"
        )
        upcoming = sum(
            1
            for event in events
            if event_is_live_broadcast(event)
            and get_event_status(event, now=now) == "upcoming"
        )
        print(
            f"{name}: entries={len(events)} direct_live_now={live_now} "
            f"direct_upcoming={upcoming} now={now.isoformat()} timezone=Asia/Almaty"
        )

    today_rows = sum(
        1 for event in championat.events
        if str(event.get("date") or "") == today.isoformat()
    )
    print(
        "Championat: "
        f"events={len(championat.events)} today={today_rows} "
        f"dates={len(championat.fetched_dates)} errors={len(championat.errors)} "
        "source_timezone=Europe/Moscow timezone=Asia/Almaty"
    )


if __name__ == "__main__":
    asyncio.run(main())

from __future__ import annotations

import asyncio

from parsers.qazsport import get_qazsport_schedule
from parsers.sportplus import get_sportplus_schedule
from services.event_contract import validate_sport_event


async def main() -> None:
    qazsport, sportplus = await asyncio.gather(
        get_qazsport_schedule(),
        get_sportplus_schedule(),
    )

    sources = {
        "Qazsport": qazsport,
        "Sport+ Qazaqstan": sportplus,
    }

    for name, events in sources.items():
        if not events:
            raise RuntimeError(f"{name}: parser returned zero schedule entries")
        for event in events:
            validate_sport_event(event)
        live_count = sum(1 for event in events if event.get("is_live", False))
        print(f"{name}: {len(events)} entries, {live_count} marked LIVE")


if __name__ == "__main__":
    asyncio.run(main())

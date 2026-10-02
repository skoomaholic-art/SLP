from __future__ import annotations

import asyncio
from datetime import datetime

import aiohttp

from parsers.championat import get_championat_calendar
from parsers.qazsport_complete import get_qazsport_schedule_complete
from parsers.sportplus_cached import (
    get_sportplus_available_dates,
    get_sportplus_schedule_cached,
)
from services.event_contract import validate_sport_event
from services.live_evidence import event_is_live_broadcast
from services.iptvx_sources import MAX_XML_BYTES, XML_URL, parse_iptvx_xml
from services.time_logic import KZ_TIMEZONE, get_event_status


async def main() -> None:
    now = datetime.now(KZ_TIMEZONE)
    today = now.date()

    async def fetch_iptvx():
        timeout = aiohttp.ClientTimeout(total=60)
        headers = {
            "User-Agent": "Mozilla/5.0 SLP-live-smoke/2.0",
            "Accept": "application/xml,text/xml,*/*",
        }
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.get(XML_URL, allow_redirects=True) as response:
                if response.status != 200:
                    raise RuntimeError(f"iptvX XMLTV returned HTTP {response.status}")
                raw = await response.read()
                if not raw or len(raw) > MAX_XML_BYTES:
                    raise RuntimeError(
                        f"iptvX XMLTV invalid size: {len(raw) if raw else 0}"
                    )
        return parse_iptvx_xml(raw)

    qazsport, sportplus, sportplus_dates, championat, iptvx = await asyncio.gather(
        get_qazsport_schedule_complete(today, include_current_live=True),
        get_sportplus_schedule_cached(today),
        get_sportplus_available_dates(today),
        get_championat_calendar(
            today,
            lookback_days=0,
            lookahead_days=1,
            force_refresh=True,
        ),
        fetch_iptvx(),
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

    iptvx_events, iptvx_stats = iptvx
    required_iptvx = (
        "Q LEAGUE",
        "Q ARENA",
        "EUROSPORT 1",
        "EUROSPORT 2",
        "viju+ Sport",
        "МАТЧ! ПЛАНЕТА",
    )
    missing = [
        channel
        for channel in required_iptvx
        if not (iptvx_stats.get("channels", {}).get(channel, {}).get("programmes", 0))
    ]
    if missing:
        raise RuntimeError(
            "iptvX XMLTV missing current programmes for: " + ", ".join(missing)
        )
    if not iptvx_events:
        raise RuntimeError("iptvX XMLTV produced no SLP sports candidates")

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
        "iptvX: "
        f"mapped={iptvx_stats.get('mapped', 0)} "
        f"candidates={len(iptvx_events)} "
        f"explicit_live={iptvx_stats.get('explicit_live', 0)} "
        f"required_channels={len(required_iptvx)}"
    )

    print(
        "Championat: "
        f"events={len(championat.events)} today={today_rows} "
        f"dates={len(championat.fetched_dates)} errors={len(championat.errors)} "
        "source_timezone=Europe/Moscow timezone=Asia/Almaty"
    )


if __name__ == "__main__":
    asyncio.run(main())

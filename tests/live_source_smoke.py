from __future__ import annotations

import asyncio
from datetime import datetime

import aiohttp
from bs4 import BeautifulSoup

from parsers.championat import get_championat_calendar
from parsers.qazsport_complete import get_qazsport_schedule_complete
from parsers.sportplus_cached import (
    get_sportplus_available_dates,
    get_sportplus_schedule_cached,
)
from parsers.vsetv_live import build_week_url, get_vsetv_live_evidence
from services.event_contract import validate_sport_event
from services.live_evidence import event_is_live_broadcast
from services.time_logic import KZ_TIMEZONE, get_event_status


async def _vsetv_signature() -> str:
    url = build_week_url(771)
    timeout = aiohttp.ClientTimeout(total=20)
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/140.0 Safari/537.36",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.5",
    }
    try:
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.get(url, allow_redirects=True) as response:
                html = await response.text()
                soup = BeautifulSoup(html, "html.parser")
                title = " ".join((soup.title.get_text(" ", strip=True) if soup.title else "").split())
                visible = " ".join(soup.stripped_strings)
                return (
                    f"status={response.status} final_url={response.url} bytes={len(html.encode('utf-8'))} "
                    f"title={title!r} prname2={len(soup.select('div.prname2'))} "
                    f"schedule_containers={len(soup.select('#schedule_container'))} "
                    f"ico_live={html.casefold().count('ico_live.gif')} "
                    f"direct_text={visible.casefold().count('прямая трансляция')} "
                    f"preview={visible[:160]!r}"
                )
    except Exception as error:
        return f"probe_error={type(error).__name__}:{error}"


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
    vsetv_rows, vsetv_errors = await get_vsetv_live_evidence(
        today,
        force_refresh=True,
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
        1
        for event in championat.events
        if str(event.get("date") or "") == today.isoformat()
    )
    print(
        "Championat: "
        f"events={len(championat.events)} today={today_rows} "
        f"dates={len(championat.fetched_dates)} errors={len(championat.errors)} "
        "source_timezone=Europe/Moscow timezone=Asia/Almaty"
    )
    print(
        "VseTV: "
        f"rows={len(vsetv_rows)} errors={len(vsetv_errors)} "
        f"signature={await _vsetv_signature()}"
    )


if __name__ == "__main__":
    asyncio.run(main())

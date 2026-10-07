"""One-command live check of the 14 iptvX channel pages (read-only, no DB).

    PYTHONPATH=. python scripts/check_iptvx_live.py

Prints, per channel: programmes on the page, rows the provider marks LIVE!,
events SLP would keep, and the next upcoming LIVE events in Asia/Almaty time.
Exit code 1 if any page fails or no page yields a single LIVE! mark, so a
changed page layout is noticed instead of silently producing an empty grid.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
import sys

import aiohttp

from services.channel_registry import IPTVX_CHANNELS
from services.iptvx_sources import (
    MAX_PAGE_BYTES,
    REQUEST_TIMEOUT_SECONDS,
    _fetch_bytes,
    page_url_for,
    parse_iptvx_page,
)
from services.time_logic import KZ_TIMEZONE


async def main() -> int:
    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
    headers = {"User-Agent": "Mozilla/5.0 SLP/2.0"}
    semaphore = asyncio.Semaphore(6)
    results: dict[str, tuple[list[dict], dict]] = {}
    failures: dict[str, str] = {}

    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        async def load(channel: str, page_id: str) -> None:
            url = page_url_for(channel)
            try:
                raw = await _fetch_bytes(
                    session, url, maximum=MAX_PAGE_BYTES, semaphore=semaphore
                )
                results[channel] = parse_iptvx_page(
                    raw, channel=channel, page_id=page_id, source_url=url
                )
            except Exception as exc:  # report, never hide
                failures[channel] = type(exc).__name__ + ": " + str(exc)[:160]

        await asyncio.gather(*(
            load(channel, page_id) for channel, page_id in IPTVX_CHANNELS.items()
        ))

    now = datetime.now(KZ_TIMEZONE)
    stamp = now.strftime("%Y-%m-%d %H:%M")
    print("iptvX live check at " + stamp + " Asia/Almaty")
    print(f"{'channel':20} {'id':20} {'rows':>5} {'LIVE!':>6} {'kept':>5}  rule")
    total_marked = 0
    upcoming: list[tuple[str, str, str, str]] = []
    for channel, page_id in IPTVX_CHANNELS.items():
        if channel in failures:
            print(f"{channel:20} {page_id:20} FAILED  {failures[channel]}")
            continue
        events, meta = results[channel]
        total_marked += int(meta["live_marked"])
        rule = "icon required" if meta["live_marker_required"] else "no icons: candidates"
        print(
            f"{channel:20} {page_id:20} {meta['programmes']:>5} "
            f"{meta['live_marked']:>6} {len(events):>5}  {rule}"
        )
        for event in events:
            if event["live_state"] != "live":
                continue
            if (event["date"], event["time"]) >= (
                now.date().isoformat(), now.strftime("%H:%M")
            ):
                upcoming.append(
                    (event["date"], event["time"], channel, event["title"])
                )

    print()
    print("Upcoming LIVE (Asia/Almaty): " + str(len(upcoming)))
    for day, clock, channel, title in sorted(upcoming)[:40]:
        print(f"  {day} {clock}  {channel:18} {title}")

    if failures or total_marked == 0:
        print()
        print("RESULT: PROBLEM - "
              + (str(len(failures)) + " page(s) failed" if failures
                 else "no LIVE! marks found on any page (layout changed?)"))
        return 1
    print()
    print("RESULT: OK")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

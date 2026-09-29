"""Fight Club third-party EPG discovery.

Guide times are requested in Asia/Almaty explicitly. Only text that
unambiguously marks a programme as LIVE can reach the public sports feed.
Ordinary MMA/boxing reruns remain in the diagnostic source archive.
"""
from __future__ import annotations

import asyncio
import base64
from collections import defaultdict
from datetime import date, datetime, timedelta
import logging
import re

import aiohttp
from bs4 import BeautifulSoup

from services.live_evidence import classify_live_evidence, event_is_editorial_or_replay
from services.time_logic import KZ_TIMEZONE

logger = logging.getLogger(__name__)
SOURCE = "web_fightclub"
GUIDE_ID = 497494
_TIME_ROW = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)\s+(.+)$")
_DATE_ROW = re.compile(r"^(20\d{2})-(\d{2})-(\d{2})$")
_FIGHT_SPORTS = (
    (re.compile(r"бокс|box(?:ing)?|кикбокс|kickbox", re.I), "Бокс"),
    (re.compile(r"mma|мма|смешанн|ufc|lfa|efc|one championship|enfusion|sft combat", re.I), "ММА"),
)


def guide_url() -> str:
    zone = base64.urlsafe_b64encode(b"Asia/Almaty").decode()
    return f"https://epg.pw/last/{GUIDE_ID}.html?lang=ru&timezone={zone}"


def parse_fight_club_guide(html: str) -> dict[str, list[dict]]:
    soup = BeautifulSoup(html, "html.parser")
    for element in soup.select("script,style,nav,footer"):
        element.decompose()
    # This guide prints YYYY-MM-DD headings and HH:MM programme lines.
    # Absence of that structure must not be treated as an empty replacement.
    lines = [
        " ".join(line.split()) for line in
        soup.get_text(separator="\n", strip=True).splitlines()
    ]
    by_day: dict[str, list[dict]] = defaultdict(list)
    current_day = None
    seen = set()
    for line in lines:
        date_match = _DATE_ROW.fullmatch(line)
        if date_match:
            try:
                current_day = date(*map(int, date_match.groups())).isoformat()
            except ValueError:
                current_day = None
            continue
        if not current_day:
            continue
        match = _TIME_ROW.match(line)
        if not match:
            continue
        time_text = f"{int(match.group(1)):02d}:{match.group(2)}"
        title = match.group(3).strip()
        if not title or len(title) < 5:
            continue
        key = (current_day, time_text, title.casefold())
        if key in seen:
            continue
        seen.add(key)
        sport = next((name for pattern, name in _FIGHT_SPORTS if pattern.search(title)), "")
        evidence = classify_live_evidence(title)
        event = {
            "source": SOURCE, "source_url": guide_url(),
            "provider_source": "epg.pw", "channel": "Fight Club",
            "date": current_day, "time": time_text,
            "timezone": "Asia/Almaty",
            "source_timezone": "Asia/Almaty",
            "time_normalization": "provider_explicit_timezone",
            "title": title, "raw_title": title, "sport": sport,
            "tournament": "", "is_sport_event": bool(sport),
            "is_live": evidence.is_live,
            "is_live_broadcast": (
                bool(sport) and evidence.is_live and
                not event_is_editorial_or_replay({"raw_title": title})
            ),
            "live_state": evidence.state,
            "live_evidence_method": (
                "third_party_live_text" if evidence.is_live else "none"
            ),
            "live_evidence_value": evidence.value,
        }
        by_day[current_day].append(event)
    return dict(by_day)


async def refresh_fight_club_source(database, *, today: date | None = None) -> dict:
    today = today or datetime.now(KZ_TIMEZONE).date()
    run_id = "fightclub-" + datetime.now(KZ_TIMEZONE).strftime("%Y%m%d%H%M%S%f")
    url = guide_url()
    error = ""
    by_day = {}
    try:
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as response:
                response.raise_for_status()
                by_day = parse_fight_club_guide(await response.text())
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        error = type(exc).__name__
    except Exception as exc:
        logger.warning("fightclub guide parser failed: %s", type(exc).__name__)
        error = "parse_" + type(exc).__name__

    accepted = []
    held = []
    event_count = 0
    live_count = 0
    for day, events in by_day.items():
        if not today <= date.fromisoformat(day) <= today + timedelta(days=7):
            continue
        previous = database.load_active_source_snapshot(SOURCE, day)
        prev_keys = {(e.get("time"), e.get("raw_title")) for e in previous}
        new_keys = {(e.get("time"), e.get("raw_title")) for e in events}
        if prev_keys - new_keys:
            held.append(day)
            continue
        database.upsert_source_snapshot(
            run_id=run_id, source=SOURCE, scope_date=day, events=events,
        )
        accepted.append(day)
        event_count += len(events)
        live_count += sum(bool(e["is_live_broadcast"]) for e in events)

    status = ("ok" if accepted else "warning" if by_day else "error")
    if not by_day and not error:
        error = "missing_date_or_programme_rows"
    if held:
        status = "warning"
        error = (error + ";" if error else "") + "partial_update_held"
    database.record_parser_run(
        run_id=run_id, source=SOURCE, scope_date=today.isoformat(),
        status=status, event_count=event_count,
        previous_count=database.latest_successful_count(SOURCE, today.isoformat()),
        error=error,
        details={
            "guide": url, "timezone": "Asia/Almaty",
            "live_count": live_count, "days": accepted,
            "held_days": held, "stale_data_kept": not bool(accepted),
        },
    )
    return {"channel": "Fight Club", "count": event_count,
            "live_count": live_count, "status": status, "error": error}

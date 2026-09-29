"""Conservative LIVE-only VseTV web feed, separate from the existing bot.

The source displays Moscow time. Conversion to Asia/Almaty occurs exactly once
before records are persisted. A failed/empty page does not erase last-good
source snapshots. This module does not modify the bot's Setanta evidence feed.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import date, datetime, timedelta
import re
from zoneinfo import ZoneInfo

import aiohttp

from parsers.vsetv_live import (
    REQUEST_TIMEOUT_SECONDS, _fetch_week_channel,
)
from services.live_evidence import event_is_editorial_or_replay
from services.time_logic import KZ_TIMEZONE

MSK = ZoneInfo("Europe/Moscow")

WEB_CHANNEL_IDS = {
    "KHL PRIME": 806,
    "KHL HD": 1641,
    "EUROSPORT 1": 535,
    "EUROSPORT 2": 1082,
    "МАТЧ! ПЛАНЕТА": 32,
    "viju+ Sport": 332,
}
_SOURCE = {name: "web_vsetv_" + str(cid)
           for name, cid in WEB_CHANNEL_IDS.items()}
BETTING_RE = re.compile(
    r"\b(?:фонбет|fonbet|бетбум|betboom|бетсити|betcity|"
    r"винлайн|winline|1xbet|лига ставок)\b\s*", re.I
)
STUDIO_RE = re.compile(
    r"студия|студийн|студиялық|обзор|шолу|повтор|"
    r"подробно|новости|журнал|итоги|перед матчем|"
    r"replay|highlights|видео дня|всё о хоккее|все о хоккее", re.I
)
SPORT_RE = (
    (re.compile(r"хокке|кхл|мхл", re.I), "Хоккей"),
    (re.compile(r"футбол|чемпионат мира по футболу", re.I), "Футбол"),
    (re.compile(r"волейбол", re.I), "Волейбол"),
    (re.compile(r"баскетбол", re.I), "Баскетбол"),
    (re.compile(r"теннис", re.I), "Теннис"),
    (re.compile(r"велоспорт", re.I), "Велоспорт"),
    (re.compile(r"снукер", re.I), "Снукер"),
    (re.compile(r"гандбол", re.I), "Гандбол"),
    (re.compile(r"формул[аы][-\s]*1|гран[-\s]*при|формула[-\s]*один", re.I), "Формула-1"),
    (re.compile(r"мма|mma|pfl|ufc", re.I), "ММА"),
    (re.compile(r"бокс", re.I), "Бокс"),
    (re.compile(r"дзюдо", re.I), "Дзюдо"),
    (re.compile(r"лыжн|биатлон", re.I), "Лыжный спорт"),
    (re.compile(r"атлетик", re.I), "Лёгкая атлетика"),
    (re.compile(r"автоспорт|мотоспорт|мотокросс|wec|wrc", re.I), "Автоспорт"),
    (re.compile(r"скалолазание", re.I), "Скалолазание"),
    (re.compile(r"маунтинбайк", re.I), "Маунтинбайк"),
    (re.compile(r"гимнастик", re.I), "Гимнастика"),
)


def normalize_live_record(row: dict, *, channel: str) -> dict | None:
    """Require the actual provider-specific LIVE icon in this programme row."""
    if not row.get("third_party_live_badge"):
        return None
    title = " ".join(str(row.get("title") or "").split())
    if not title or STUDIO_RE.search(title):
        return None
    cleaned = BETTING_RE.sub("", title)
    cleaned = re.sub(r"\s*[.]?\s*Прямая трансляция[. ]*$", "", cleaned, flags=re.I).strip(" ,.")
    if not cleaned:
        return None
    sport = next((sport for expression, sport in SPORT_RE if expression.search(cleaned)), "")
    if not sport:
        return None
    item = {"title": cleaned, "raw_title": title, "sport": sport,
            "tournament": ""}
    if event_is_editorial_or_replay(item):
        return None
    # Preserve full fixture title; do not invent teams or imply every race is
    # a two-team match.
    parts = [p.strip() for p in cleaned.split(". ") if p.strip()]
    if len(parts) >= 2:
        # A page may begin with "Футбол. АПЛ. Команда А - Команда Б"
        # or with "Чемпионат КХЛ. Спартак - Барыс", with no sport prefix.
        initial_is_sport = parts[0].casefold() in {
            "футбол", "хоккей", "теннис", "бокс", "мма", "снукер",
            "велоспорт", "баскетбол", "волейбол", "дзюдо",
            "формула-1", "мотоспорт", "автоспорт",
        }
        core = parts[1:] if initial_is_sport else parts
        tournament = ". ".join(core[:-1]) if len(core) > 1 else core[0]
        event_title = core[-1] if len(core) > 1 else core[0]
    else:
        tournament = parts[0]
        event_title = cleaned
    if not event_title or len(event_title) < 3:
        return None
    try:
        scheduled = datetime.strptime(
            str(row["date"]) + " " + str(row["time"]),
            "%Y-%m-%d %H:%M"
        ).replace(tzinfo=MSK)
        start = scheduled.astimezone(KZ_TIMEZONE)
    except (ValueError, KeyError):
        return None
    source = _SOURCE[channel]
    return {
        "source": source, "source_url": row.get("source_url") or "",
        "provider_source": "vsetv", "channel": channel,
        "date": start.date().isoformat(), "time": start.strftime("%H:%M"),
        "source_timezone": "Europe/Moscow",
        "source_start_at": scheduled.isoformat(),
        "timezone": "Asia/Almaty", "time_normalization": "msk_to_kz",
        "title": event_title, "raw_title": title, "sport": sport,
        "tournament": tournament,
        "is_sport_event": True, "is_live": True, "is_live_broadcast": True,
        "live_state": "live", "live_evidence_method": "third_party_live_badge",
        "live_evidence_value": "ico_live.gif on this VseTV programme row",
        "explicit_direct_text": row.get("explicit_direct_text", False),
    }


async def refresh_vsetv_web_sources(database, *, today: date | None = None) -> dict:
    today = today or datetime.now(KZ_TIMEZONE).date()
    run_id = "webvsetv-" + datetime.now(KZ_TIMEZONE).strftime("%Y%m%d%H%M%S%f")
    stats = []
    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/140.0 Safari/537.36",
        "Accept-Language": "ru-RU,ru;q=0.9",
    }
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        for channel, channel_id in WEB_CHANNEL_IDS.items():
            source = _SOURCE[channel]
            try:
                rows, error = await asyncio.wait_for(
                    _fetch_week_channel(session, channel, channel_id, today),
                    timeout=18,
                )
            except Exception as exc:
                rows, error = [], type(exc).__name__
            events = [
                normalized for row in rows
                if (normalized := normalize_live_record(row, channel=channel))
                is not None
            ]
            by_day = defaultdict(list)
            for event in events:
                by_day[event["date"]].append(event)
            status = "ok" if events and not error else (
                "warning" if events else "error" if error else "warning"
            )
            # Empty/unreachable pages cannot wipe valid previous LIVE events.
            if events:
                for day, day_rows in by_day.items():
                    database.upsert_source_snapshot(
                        run_id=run_id, source=source,
                        scope_date=day, events=day_rows,
                    )
            database.record_parser_run(
                run_id=run_id, source=source, scope_date=today.isoformat(),
                status=status, event_count=len(events),
                previous_count=database.latest_successful_count(
                    source, today.isoformat()
                ),
                error=error, details={
                    "channel": channel, "days": sorted(by_day),
                    "timezone": "Europe/Moscow -> Asia/Almaty",
                    "stale_data_kept": not bool(events),
                },
            )
            stats.append({"channel": channel, "count": len(events),
                          "status": status, "error": error})
            # Avoid bursts to the third-party guide.
            await asyncio.sleep(0.35)
    return {"sources": stats, "total": sum(x["count"] for x in stats)}

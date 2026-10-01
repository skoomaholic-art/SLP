from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable
from urllib.parse import urlencode

import aiohttp

from services.channel_registry import CHANNELS
from services.event_contract import build_sport_event
from services.live_evidence import classify_live_evidence
from services.time_logic import KZ_TIMEZONE

API_BASE = "https://kt.server-api.lfstrm.tv"
WEB_BASE = "https://tv.telecom.kz/channels"
SOURCE = "tvplus"
MOBIKINO_API_BASE = "https://kcell.server-api.lfstrm.tv"
MOBIKINO_WEB_BASE = "https://mobikino.kz/channels"
MOBIKINO_SOURCE = "mobikino"


@dataclass(frozen=True)
class TVPlusChannel:
    name: str
    channel_id: str
    sport_hint: str = ""
    api_base: str = API_BASE
    web_base: str = WEB_BASE
    source: str = SOURCE


def _tvplus_channel_from_registry(channel) -> TVPlusChannel:
    if channel.guide_backend == "mobikino":
        return TVPlusChannel(
            channel.tvplus_name,
            channel.tvplus_id,
            channel.sport_hint,
            api_base=MOBIKINO_API_BASE,
            web_base=MOBIKINO_WEB_BASE,
            source=MOBIKINO_SOURCE,
        )
    return TVPlusChannel(
        channel.tvplus_name,
        channel.tvplus_id,
        channel.sport_hint,
    )


ALL_GUIDE_CHANNELS = tuple(
    _tvplus_channel_from_registry(channel)
    for channel in CHANNELS
    if channel.tvplus_id
)

TARGET_CHANNELS = tuple(
    channel
    for channel in ALL_GUIDE_CHANNELS
    if channel.source == SOURCE
)

EUROSPORT_CHANNELS = tuple(
    channel
    for channel in ALL_GUIDE_CHANNELS
    if channel.source == MOBIKINO_SOURCE
)

Q_CHANNEL_NAMES = frozenset({"Q Arena", "Q Football", "Q League"})


SPORT_PREFIXES = (
    ("Пляжный волейбол", "Пляжный волейбол"),
    ("Спортивная гимнастика", "Спортивная гимнастика"),
    ("Тяжёлая атлетика", "Тяжёлая атлетика"),
    ("Тяжелая атлетика", "Тяжёлая атлетика"),
    ("Вольная борьба", "Борьба"),
    ("Автоспорт", "Автоспорт"),
    ("Мотоспорт", "Мотоспорт"),
    ("Волейбол", "Волейбол"),
    ("Баскетбол", "Баскетбол"),
    ("Футбол", "Футбол"),
    ("Хоккей", "Хоккей"),
    ("Теннис", "Теннис"),
    ("Снукер", "Снукер"),
    ("Бильярд", "Бильярд"),
    ("Дзюдо", "Дзюдо"),
    ("Бокс", "Бокс"),
    ("Кәсіпқой бокс", "Бокс"),
    ("Жеңіл атлетика", "Лёгкая атлетика"),
    ("Жағажай волейболы", "Волейбол"),
    ("Қазақ күресі", "Борьба"),
    ("MMA", "MMA"),
)


def _coerce_date(value: date | datetime | str | None) -> date:
    if value is None:
        return datetime.now(KZ_TIMEZONE).date()
    if isinstance(value, datetime):
        return value.astimezone(KZ_TIMEZONE).date() if value.tzinfo else value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(value, "%Y-%m-%d").date()


def _local_epg_datetime(value: str) -> datetime:
    """TV+ EPG uses Z-suffixed wall-clock values shown unchanged in Kazakhstan UI."""
    cleaned = str(value or "").strip().removesuffix("Z")
    if not cleaned:
        raise ValueError("TV+ EPG: пустое время")
    parsed = datetime.fromisoformat(cleaned)
    return parsed.replace(tzinfo=KZ_TIMEZONE)


def _clean_title(value: str) -> str:
    text = " ".join(str(value or "").split())
    text = re.sub(
        r"\s*[.·-]?\s*(?:ПРЯМАЯ ТРАНСЛЯЦИЯ|ПРЯМОЙ ЭФИР|ТІКЕЛЕЙ ЭФИР|ТIКЕЛЕЙ ЭФИР|LIVE)\s*$",
        "",
        text,
        flags=re.IGNORECASE,
    )
    return text.strip(" .-–—")


def _sport_from_title(title: str, hint: str = "") -> tuple[str, str]:
    text = _clean_title(title)
    for prefix, sport in SPORT_PREFIXES:
        match = re.match(rf"^{re.escape(prefix)}(?:\s*[.:,-]\s*|\s+)", text, re.I)
        if match:
            return sport, text[match.end():].strip(" .-–—")
    return hint, text


def _split_tournament_title(remainder: str) -> tuple[str, str]:
    text = remainder.strip(" .-–—")
    if not text:
        return "", ""

    if ":" in text:
        left, right = text.split(":", 1)
        if re.search(r"\s(?:-|–|—|vs\.?|v\.)\s", right, re.I):
            return left.strip(" .,-–—"), right.strip(" .,-–—")

    match = re.match(
        r"^(?P<tournament>.+?[,;.])\s*(?P<title>[^,;]+\s(?:-|–|—|vs\.?|v\.)\s[^,;]+)$",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        return (
            match.group("tournament").strip(" .,-–—"),
            match.group("title").strip(" .,-–—"),
        )

    return "", text


def parse_tvplus_title(raw_title: str, *, sport_hint: str = "") -> dict[str, str]:
    sport, remainder = _sport_from_title(raw_title, sport_hint)
    tournament, title = _split_tournament_title(remainder)
    return {
        "sport": sport,
        "tournament": tournament,
        "title": title or _clean_title(raw_title),
        "raw_event_title": remainder,
    }


def _channel_url(channel: TVPlusChannel) -> str:
    return f"{channel.web_base}/{channel.channel_id}/program"


def _page_id(target_date: date) -> str:
    return f"{target_date.isoformat()}t00d12h"


@dataclass
class TVPlusSchedules:
    by_date: dict[date, list[dict]]
    errors: list[str]


async def _get_json(
    session: aiohttp.ClientSession,
    api_base: str,
    path: str,
    *,
    params=None,
) -> dict:
    async with session.get(f"{api_base}{path}", params=params) as response:
        response.raise_for_status()
        data = await response.json()
    if not isinstance(data, dict):
        raise RuntimeError(f"TV+: неожиданный ответ {path}")
    return data


async def _schedule_ids(
    session: aiohttp.ClientSession,
    api_base: str,
) -> dict[str, str]:
    token_data = await _get_json(session, api_base, "/user/v1/asset-tokens")
    token = str(token_data.get("tvAssetToken") or "")
    if not token:
        raise RuntimeError("TV+: не получен tvAssetToken")

    media_data = await _get_json(
        session, api_base, "/tv/v2/medias",
        params={"tv-asset-token": token},
    )
    result = {}
    for media in media_data.get("medias", []):
        channel_id = str(media.get("channelId") or "")
        schedule_id = str(media.get("scheduleId") or "")
        if channel_id and schedule_id:
            result[channel_id] = schedule_id
    return result


def _event_from_epg(channel: TVPlusChannel, item: dict) -> dict:
    scheduled = item.get("scheduledFor") or {}
    start = _local_epg_datetime(str(scheduled.get("begin") or ""))
    end = _local_epg_datetime(str(scheduled.get("end") or ""))
    raw_title = " ".join(str(item.get("title") or "").split())
    description = " ".join(str(item.get("eventDescriptionMedium") or "").split())
    if not raw_title:
        raise RuntimeError(f"TV+ {channel.name}: событие без названия")

    evidence = classify_live_evidence(f"{raw_title} {description}".strip())
    method = evidence.method
    if method.startswith("official_"):
        method = "provider_" + method[len("official_"):]

    parsed = parse_tvplus_title(raw_title, sport_hint=channel.sport_hint)
    event = {
        "date": start.date().isoformat(),
        "time": start.strftime("%H:%M"),
        "channel": channel.name,
        "is_live": evidence.is_live,
        "live_state": evidence.state,
        "live_evidence_method": method,
        "live_evidence_value": evidence.value,
        "live_evidence_confidence": evidence.confidence,
        "raw_title": raw_title,
        "sport": parsed["sport"],
        "tournament": parsed["tournament"],
        "title": parsed["title"],
        "raw_event_title": parsed["raw_event_title"],
        "schedule_offset": start.hour * 60 + start.minute,
        "estimated_broadcast_end_date": end.date().isoformat(),
        "estimated_broadcast_end": end.strftime("%H:%M"),
        "end_estimation_method": "provider_epg",
        "end_confidence": "high",
    }
    return build_sport_event(
        event,
        source=channel.source,
        source_url=_channel_url(channel),
    )


async def _load_channel_date(
    session: aiohttp.ClientSession,
    channel: TVPlusChannel,
    schedule_id: str,
    target_date: date,
) -> list[dict]:
    data = await _get_json(
        session,
        channel.api_base,
        f"/epg/v2/schedules/{schedule_id}/spread",
        params={"centralPageId": _page_id(target_date)},
    )
    if "pagesWithEvents" not in data:
        raise RuntimeError(f"TV+ {channel.name}: отсутствует pagesWithEvents")

    events = []
    seen = set()
    for page in data.get("pagesWithEvents") or []:
        for item in page.get("events") or []:
            scheduled = item.get("scheduledFor") or {}
            begin = str(scheduled.get("begin") or "")
            if not begin:
                continue
            start = _local_epg_datetime(begin)
            if start.date() != target_date:
                continue
            event = _event_from_epg(channel, item)
            key = (event["date"], event["time"], event["raw_title"])
            if key not in seen:
                seen.add(key)
                events.append(event)

    if not events:
        raise RuntimeError(f"TV+ {channel.name}: телепрограмма за {target_date} пуста")
    events.sort(key=lambda event: (event["date"], event["time"], event["title"]))
    return events


async def fetch_tvplus_schedules(target_dates: Iterable[date | datetime | str]) -> TVPlusSchedules:
    dates = sorted({_coerce_date(value) for value in target_dates})
    if not dates:
        return TVPlusSchedules(by_date={}, errors=[])

    timeout = aiohttp.ClientTimeout(total=25)
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
        "Accept-Language": "ru-RU,ru;q=0.9",
    }
    connector = aiohttp.TCPConnector(limit=8)
    by_date = {value: [] for value in dates}
    errors: list[str] = []

    async with aiohttp.ClientSession(headers=headers, timeout=timeout, connector=connector) as session:
        channels = ALL_GUIDE_CHANNELS
        schedule_ids_by_api: dict[str, dict[str, str]] = {}
        for api_base in sorted({channel.api_base for channel in channels}):
            try:
                schedule_ids_by_api[api_base] = await _schedule_ids(session, api_base)
            except Exception as error:
                schedule_ids_by_api[api_base] = {}
                errors.append(f"Provider API {api_base}: {type(error).__name__}")
                print(f"Ошибка provider API {api_base}:", repr(error))

        jobs = []
        metadata = []
        for channel in channels:
            schedule_id = schedule_ids_by_api.get(channel.api_base, {}).get(channel.channel_id)
            if not schedule_id:
                errors.append(f"{channel.name}: scheduleId не найден")
                continue
            for target_date in dates:
                jobs.append(_load_channel_date(session, channel, schedule_id, target_date))
                metadata.append((channel, target_date))

        results = await asyncio.gather(*jobs, return_exceptions=True)
        for (channel, target_date), result in zip(metadata, results):
            if isinstance(result, Exception):
                errors.append(f"TV+ {channel.name} {target_date}: {type(result).__name__}")
                print(f"Ошибка источника TV+ {channel.name} {target_date}:", repr(result))
                continue
            by_date[target_date].extend(result)

    return TVPlusSchedules(by_date=by_date, errors=errors)


async def get_tvplus_schedule(
    target_date: date | datetime | str | None = None,
) -> list[dict]:
    requested = _coerce_date(target_date)
    loaded = await fetch_tvplus_schedules([requested])
    if loaded.errors:
        raise RuntimeError("; ".join(loaded.errors))
    return loaded.by_date.get(requested, [])


async def main() -> None:
    today = datetime.now(KZ_TIMEZONE).date()
    loaded = await fetch_tvplus_schedules([today])
    events = loaded.by_date.get(today, [])
    direct = [event for event in events if event.get("live_state") == "live"]
    print(f"TV+ программ: {len(events)}; подтверждённых LIVE: {len(direct)}")
    for event in direct:
        print(event["time"], event["channel"], event["title"])
    if loaded.errors:
        print("Ошибки:", *loaded.errors, sep="\n- ")


if __name__ == "__main__":
    asyncio.run(main())

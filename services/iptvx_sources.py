"""iptvX|one XMLTV collector for the 14 SLP sports channels.

Primary source:
    https://iptvx.one/EPG_NOARCH

The feed is fetched once per collection and filtered by the editor-approved
tvg-id whitelist. The per-channel pages remain source/control URLs and are used
only as a fallback if the XMLTV feed is unavailable.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
import asyncio
import bz2
import gzip
import io
import lzma
import os
import re
import zipfile
import xml.etree.ElementTree as ET

import aiohttp
from bs4 import BeautifulSoup

from services.channel_registry import CHANNEL_BY_NAME, IPTVX_CHANNELS
from services.event_text_ru import normalize_event_fields
from services.live_evidence import (
    SPORT_TEXT_MARKERS,
    classify_live_evidence,
    event_is_editorial_or_replay,
)
from services.time_logic import KZ_TIMEZONE

PAGE_BASE_URL = os.getenv(
    "IPTVX_PAGE_BASE_URL", "https://epg.iptvx.one/id"
).strip().rstrip("/")
XML_URL = os.getenv(
    "IPTVX_EPG_URL", "https://iptvx.one/EPG_NOARCH"
).strip()
REQUEST_TIMEOUT_SECONDS = 45
MAX_PAGE_BYTES = 3 * 1024 * 1024
MAX_XML_BYTES = 100 * 1024 * 1024
MSK_TIMEZONE = timezone(timedelta(hours=3))

ID_TO_CHANNEL = {tvg_id: channel for channel, tvg_id in IPTVX_CHANNELS.items()}
_SOURCE = {
    channel: "web_iptvx_" + tvg_id
    for channel, tvg_id in IPTVX_CHANNELS.items()
}
_PAGE_URL = {
    channel: PAGE_BASE_URL + "/" + tvg_id
    for channel, tvg_id in IPTVX_CHANNELS.items()
}
_LEGACY_SOURCES = {
    "SETANTA SPORTS 1": ("web_iptvx_setanta1-kz",),
    "SETANTA SPORTS 2": ("web_iptvx_setanta2-kz",),
}

_MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4,
    "мая": 5, "июня": 6, "июля": 7, "августа": 8,
    "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}
_DAY_RE = re.compile(
    r"(?iu)(?:понедельник|вторник|среда|четверг|пятница|"
    r"суббота|воскресенье)?\s*,?\s*(\d{1,2})\s+"
    r"([а-яё]+)\s+(\d{4})\s*г\.?"
)
_PROGRAM_RE = re.compile(r"^\s*(\d{1,2}:\d{2})\s+(.+?)\s*$")
_MATCH_LIKE_RE = re.compile(
    r"(?iu)\S+\s+(?:[-–—:]|vs\.?|v\.)\s+\S+"
)
_NON_EVENT_RE = re.compile(
    r"(?iu)\b(?:новости|news|студия|studio|журнал|magazine|"
    r"документальн|documentary|дайджест|digest|интервью|interview|"
    r"топ[- ]?10|лучшие моменты|highlights?|обзор|review|повтор|replay|"
    r"классика|archive|архив|превью|preview|анонс|promo)\b"
)

_SPORT_INFERENCE = (
    (re.compile(r"(?iu)\b(?:футбол|football|soccer|АПЛ|EPL|ЛЧ|UCL)\b"), "Футбол"),
    (re.compile(r"(?iu)\b(?:хоккей|КХЛ|KHL|NHL)\b"), "Хоккей"),
    (re.compile(r"(?iu)\b(?:баскетбол|NBA|Евролига|EuroLeague)\b"), "Баскетбол"),
    (re.compile(r"(?iu)\b(?:ATP|WTA|теннис|tennis)\b"), "Теннис"),
    (re.compile(r"(?iu)\b(?:UFC|MMA|ММА|бокс|boxing)\b"), "ММА"),
    (re.compile(r"(?iu)\b(?:F1|Формула[- ]?1|WRC|WEC|MotoGP)\b"), "Автоспорт"),
    (re.compile(r"(?iu)\b(?:волейбол|volleyball)\b"), "Волейбол"),
    (re.compile(r"(?iu)\b(?:гандбол|handball)\b"), "Гандбол"),
    (re.compile(r"(?iu)\b(?:биатлон|biathlon|лыж|ski)\b"), "Зимний спорт"),
    (re.compile(r"(?iu)\b(?:снукер|snooker)\b"), "Снукер"),
    (re.compile(r"(?iu)\b(?:велоспорт|cycling|велогон)\b"), "Велоспорт"),
)


def page_url_for(channel: str) -> str:
    return _PAGE_URL.get(channel, "")


def _local_name(tag: str) -> str:
    return str(tag or "").rsplit("}", 1)[-1].casefold()


def _text(node: ET.Element, name: str) -> str:
    for child in node:
        if _local_name(child.tag) == name and child.text:
            return " ".join(child.text.split())
    return ""


def _xmltv_datetime(value: str) -> datetime:
    raw = str(value or "").strip()
    match = re.match(r"^(\d{8,14})(?:\s*([+-]\d{4}|Z))?", raw)
    if not match:
        raise ValueError("invalid_xmltv_datetime")
    stamp = match.group(1).ljust(14, "0")
    parsed = datetime.strptime(stamp[:14], "%Y%m%d%H%M%S")
    offset = match.group(2)
    if offset == "Z":
        return parsed.replace(tzinfo=timezone.utc)
    if offset:
        sign = 1 if offset[0] == "+" else -1
        minutes = int(offset[1:3]) * 60 + int(offset[3:5])
        return parsed.replace(
            tzinfo=timezone(sign * timedelta(minutes=minutes))
        )
    return parsed.replace(tzinfo=MSK_TIMEZONE)


def _parse_day(value: str) -> date | None:
    match = _DAY_RE.search(" ".join(str(value or "").split()))
    if not match:
        return None
    month = _MONTHS.get(match.group(2).casefold())
    if not month:
        return None
    try:
        return date(int(match.group(3)), month, int(match.group(1)))
    except ValueError:
        return None


def _channel_hint(channel: str) -> str:
    item = CHANNEL_BY_NAME.get(channel)
    return str(item.sport_hint or "") if item else ""


def _infer_sport(raw_title: str, normalized_sport: str, channel: str) -> str:
    if normalized_sport and normalized_sport.casefold() not in {"спорт", "sports"}:
        return normalized_sport
    hint = _channel_hint(channel)
    if hint:
        return hint
    for pattern, sport in _SPORT_INFERENCE:
        if pattern.search(raw_title):
            return sport
    return normalized_sport if normalized_sport else ""


def _looks_like_sport(
    raw_title: str,
    *,
    sport: str,
    categories: list[str],
) -> bool:
    if sport:
        return True
    category_text = " ".join(categories).casefold()
    if any(marker in category_text for marker in (
        "спорт", "sport", "football", "soccer", "hockey", "tennis",
        "basketball", "boxing", "mma", "racing",
    )):
        return True
    upper = " ".join(str(raw_title or "").upper().split())
    if any(marker in upper for marker in SPORT_TEXT_MARKERS):
        return True
    return bool(_MATCH_LIKE_RE.search(raw_title))


def _candidate_event(
    *,
    channel: str,
    page_id: str,
    raw_title: str,
    subtitle: str,
    desc: str,
    categories: list[str],
    start_source: datetime,
    stop_source: datetime | None,
    evidence,
    provider_source: str,
) -> dict | None:
    combined_title = raw_title or subtitle
    if not combined_title:
        return None

    normalized = normalize_event_fields(
        title=combined_title,
        sport=categories[0] if categories else "",
        tournament="",
    )
    sport = _infer_sport(combined_title, normalized["sport"], channel)
    event_title = normalized["title"] or combined_title
    candidate = {
        "raw_title": combined_title,
        "title": event_title,
        "sport": sport,
        "tournament": normalized["tournament"],
    }

    if evidence.state == "not_live":
        return None
    if _NON_EVENT_RE.search(" ".join([combined_title, subtitle, desc])):
        return None
    if event_is_editorial_or_replay(candidate):
        return None
    if not _looks_like_sport(
        combined_title,
        sport=sport,
        categories=categories,
    ):
        return None

    start = start_source.astimezone(KZ_TIMEZONE)
    stop = stop_source.astimezone(KZ_TIMEZONE) if stop_source else None
    event = {
        "source": _SOURCE[channel],
        # Keep the human-checkable channel page on the event/source button.
        "source_url": page_url_for(channel),
        "provider_source": provider_source,
        "provider_channel_id": page_id,
        "channel": channel,
        "date": start.date().isoformat(),
        "time": start.strftime("%H:%M"),
        "source_start_at": start_source.isoformat(),
        "timezone": "Asia/Almaty",
        "time_normalization": "xmltv_offset_to_kz",
        "title": event_title,
        "raw_title": combined_title,
        "sport": sport,
        "tournament": normalized["tournament"],
        "is_sport_event": True,
        "is_live": True,
        "is_live_broadcast": True,
        "live_state": "live" if evidence.is_live else "candidate",
        "live_evidence_method": (
            "provider_live_text"
            if evidence.is_live
            else "iptvx_sports_channel_candidate"
        ),
        "live_evidence_value": (
            evidence.value if evidence.is_live else page_id
        ),
        "live_confidence": "high" if evidence.is_live else "candidate",
    }
    if stop and stop > start:
        event["estimated_broadcast_end_date"] = stop.date().isoformat()
        event["estimated_broadcast_end"] = stop.strftime("%H:%M")
        event["end_estimation_method"] = "provider_epg"
    return event


def _decode_xml_payload(raw: bytes | str) -> bytes:
    """Return plain XML bytes from iptvX extensionless compressed feeds."""
    if isinstance(raw, str):
        payload = raw.encode("utf-8")
    else:
        payload = bytes(raw or b"")
    if not payload:
        raise ValueError("empty_iptvx_payload")

    # Some iptvX feed URLs are extensionless and may return a compressed file
    # without a Content-Encoding header, so aiohttp cannot always decompress it.
    if payload.startswith(b"\x1f\x8b"):
        payload = gzip.decompress(payload)
    elif payload.startswith(b"PK\x03\x04"):
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            names = [
                name for name in archive.namelist()
                if not name.endswith("/")
            ]
            preferred = next(
                (
                    name for name in names
                    if name.casefold().endswith((".xml", ".xmltv"))
                ),
                names[0] if names else "",
            )
            if not preferred:
                raise ValueError("empty_iptvx_zip")
            payload = archive.read(preferred)
    elif payload.startswith(b"BZh"):
        payload = bz2.decompress(payload)
    elif payload.startswith(b"\xfd7zXZ\x00"):
        payload = lzma.decompress(payload)

    payload = payload.lstrip(b"\xef\xbb\xbf\x00\r\n\t ")
    if not payload.startswith(b"<"):
        prefix = payload[:24].hex()
        raise ValueError("unknown_iptvx_payload:" + prefix)
    return payload


def parse_iptvx_xml(raw: bytes | str) -> tuple[list[dict], dict]:
    """Parse the XMLTV feed for only the 14 editor-approved sports channels.

    Explicit LIVE is preferred, but it is not mandatory. On these whitelisted
    sports channels a concrete sports programme is accepted as a candidate
    unless it is explicitly not-live, editorial, a replay, or a highlights
    programme. This avoids the previous failure mode where valid schedules were
    discarded simply because the XMLTV row omitted a LIVE tag.
    """
    payload = _decode_xml_payload(raw)
    root = ET.fromstring(payload)
    events: list[dict] = []
    channel_stats = {
        channel: {
            "programmes": 0,
            "candidates": 0,
            "explicit_live": 0,
            "filtered": 0,
            "days": set(),
        }
        for channel in IPTVX_CHANNELS
    }
    stats = {
        "programmes": 0,
        "mapped": 0,
        "candidates": 0,
        "explicit_live": 0,
        "filtered": 0,
        "channels": channel_stats,
    }

    for programme in root.iter():
        if _local_name(programme.tag) != "programme":
            continue
        stats["programmes"] += 1
        page_id = str(programme.attrib.get("channel") or "").strip()
        channel = ID_TO_CHANNEL.get(page_id)
        if not channel:
            continue

        stats["mapped"] += 1
        per_channel = channel_stats[channel]
        per_channel["programmes"] += 1

        try:
            start_source = _xmltv_datetime(programme.attrib.get("start", ""))
        except ValueError:
            per_channel["filtered"] += 1
            stats["filtered"] += 1
            continue
        start_kz = start_source.astimezone(KZ_TIMEZONE)
        per_channel["days"].add(start_kz.date().isoformat())

        stop_raw = str(programme.attrib.get("stop") or "").strip()
        try:
            stop_source = _xmltv_datetime(stop_raw) if stop_raw else None
        except ValueError:
            stop_source = None

        title = _text(programme, "title")
        subtitle = _text(programme, "sub-title")
        desc = _text(programme, "desc")
        categories = [
            " ".join((child.text or "").split())
            for child in programme
            if _local_name(child.tag) == "category" and child.text
        ]
        asset_hints = [
            str(child.attrib.get("src") or "")
            for child in programme
            if _local_name(child.tag) == "icon"
        ]
        has_live_element = any(
            _local_name(child.tag) == "live" for child in programme
        )
        evidence = classify_live_evidence(
            " ".join([
                title, subtitle, desc, *categories,
                "LIVE" if has_live_element else "",
            ]),
            asset_hints=asset_hints,
            live_asset_patterns=("ico_live", "/live", "live."),
        )

        event = _candidate_event(
            channel=channel,
            page_id=page_id,
            raw_title=title,
            subtitle=subtitle,
            desc=desc,
            categories=categories,
            start_source=start_source,
            stop_source=stop_source,
            evidence=evidence,
            provider_source="iptvx_xmltv",
        )
        if event is None:
            per_channel["filtered"] += 1
            stats["filtered"] += 1
            continue

        events.append(event)
        per_channel["candidates"] += 1
        stats["candidates"] += 1
        if evidence.is_live:
            per_channel["explicit_live"] += 1
            stats["explicit_live"] += 1

    for values in channel_stats.values():
        values["days"] = sorted(values["days"])
    return events, stats


def parse_iptvx_page(
    html: bytes | str,
    *,
    channel: str,
    page_id: str,
    source_url: str | None = None,
) -> tuple[list[dict], dict]:
    """Fallback parser for the public human-readable channel page."""
    text = (
        html.decode("utf-8", errors="replace")
        if isinstance(html, bytes)
        else str(html or "")
    )
    soup = BeautifulSoup(text, "html.parser")
    page_text = " ".join(soup.stripped_strings)
    if page_id.casefold() not in page_text.casefold():
        raise ValueError("iptvx_page_id_mismatch")

    raw_rows: list[dict] = []
    source_days: set[date] = set()
    for heading in soup.find_all(["h2", "h3", "h4"]):
        source_day = _parse_day(heading.get_text(" ", strip=True))
        if source_day is None:
            continue
        source_days.add(source_day)
        node = heading.find_next()
        while node is not None:
            if node.name in {"h2", "h3", "h4"} and node is not heading:
                break
            if node.name in {"p", "li", "div"}:
                value = " ".join(node.get_text(" ", strip=True).split())
                match = _PROGRAM_RE.match(value)
                if match:
                    hour, minute = [
                        int(part) for part in match.group(1).split(":")
                    ]
                    raw_rows.append({
                        "start_source": datetime(
                            source_day.year, source_day.month, source_day.day,
                            hour, minute, tzinfo=MSK_TIMEZONE,
                        ),
                        "raw_title": match.group(2).strip(),
                    })
            node = node.find_next()

    deduped = {}
    for row in raw_rows:
        key = (row["start_source"], row["raw_title"])
        deduped[key] = row
    raw_rows = sorted(
        deduped.values(), key=lambda item: item["start_source"]
    )

    events: list[dict] = []
    filtered = 0
    for index, row in enumerate(raw_rows):
        raw_title = row["raw_title"]
        evidence = classify_live_evidence(raw_title)
        next_start = (
            raw_rows[index + 1]["start_source"]
            if index + 1 < len(raw_rows)
            else None
        )
        event = _candidate_event(
            channel=channel,
            page_id=page_id,
            raw_title=raw_title,
            subtitle="",
            desc="",
            categories=[],
            start_source=row["start_source"],
            stop_source=next_start,
            evidence=evidence,
            provider_source="iptvx_page_fallback",
        )
        if event is None:
            filtered += 1
        else:
            event["source_url"] = source_url or page_url_for(channel)
            event["time_normalization"] = "iptvx_msk_page_to_kz"
            events.append(event)

    kz_days = set()
    for source_day in source_days:
        for hour, minute in ((0, 0), (23, 59)):
            kz_days.add(
                datetime(
                    source_day.year, source_day.month, source_day.day,
                    hour, minute, tzinfo=MSK_TIMEZONE,
                ).astimezone(KZ_TIMEZONE).date().isoformat()
            )
    return events, {
        "programmes": len(raw_rows),
        "candidates": len(events),
        "filtered": filtered,
        "days": sorted(kz_days),
    }


async def _fetch_bytes(
    session: aiohttp.ClientSession,
    url: str,
    *,
    maximum: int,
    semaphore: asyncio.Semaphore,
) -> bytes:
    last_error = "request_failed"
    for attempt in range(3):
        retryable = False
        try:
            async with semaphore:
                async with session.get(url, allow_redirects=True) as response:
                    if response.status == 200:
                        raw = await response.read()
                        if not raw or len(raw) > maximum:
                            raise RuntimeError("invalid_size")
                        return raw
                    last_error = f"http_{response.status}"
                    retryable = response.status in {
                        408, 425, 429, 500, 502, 503, 504,
                    }
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            last_error = type(exc).__name__
            retryable = True
        if not retryable or attempt == 2:
            break
        await asyncio.sleep(1.5 * (attempt + 1))
    raise RuntimeError(last_error)


async def _page_fallback(
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
) -> tuple[dict[str, tuple[list[dict], dict]], dict[str, str]]:
    results: dict[str, tuple[list[dict], dict]] = {}
    failures: dict[str, str] = {}

    async def load(channel: str, page_id: str):
        url = page_url_for(channel)
        try:
            raw = await _fetch_bytes(
                session, url,
                maximum=MAX_PAGE_BYTES,
                semaphore=semaphore,
            )
            results[channel] = parse_iptvx_page(
                raw, channel=channel, page_id=page_id, source_url=url
            )
        except Exception as exc:
            failures[channel] = type(exc).__name__ + ": " + str(exc)[:120]

    await asyncio.gather(*(
        load(channel, page_id)
        for channel, page_id in IPTVX_CHANNELS.items()
    ))
    return results, failures


async def refresh_iptvx_sources(database) -> dict:
    """Fetch XMLTV once, filter 14 ids, then persist per-channel snapshots."""
    if not XML_URL:
        return {"status": "disabled", "sources": [], "stats": {}}

    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
    semaphore = asyncio.Semaphore(4)
    xml_error = ""
    xml_stats: dict = {}
    events: list[dict] = []
    fallback: dict[str, tuple[list[dict], dict]] = {}
    fallback_failures: dict[str, str] = {}

    headers = {
        "User-Agent": "Mozilla/5.0 SLP/2.0",
        "Accept": "application/xml,text/xml,*/*",
        "Accept-Encoding": "gzip, deflate",
    }
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        try:
            raw = await _fetch_bytes(
                session, XML_URL,
                maximum=MAX_XML_BYTES,
                semaphore=semaphore,
            )
            events, xml_stats = parse_iptvx_xml(raw)
        except Exception as exc:
            xml_error = type(exc).__name__ + ": " + str(exc)[:160]
            fallback, fallback_failures = await _page_fallback(
                session, semaphore
            )

    today = datetime.now(KZ_TIMEZONE).date()
    first_day = today - timedelta(days=1)
    last_day = today + timedelta(days=8)
    run_id = "iptvx-" + datetime.now(KZ_TIMEZONE).strftime("%Y%m%d%H%M%S%f")

    by_channel: dict[str, list[dict]] = defaultdict(list)
    for event in events:
        by_channel[event["channel"]].append(event)

    source_stats = []
    for channel, page_id in IPTVX_CHANNELS.items():
        source = _SOURCE[channel]
        mode = "xmltv"
        error = ""
        channel_events = by_channel.get(channel, [])
        channel_meta = (
            (xml_stats.get("channels") or {}).get(channel, {})
            if not xml_error else {}
        )
        scope_days = set(channel_meta.get("days") or [])

        if xml_error:
            mode = "page_fallback"
            error = xml_error
            page_events, page_meta = fallback.get(
                channel, ([], {"days": []})
            )
            channel_events = page_events
            scope_days = set(page_meta.get("days") or [])
            if channel in fallback_failures:
                error += "; page: " + fallback_failures[channel]

        channel_events = [
            event for event in channel_events
            if first_day.isoformat() <= event["date"] <= last_day.isoformat()
        ]
        scope_days = {
            day for day in scope_days
            if first_day.isoformat() <= day <= last_day.isoformat()
        }
        by_day: dict[str, list[dict]] = defaultdict(list)
        for event in channel_events:
            by_day[event["date"]].append(event)
            scope_days.add(event["date"])

        # Always retire the old wrong Setanta ids when the new source was
        # successfully read for the current window.
        if scope_days and (not xml_error or channel in fallback):
            for legacy_source in _LEGACY_SOURCES.get(channel, ()):
                for day in sorted(scope_days):
                    database.upsert_source_snapshot(
                        run_id=run_id,
                        source=legacy_source,
                        scope_date=day,
                        events=[],
                    )

        if not scope_days:
            database.record_parser_run(
                run_id=run_id,
                source=source,
                scope_date=today.isoformat(),
                status="warning",
                event_count=0,
                previous_count=None,
                error=error or "channel_not_present_in_current_feed",
                details={
                    "provider": "iptvx",
                    "mode": mode,
                    "page_id": page_id,
                    "xml_url": XML_URL,
                    "page_url": page_url_for(channel),
                },
            )
        else:
            for day in sorted(scope_days):
                rows = sorted(
                    by_day.get(day, []),
                    key=lambda item: (item["time"], item["title"]),
                )
                previous = database.load_active_source_snapshot(source, day)
                database.upsert_source_snapshot(
                    run_id=run_id,
                    source=source,
                    scope_date=day,
                    events=rows,
                )
                database.record_parser_run(
                    run_id=run_id,
                    source=source,
                    scope_date=day,
                    status="ok" if rows else "warning",
                    event_count=len(rows),
                    previous_count=len(previous),
                    error="" if rows else "no_sports_candidates",
                    details={
                        "provider": "iptvx",
                        "mode": mode,
                        "page_id": page_id,
                        "xml_url": XML_URL,
                        "page_url": page_url_for(channel),
                        "programmes": channel_meta.get("programmes", 0),
                        "candidates": channel_meta.get(
                            "candidates", len(channel_events)
                        ),
                        "explicit_live": channel_meta.get(
                            "explicit_live", 0
                        ),
                        "filtered": channel_meta.get("filtered", 0),
                    },
                )

        source_stats.append({
            "channel": channel,
            "page_id": page_id,
            "page_url": page_url_for(channel),
            "mode": mode,
            "events": len(channel_events),
            "days": sorted(scope_days),
            "error": error,
        })

    return {
        "status": "ok" if not xml_error else "warning",
        "source": "xmltv_whitelist",
        "xml_url": XML_URL,
        "sources": source_stats,
        "xml_stats": xml_stats,
        "xml_error": xml_error,
        "page_fallback_failures": fallback_failures,
    }


async def probe_iptvx_source() -> dict:
    """Non-mutating production connectivity probe for the iptvX XMLTV feed."""
    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
    semaphore = asyncio.Semaphore(1)
    headers = {
        "User-Agent": "Mozilla/5.0 SLP/2.0",
        "Accept": "application/xml,text/xml,*/*",
        "Accept-Encoding": "gzip, deflate",
    }
    try:
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            raw = await _fetch_bytes(
                session,
                XML_URL,
                maximum=MAX_XML_BYTES,
                semaphore=semaphore,
            )
        events, stats = parse_iptvx_xml(raw)
        today = datetime.now(KZ_TIMEZONE).date()
        first_day = today - timedelta(days=1)
        last_day = today + timedelta(days=8)
        current_counts: dict[str, int] = defaultdict(int)
        for event in events:
            event_day = str(event.get("date") or "")
            if first_day.isoformat() <= event_day <= last_day.isoformat():
                current_counts[str(event.get("channel") or "")] += 1

        channels = {}
        for channel, page_id in IPTVX_CHANNELS.items():
            meta = (stats.get("channels") or {}).get(channel, {})
            channels[channel] = {
                "page_id": page_id,
                "programmes": int(meta.get("programmes") or 0),
                "candidates": int(meta.get("candidates") or 0),
                "current_window_candidates": int(current_counts.get(channel, 0)),
                "days": list(meta.get("days") or []),
            }
        return {
            "status": "ok",
            "xml_url": XML_URL,
            "payload_bytes": len(raw),
            "mapped": int(stats.get("mapped") or 0),
            "candidates": int(stats.get("candidates") or 0),
            "explicit_live": int(stats.get("explicit_live") or 0),
            "channels": channels,
        }
    except Exception as exc:
        return {
            "status": "error",
            "xml_url": XML_URL,
            "error": type(exc).__name__ + ": " + str(exc)[:240],
            "channels": {},
        }

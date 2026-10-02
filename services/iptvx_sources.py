"""Direct iptvX|one channel-page collector for SLP.

Primary source: the exact per-channel pages supplied by the editor:
https://epg.iptvx.one/id/{tvg-id}

The aggregate XMLTV feed is retained only as a fail-safe when an individual
page is unavailable. Channel pages do not expose a reliable LIVE flag, so SLP
keeps only concrete sports-programme rows and rejects obvious replay/editorial
content. External event validation remains a separate safety layer.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
import asyncio
import os
import re
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
XML_FALLBACK_URL = os.getenv(
    "IPTVX_EPG_URL", "https://iptvx.one/EPG_NOARCH"
).strip()
REQUEST_TIMEOUT_SECONDS = 35
MAX_PAGE_BYTES = 3 * 1024 * 1024
MAX_XML_BYTES = 80 * 1024 * 1024
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

_MONTHS = {
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
}
_DAY_RE = re.compile(
    r"(?iu)(?:понедельник|вторник|среда|четверг|пятница|"
    r"суббота|воскресенье)?\s*,?\s*(\d{1,2})\s+"
    r"([а-яё]+)\s+(\d{4})\s*г\.?"
)
_PROGRAM_RE = re.compile(r"^\s*(\d{1,2}:\d{2})\s+(.+?)\s*$")

_SPORT_INFERENCE = (
    (re.compile(r"(?iu)\b(?:ATP|WTA)\b"), "Теннис"),
    (re.compile(r"(?iu)\b(?:UFC|MMA|ММА)\b"), "ММА"),
    (re.compile(r"(?iu)\b(?:КХЛ|KHL)\b"), "Хоккей"),
    (re.compile(r"(?iu)\b(?:F1|Формула[- ]?1|WRC|WEC)\b"), "Автоспорт"),
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


def _infer_sport(raw_title: str, normalized_sport: str, channel: str) -> str:
    if normalized_sport:
        return normalized_sport
    hint = str((CHANNEL_BY_NAME.get(channel) or object()).sport_hint or "") \
        if channel in CHANNEL_BY_NAME else ""
    if hint:
        return hint
    for pattern, sport in _SPORT_INFERENCE:
        if pattern.search(raw_title):
            return sport
    return ""


def _looks_like_sport(raw_title: str, sport: str) -> bool:
    if sport:
        return True
    upper = " ".join(str(raw_title or "").upper().split())
    return any(marker in upper for marker in SPORT_TEXT_MARKERS)


def parse_iptvx_page(
    html: bytes | str,
    *,
    channel: str,
    page_id: str,
    source_url: str | None = None,
) -> tuple[list[dict], dict]:
    """Parse one exact iptvX channel page.

    Page times are displayed in the source's MSK EPG convention. They are
    converted once to Asia/Almaty.
    """
    if isinstance(html, bytes):
        text = html.decode("utf-8", errors="replace")
    else:
        text = str(html or "")
    soup = BeautifulSoup(text, "html.parser")

    tvg_marker = soup.find(
        "u", string=re.compile(r"tvg-id\s*=\s*[\"']?" + re.escape(page_id), re.I)
    )
    if tvg_marker is None:
        raise ValueError("iptvx_page_id_mismatch")

    raw_rows: list[dict] = []
    source_days: set[date] = set()
    for heading in soup.find_all("h3"):
        source_day = _parse_day(heading.get_text(" ", strip=True))
        if source_day is None:
            continue
        section = heading.find_next_sibling("section")
        if section is None:
            continue
        source_days.add(source_day)
        for paragraph in section.find_all("p", recursive=False):
            value = " ".join(paragraph.get_text(" ", strip=True).split())
            match = _PROGRAM_RE.match(value)
            if not match:
                continue
            try:
                hour, minute = [int(part) for part in match.group(1).split(":")]
                start_msk = datetime(
                    source_day.year, source_day.month, source_day.day,
                    hour, minute, tzinfo=MSK_TIMEZONE,
                )
            except (ValueError, TypeError):
                continue
            raw_rows.append({
                "start_msk": start_msk,
                "raw_title": match.group(2).strip(),
            })

    raw_rows.sort(key=lambda item: item["start_msk"])
    events: list[dict] = []
    filtered = 0
    for index, row in enumerate(raw_rows):
        raw_title = row["raw_title"]
        explicit = classify_live_evidence(raw_title)
        if explicit.state == "not_live":
            filtered += 1
            continue

        normalized = normalize_event_fields(
            title=raw_title,
            sport="",
            tournament="",
        )
        sport = _infer_sport(raw_title, normalized["sport"], channel)
        event_title = normalized["title"] or raw_title
        candidate = {
            "raw_title": raw_title,
            "title": event_title,
            "sport": sport,
            "tournament": normalized["tournament"],
        }
        if (
            not _looks_like_sport(raw_title, sport)
            or event_is_editorial_or_replay(candidate)
        ):
            filtered += 1
            continue

        start_msk = row["start_msk"]
        start_kz = start_msk.astimezone(KZ_TIMEZONE)
        event = {
            "source": _SOURCE[channel],
            "source_url": source_url or page_url_for(channel),
            "provider_source": "iptvx_page",
            "provider_channel_id": page_id,
            "channel": channel,
            "date": start_kz.date().isoformat(),
            "time": start_kz.strftime("%H:%M"),
            "source_start_at": start_msk.isoformat(),
            "timezone": "Asia/Almaty",
            "time_normalization": "iptvx_msk_page_to_kz",
            "title": event_title,
            "raw_title": raw_title,
            "sport": sport,
            "tournament": normalized["tournament"],
            "is_sport_event": True,
            "is_live": True,
            "is_live_broadcast": True,
            "live_state": "live" if explicit.is_live else "candidate",
            "live_evidence_method": (
                "provider_live_text"
                if explicit.is_live
                else "iptvx_channel_page_schedule"
            ),
            "live_evidence_value": (
                explicit.value if explicit.is_live else page_id
            ),
            "live_confidence": "high" if explicit.is_live else "candidate",
        }

        if index + 1 < len(raw_rows):
            next_start = raw_rows[index + 1]["start_msk"]
            if next_start > start_msk:
                end_kz = next_start.astimezone(KZ_TIMEZONE)
                event["estimated_broadcast_end_date"] = end_kz.date().isoformat()
                event["estimated_broadcast_end"] = end_kz.strftime("%H:%M")
                event["end_estimation_method"] = "provider_epg"

        events.append(event)

    kz_days: set[str] = set()
    for source_day in source_days:
        # MSK 00:00-23:59 maps across two KZ calendar dates.
        kz_days.add(
            datetime(
                source_day.year, source_day.month, source_day.day,
                0, 0, tzinfo=MSK_TIMEZONE,
            ).astimezone(KZ_TIMEZONE).date().isoformat()
        )
        kz_days.add(
            datetime(
                source_day.year, source_day.month, source_day.day,
                23, 59, tzinfo=MSK_TIMEZONE,
            ).astimezone(KZ_TIMEZONE).date().isoformat()
        )

    return events, {
        "programmes": len(raw_rows),
        "sport_candidates": len(events),
        "filtered": filtered,
        "source_days": sorted(day.isoformat() for day in source_days),
        "kz_days": sorted(kz_days),
    }


def parse_iptvx_xml(raw: bytes | str) -> tuple[list[dict], dict]:
    """Fail-safe XML parser. Only explicit LIVE evidence is accepted here."""
    payload = raw.encode("utf-8") if isinstance(raw, str) else raw
    root = ET.fromstring(payload)
    events: list[dict] = []
    stats = {"programmes": 0, "mapped": 0, "live": 0, "unconfirmed": 0}

    for programme in root.iter():
        if _local_name(programme.tag) != "programme":
            continue
        stats["programmes"] += 1
        tvg_id = str(programme.attrib.get("channel") or "").strip()
        channel = ID_TO_CHANNEL.get(tvg_id)
        if not channel:
            continue
        stats["mapped"] += 1

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
        if not evidence.is_live:
            stats["unconfirmed"] += 1
            continue

        normalized = normalize_event_fields(
            title=title or subtitle,
            sport=categories[0] if categories else "",
            tournament="",
        )
        sport = _infer_sport(title or subtitle, normalized["sport"], channel)
        event_title = normalized["title"] or title or subtitle
        if not event_title:
            continue
        start_source = _xmltv_datetime(programme.attrib.get("start", ""))
        start = start_source.astimezone(KZ_TIMEZONE)
        stop_raw = str(programme.attrib.get("stop") or "").strip()
        stop = (
            _xmltv_datetime(stop_raw).astimezone(KZ_TIMEZONE)
            if stop_raw else None
        )

        event = {
            "source": _SOURCE[channel],
            "source_url": XML_FALLBACK_URL,
            "provider_source": "iptvx_xml_fallback",
            "provider_channel_id": tvg_id,
            "channel": channel,
            "date": start.date().isoformat(),
            "time": start.strftime("%H:%M"),
            "source_start_at": start_source.isoformat(),
            "timezone": "Asia/Almaty",
            "time_normalization": "xmltv_offset_to_kz",
            "title": event_title,
            "raw_title": title or subtitle,
            "sport": sport,
            "tournament": normalized["tournament"],
            "is_sport_event": bool(sport or categories),
            "is_live": True,
            "is_live_broadcast": True,
            "live_state": "live",
            "live_evidence_method": (
                "provider_live_text"
                if evidence.method.endswith("_text")
                else "provider_live_asset"
            ),
            "live_evidence_value": evidence.value,
            "live_confidence": "high",
        }
        if stop and stop > start:
            event["estimated_broadcast_end_date"] = stop.date().isoformat()
            event["estimated_broadcast_end"] = stop.strftime("%H:%M")
            event["end_estimation_method"] = "provider_epg"

        if (
            not event["is_sport_event"]
            or event_is_editorial_or_replay(event)
        ):
            continue
        events.append(event)
        stats["live"] += 1

    return events, stats


async def _fetch_bytes(
    session: aiohttp.ClientSession,
    url: str,
    *,
    maximum: int,
    semaphore: asyncio.Semaphore,
) -> bytes:
    last_error = "request_failed"
    for attempt in range(3):
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


async def refresh_iptvx_sources(database) -> dict:
    """Fetch all exact editor-approved channel pages and persist candidates."""
    if not PAGE_BASE_URL:
        return {"status": "disabled", "sources": [], "stats": {}}

    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
    headers = {
        "User-Agent": "Mozilla/5.0 SLP/2.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Encoding": "gzip, deflate",
    }
    semaphore = asyncio.Semaphore(4)
    direct: dict[str, tuple[list[dict], dict]] = {}
    failures: dict[str, str] = {}

    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        async def load(channel: str, page_id: str):
            url = page_url_for(channel)
            try:
                raw = await _fetch_bytes(
                    session, url,
                    maximum=MAX_PAGE_BYTES,
                    semaphore=semaphore,
                )
                direct[channel] = parse_iptvx_page(
                    raw,
                    channel=channel,
                    page_id=page_id,
                    source_url=url,
                )
            except Exception as exc:
                failures[channel] = type(exc).__name__ + ": " + str(exc)[:120]

        await asyncio.gather(*(
            load(channel, page_id)
            for channel, page_id in IPTVX_CHANNELS.items()
        ))

        fallback_events: list[dict] = []
        fallback_stats: dict = {}
        if failures and XML_FALLBACK_URL:
            try:
                raw_xml = await _fetch_bytes(
                    session, XML_FALLBACK_URL,
                    maximum=MAX_XML_BYTES,
                    semaphore=semaphore,
                )
                fallback_events, fallback_stats = parse_iptvx_xml(raw_xml)
            except Exception as exc:
                fallback_stats = {
                    "error": type(exc).__name__ + ": " + str(exc)[:120]
                }

    today = datetime.now(KZ_TIMEZONE).date()
    first_day = today - timedelta(days=1)
    last_day = today + timedelta(days=8)
    run_id = "iptvx-" + datetime.now(KZ_TIMEZONE).strftime("%Y%m%d%H%M%S%f")
    source_stats = []

    fallback_by_channel: dict[str, list[dict]] = defaultdict(list)
    for event in fallback_events:
        fallback_by_channel[event["channel"]].append(event)

    for channel, page_id in IPTVX_CHANNELS.items():
        source = _SOURCE[channel]
        mode = "page"
        error = failures.get(channel, "")
        if channel in direct:
            events, page_stats = direct[channel]
            scope_days = {
                value for value in page_stats.get("kz_days", [])
                if first_day.isoformat() <= value <= last_day.isoformat()
            }
        else:
            mode = "xml_fallback"
            events = fallback_by_channel.get(channel, [])
            page_stats = {
                "programmes": 0,
                "sport_candidates": len(events),
                "filtered": 0,
                "source_days": [],
                "kz_days": sorted({event["date"] for event in events}),
            }
            scope_days = set(page_stats["kz_days"])

        events = [
            event for event in events
            if first_day.isoformat() <= event["date"] <= last_day.isoformat()
        ]
        by_day: dict[str, list[dict]] = defaultdict(list)
        for event in events:
            by_day[event["date"]].append(event)
            scope_days.add(event["date"])

        if not scope_days:
            database.record_parser_run(
                run_id=run_id,
                source=source,
                scope_date=today.isoformat(),
                status="warning",
                event_count=0,
                previous_count=None,
                error=error or "no_schedule_days",
                details={
                    "provider": "iptvx",
                    "mode": mode,
                    "page_id": page_id,
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
                    status="ok" if mode == "page" else "warning",
                    event_count=len(rows),
                    previous_count=len(previous),
                    error=error if mode != "page" else "",
                    details={
                        "provider": "iptvx",
                        "mode": mode,
                        "page_id": page_id,
                        "page_url": page_url_for(channel),
                        "programmes": page_stats.get("programmes", 0),
                        "sport_candidates": page_stats.get(
                            "sport_candidates", len(events)
                        ),
                        "filtered": page_stats.get("filtered", 0),
                    },
                )

        source_stats.append({
            "channel": channel,
            "page_id": page_id,
            "page_url": page_url_for(channel),
            "mode": mode,
            "events": len(events),
            "days": sorted(scope_days),
            "error": error,
        })

    return {
        "status": "ok" if not failures else "warning",
        "source": "direct_channel_pages",
        "page_base": PAGE_BASE_URL,
        "sources": source_stats,
        "failed_pages": failures,
        "xml_fallback_stats": fallback_stats,
    }

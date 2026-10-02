"""Best-effort iptvX|one XMLTV source for SLP.

The public EPG_NOARCH feed is fetched once per collection and filtered to the
small SLP whitelist. Only programmes with explicit LIVE evidence are persisted
as schedule candidates. Ordinary EPG rows remain diagnostic-only and can never
replace supplier Excel or official LIVE evidence.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
import os
import re
import xml.etree.ElementTree as ET

import aiohttp

from services.channel_registry import IPTVX_CHANNELS
from services.event_text_ru import normalize_event_fields
from services.live_evidence import classify_live_evidence, event_is_editorial_or_replay
from services.time_logic import KZ_TIMEZONE

EPG_URL = os.getenv("IPTVX_EPG_URL", "https://iptvx.one/EPG_NOARCH").strip()
REQUEST_TIMEOUT_SECONDS = 35
ID_TO_CHANNEL = {tvg_id: channel for channel, tvg_id in IPTVX_CHANNELS.items()}
_SOURCE = {
    channel: "web_iptvx_" + tvg_id
    for channel, tvg_id in IPTVX_CHANNELS.items()
}


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
    stamp = match.group(1)
    stamp = stamp.ljust(14, "0")
    parsed = datetime.strptime(stamp[:14], "%Y%m%d%H%M%S")
    offset = match.group(2)
    if offset == "Z":
        return parsed.replace(tzinfo=timezone.utc)
    if offset:
        sign = 1 if offset[0] == "+" else -1
        minutes = int(offset[1:3]) * 60 + int(offset[3:5])
        return parsed.replace(tzinfo=timezone(sign * timedelta(minutes=minutes)))
    # iptvX pages publish their update times in MSK; an XMLTV row without an
    # explicit offset is therefore treated conservatively as UTC+3.
    return parsed.replace(tzinfo=timezone(timedelta(hours=3)))


def parse_iptvx_xml(raw: bytes | str) -> tuple[list[dict], dict]:
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
            " ".join([title, subtitle, desc, *categories, "LIVE" if has_live_element else ""]),
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
        event_title = normalized["title"] or title or subtitle
        if not event_title:
            continue
        start_source = _xmltv_datetime(programme.attrib.get("start", ""))
        start = start_source.astimezone(KZ_TIMEZONE)
        stop_raw = str(programme.attrib.get("stop") or "").strip()
        stop = _xmltv_datetime(stop_raw).astimezone(KZ_TIMEZONE) if stop_raw else None

        event = {
            "source": _SOURCE[channel],
            "source_url": EPG_URL,
            "provider_source": "iptvx",
            "provider_channel_id": tvg_id,
            "channel": channel,
            "date": start.date().isoformat(),
            "time": start.strftime("%H:%M"),
            "source_start_at": start_source.isoformat(),
            "timezone": "Asia/Almaty",
            "time_normalization": "xmltv_offset_to_kz",
            "title": event_title,
            "raw_title": title or subtitle,
            "sport": normalized["sport"],
            "tournament": normalized["tournament"],
            "is_sport_event": bool(normalized["sport"] or categories),
            "is_live": True,
            "is_live_broadcast": True,
            "live_state": "live",
            "live_evidence_method": "provider_live_text"
                if evidence.method.endswith("_text") else "provider_live_asset",
            "live_evidence_value": evidence.value,
        }
        if stop and stop > start:
            event["estimated_broadcast_end_date"] = stop.date().isoformat()
            event["estimated_broadcast_end"] = stop.strftime("%H:%M")
            event["end_estimation_method"] = "provider_epg"

        if not event["is_sport_event"] or event_is_editorial_or_replay(event):
            continue
        events.append(event)
        stats["live"] += 1

    return events, stats


async def refresh_iptvx_sources(database) -> dict:
    if not EPG_URL:
        return {"status": "disabled", "sources": [], "stats": {}}

    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
    headers = {
        "User-Agent": "Mozilla/5.0 SLP/2.0",
        "Accept": "application/xml,text/xml,*/*",
        "Accept-Encoding": "gzip, deflate",
    }
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        async with session.get(EPG_URL, allow_redirects=True) as response:
            if response.status != 200:
                raise RuntimeError(f"iptvx_http_{response.status}")
            raw = await response.read()
            if not raw or len(raw) > 80 * 1024 * 1024:
                raise RuntimeError("iptvx_invalid_size")

    events, parse_stats = parse_iptvx_xml(raw)
    run_id = "iptvx-" + datetime.now(KZ_TIMEZONE).strftime("%Y%m%d%H%M%S%f")
    by_source_day: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for event in events:
        by_source_day[(event["source"], event["date"])].append(event)

    source_stats = []
    for channel, tvg_id in IPTVX_CHANNELS.items():
        source = _SOURCE[channel]
        channel_rows = [
            event for event in events if event["channel"] == channel
        ]
        days = sorted({event["date"] for event in channel_rows})
        if not days:
            database.record_parser_run(
                run_id=run_id,
                source=source,
                scope_date=datetime.now(KZ_TIMEZONE).date().isoformat(),
                status="warning",
                event_count=0,
                previous_count=None,
                error="no_explicit_live_rows",
                details={"provider": "iptvx", "tvg_id": tvg_id},
            )
        for day in days:
            rows = by_source_day[(source, day)]
            previous = database.load_active_source_snapshot(source, day)
            # Do not erase an existing last-good day merely because the public
            # aggregate feed became partial.
            if previous and len(rows) < len(previous):
                database.record_parser_run(
                    run_id=run_id, source=source, scope_date=day,
                    status="warning", event_count=len(rows),
                    previous_count=len(previous), error="partial_update_held",
                    details={"provider": "iptvx", "tvg_id": tvg_id},
                )
                continue
            database.upsert_source_snapshot(
                run_id=run_id, source=source, scope_date=day, events=rows,
            )
            database.record_parser_run(
                run_id=run_id, source=source, scope_date=day,
                status="ok", event_count=len(rows),
                previous_count=len(previous),
                details={"provider": "iptvx", "tvg_id": tvg_id},
            )
        source_stats.append({
            "channel": channel,
            "tvg_id": tvg_id,
            "live_events": len(channel_rows),
            "days": days,
        })

    return {
        "status": "ok",
        "url": EPG_URL,
        "sources": source_stats,
        "stats": parse_stats,
    }

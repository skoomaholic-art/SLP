from __future__ import annotations

import logging
from datetime import date, datetime

from parsers.tvguide_cached import get_tvguide_schedule
from parsers.vsetv_live import get_vsetv_live_evidence
from services.broadcast_evidence import add_broadcast_evidence
from services.time_logic import KZ_TIMEZONE
from verifiers.web_search import matches_event


logger = logging.getLogger(__name__)


def _vsetv_matches_event(row: dict, event: dict) -> bool:
    if str(row.get("channel") or "") != str(event.get("channel") or ""):
        return False
    result = {
        "title": str(row.get("title") or ""),
        "snippet": str(row.get("raw_title") or ""),
        "url": str(row.get("source_url") or ""),
    }
    return matches_event(result, event)


def apply_vsetv_evidence(events: list[dict], rows: list[dict]) -> list[dict]:
    by_channel: dict[str, list[dict]] = {}
    for row in rows:
        by_channel.setdefault(str(row.get("channel") or ""), []).append(row)

    result: list[dict] = []
    matched = 0
    for event in events:
        item = dict(event)
        channel_rows = by_channel.get(str(item.get("channel") or ""), [])
        for row in channel_rows:
            if not _vsetv_matches_event(row, item):
                continue
            matched += 1
            value = str(row.get("title") or "VseTV LIVE badge")
            item = add_broadcast_evidence(
                item,
                method="third_party_live_badge",
                source="vsetv.com",
                value=value,
                confidence="medium-high" if row.get("explicit_direct_text") else "medium",
            )
            item["vsetv_live_time"] = str(row.get("time") or "")
            item["vsetv_source_url"] = str(row.get("source_url") or "")
            if item.get("reconciliation_state") in {
                "unverified",
                "unknown",
                "pending_official_fallback",
            }:
                item["reconciliation_state"] = "third_party_live_unconfirmed"
            break
        result.append(item)

    logger.info(
        "tvguide VseTV evidence rows=%d matched=%d",
        len(rows),
        matched,
    )
    return result


async def get_tvguide_schedule_with_evidence(
    target_date: date | datetime | str | None = None,
) -> list[dict]:
    events = await get_tvguide_schedule(target_date)
    if target_date is None:
        requested = datetime.now(KZ_TIMEZONE).date()
    elif isinstance(target_date, datetime):
        requested = target_date.astimezone(KZ_TIMEZONE).date() if target_date.tzinfo else target_date.date()
    elif isinstance(target_date, date):
        requested = target_date
    else:
        requested = datetime.strptime(str(target_date), "%Y-%m-%d").date()

    try:
        rows, errors = await get_vsetv_live_evidence(requested)
    except Exception:
        logger.warning(
            "VseTV evidence unavailable date=%s",
            requested,
            exc_info=True,
        )
        return events

    if errors:
        logger.warning("VseTV evidence partial errors=%s", errors[:10])
    return apply_vsetv_evidence(events, rows)

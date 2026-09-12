from __future__ import annotations

import logging
from datetime import date, datetime

from services.time_logic import KZ_TIMEZONE
from verifiers.broadcast_occurrence import (
    FAST_TIMEOUT_SECONDS,
    OFFICIAL_EVENT_DOMAINS,
    SEARCH_ENGINE_GROUPS,
    _dated_candidates,
    _extract_matching_pages,
    _openserp_search_any,
    _relaxed_match,
    verify_broadcast_occurrence,
)
from verifiers.web_search import (
    build_queries,
    dedupe_results,
    get_base_domain,
    get_domain,
)

logger = logging.getLogger(__name__)
CHAMPIONAT_DOMAIN = "championat.com"
MAX_PRE_SHOW_MINUTES = 45
MAX_LATE_JOIN_MINUTES = 10
MAX_RESULTS = 12


def _championat_queries(event: dict) -> list[str]:
    title = str(
        event.get("title")
        or event.get("raw_event_title")
        or event.get("raw_title")
        or ""
    ).strip()
    tournament = str(event.get("tournament") or "").strip()
    sport = str(event.get("sport") or "").strip()
    date_text = str(event.get("date") or "").strip()

    raw = " ".join(value for value in (title, tournament, sport, date_text) if value)
    queries = [f"site:{CHAMPIONAT_DOMAIN} {raw}".strip()]
    for query in build_queries(event):
        value = f"site:{CHAMPIONAT_DOMAIN} {query}".strip()
        if value not in queries:
            queries.append(value)
    return queries


def _is_championat_result(result: dict) -> bool:
    url = str(result.get("url") or result.get("link") or "")
    return get_base_domain(get_domain(url)) == CHAMPIONAT_DOMAIN


def _search_championat(event: dict) -> tuple[list[dict], list[dict], list[str]]:
    all_results: list[dict] = []
    meta: list[dict] = []
    errors: list[str] = []

    for query_index, query in enumerate(_championat_queries(event)):
        for engines in SEARCH_ENGINE_GROUPS:
            try:
                results, search_meta = _openserp_search_any(
                    query,
                    engines=engines,
                    timeout=FAST_TIMEOUT_SECONDS,
                )
                meta.append(
                    {
                        **search_meta,
                        "query_index": query_index,
                        "engine_group": list(engines),
                        "source_filter": CHAMPIONAT_DOMAIN,
                    }
                )
                filtered = [result for result in results if _is_championat_result(result)]
                if filtered:
                    all_results.extend(filtered)
                    break
            except Exception as error:
                errors.append(
                    f"{','.join(engines)}:{type(error).__name__}:{error}"
                )

        all_results = dedupe_results(all_results)[:MAX_RESULTS]
        if any(_relaxed_match(result, event) for result in all_results):
            break

    return all_results, meta, errors


def _plausible_difference(minutes: int) -> bool:
    # TV coverage may start before the real event (studio/pregame), but a TV slot
    # that begins materially after the actual start is more likely delayed/replay.
    return -MAX_LATE_JOIN_MINUTES <= minutes <= MAX_PRE_SHOW_MINUTES


def _official_fallback_allowed(result: dict) -> bool:
    if result.get("state") != "confirmed_direct":
        return False
    for source in result.get("sources") or []:
        domain = get_base_domain(get_domain(str(source.get("url") or "")))
        if domain in OFFICIAL_EVENT_DOMAINS:
            return True
    return False


def verify_championat_occurrence(event: dict) -> dict:
    """Fail-closed TVGuide verification.

    Championat.com is the primary reference for the real sporting occurrence.
    A TV EPG row becomes publishable only when Championat confirms the same
    event close to the TV slot. If Championat has no usable result, an official
    league/federation source may confirm it as a fallback. Arbitrary media
    consensus is intentionally insufficient for TVGuide LIVE publication.
    """
    results, search_meta, search_errors = _search_championat(event)
    matching = [result for result in results if _relaxed_match(result, event)]
    broadcast = datetime.strptime(
        f"{event['date']} {event['time']}", "%Y-%m-%d %H:%M"
    ).replace(tzinfo=KZ_TIMEZONE)

    candidates: list[dict] = []
    seen_dates: set[date] = set()
    for result in matching:
        extracted, dates = _dated_candidates(result)
        candidates.extend(extracted)
        seen_dates.update(dates)

    if not candidates and matching:
        for page in _extract_matching_pages(matching):
            if not _is_championat_result(page) or not _relaxed_match(page, event):
                continue
            extracted, dates = _dated_candidates(page)
            candidates.extend(extracted)
            seen_dates.update(dates)

    enriched: list[dict] = []
    for candidate in candidates:
        difference = int((candidate["dt_kz"] - broadcast).total_seconds() / 60)
        enriched.append({**candidate, "difference_minutes": difference})

    plausible = [
        candidate for candidate in enriched
        if _plausible_difference(int(candidate["difference_minutes"]))
    ]
    if plausible:
        best = min(
            plausible,
            key=lambda item: (
                abs(int(item["difference_minutes"])),
                -int(item.get("context_score") or 0),
            ),
        )
        logger.info(
            "championat confirmed channel=%s scheduled=%s %s external=%s diff=%d",
            event.get("channel"),
            event.get("date"),
            event.get("time"),
            best["dt_kz"].isoformat(),
            best["difference_minutes"],
        )
        return {
            "state": "confirmed_direct",
            "verification_source": CHAMPIONAT_DOMAIN,
            "difference_minutes": best["difference_minutes"],
            "external_time_kz": best["dt_kz"].isoformat(),
            "sources": [
                {
                    "source_name": CHAMPIONAT_DOMAIN,
                    "url": best["url"],
                    "difference_minutes": best["difference_minutes"],
                }
            ],
            "search_meta": search_meta,
            "search_errors": search_errors,
        }

    if enriched:
        closest = min(enriched, key=lambda item: abs(int(item["difference_minutes"])))
        logger.info(
            "championat mismatch channel=%s scheduled=%s %s external=%s diff=%d",
            event.get("channel"),
            event.get("date"),
            event.get("time"),
            closest["dt_kz"].isoformat(),
            closest["difference_minutes"],
        )
        return {
            "state": "mismatch",
            "verification_source": CHAMPIONAT_DOMAIN,
            "reason": "championat_time_mismatch",
            "difference_minutes": closest["difference_minutes"],
            "external_time_kz": closest["dt_kz"].isoformat(),
            "sources": [
                {
                    "source_name": CHAMPIONAT_DOMAIN,
                    "url": closest["url"],
                    "difference_minutes": closest["difference_minutes"],
                }
            ],
            "search_meta": search_meta,
            "search_errors": search_errors,
        }

    if seen_dates and all(abs((found - broadcast.date()).days) > 1 for found in seen_dates):
        return {
            "state": "mismatch",
            "verification_source": CHAMPIONAT_DOMAIN,
            "reason": "championat_date_mismatch",
            "external_dates": sorted(value.isoformat() for value in seen_dates),
            "sources": [],
            "search_meta": search_meta,
            "search_errors": search_errors,
        }

    # Championat does not cover absolutely every niche/local event. In that case
    # allow only a real official organizer/league/federation page to promote the
    # EPG row. Two generic media snippets are no longer sufficient.
    fallback = verify_broadcast_occurrence(event)
    if _official_fallback_allowed(fallback):
        return {
            **fallback,
            "verification_source": "official_fallback",
            "championat_matching_results_count": len(matching),
        }

    if fallback.get("state") == "mismatch":
        official_sources = []
        for source in fallback.get("sources") or []:
            domain = get_base_domain(get_domain(str(source.get("url") or "")))
            if domain in OFFICIAL_EVENT_DOMAINS:
                official_sources.append(source)
        if official_sources:
            return {
                **fallback,
                "verification_source": "official_fallback",
                "sources": official_sources,
                "championat_matching_results_count": len(matching),
            }

    unavailable = bool(search_errors and not results and fallback.get("state") == "unavailable")
    return {
        "state": "unavailable" if unavailable else "unknown",
        "verification_source": CHAMPIONAT_DOMAIN,
        "reason": "championat_not_confirmed",
        "matching_results_count": len(matching),
        "search_results_count": len(results),
        "sources": [],
        "search_meta": search_meta,
        "search_errors": search_errors,
    }

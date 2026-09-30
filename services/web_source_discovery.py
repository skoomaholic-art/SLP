"""Bounded open-web fallback for channels whose current EPG source is unavailable.

The public web is a *discovery* source, not authority for linear TV carriage.
Only a dated channel programme with an explicit direct-broadcast marker may
enter the LIVE feed. Search-engine snippets and sports fixture calendars are
never converted into confirmed channel broadcasts.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import date, datetime, timedelta
import html
import re
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

import aiohttp
from bs4 import BeautifulSoup

from services.channel_registry import CHANNELS
from services.time_logic import KZ_TIMEZONE

MSK = ZoneInfo("Europe/Moscow")
REQUEST_TIMEOUT = 12
SEARCH_HOST = "www.bing.com"
MATCHTV_URL = "https://matchtv.ru/tvguide"
# The provider's channel names, not vaguely similar sports brand names.
MATCHTV_SECTIONS = {
    "Матч Планета": "МАТЧ! ПЛАНЕТА",
    "КХЛ ТВ": "KHL HD",
    "КХЛ Prime": "KHL PRIME",
}
ALL_MATCHTV_HEADINGS = (
    "Матч ТВ", "Матч Премьер", "Матч Страна", "Футбол 1", "Футбол 2",
    "Футбол 3", "КХЛ ТВ", "КХЛ Prime", "Матч Боец", "Конный мир",
    "Матч Арена", "Матч Игра", "Матч Планета",
)
# Browse-only sources: a channel's programme from a third party needs
# confirmation and cannot silently replace a supplier/official listing.
EXTERNAL_GUIDES = {
    "SETANTA SPORTS 1": (
        "https://tv.yandex.kz/163/channel/setanta-sports-1-1546",),
    "SETANTA SPORTS 2": (
        "https://tv.yandex.kz/159/channel/setanta-sports-2-1547",),
    "Q LEAGUE": ("https://tv.yandex.kz/channel/q-league-1572",),
    "Q ARENA": ("https://tv.yandex.kz/channel/q-arena-1570",),
    "viju+ Sport": (
        "https://tv.yandex.kz/channel/viasat-sport-548",
        "https://viju.ru/tv-channels/vijuplus-sport/"),
    "Q FOOTBALL": ("https://qsport.tv/ru/tv-program/",),
}
OFFICIAL_GUIDES = {
    "Q LEAGUE": ("https://qsport.tv/ru/tv-program/",),
    "Q ARENA": ("https://qsport.tv/ru/tv-program/",),
    "Q FOOTBALL": ("https://qsport.tv/ru/tv-program/",),
    "QAZSPORT HD": ("https://qazsporttv.kz/ru/program",),
    "SPORT+ Qazaqstan": ("https://sportplustv.kz/ru/tvguide",),
    "viju+ Sport": ("https://viju.ru/tv-channels/vijuplus-sport/",),
    "МАТЧ! ПЛАНЕТА": (MATCHTV_URL,),
    "KHL HD": (MATCHTV_URL,),
    "KHL PRIME": (MATCHTV_URL,),
}
LIVE_RE = re.compile(r"(?<![а-яa-z])(?:LIVE|ПРЯМАЯ ТРАНСЛЯЦИЯ|ПРЯМОЙ ЭФИР|ТІКЕЛЕЙ ЭФИР)(?![а-яa-z])", re.I)
REPLAY_RE = re.compile(r"повтор|запись|обзор|replay|highlights|студия|дневник", re.I)
TIME_RE = re.compile(r"^(?:[01]?\d|2[0-3]):[0-5]\d$")
SPORTS = (
    (re.compile(r"футбол|футзал", re.I), "Футбол"),
    (re.compile(r"хокке|кхл", re.I), "Хоккей"),
    (re.compile(r"баскетбол", re.I), "Баскетбол"),
    (re.compile(r"волейбол", re.I), "Волейбол"),
    (re.compile(r"теннис", re.I), "Теннис"),
    (re.compile(r"снукер", re.I), "Снукер"),
    (re.compile(r"биатлон", re.I), "Биатлон"),
    (re.compile(r"лыжн", re.I), "Лыжный спорт"),
    (re.compile(r"мотоспорт|автоспорт|формул", re.I), "Автоспорт"),
    (re.compile(r"бокс|mma|мма|ufc", re.I), "ММА"),
    (re.compile(r"гимнастик", re.I), "Гимнастика"),
    (re.compile(r"борьб|дзюдо|самбо", re.I), "Борьба"),
    (re.compile(r"регби", re.I), "Регби"),
    (re.compile(r"велоспорт", re.I), "Велоспорт"),
)
CHANNEL_LABELS = {channel.name: channel.name for channel in CHANNELS}


def slug(channel: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", channel.casefold()).strip("_") or "channel"


def _sport(title: str) -> str:
    return next((sport for expr, sport in SPORTS if expr.search(title)), "")


def _clean_title(title: str) -> str:
    return " ".join(REPLAY_RE.sub("", title).split())


def parse_matchtv_day(markup: str, day: date) -> list[dict]:
    """Extract explicit LIVE slots from official Match TV channel sections.

    A remote guide may publish an empty date. It does not mean all previously
    confirmed broadcasts are cancelled. We only accept structured heading
    sections; search-engine summaries are deliberately not used here.
    """
    soup = BeautifulSoup(markup, "html.parser")
    for unwanted in soup.select("script, style, nav, footer"):
        unwanted.decompose()
    tokens = [" ".join(s.split()) for s in soup.stripped_strings]
    out = []
    # Do not accept a generic article which merely mentions a channel name.
    if "Телепрограмма" not in tokens:
        return out
    # A programme can run over midnight. The guide's date belongs to its first
    # slot; an overnight rollover is a next-day event in Asia/Almaty.
    active = None
    last_minutes = None
    guide_day = day
    for i, token in enumerate(tokens):
        if token in ALL_MATCHTV_HEADINGS:
            active = MATCHTV_SECTIONS.get(token)
            last_minutes = None
            guide_day = day
            continue
        if active is None or not TIME_RE.fullmatch(token):
            continue
        if i + 1 >= len(tokens):
            continue
        title = tokens[i + 1]
        # Some templates split a fixture description from a LIVE marker.
        # Never infer LIVE solely from an exciting sporting title.
        if not (LIVE_RE.search(title) and not REPLAY_RE.search(title)):
            continue
        sport = _sport(title)
        if not sport:
            continue
        hour, minute = map(int, token.split(":"))
        minutes = hour * 60 + minute
        if last_minutes is not None and minutes < last_minutes and last_minutes > 20 * 60 and minutes < 6 * 60:
            guide_day += timedelta(days=1)
        last_minutes = minutes
        start_msk = datetime.combine(guide_day, datetime.min.time()).replace(
            hour=hour, minute=minute, tzinfo=MSK,
        )
        start_kz = start_msk.astimezone(KZ_TIMEZONE)
        out.append({
            "source": "web_official_matchtv", "provider_source": "matchtv.ru",
            "source_url": MATCHTV_URL + "?" + urlencode({"date": day.strftime("%d-%m-%Y")}),
            "channel": active, "date": start_kz.date().isoformat(),
            "time": start_kz.strftime("%H:%M"),
            "source_start_at": start_msk.isoformat(),
            "source_timezone": "Europe/Moscow", "timezone": "Asia/Almaty",
            "time_normalization": "msk_to_kz",
            "title": title, "raw_title": title, "sport": sport,
            "tournament": "", "is_live": True, "is_live_broadcast": True,
            "is_sport_event": True, "live_state": "live",
            "live_evidence_method": "official_live_text",
            "live_evidence_value": "Official Match TV dated guide: explicit direct broadcast",
        })
    # A source with duplicate template snippets cannot double-publish.
    return list({(x["channel"], x["date"], x["time"], x["title"]): x for x in out}.values())


def parse_search_rss(xml: str) -> list[dict]:
    """Evidence leads only: RSS snippets have no reliable channel+start tuple."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return []
    leads = []
    for item in root.findall(".//item"):
        link = (item.findtext("link") or "").strip()
        parsed = urlsplit(link)
        if parsed.scheme not in {"https", "http"} or not parsed.hostname:
            continue
        title = html.unescape((item.findtext("title") or "").strip())
        snippet = html.unescape((item.findtext("description") or "").strip())
        if not title:
            continue
        leads.append({"url": link, "title": title[:230], "snippet": snippet[:350]})
        if len(leads) == 6:
            break
    return leads


async def _fetch(session: aiohttp.ClientSession, url: str) -> tuple[str, str]:
    async with session.get(url, allow_redirects=True, max_redirects=3) as response:
        response.raise_for_status()
        return await response.text(), str(response.url)


def _save_non_destructive(database, *, run_id: str, source: str,
                          by_day: dict[str, list[dict]]):
    accepted = []
    held = []
    for day, fresh in by_day.items():
        previous = database.load_active_source_snapshot(source, day)
        fingerprint = lambda x: (x.get("channel"), x.get("time"), x.get("title"))
        if {fingerprint(x) for x in previous} - {fingerprint(x) for x in fresh}:
            held.append(day)
            continue
        database.upsert_source_snapshot(
            run_id=run_id, source=source, scope_date=day, events=fresh
        )
        accepted.append(day)
    return accepted, held


async def refresh_open_web_sources(database, *, today: date | None = None,
                                   discover: bool = True) -> dict:
    """Research all 14 channels and ingest only verified official LIVE slots.

    Discovery searches are advisory even if a search result says 'LIVE'.
    Every failure is recorded by channel instead of presenting a global green
    success when nine channels have no actual broadcasts.
    """
    today = today or datetime.now(KZ_TIMEZONE).date()
    run_id = "openweb-" + datetime.now(KZ_TIMEZONE).strftime("%Y%m%d%H%M%S%f")
    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
    headers = {"User-Agent": "SLP schedule research/1.0",
               "Accept-Language": "ru-RU,ru;q=0.9"}
    stats = []
    official_results = defaultdict(list)
    official_errors = []
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        # First-party live guide for KHL / Match Planet, independent of TV+.
        for offset in range(0, 4):
            target = today + timedelta(days=offset)
            try:
                raw, _ = await asyncio.wait_for(
                    _fetch(session, MATCHTV_URL + "?" + urlencode({
                        "date": target.strftime("%d-%m-%Y")
                    })), timeout=REQUEST_TIMEOUT + 1
                )
                rows = parse_matchtv_day(raw, target)
                for row in rows:
                    official_results[row["date"]].append(row)
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                official_errors.append(f"{target}: {type(exc).__name__}")

        by_channel = defaultdict(lambda: defaultdict(list))
        for d, rows in official_results.items():
            for row in rows:
                by_channel[row["channel"]][d].append(row)
        accepted, held = _save_non_destructive(
            database, run_id=run_id, source="web_official_matchtv",
            by_day=official_results,
        )
        # Slow, bounded cross-source discovery. No API keys or paid AI needed.
        semaphore = asyncio.Semaphore(3)
        async def discover_one(channel: str) -> tuple[list[dict], str]:
            if not discover:
                return [], ""
            query = f'"{channel}" программа передач LIVE ' + today.strftime("%d.%m.%Y")
            url = "https://" + SEARCH_HOST + "/search?" + urlencode({
                "q": query, "format": "rss"
            })
            async with semaphore:
                try:
                    raw, _ = await asyncio.wait_for(_fetch(session, url), timeout=REQUEST_TIMEOUT + 1)
                    leads = parse_search_rss(raw)
                    return leads, "" if leads else "search_empty_or_markup_changed"
                except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                    return [], type(exc).__name__

        discovered = await asyncio.gather(*(discover_one(ch.name) for ch in CHANNELS))
    for channel, (leads, search_error) in zip(CHANNELS, discovered):
        events = sum(len(v) for v in by_channel[channel.name].values())
        # These are separate dimensions: web leads != accepted broadcasts.
        status = "ok" if events else "warning"
        reason = "; ".join(filter(None, (
            search_error,
            "; ".join(official_errors) if channel.name in MATCHTV_SECTIONS.values() else "",
            "no_confirmed_direct_broadcasts" if not events else "",
        )))
        source = "web_discovery_" + slug(channel.name)
        database.record_parser_run(
            run_id=run_id, source=source, scope_date=today.isoformat(),
            status=status, event_count=events,
            previous_count=database.latest_successful_count(source, today.isoformat()),
            error=reason or None,
            details={
                "channel": channel.name, "discovered_pages": leads,
                "official_guides": list(OFFICIAL_GUIDES.get(channel.name, ())),
                "external_guides": list(EXTERNAL_GUIDES.get(channel.name, ())),
                "search_error": search_error,
                "confirmed_live_slots": events,
                "note": "Search hits are unverified research, never imported as live.",
                "held_days": held if channel.name in MATCHTV_SECTIONS.values() else [],
                "accepted_days": accepted if channel.name in MATCHTV_SECTIONS.values() else [],
            },
        )
        stats.append({
            "channel": channel.name, "status": status,
            "confirmed_live_slots": events,
            "research_leads": len(leads),
            "error": reason,
        })
    return {"sources": stats,
            "confirmed_official_slots": sum(len(v) for v in official_results.values()),
            "research_leads": sum(x["research_leads"] for x in stats)}

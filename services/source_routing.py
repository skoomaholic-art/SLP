from __future__ import annotations

from dataclasses import dataclass

from services.channel_registry import CHANNELS, CHANNEL_BY_NAME, Channel


@dataclass(frozen=True)
class ChannelRoute:
    channel: str
    primary_transport: str
    fallback_transports: tuple[str, ...]
    confirmation_transports: tuple[str, ...]
    agent_reach_role: str

    @property
    def uses_agent_reach(self) -> bool:
        return bool(self.agent_reach_role)


def _guide_transport(channel: Channel) -> str:
    if channel.guide_backend == "mobikino":
        return "mobikino_api"
    return "tvplus_api"


def _fallbacks(
    channel: Channel,
    *extra: str,
    include_guide: bool = True,
) -> tuple[str, ...]:
    result: list[str] = []
    for value in extra:
        if value and value not in result:
            result.append(value)
    if channel.vsetv_id is not None and "vsetv_live_badge" not in result:
        result.append("vsetv_live_badge")
    guide = _guide_transport(channel)
    if include_guide and channel.tvplus_id and guide not in result:
        result.append(guide)
    return tuple(result)


ROUTES = {
    "SETANTA SPORTS 1": ChannelRoute(
        channel="SETANTA SPORTS 1",
        primary_transport="supplier_xlsx",
        fallback_transports=_fallbacks(CHANNEL_BY_NAME["SETANTA SPORTS 1"]),
        confirmation_transports=("championat_calendar", "official_event_sources"),
        agent_reach_role="championat_reader_and_official_page_extraction_fallback",
    ),
    "SETANTA SPORTS 2": ChannelRoute(
        channel="SETANTA SPORTS 2",
        primary_transport="supplier_xlsx",
        fallback_transports=_fallbacks(CHANNEL_BY_NAME["SETANTA SPORTS 2"]),
        confirmation_transports=("championat_calendar", "official_event_sources"),
        agent_reach_role="championat_reader_and_official_page_extraction_fallback",
    ),
    "SETANTA SPORTS KZ": ChannelRoute(
        channel="SETANTA SPORTS KZ",
        primary_transport="supplier_xlsx",
        fallback_transports=_fallbacks(CHANNEL_BY_NAME["SETANTA SPORTS KZ"]),
        confirmation_transports=("championat_calendar", "official_event_sources"),
        agent_reach_role="championat_reader_and_official_page_extraction_fallback",
    ),
    "QAZSPORT HD": ChannelRoute(
        channel="QAZSPORT HD",
        primary_transport="official_qazsport_html",
        fallback_transports=_fallbacks(
            CHANNEL_BY_NAME["QAZSPORT HD"],
            "agent_reach_official_page",
            "supplier_xlsx",
        ),
        confirmation_transports=("official_live_badge", "championat_calendar"),
        agent_reach_role="official_page_fallback_and_championat_reader",
    ),
    "SPORT+ Qazaqstan": ChannelRoute(
        channel="SPORT+ Qazaqstan",
        primary_transport="official_sportplus_html",
        fallback_transports=_fallbacks(
            CHANNEL_BY_NAME["SPORT+ Qazaqstan"],
            "supplier_xlsx",
        ),
        confirmation_transports=(
            "official_live_text",
            "championat_calendar",
            "official_event_sources",
        ),
        agent_reach_role="championat_reader_and_official_page_extraction_fallback",
    ),
    "Q LEAGUE": ChannelRoute(
        channel="Q LEAGUE",
        primary_transport="supplier_xlsx",
        fallback_transports=_fallbacks(CHANNEL_BY_NAME["Q LEAGUE"]),
        confirmation_transports=("championat_exact_match",),
        agent_reach_role="championat_reader_for_exact_match",
    ),
    "Q FOOTBALL": ChannelRoute(
        channel="Q FOOTBALL",
        primary_transport="supplier_xlsx",
        fallback_transports=_fallbacks(CHANNEL_BY_NAME["Q FOOTBALL"]),
        confirmation_transports=("championat_exact_match",),
        agent_reach_role="championat_reader_for_exact_match",
    ),
    "Q ARENA": ChannelRoute(
        channel="Q ARENA",
        primary_transport="supplier_xlsx",
        fallback_transports=_fallbacks(CHANNEL_BY_NAME["Q ARENA"]),
        confirmation_transports=("championat_exact_match",),
        agent_reach_role="championat_reader_for_exact_match",
    ),
    "EUROSPORT 1": ChannelRoute(
        channel="EUROSPORT 1",
        primary_transport="mobikino_api",
        fallback_transports=_fallbacks(
            CHANNEL_BY_NAME["EUROSPORT 1"],
            include_guide=False,
        ),
        confirmation_transports=("vsetv_live_badge", "championat_calendar", "official_event_sources"),
        agent_reach_role="championat_reader_and_official_page_extraction_fallback",
    ),
    "EUROSPORT 2": ChannelRoute(
        channel="EUROSPORT 2",
        primary_transport="mobikino_api",
        fallback_transports=_fallbacks(
            CHANNEL_BY_NAME["EUROSPORT 2"],
            include_guide=False,
        ),
        confirmation_transports=("vsetv_live_badge", "championat_calendar", "official_event_sources"),
        agent_reach_role="championat_reader_and_official_page_extraction_fallback",
    ),
    "viju+ Sport": ChannelRoute(
        channel="viju+ Sport",
        primary_transport="tvplus_api",
        fallback_transports=_fallbacks(
            CHANNEL_BY_NAME["viju+ Sport"],
            include_guide=False,
        ),
        confirmation_transports=("vsetv_live_badge", "championat_calendar", "official_event_sources"),
        agent_reach_role="championat_reader_and_official_page_extraction_fallback",
    ),
    "KHL HD": ChannelRoute(
        channel="KHL HD",
        primary_transport="tvplus_api",
        fallback_transports=_fallbacks(
            CHANNEL_BY_NAME["KHL HD"],
            include_guide=False,
        ),
        confirmation_transports=("vsetv_live_badge", "championat_calendar", "official_event_sources"),
        agent_reach_role="championat_reader_and_official_page_extraction_fallback",
    ),
    "KHL PRIME": ChannelRoute(
        channel="KHL PRIME",
        primary_transport="tvplus_api",
        fallback_transports=_fallbacks(
            CHANNEL_BY_NAME["KHL PRIME"],
            include_guide=False,
        ),
        confirmation_transports=("vsetv_live_badge", "championat_calendar", "official_event_sources"),
        agent_reach_role="championat_reader_and_official_page_extraction_fallback",
    ),
    "МАТЧ! ПЛАНЕТА": ChannelRoute(
        channel="МАТЧ! ПЛАНЕТА",
        primary_transport="tvplus_api",
        fallback_transports=_fallbacks(
            CHANNEL_BY_NAME["МАТЧ! ПЛАНЕТА"],
            include_guide=False,
        ),
        confirmation_transports=("vsetv_live_badge", "championat_calendar", "official_event_sources"),
        agent_reach_role="championat_reader_and_official_page_extraction_fallback",
    ),
}


def route_for_channel(channel_name: str) -> ChannelRoute:
    return ROUTES[channel_name]


def runtime_source_names(channel: Channel) -> tuple[str, ...]:
    names: list[str] = []
    if channel.name == "QAZSPORT HD":
        names.append("qazsport")
    if channel.name == "SPORT+ Qazaqstan":
        names.append("sportplus")
    if channel.vsetv_id is not None:
        names.append("web_vsetv_" + str(channel.vsetv_id))
    if channel.tvplus_id:
        names.append("tvguide")
    return tuple(names)


def validate_routes() -> None:
    expected = {channel.name for channel in CHANNELS}
    actual = set(ROUTES)
    if expected != actual:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise RuntimeError(
            f"source routing mismatch missing={missing} extra={extra}"
        )


validate_routes()

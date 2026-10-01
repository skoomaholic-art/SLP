"""Single SLP channel inventory. IDs are provider-specific, never interchangeable.

tvplus_id: verified TV+/Mobikino channel page identifier.
guide_backend: provider API used for that channel (tvplus or mobikino).
vsetv_id: previously verified VseTV weekly guide identifier.
A missing ID is not guessed or replaced with a similarly named channel.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Channel:
    name: str
    tvplus_name: str = ""
    tvplus_id: str = ""
    vsetv_name: str = ""
    vsetv_id: int | None = None
    official_site: str = ""
    supplier_source: str = ""
    secondary_guide: str = ""
    guide_backend: str = "tvplus"
    sport_hint: str = ""


CHANNELS = (
    Channel(
        "SETANTA SPORTS 1",
        "Setanta Sports 1",
        "5f9984549e0766c2417d4076",
        "Setanta Sports 1",
        771,
        supplier_source="email_epg_setanta1",
    ),
    Channel(
        "SETANTA SPORTS 2",
        "Setanta Sports 2",
        "5f9984649e0766c2417d4079",
        "Setanta Sports 2",
        984,
        supplier_source="email_epg_setanta2",
    ),
    Channel(
        "SETANTA SPORTS KZ",
        "Setanta Sports KZ",
        "5f9984409e0766c2417d4073",
        supplier_source="email_epg_setantakz",
    ),
    Channel(
        "QAZSPORT HD",
        "Qazsport",
        "5d385e7355153152ba34f5f2",
        official_site="https://qazsporttv.kz/ru/program",
        supplier_source="email_epg_qazsport",
    ),
    Channel(
        "SPORT+ Qazaqstan",
        "Sport+ Qazaqstan",
        "650a658f983c40152a652d79",
        official_site="https://sportplustv.kz/ru/tvguide",
        supplier_source="email_epg_sportplus",
    ),
    Channel(
        "Q LEAGUE",
        "Q League",
        "64507c71071b52869ab93ab3",
        supplier_source="email_epg_qleague",
    ),
    Channel(
        "Q FOOTBALL",
        "Q Football",
        "6642f1b03816a50602c38f66",
        supplier_source="email_epg_qfootball",
        sport_hint="Футбол",
    ),
    Channel(
        "Q ARENA",
        "Q Arena",
        "5f4d1b72387cfb655279442f",
        supplier_source="email_epg_qarena",
    ),
    Channel(
        "EUROSPORT 1",
        "Eurosport",
        "559d211778d72701950089f9",
        "EUROSPORT 1",
        535,
        guide_backend="mobikino",
    ),
    Channel(
        "EUROSPORT 2",
        "Eurosport 2",
        "559d22f678d7270195008a26",
        "EUROSPORT 2",
        1082,
        guide_backend="mobikino",
    ),
    Channel(
        "viju+ Sport",
        "viju+ Sport",
        "5d38606a551531590a663470",
        "viju+ Sport",
        332,
    ),
    Channel(
        "KHL HD",
        "KHL HD",
        "56cf2d9c4e2e67121b9d66ee",
        "KHL HD",
        1641,
        sport_hint="Хоккей",
    ),
    Channel(
        "KHL PRIME",
        "KHL Prime",
        "56cf2e264e2e67121b9d66fb",
        "KHL PRIME",
        806,
        sport_hint="Хоккей",
    ),
    Channel(
        "МАТЧ! ПЛАНЕТА",
        "МАТЧ! Планета",
        "5d385ea755153152ba34f60e",
        "МАТЧ! ПЛАНЕТА",
        32,
    ),
)

_VSETV_IDS = {
    channel.vsetv_name: channel.vsetv_id
    for channel in CHANNELS
    if channel.vsetv_id is not None
}
# Keep legacy import order stable; existing source snapshots and regression
# diagnostics expect the original six before the two new Setanta imports.
VSETV_CHANNELS = {
    name: _VSETV_IDS[name]
    for name in (
        "KHL PRIME",
        "KHL HD",
        "EUROSPORT 1",
        "EUROSPORT 2",
        "МАТЧ! ПЛАНЕТА",
        "viju+ Sport",
        "Setanta Sports 1",
        "Setanta Sports 2",
    )
}
TVPLUS_CHANNELS = {
    channel.tvplus_name: channel.tvplus_id
    for channel in CHANNELS
    if channel.tvplus_id
}
CHANNEL_BY_NAME = {channel.name: channel for channel in CHANNELS}

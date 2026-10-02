"""Canonical SLP channel names across supplier, EPG and public API sources."""
from __future__ import annotations

import re
from difflib import SequenceMatcher

CANONICAL_ALIASES = {
    "QAZSPORT HD": ("qazsport", "qazsport hd", "kazsport", "казспорт"),
    "SPORT+ Qazaqstan": (
        "sport+ qazaqstan", "sport+ kazakhstan", "sport+ kz", "sport plus qazaqstan",
    ),
    "SETANTA SPORTS 1": (
        "setanta sports 1", "setanta 1", "setanta sports 1 kz", "setanta1 kz",
    ),
    "SETANTA SPORTS 2": (
        "setanta sports 2", "setanta 2", "setanta sports 2 kz", "setanta2 kz",
    ),
    "SETANTA SPORTS KZ": (
        "setanta kz", "setanta qazaqstan", "setanta kazakhstan",
        "сетанта казахстан", "setanta sports kz",
    ),
    "Q LEAGUE": ("q league", "q sport league", "q sport extra"),
    "Q FOOTBALL": ("q football",),
    "Q ARENA": ("q arena", "q sport arena", "qsport arena"),
    "EUROSPORT 1": ("eurosport", "eurosport 1", "eurosport1"),
    "EUROSPORT 2": ("eurosport 2", "eurosport2"),
    "viju+ Sport": ("viju+ sport", "viju sport", "viasat sport", "vijuplus sport"),
    "KHL HD": ("khl hd", "кхл hd"),
    "KHL PRIME": ("khl prime", "кхл prime"),
    "МАТЧ! ПЛАНЕТА": ("матч! планета", "матч планета", "match planeta"),
}

_NOISE = re.compile(
    r"\b(?:uhd|fhd|hd|sd|4k|8k|1080p|1080i|720p|50fps|60fps|"
    r"hevc|h\.?265|h\.?264|live|stream|backup|feed)\b",
    re.I,
)


def normalize_channel_name(value: str) -> str:
    text = " ".join(str(value or "").split()).strip()
    text = re.sub(r"^\[[^\]]+\]\s*", "", text)
    text = re.sub(r"^[A-Z]{2,3}\s*[:|/-]\s*", "", text)
    text = _NOISE.sub(" ", text)
    text = re.sub(r"[|:_/]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" -").casefold()
    return text


_NORMALIZED = {
    normalize_channel_name(alias): canonical
    for canonical, aliases in CANONICAL_ALIASES.items()
    for alias in (canonical, *aliases)
}


def canonical_channel_name(value: str) -> str:
    raw = " ".join(str(value or "").split()).strip()
    normalized = normalize_channel_name(raw)
    return _NORMALIZED.get(normalized, raw)


def channel_similarity(first: str, second: str) -> float:
    left = normalize_channel_name(first)
    right = normalize_channel_name(second)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0

    left_canonical = canonical_channel_name(left)
    right_canonical = canonical_channel_name(right)
    if left_canonical == right_canonical and left_canonical != left:
        return 0.99

    lt = set(left.split())
    rt = set(right.split())
    ld = {token for token in lt if token.isdigit()}
    rd = {token for token in rt if token.isdigit()}
    if ld and rd and ld != rd:
        return 0.0
    if ld and not rd:
        return 0.3

    intersection = lt & rt
    union = lt | rt
    coverage = len(intersection) / len(lt) if lt else 0.0
    jaccard = len(intersection) / len(union) if union else 0.0
    sequence = SequenceMatcher(None, left, right).ratio()
    if coverage == 1.0:
        return round(max(0.92, 0.5 * coverage + 0.5 * sequence), 3)
    return round(min(1.0, 0.5 * coverage + 0.2 * jaccard + 0.3 * sequence), 3)

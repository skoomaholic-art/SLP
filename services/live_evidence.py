from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

LIVE_TEXT_MARKERS = (
    "ПРЯМАЯ ТРАНСЛЯЦИЯ",
    "ПРЯМОЙ ЭФИР",
    "ТІКЕЛЕЙ ЭФИР",
    "ТIКЕЛЕЙ ЭФИР",
    "LIVE",
)

REPLAY_TEXT_MARKERS = (
    "ПОВТОР",
    "ЗАПИСЬ",
    "REPLAY",
    "RE-RUN",
    "RERUN",
    "ARCHIVE",
    "АРХИВ",
    "CATCH-UP",
    "REVIEW",
    "ОБЗОР",
    "ШОЛУ",
    "HIGHLIGHTS",
    "ПРЕВЬЮ",
    "PREVIEW",
    "КЛАССИКА",
    "CLASSIC",
)

EDITORIAL_TEXT_MARKERS = REPLAY_TEXT_MARKERS + (
    "СТУДИЯ",
    "СТУДИЙНАЯ ПРОГРАММА",
    "ТОК-ШОУ",
    "ЖУРНАЛ",
    "НОВОСТИ",
    "SPORT REVIEW",
    "АРНАЙЫ РЕПОРТАЖ",
    "СҰХБАТ",
    "ӘНҰРАН",
    "ПРОГРАММА ТУР ПО ТУРУ",
    "КХЛ. ПОДРОБНО",
    "КХЛ. ТРАНСФЕРЫ",
    "ДНЕВНИК",
    "ИТОГИ",
)

SPORT_TEXT_MARKERS = (
    "ФУТБОЛ",
    "ХОККЕЙ",
    "ВОЛЕЙБОЛ",
    "БАСКЕТБОЛ",
    "ТЕННИС",
    "СНУКЕР",
    "БИЛЬЯРД",
    "ДЗЮДО",
    "БОКС",
    "MMA",
    "ММА",
    "UFC",
    "ФОРМУЛА",
    "FORMULA",
    "WRC",
    "WEC",
    "МОТОСПОРТ",
    "АВТОСПОРТ",
    "БОРЬБ",
    "АТЛЕТИК",
    "ГИМНАСТИК",
    "БИАТЛОН",
    "КХЛ",
    "KHL",
    "ГРАН-ПРИ",
    "GRAND PRIX",
    "ЧЕМПИОНАТ",
    "ТУРНИР",
    "КУБОК",
)

EXPLICIT_LIVE_METHODS = {
    "official_live_text",
    "official_live_asset",
    "provider_live_text",
    "provider_live_asset",
    "qazsport_live_text",
    "qazsport_live_asset",
    "qazsport_page_live_text",
    "qazsport_page_live_asset",
    "external_schedule_consensus",
}


@dataclass(frozen=True)
class LiveEvidence:
    state: str
    method: str
    value: str
    confidence: str

    @property
    def is_live(self) -> bool:
        return self.state == "live"


def _upper(value: object) -> str:
    return " ".join(str(value or "").upper().split())


def _event_text(event: dict) -> str:
    return _upper(
        " ".join(
            str(event.get(key) or "")
            for key in ("raw_title", "title", "tournament", "sport")
        )
    )


def event_is_editorial_or_replay(event: dict) -> bool:
    text = _event_text(event)
    return any(marker in text for marker in EDITORIAL_TEXT_MARKERS)


def extract_asset_hints(element) -> tuple[str, ...]:
    if element is None:
        return ()

    hints: list[str] = []
    nodes = [element]
    try:
        nodes.extend(element.find_all(True))
    except Exception:
        pass

    for node in nodes:
        attrs = getattr(node, "attrs", {}) or {}
        for key, value in attrs.items():
            if key not in {"src", "srcset", "href", "style", "class", "alt"} and not str(key).startswith("data-"):
                continue
            if isinstance(value, (list, tuple)):
                hints.extend(str(item) for item in value)
            elif value is not None:
                hints.append(str(value))

    return tuple(dict.fromkeys(hint for hint in hints if hint))


def classify_live_evidence(
    text: object,
    *,
    asset_hints: Iterable[str] = (),
    live_asset_patterns: Iterable[str] = (),
) -> LiveEvidence:
    normalized = _upper(text)

    for marker in REPLAY_TEXT_MARKERS:
        if marker in normalized:
            return LiveEvidence("not_live", "official_replay_text", marker, "high")

    for marker in LIVE_TEXT_MARKERS:
        if marker == "LIVE":
            words = normalized.replace("/", " ").replace("-", " ").split()
            if "LIVE" not in words:
                continue
        elif marker not in normalized:
            continue
        return LiveEvidence("live", "official_live_text", marker, "high")

    hints = tuple(str(value or "") for value in asset_hints)
    for pattern in live_asset_patterns:
        needle = str(pattern or "").strip().casefold()
        if not needle:
            continue
        for hint in hints:
            if needle in hint.casefold():
                return LiveEvidence("live", "official_live_asset", hint, "medium")

    return LiveEvidence("unknown", "none", "", "unknown")


def event_is_live_broadcast(event: dict) -> bool:
    if event_is_editorial_or_replay(event):
        return False

    if "is_live_broadcast" in event:
        return bool(event.get("is_live_broadcast"))

    source = str(event.get("source") or "").casefold()
    if source == "tvguide":
        method = str(event.get("live_evidence_method") or "")
        return bool(
            event.get("is_live")
            and event.get("live_state") == "live"
            and method in EXPLICIT_LIVE_METHODS
        )

    return bool(event.get("is_live", False))


def event_is_official_live(event: dict) -> bool:
    method = str(event.get("live_evidence_method") or "")
    if method in EXPLICIT_LIVE_METHODS:
        return bool(event.get("live_state") == "live") and not event_is_editorial_or_replay(event)
    return event_is_live_broadcast(event) and str(event.get("source") or "") != "tvguide"


def event_is_schedule_candidate(event: dict) -> bool:
    if event_is_editorial_or_replay(event):
        return False

    if "is_sport_event" in event:
        return bool(event.get("is_sport_event"))

    if event_is_live_broadcast(event):
        return True

    text = _event_text(event)
    if str(event.get("sport") or "").strip() or str(event.get("tournament") or "").strip():
        return True

    return any(marker in text for marker in SPORT_TEXT_MARKERS)

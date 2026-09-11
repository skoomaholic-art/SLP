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
)


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


def extract_asset_hints(element) -> tuple[str, ...]:
    """Return asset/class hints scoped to one event DOM subtree."""
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
    """Classify official LIVE evidence for one concrete event card.

    Replay/recording markers always win. Asset evidence is accepted only when
    a source-specific allowlist is supplied by the adapter.
    """
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


def event_is_official_live(event: dict) -> bool:
    """Require explicit adapter evidence; a bare is_live=True is insufficient."""
    return bool(
        event.get("is_live")
        and event.get("live_state") == "live"
        and str(event.get("live_evidence_method") or "") not in {"", "none"}
    )

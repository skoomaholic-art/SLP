from __future__ import annotations

STUDIO_MARKERS = (
    "студийная программа",
    "студиялық бағдарлама",
    "студия",
    "studio",
    "тудия",
    "перед матчем",
    "матч қарсаңында",
    "ток-шоу",
    "футбол плюс",
)


def get_event_title(event: dict) -> str:
    return str(event.get("title") or event.get("raw_title") or "Без названия")


def is_user_event(event: dict) -> bool:
    """Keep actual sports broadcasts and reject studio/talk programming."""
    text = " ".join(
        str(event.get(key) or "")
        for key in ("title", "raw_title", "raw_event_title")
    ).casefold()
    return not any(marker in text for marker in STUDIO_MARKERS)

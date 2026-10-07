from __future__ import annotations

import os
from dataclasses import dataclass


def _read_positive_int(name: str, default: int) -> int:
    raw = str(os.getenv(name, "")).strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as error:
        raise RuntimeError(f"{name} должен быть целым числом") from error
    if value <= 0:
        raise RuntimeError(f"{name} должен быть больше нуля")
    return value


def _read_admin_ids() -> frozenset[int]:
    values: set[int] = set()
    for item in str(os.getenv("ADMIN_IDS", "")).split(","):
        item = item.strip()
        if not item:
            continue
        try:
            values.add(int(item))
        except ValueError as error:
            raise RuntimeError("ADMIN_IDS должен содержать Telegram ID через запятую") from error
    return frozenset(values)


@dataclass(frozen=True, slots=True)
class Settings:
    bot_token: str
    refresh_interval_seconds: int
    admin_ids: frozenset[int]

    def is_admin(self, user_id: int) -> bool:
        # Fail closed: without ADMIN_IDS nobody gets admin commands.
        return int(user_id) in self.admin_ids


def load_settings(*, require_bot_token: bool = True) -> Settings:
    token = str(os.getenv("BOT_TOKEN", "")).strip()
    if require_bot_token and not token:
        raise RuntimeError("BOT_TOKEN не найден в переменных окружения")

    return Settings(
        bot_token=token,
        refresh_interval_seconds=_read_positive_int(
            "SLP_REFRESH_INTERVAL_SECONDS",
            240,
        ),
        admin_ids=_read_admin_ids(),
    )

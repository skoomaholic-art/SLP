from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta

from parsers.qazsport import get_qazsport_schedule
from parsers.sportplus import fetch_sportplus_html, parse_sportplus_html
from parsers.tvplus import TVPlusSchedules, fetch_tvplus_schedules
from services.schedule_merge import merge_source_schedules
from services.time_logic import KZ_TIMEZONE


@dataclass
class SourceSchedules:
    today: list[dict]
    yesterday: list[dict]
    errors: list[str]


async def _safe(label: str, awaitable):
    try:
        return await awaitable, None
    except Exception as error:
        print(f"Ошибка источника {label}:", repr(error))
        return None, label


async def load_source_schedules(now: datetime | None = None) -> SourceSchedules:
    now = now or datetime.now(KZ_TIMEZONE)
    today = now.date()
    yesterday = today - timedelta(days=1)

    (
        q_today_result,
        q_yesterday_result,
        sport_html_result,
        tvplus_result,
    ) = await asyncio.gather(
        _safe("Qazsport сегодня", get_qazsport_schedule(include_current_live=True)),
        _safe("Qazsport вчера", get_qazsport_schedule(yesterday, include_current_live=False)),
        _safe("Sport+ телепрограмма", fetch_sportplus_html()),
        _safe("TV+ спортивные каналы", fetch_tvplus_schedules([today, yesterday])),
    )

    errors = [
        error
        for _, error in (
            q_today_result,
            q_yesterday_result,
            sport_html_result,
            tvplus_result,
        )
        if error
    ]
    q_today = q_today_result[0] or []
    q_yesterday = q_yesterday_result[0] or []
    sport_html = sport_html_result[0]
    tvplus: TVPlusSchedules | None = tvplus_result[0]

    sport_today: list[dict] = []
    sport_yesterday: list[dict] = []
    if sport_html:
        try:
            sport_today = parse_sportplus_html(
                sport_html, target_date=today, reference_date=today
            )
            sport_yesterday = parse_sportplus_html(
                sport_html, target_date=yesterday, reference_date=today
            )
        except Exception as error:
            print("Ошибка источника Sport+ разбор телепрограммы:", repr(error))
            errors.append("Sport+ разбор телепрограммы")

    tv_today: list[dict] = []
    tv_yesterday: list[dict] = []
    if tvplus is not None:
        tv_today = tvplus.by_date.get(today, [])
        tv_yesterday = tvplus.by_date.get(yesterday, [])
        errors.extend(tvplus.errors)

    return SourceSchedules(
        today=merge_source_schedules(q_today, sport_today, tv_today),
        yesterday=merge_source_schedules(q_yesterday, sport_yesterday, tv_yesterday),
        errors=errors,
    )

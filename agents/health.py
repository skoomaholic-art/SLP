from __future__ import annotations

from collections import Counter
from datetime import datetime

from agents.orchestrator import SOURCE_LABELS
from services.channel_registry import CHANNELS
from services.time_logic import KZ_TIMEZONE
from storage.database import SLPDatabase


SOURCE_NAMES = SOURCE_LABELS
CHANNEL_SOURCES = tuple(sorted(
    (
        (
            "Qazsport" if channel.name == "QAZSPORT HD" else
            "Sport+ Qazaqstan" if channel.name == "SPORT+ Qazaqstan" else
            channel.tvplus_name if channel.tvplus_name else "Fight Club"
        ),
        (
            "qazsport" if channel.name == "QAZSPORT HD" else
            "sportplus" if channel.name == "SPORT+ Qazaqstan" else
            "web_fightclub" if channel.name == "FIGHT CLUB" else
            "tvguide"
        )
    )
    for channel in CHANNELS
), key=lambda item: item[0].casefold())


STATUS_ICON = {
    "ok": "🟢",
    "warning": "🟡",
    "blocked": "🔴",
    "error": "🔴",
}


def _active_channel_counts(
    database: SLPDatabase,
    runs: dict[str, dict],
) -> Counter[str]:
    counts: Counter[str] = Counter()
    for source in {source for _, source in CHANNEL_SOURCES}:
        run = runs.get(source) or {}
        scope_date = str(run.get("scope_date") or "")
        if not scope_date:
            continue
        for event in database.load_active_source_snapshot(source, scope_date):
            channel = str(event.get("channel") or "").strip()
            if channel:
                counts[channel] += 1
    return counts


def build_health_text(database: SLPDatabase) -> str:
    now = datetime.now(KZ_TIMEZONE)
    runs = database.latest_source_runs()
    channel_counts = _active_channel_counts(database, runs)
    agent_run = database.latest_agent_run()
    incidents = database.unresolved_incidents(limit=5)

    lines = [
        "🧠 SLP Agent Health",
        f"{now:%d.%m.%Y %H:%M} · Asia/Almaty",
        "",
    ]

    for name, source in CHANNEL_SOURCES:
        run = runs.get(source)
        if not run:
            lines.append(f"⚪ {name} · ещё нет запусков")
            continue

        status = str(run.get("status") or "unknown")
        icon = STATUS_ICON.get(status, "⚪")
        current = channel_counts.get(name, 0)
        scope_date = run.get("scope_date")
        details = run.get("details") or {}
        reason = details.get("reason") or status
        fallback = " · fallback" if details.get("used_fallback") else ""

        lines.extend(
            [
                f"{icon} {name}",
                f"   {scope_date} · {current} событий{fallback}",
                f"   {reason}",
            ]
        )

    lines.append("")
    if agent_run:
        summary = agent_run.get("summary") or {}
        agent_status = str(agent_run.get("status") or "unknown")
        agent_icon = "🟢" if agent_status == "ok" else "🟠"
        lines.extend(
            [
                f"{agent_icon} Orchestrator · {agent_status}",
                f"   событий в выдаче: {summary.get('event_count', 0)}",
                f"   run: {agent_run.get('run_id')}",
            ]
        )
    else:
        lines.append("⚪ Orchestrator · ещё не запускался")

    lines.extend(
        [
            "",
            f"💾 Активных записей в SQLite: {database.active_event_count()}",
            f"🚨 Открытых инцидентов: {len(incidents)}",
        ]
    )

    for incident in incidents[:3]:
        lines.append(
            "• "
            f"{SOURCE_NAMES.get(str(incident.get('source')), incident.get('source'))}: "
            f"{incident.get('incident_type')} · {incident.get('message')}"
        )

    return "\n".join(lines)

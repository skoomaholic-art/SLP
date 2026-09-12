from __future__ import annotations

from datetime import datetime

from services.time_logic import KZ_TIMEZONE
from storage.database import SLPDatabase


SOURCE_NAMES = {
    "qazsport": "Qazsport",
    "sportplus": "Sport+ Qazaqstan",
}

STATUS_ICON = {
    "ok": "🟢",
    "warning": "🟡",
    "blocked": "🔴",
    "error": "🔴",
}


def build_health_text(database: SLPDatabase) -> str:
    now = datetime.now(KZ_TIMEZONE)
    runs = database.latest_source_runs()
    agent_run = database.latest_agent_run()
    incidents = database.unresolved_incidents(limit=5)

    lines = [
        "🧠 SLP Agent Health",
        f"{now:%d.%m.%Y %H:%M} · Asia/Almaty",
        "",
    ]

    for source in ("qazsport", "sportplus"):
        run = runs.get(source)
        name = SOURCE_NAMES[source]
        if not run:
            lines.append(f"⚪ {name} · ещё нет запусков")
            continue

        status = str(run.get("status") or "unknown")
        icon = STATUS_ICON.get(status, "⚪")
        current = run.get("event_count")
        previous = run.get("previous_count")
        scope_date = run.get("scope_date")
        details = run.get("details") or {}
        reason = details.get("reason") or status
        fallback = " · fallback" if details.get("used_fallback") else ""

        comparison = f"{current} событий"
        if previous is not None:
            comparison += f" (было {previous})"

        lines.extend(
            [
                f"{icon} {name}",
                f"   {scope_date} · {comparison}{fallback}",
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

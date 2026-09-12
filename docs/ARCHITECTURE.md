# SLP architecture

This document describes the production path that coding agents must preserve unless a task explicitly requires an architectural change.

## 1. Process entrypoint

`main.py` is the only production entrypoint.

Startup order:

1. configure logging;
2. load settings;
3. open/init `SLPDatabase`;
4. create `RuntimeParserOrchestrator`;
5. create `ScheduleService`;
6. perform one startup refresh;
7. start background scheduler;
8. start aiogram polling.

The startup refresh exists so a brand-new database does not answer Telegram from an empty store before the first scheduled refresh.

## 2. Source layer

Production loader wiring is defined in `agents/runtime_orchestrator.py`.

| source key | production loader | purpose |
| --- | --- | --- |
| `qazsport` | `parsers.qazsport_complete.get_qazsport_schedule_complete` | Qazsport schedule plus completeness/current-LIVE handling |
| `sportplus` | `parsers.sportplus_cached.get_sportplus_schedule_cached` | Sport+ Qazaqstan schedule with cached multi-day source handling |
| `tvguide` | `parsers.tvguide_cached.get_tvguide_schedule` | provider/EPG-derived schedule used by the runtime |

Lower-level parser modules may exist behind these wrappers. Do not bypass the production wrappers in application code just because a lower-level function looks simpler.

## 3. Orchestration / guardrails

`ParserOrchestrator` owns refresh runs and source isolation. Its responsibilities include:

- per-source refresh;
- retry/required-scope behavior;
- event-contract QA;
- source-health anomaly detection;
- incident recording;
- last-good fallback;
- persistence of accepted snapshots.

Files under `agents/` are runtime guardrail components. They are not autonomous coding agents and must not be confused with the repository-level coding-agent harness in `AGENTS.md`.

## 4. Persistence

`storage/database.py` stores:

- active accepted source snapshots in `events`;
- parser run history in `parser_runs`;
- orchestrator run history in `agent_runs`;
- unresolved/resolved incidents in `incidents`.

Each event stores source, source URL, channel, raw/normalized title, sport, tournament, event date/time, normalized start/end datetimes, timezone, direct-live flag, state-at-ingest, timestamps and original payload JSON.

The database is the handoff between refresh logic and user-facing reads. Telegram handlers should not scrape source websites directly.

## 5. Status and evidence semantics

Temporal status is calculated by `services/time_logic.py` and consumed centrally by `ScheduleService`.

```text
upcoming: now < start
live:     start <= now < end
finished: now >= end
```

Direct-broadcast evidence is separate and handled through `services/live_evidence.py` and parser-specific evidence. A currently airing EPG programme is not automatically a direct sports broadcast.

This separation is a core invariant. Do not collapse `is_live_broadcast` and temporal `status` into one flag.

## 6. ScheduleService

`services/schedule_service.py` is the application read boundary.

It:

- reads accepted source snapshots from SQLite;
- merges source schedules;
- removes non-schedule candidates and non-user studio rows;
- computes current temporal state;
- retains yesterday only for possible overnight LIVE events;
- exposes user schedule, LIVE and upcoming views.

When Telegram results are wrong, inspect the pipeline in this order:

```text
raw official source
-> production loader output
-> accepted DB snapshot
-> merged ScheduleService events
-> status/direct-broadcast filters
-> handler formatter/output
```

Do not jump directly to changing a parser.

## 7. Telegram boundary

`bot/handlers.py` owns commands/buttons and receives `schedule_service` from aiogram dependency injection. Formatting belongs in `bot/formatters.py`; keyboards belong in `bot/keyboards.py`; external verification helpers belong in `bot/verification.py`.

User-facing exceptions should not expose tracebacks. Full exceptions belong in logs/diagnostics.

## 8. Scheduler

`scheduler/jobs.py` performs recurring refresh work using the same `ScheduleService` / orchestrator path as startup refresh. Do not create a second refresh implementation.

Default interval is controlled by `SLP_REFRESH_INTERVAL_SECONDS` and defaults to four minutes according to the current project contract.

## 9. Exports / verification / diagnostics

Important existing boundaries include:

- XLSX export service(s) under `services/`;
- `scripts/diagnose_live.py` for real refresh and status inspection;
- `tests/live_source_smoke.py` for current official source smoke;
- `/health`, `/status`, `/refresh`, `/errors` for runtime diagnostics.

Use these before inventing new debugging systems.

## 10. Test hierarchy

Use three layers:

1. **deterministic regression** — unit/integration tests in `tests/test_*.py`;
2. **live source smoke** — real current external pages, catches source markup/network drift;
3. **deployed end-to-end** — actual running Telegram/Railway path and deployed SQLite/configuration.

A change is not production-proven when only layer 1 passes. Parser changes normally require layers 1 and 2. User-facing production defects require all three.

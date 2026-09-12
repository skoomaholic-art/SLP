# SLP project state

Last updated: 2026-09-13 (Asia/Almaty)

This file is the durable handoff between coding-agent sessions. Update it after meaningful verified work. Do not use chat history as the authoritative project state.

## Baseline

Baseline inspected before adding the agent harness:

- branch: `main`
- baseline commit: `b8f094df605e5c43b1d98dbde9fd241f5ac864d9`
- commit message: `fix: complete Qazsport DOM-level LIVE detection`
- GitHub Actions `SLP v2 CI`: success for that commit
- GitHub Actions `SLP live source smoke`: success for that commit
- production entrypoint: `python main.py`
- application timezone: `Asia/Almaty`

The green baseline proves repository compile/import/regression checks and the current source-smoke workflow passed. It does **not** prove the deployed Telegram/Railway instance is healthy or returning the expected events to the user.

## Current production path

```text
main.py
  -> RuntimeParserOrchestrator
       -> qazsport: qazsport_complete
       -> sportplus: sportplus_cached
       -> tvguide: tvguide_cached
  -> ParserOrchestrator
  -> SLPDatabase (SQLite accepted snapshots)
  -> ScheduleService
  -> Telegram handlers / XLSX / notifications
```

## Verified working from repository evidence

- CI compiles the project and imports the production runtime.
- Full `unittest` regression suite is wired into CI.
- A current-source smoke workflow is wired and ran successfully on baseline.
- Qazsport and Sport+ current source parsers are exercised by `tests/live_source_smoke.py`.
- SQLite persists accepted source snapshots plus parser/agent run history and incidents.
- `ScheduleService` calculates temporal status centrally.
- `main.py` performs an initial refresh before Telegram polling.
- Parser/QA rejection keeps last-good snapshots instead of blindly replacing them.
- Data-correctness regression coverage exists for the 2026-09-12 audit cases.

## Not yet proven end-to-end

These are not failures by assumption; they are verification gaps and therefore remain open until measured:

- deployed Telegram bot startup on the actual host;
- deployed host is running the current `main` commit;
- runtime environment has the expected `BOT_TOKEN`, DB path and refresh interval;
- deployed SQLite contains fresh accepted snapshots;
- `/health`, `/refresh`, `/errors`, `/today`, `/live` work against the deployed database;
- user-facing LIVE and upcoming results match current source evidence at the same reference time;
- XLSX exports produced by the deployed bot match the same accepted snapshot/status semantics;
- repeated background refresh remains healthy over multiple cycles in the deployed environment.

## Current P0

**Prove and, if necessary, repair the production end-to-end path from official source -> accepted SQLite snapshot -> ScheduleService -> Telegram response.**

Do not open a speculative parser/refactor P0 until this is measured. The symptom to resolve is user-visible "no results / wrong results" despite green repository checks.

Active execution plan: `docs/exec-plans/active/finish-project.md`.

## Current suspected risk areas to measure, not guess

1. deployment may be stale relative to `main`;
2. runtime configuration or DB path may differ from development assumptions;
3. source refresh may succeed in GitHub Actions but fail from the deployment network;
4. source data may parse but fail direct-broadcast/status filtering before Telegram;
5. background scheduler may be alive while stored snapshots are stale;
6. current docs historically described fewer source loaders than the production runtime actually wires.

None of the above is a confirmed root cause until evidence is recorded.

## Session handoff format

Every agent session that changes behavior should leave this section updated:

- **Verified commit:** `<sha>`
- **Original symptom:** `<exact user-visible or test symptom>`
- **Root cause:** `<specific mechanism, not speculation>`
- **Changed:** `<files / behavior>`
- **Verification:** `<commands + production check>`
- **Still open:** `<next single P0 item>`

Until production verification is completed, status is: **NOT DONE**.

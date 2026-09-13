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

## Latest verified candidate

- **Verified commit:** `c87a79011717951d55eb665f5ea75f4ff4ec711f` on PR #34; `SLP v2 CI` run #105 succeeded.
- **Original symptom:** `/health` collapsed the 14 TV+/Mobikino channels into one provider row instead of reporting all 16 channels separately.
- **Root cause:** `agents/health.py` iterated the three provider entries in `SOURCE_LABELS`; it did not use the canonical channel inventory or count active snapshot rows by channel.
- **Changed:** `/health` now lists every channel separately, inherits the owning provider status, and reports the active SQLite event count for that channel; regression coverage asserts the 16-channel inventory and per-channel counts.
- **Verification:** targeted compile and `python -m unittest tests.test_health -v` passed locally; full `SLP v2 CI` run #105 passed on the candidate commit.
- **Still open:** merge PR #34, release the resulting current `main` SHA through `.github/workflows/production-release.yml`, then verify `/health` in Telegram and continue the three-cycle P0 observation.


## Current presentation fix candidate

- **Candidate commit:** `fafd61b4d02c83bc8694b21f9dda87307d13df24` on PR #35.
- **Original symptom:** event names in the `/check` inline keyboard were still displayed in provider-supplied ALL CAPS even though schedule messages were normalized.
- **Root cause:** `bot.keyboards.event_check_keyboard()` read `title`/`raw_title` directly and bypassed the existing Telegram presentation normalizer.
- **Changed:** `/check` button labels now use `display_title(event)`; regression coverage includes uppercase Italian and Turkish club names.
- **Verification:** isolated compile/call-path test passed; full PR CI and deployed Telegram verification remain pending.
- **Still open:** pass PR CI, merge only on explicit user instruction, release the resulting current `main` SHA through the production gate, then verify `/check` in Telegram.

Until production verification is completed, status is: **NOT DONE**.

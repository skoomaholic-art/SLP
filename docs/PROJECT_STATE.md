# SLP project state

Last updated: 2026-09-13 (Asia/Almaty)

This file is the durable handoff between coding-agent sessions. Update it after meaningful verified work. Do not use chat history as the authoritative project state.

## Repository / release baseline

Current integration state measured during the P0 investigation:

- GitHub branch: `main`
- current main at governance handoff: `963acf964c2df671ff2f4d34dec007ceac617d27`
- last code/test commit before the governance-only commit: `0824b3d3393b908180b74bb0d5b535bdc3b8ac25`
- GitHub Actions `SLP v2 CI` for `0824b3d...`: **success**
- Railway production deployment: `0af98a58-0795-446d-819c-4234c076ecaf`
- Railway production commit: `331d9b2d64149c0349b813e31343bf068f0a6488`
- production entrypoint: `python main.py`
- application timezone: `Asia/Almaty`
- Railway auto-deploy: **disabled**

Important: production is currently behind GitHub `main`. A green main commit is therefore not a deployed result. Do not diagnose the deployed bot as though it were running `0824b3d...`/`963acf9...` until a fresh deployment of current main is performed and its revision is verified.

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

## P0 evidence captured from Railway

### Deployment / runtime

Verified on production:

- service: `slp-bot`;
- start command: `python main.py`;
- SQLite path: `/data/slp.db`;
- refresh interval: `240` seconds;
- `BOT_TOKEN` variable is configured (value was not exposed);
- one production replica is running;
- aiogram starts polling as `@Skoomaholic_Live_Parser_Bot`;
- persistent SQLite/WAL activity is present.

### Source refresh on deployed `331d9b2...`

Startup around `2026-09-13 01:08 Asia/Almaty` showed:

- Sport+ 2026-09-13: 4 fetched / 4 accepted / 4 upcoming;
- Qazsport 2026-09-13: 16 fetched / 16 accepted, 3 provider direct markers at 15:25, 20:00, 22:30;
- TVGuide cache: 2239 EPG rows across 10 dates;
- TVGuide reconciliation: 80 newly checked, 0 confirmed direct at that moment, 500 pending in the 36-hour reconciliation window;
- TVGuide retains recurring partial future-date errors for Setanta Sports 1/2/KZ where provider responses omit `pagesWithEvents`;
- orchestrator startup completed `status=ok`, `source_errors=0`, with warnings rather than a fatal refresh failure.

The outer Railway log severity field frequently labels application INFO/WARNING records as `error`; judge the embedded Python log level/message, not Railway severity alone.

### Data/read boundary

On the previous deployed revision during the same investigation, `ScheduleService` repeatedly returned roughly 1100 current/future rows from accepted SQLite snapshots. This proves that the user-visible symptom cannot be reduced to “the database is empty”.

A direct-live count of zero around 01:00 Asia/Almaty can be legitimate; it is not by itself evidence that polling or parsing is broken.

## Confirmed Telegram responsiveness defect found in code

The Telegram schedule path had an independent, reproducible design defect:

```text
/today or 📅 Расписание
-> schedule_service.get_events()
-> complete 14-day horizon (often >1000 rows)
-> build_schedule_messages(...)
-> _send_messages sends every generated chunk sequentially
```

Production logs contained one handled Telegram update lasting ~25.5 seconds, consistent with this unbounded output path. Absence of later update logs is **not** treated as proof of a polling freeze because there may simply have been no later user updates.

Fix now on GitHub main:

- Telegram schedule output is bounded to the nearest 60 events;
- full 14-day data remains available to SQLite/XLSX;
- the response states total vs shown count;
- schedule and LIVE view boundaries now log output counts/chunk counts;
- regression coverage protects the cap.

Code/test commits:

- `e1e252acca3ca6ab7174f79fcb30630f7e59b27c` — bounded Telegram schedule + logging;
- `0824b3d3393b908180b74bb0d5b535bdc3b8ac25` — regression tests;
- CI for `0824b3d...`: success.

This fix is **not yet deployed** because Railway is still running `331d9b2...`.

## Concurrency / agent finding

A second implementation stream merged `331d9b2...` (`fix: reconcile TV EPG with real event schedules`) while the P0 work was in progress. The merge incorporated the formatter parent cleanly, and subsequent handler/test commits were based on that merge, so current main contains both streams.

However, `main` was unprotected and multiple agents/branches were able to converge within seconds. `AGENTS.md` now requires normal coding-agent work to use a single task branch + PR and one active P0. The dedicated continuation branch is:

`agent/p0-production-e2e`

GitHub issue `#13` is the active production P0 queue.

## Current P0

**Prove and, if necessary, repair the production end-to-end path from official source -> accepted SQLite snapshot -> ScheduleService -> Telegram response.**

### Completed / materially proven

- [x] deployed revision identified;
- [x] start command identified;
- [x] DB path and refresh interval identified;
- [x] required bot token variable presence confirmed without exposing the token;
- [x] source refresh is running on Railway;
- [x] SQLite is populated/active rather than empty;
- [x] production polling startup is visible in logs;
- [x] a concrete Telegram output-flood defect was found, fixed and regression-protected on main;
- [x] CI for the output fix is green.

### Still open

- [ ] deploy current GitHub main rather than the old Railway snapshot;
- [ ] confirm Railway reports the exact deployed current-main SHA;
- [ ] exercise `/start`, `📅 Расписание`, `/live`, `/health`, `/refresh`, `/errors` in the actual Telegram chat after that deployment;
- [ ] confirm bounded schedule logs show `total`, `shown<=60`, and a small chunk count;
- [ ] compare user-visible LIVE against source evidence at the same reference timestamp;
- [ ] observe at least 3 consecutive scheduled refresh cycles on the final deployed revision;
- [ ] finish XLSX end-to-end verification.

## Next single action

Do **not** apply another parser fix first.

The next boundary is release drift: deploy the current verified GitHub main to Railway, record the deployed SHA, then perform Telegram E2E. If the actual chat still fails, use the new handler logs to classify the precise send/view failure before editing code again.

Until production verification is completed, status is: **NOT DONE**.

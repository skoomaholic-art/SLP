# Active execution plan — finish SLP

Owner: repository coding agent

Status: ACTIVE

Rule: work top to bottom. There is exactly one active P0. Do not start P1/P2 refactors while P0 remains unverified.

## P0 — prove the deployed source -> DB -> service -> Telegram path

### Problem statement

Repository CI and live-source smoke can be green while the user still receives no events or incorrect events from the deployed Telegram bot. We need to identify the first boundary where deployed behavior diverges from the repository contract.

### P0.1 Capture the exact deployed revision and runtime configuration

Acceptance evidence:

- [ ] identify the deployed Git commit/revision;
- [ ] confirm the deployed command/entrypoint is `python main.py`;
- [ ] confirm Python runtime is compatible with project requirements;
- [ ] confirm `SLP_REFRESH_INTERVAL_SECONDS` effective value;
- [ ] confirm effective SQLite path without exposing credentials;
- [ ] confirm required `BOT_TOKEN` exists without printing it;
- [ ] record findings in `docs/PROJECT_STATE.md`.

If the deployed revision is not current `main`, fix deployment/redeploy before changing parser logic.

### P0.2 Prove source refresh on the deployment host

Run the existing diagnostics from the deployed environment or an environment with the same network/runtime characteristics:

```bash
PYTHONPATH=. python tests/live_source_smoke.py
PYTHONPATH=. python scripts/diagnose_live.py
```

Acceptance evidence:

- [ ] Qazsport current grid is non-empty;
- [ ] Sport+ current published date is discoverable;
- [ ] tvguide loader is exercised through production orchestration/diagnostic path;
- [ ] source/network exceptions are captured, not hidden;
- [ ] parsed event counts are recorded;
- [ ] direct-LIVE-now and upcoming counts are recorded at a timestamp in `Asia/Almaty`.

If this step fails, fix only the failing source/network/runtime boundary and add a regression/smoke assertion before moving on.

### P0.3 Prove accepted SQLite snapshots

Using `/health`, `/status`, database diagnostics, or a minimal read-only inspection:

- [ ] active accepted events > 0 when sources have published schedules;
- [ ] latest parser runs are current;
- [ ] source/scope rows show expected source identity;
- [ ] start/end are normalized and timezone is `Asia/Almaty`;
- [ ] rejected runs do not deactivate healthy last-good snapshots;
- [ ] unresolved incidents explain any rejected source/scope.

If parser output is healthy but DB snapshots are empty/stale, fix persistence/orchestrator logic before touching Telegram handlers.

### P0.4 Prove ScheduleService output from the same deployed DB

At one recorded reference timestamp:

- [ ] `get_events()` returns the accepted current/future sports set;
- [ ] `get_live_events()` contains only temporally LIVE + direct-broadcast events;
- [ ] `get_upcoming_events()` contains future events in chronological order;
- [ ] yesterday is present only when needed for an overnight LIVE event;
- [ ] same event on separate channels is preserved;
- [ ] no replay/review/preview/archive false positive is promoted to direct LIVE.

If DB is correct but service output is wrong, fix status/evidence/merge filtering and add a deterministic regression for the exact event that demonstrated the failure.

### P0.5 Prove Telegram user-visible output

From the actual deployed bot:

- [ ] `/start` responds and keyboard/menu is usable;
- [ ] `/health` or `/status` reports the same source/DB state measured above;
- [ ] `/today` matches `ScheduleService.get_events()` for the same reference window;
- [ ] `/live` matches `ScheduleService.get_live_events()`;
- [ ] `/refresh` triggers refresh for an authorized admin when configured;
- [ ] `/errors` surfaces unresolved incidents rather than hiding them;
- [ ] empty results are semantically correct, not caused by stale/empty DB or swallowed source errors.

### P0.6 Stability gate

- [ ] observe at least 3 consecutive scheduled refresh cycles;
- [ ] no healthy source is erased after a temporary failure;
- [ ] no scheduler exception kills the polling process;
- [ ] source counts/timestamps continue advancing;
- [ ] Telegram remains responsive after refresh cycles.

### P0 DONE gate

P0 is DONE only after all applicable items P0.1–P0.6 are checked with evidence in `PROJECT_STATE.md`.

Before marking done, run:

```bash
python -m compileall -q .
python -m unittest discover -s tests -p 'test_*.py' -v
PYTHONPATH=. python tests/live_source_smoke.py
PYTHONPATH=. python scripts/diagnose_live.py
```

Then verify the actual deployed Telegram commands once more.

---

## P1 — reconcile source truth and documentation

Start only after P0 is production-proven.

- [ ] document exactly which channels/providers `tvguide` contributes and why it is part of production;
- [ ] reconcile README supported-source/channel wording with `RuntimeParserOrchestrator`;
- [ ] remove genuinely dead parser paths only if repository search and tests prove they are unused;
- [ ] do not collapse wrapper parsers into lower-level parsers merely for aesthetics;
- [ ] ensure each production loader has deterministic regression coverage plus live smoke where feasible.

P1 acceptance: a new coding agent can identify the production source path without reading historical chats or guessing which similarly named parser file is current.

---

## P2 — deployment observability

- [ ] health output identifies deployed revision if deployment platform exposes it safely;
- [ ] health output makes snapshot freshness obvious;
- [ ] stale-data condition has an explicit warning threshold;
- [ ] logs include refresh run id, source/scope, counts and reference time without secrets;
- [ ] deployment restart preserves intended last-good DB behavior when persistent storage is configured.

P2 acceptance: when the user says "there are no results", one health response is enough to classify the failure as deployment, source, persistence, filter, or Telegram presentation.

---

## P3 — export correctness closure

- [ ] reproduce both XLSX export modes from one reference timestamp;
- [ ] compare row sets, status and direct-broadcast fields;
- [ ] verify timezone is explicit;
- [ ] ensure LIVE and finish/upcoming filters cannot silently become identical;
- [ ] retain regression cases from the 2026-09-12 audit.

---

## P4 — finish / freeze

When P0–P3 are complete:

- [ ] move this file to `docs/exec-plans/completed/` with final evidence;
- [ ] leave no open P0/P1 entries in `docs/known-issues/bugs.md`;
- [ ] update README to match the verified production architecture;
- [ ] update `docs/PROJECT_STATE.md` with final verified commit and deployment revision;
- [ ] run full regression + live smoke + deployed Telegram check;
- [ ] declare the project stable only after all gates pass.

Do not create a new "v3" or replacement project to satisfy this plan. Finish the existing SLP repository.

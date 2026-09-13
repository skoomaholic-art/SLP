# SLP — coding-agent operating contract

This file is the entrypoint for any coding agent working on SLP.

The repository, not chat history, is the source of truth. Start every session by reading, in order:

1. `docs/PROJECT_STATE.md`
2. `docs/exec-plans/active/finish-project.md`
3. `docs/ARCHITECTURE.md`
4. `docs/PRODUCT_SPEC.md`
5. `docs/known-issues/bugs.md`
6. `docs/RELEASE_PROCESS.md`
7. `docs/CODEX_PLAYBOOK.md` when the coding agent is Codex
8. this file again before making broad architectural changes

## Mission

Finish SLP as a reliable production Telegram sports schedule parser. Do not optimize for the number of fixes or commits. Optimize for observable user results and reproducible verification.

The production entrypoint is exactly:

```bash
python main.py
```

Do not create a second production entrypoint, a parallel bot, a replacement parser stack, or monkey-patch the running Telegram application.

## Codex + repository toolkit

Codex is the preferred coding agent for scoped implementation/review tasks. `tools/claude-code` is a pinned repository toolkit, not a running Claude service. Codex may read the relevant prompts/checklists there as supplemental playbooks, following `docs/CODEX_PLAYBOOK.md`, but Claude-specific hooks/slash commands are not assumed to execute under Codex.

Use only the smallest relevant toolkit section for the current task. SLP's own `AGENTS.md`, architecture, product contract, active plan and release rules always take precedence.

## Git workflow — one writer, one P0

`main` is a release/integration branch, not an agent scratchpad.

For normal coding-agent work:

1. read GitHub issue `#13` and the active execution plan;
2. record the current `main` SHA before editing;
3. create/use one task branch named like `agent/p0-production-e2e` or `fix/<single-problem>`;
4. keep all changes for that task on that branch;
5. before opening/updating the PR, check whether `main` moved;
6. if `main` moved, integrate the new head and rerun the full applicable verification ladder;
7. open/update a PR only after local checks pass;
8. do not merge your own PR unless the user explicitly instructs you to merge that exact PR;
9. do not let two coding agents implement the same P0 in separate branches at the same time.

Coding agents must never push directly to `main`. Repository bootstrap/governance changes still go through a branch and PR.

Railway production is a separate release boundary. A green commit on `main` is not proof that Railway runs that commit. Always compare deployed revision with GitHub `main` before diagnosing production behavior.

## Hard production release boundary

A coding agent is not the production release operator.

The only supported production release path is `.github/workflows/production-release.yml`, documented in `docs/RELEASE_PROCESS.md`.

Coding agents must not:

- call Railway deploy or redeploy actions;
- run `railway up` or `railway redeploy` against production;
- enable Railway auto-deploy;
- deploy a branch, local workspace, or stale SHA;
- bypass a failed GitHub release gate with a manual Railway deployment.

If the release workflow is unavailable, missing `RAILWAY_TOKEN`, or fails verification, report `NOT RELEASED` with the exact blocker. Do not improvise another production path.

## Production wiring

`main.py` creates `RuntimeParserOrchestrator`, which currently wires these loaders:

- `qazsport` -> `parsers.qazsport_complete.get_qazsport_schedule_complete`
- `sportplus` -> `parsers.sportplus_cached.get_sportplus_schedule_cached`
- `tvguide` -> `parsers.tvguide_cached.get_tvguide_schedule`

Then:

```text
source loaders
    -> RuntimeParserOrchestrator / ParserOrchestrator
    -> deterministic QA + source health + last-good fallback
    -> SQLite accepted snapshots
    -> ScheduleService
    -> Telegram handlers / XLSX / notifications / diagnostics
```

Treat that path as authoritative until a task explicitly requires changing it.

## Non-negotiable domain rules

- Application timezone is `Asia/Almaty` (UTC+5).
- Temporal state and broadcast evidence are different concepts:
  - `upcoming`: now < start
  - `live`: start <= now < end
  - `finished`: now >= end
  - direct/LIVE-broadcast evidence is evaluated separately.
- An EPG row existing does not by itself prove a direct broadcast.
- Replay/review/preview/archive programming must not be promoted to direct LIVE.
- A rejected fresh source snapshot must not replace a healthy last-good snapshot.
- Same event on different TV channels is not a duplicate.
- Never invent schedule data to make tests pass.
- Never commit secrets, `.env`, Telegram tokens, cookies, sessions, or credentials.

## Anti-loop rules

These rules exist specifically to prevent endless "fix the fix" development.

1. **One active P0 at a time.** Work from `docs/exec-plans/active/finish-project.md` in order.
2. **Reproduce before changing code.** Record the failing command, failing test, current output, source evidence, or production symptom. If reproduction is impossible, document why before editing.
3. **No speculative refactor while P0 is open.** Do not rename/move/rewrite unrelated modules because they look untidy.
4. **No duplicate implementation.** Search the repository before adding a parser, status calculator, scheduler, DB layer, verification service, exporter, or agent.
5. **A green unit test is not production proof.** User-facing defects require an end-to-end check at the appropriate boundary.
6. **Do not weaken tests to fit the implementation.** Change an expectation only when the product contract or verified source behavior changed; document the evidence.
7. **Stop after two failed hypotheses.** Update `PROJECT_STATE.md` with evidence gathered and re-evaluate the root cause instead of applying a third blind patch.
8. **Keep scope narrow.** Every code change must map to an acceptance item in the active plan or a clearly documented newly discovered blocker.
9. **Update durable state.** At the end of meaningful work, update `PROJECT_STATE.md`, the active plan, and `bugs.md` if status changed.

## Required verification ladder

Use the smallest relevant checks while iterating, but a P0 cannot be closed until all applicable checks pass:

```bash
python -m compileall -q .
python -m unittest discover -s tests -p 'test_*.py' -v
PYTHONPATH=. python tests/live_source_smoke.py
PYTHONPATH=. python scripts/diagnose_live.py
```

For a Telegram/Railway/user-visible bug, also verify the deployed/user-facing path. Repository CI alone is insufficient.

## Definition of DONE

A task is DONE only when all of these are true:

- the original symptom is reproducible or the original evidence is preserved;
- the root cause is identified in concrete terms;
- the smallest reasonable fix is implemented;
- relevant regression coverage exists;
- full regression passes;
- live-source smoke passes when parser behavior changed;
- the actual user-facing behavior is verified when the task is user-facing;
- no secrets or runtime DB/log artifacts were committed;
- `docs/PROJECT_STATE.md` reflects the new verified state;
- the active execution plan item is checked off with evidence.

If any required item is missing, write `NOT DONE` and the exact blocker. Do not claim success.

## Diagnostics already available

- `/health` or `/status` — source runs, orchestrator state, SQLite counts and incidents
- `/refresh` — manual parser refresh
- `/errors` — unresolved parser/QA incidents
- `PYTHONPATH=. python scripts/diagnose_live.py` — real source refresh plus LIVE/upcoming diagnostic output
- `PYTHONPATH=. python tests/live_source_smoke.py` — current official source parsing smoke

Use these before adding new diagnostics.

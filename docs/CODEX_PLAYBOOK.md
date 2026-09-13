# Codex playbook for SLP

This file tells Codex how to turn the repository's existing engineering rules and the `tools/claude-code` submodule into useful work. `AGENTS.md` remains authoritative when anything conflicts.

## Purpose

Use Codex as the primary coding agent for scoped SLP engineering tasks. The `tools/claude-code` repository is supplemental source material: read its prompts, review checklists and iterative-development techniques, but do not assume Claude-specific hooks or slash commands execute under Codex.

## Native ChatGPT/Codex plugin import

OpenAI's plugin marketplace importer supports both native Codex marketplace manifests and Claude-compatible manifests. The connected toolkit repository already contains `.claude-plugin/marketplace.json`, so an eligible ChatGPT/Codex workspace can import `https://github.com/skoomaholic-art/claude-code` directly as a marketplace without installing Claude Code.

When that marketplace is available in the workspace, prefer installing only the useful workflow plugins for the current engineering job (for example feature development, code review, PR review, or security guidance) rather than enabling everything blindly. Workspace/plugin availability is an account-level capability; repository code alone cannot enable it.

Even when marketplace import is unavailable, the same files remain available through the pinned `tools/claude-code` submodule and can be used as read-only playbooks by Codex.

## Task routing

Before editing, classify the task and read only the relevant toolkit material:

| Task | Supplemental material |
| --- | --- |
| New feature / architectural extension | `tools/claude-code/plugins/feature-dev/commands/feature-dev.md` |
| Bug with deterministic tests / clear completion criteria | `tools/claude-code/plugins/ralph-wiggum/README.md` for the iterative method only |
| PR or broad code review | `tools/claude-code/plugins/code-review/README.md` and `tools/claude-code/plugins/pr-review-toolkit/` |
| Security-sensitive change | `tools/claude-code/plugins/security-guidance/README.md` |
| Git/PR hygiene | `tools/claude-code/plugins/commit-commands/` as reference only |

Do not load the whole toolkit into context for every task. Read the smallest relevant playbook, then return to SLP's own architecture/product docs.

## Iterative bug-fix loop

For a well-scoped bug, adapt the Ralph technique without relying on Claude hooks:

1. preserve the original failing evidence;
2. define an explicit success condition;
3. make the smallest plausible change;
4. run the narrowest deterministic test;
5. inspect the failure rather than guessing;
6. repeat only while each iteration produces new evidence;
7. after two failed hypotheses, obey `AGENTS.md`: stop blind patching, document evidence, and reassess the root cause;
8. once narrow checks pass, run the full applicable SLP verification ladder.

Never implement an unlimited self-loop. For Codex tasks, use a practical cap of 6 implementation/debug iterations before reporting the blocker and evidence gathered.

## What Codex should do autonomously

Within one assigned issue/task, Codex should normally:

- inspect the current `main` and relevant durable docs;
- reproduce the problem when possible;
- search for existing implementations before adding new ones;
- edit only the task-relevant files;
- add/update regression coverage;
- run compile/tests and live-source smoke when parser behavior changes;
- inspect CI failures and repair its branch when the root cause is within scope;
- prepare a clear PR summary with exact verification evidence;
- update durable project state when behavior/status materially changes.

## What Codex must not do autonomously

- push directly to `main`;
- merge its own PR unless the user explicitly requests that exact merge;
- release or redeploy Railway production;
- expose or search for secrets/tokens;
- treat EPG presence as proof of direct LIVE;
- weaken tests to make a change pass;
- create a replacement parser stack or second production entrypoint;
- run two agents that both edit the same active P0.

## SLP completion evidence

A useful Codex result is not "code written". It must report:

- original symptom/evidence;
- root cause;
- files changed;
- tests/commands executed and their result;
- live-source evidence when relevant;
- remaining blocker(s), if any;
- branch/PR containing the work.

For production/user-visible tasks, repository success is still not proof of release. The production boundary remains `docs/RELEASE_PROCESS.md`.
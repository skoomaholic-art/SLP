# SLP coding-agent instructions

Before editing code, read `AGENTS.md` and follow it as the repository operating contract.

Then read, in order:

1. `docs/PROJECT_STATE.md`
2. `docs/exec-plans/active/finish-project.md`
3. `docs/ARCHITECTURE.md`
4. `docs/PRODUCT_SPEC.md`
5. `docs/known-issues/bugs.md`

Work only on the first unresolved P0 item unless the user explicitly changes priority.

Do not create a replacement project, second bot entrypoint, parallel parser stack, duplicate scheduler, duplicate database layer, or speculative refactor.

For user-visible defects, do not claim success from unit tests alone. Follow the verification ladder in `AGENTS.md` and update durable project state before ending the task.

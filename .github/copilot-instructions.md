# SLP coding-agent instructions

Before editing code, read `AGENTS.md` and follow it as the repository operating contract.

Then read, in order:

1. `docs/PROJECT_STATE.md`
2. `docs/exec-plans/active/finish-project.md`
3. `docs/ARCHITECTURE.md`
4. `docs/PRODUCT_SPEC.md`
5. `docs/known-issues/bugs.md`
6. `docs/RELEASE_PROCESS.md`

Work only on the first unresolved P0 item unless the user explicitly changes priority.

Do not create a replacement project, second bot entrypoint, parallel parser stack, duplicate scheduler, duplicate database layer, or speculative refactor.

## Hard release boundary

You are a coding agent, not the production release operator.

- Never push directly to `main`.
- Work on a task branch and open/update a PR.
- Do not merge your own PR unless the user explicitly instructs you to merge that exact PR.
- Never call Railway deploy/redeploy actions, Railway CLI deploy commands, or enable Railway auto-deploy during ordinary coding work.
- Production releases are allowed only through `.github/workflows/production-release.yml` after the change is already on `main`.
- If that release workflow is unavailable or fails, report the blocker. Do not bypass it with a direct Railway deployment.

For user-visible defects, do not claim success from unit tests alone. Follow the verification ladder in `AGENTS.md` and update durable project state before ending the task.

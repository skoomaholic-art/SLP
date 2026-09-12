# Claude Code operating contract for SLP

You are a coding agent working inside the SLP repository. The repository is the source of truth.

Before changing code, read in this order:

1. `AGENTS.md`
2. `docs/PROJECT_STATE.md`
3. `docs/exec-plans/active/finish-project.md`
4. `docs/ARCHITECTURE.md`
5. `docs/PRODUCT_SPEC.md`
6. `docs/known-issues/bugs.md`
7. `docs/RELEASE_PROCESS.md`

## Working rules

- Work on exactly the issue/task you were assigned. Do not start unrelated refactors.
- Reproduce the defect before editing when possible.
- Keep raw provider data separate from presentation formatting.
- EPG presence is never proof of a direct LIVE broadcast.
- Preserve `Asia/Almaty` as the application timezone.
- Never commit or print tokens, secrets, cookies, `.env` contents, production DBs or credentials.
- Never deploy or redeploy Railway production. Production release is controlled only by `.github/workflows/production-release.yml`.
- Never push directly to `main` and never merge your own PR.
- If `main` moves while you work, integrate the new head and rerun verification.

## Verification

For code changes run the applicable checks, and for broad changes run all of them:

```bash
python -m compileall -q .
python -m unittest discover -s tests -p 'test_*.py' -v
PYTHONPATH=. python tests/live_source_smoke.py
PYTHONPATH=. python scripts/diagnose_live.py
```

A task is not complete because a unit test passed. Provide the exact evidence you collected, the tests you ran, remaining blockers, and the PR/branch containing the work.

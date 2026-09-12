# SLP production release process

Production is a separate boundary from coding and merging.

## Allowed path

```text
coding agent
  -> task branch
  -> pull request
  -> SLP v2 CI
  -> merge to main
  -> manual GitHub Actions workflow: SLP production release gate
  -> full regression + live-source smoke on one exact main SHA
  -> Railway production
```

There is exactly one supported production release path: `.github/workflows/production-release.yml`.

## Forbidden paths

Coding agents must not:

- push directly to `main`;
- merge their own PR unless the user explicitly instructs them to merge that exact PR;
- call Railway deploy/redeploy APIs or the Railway UI as part of ordinary coding work;
- enable Railway auto-deploy;
- deploy a branch, dirty workspace, or SHA that is not the current `main` head;
- bypass a failed release gate by using `railway redeploy` or a manual Railway deploy.

`railway redeploy` is not a release mechanism: it rebuilds/restarts an existing deployment snapshot and does not prove that the newest GitHub `main` is being deployed.

## Release gate guarantees

The manual workflow:

1. runs only from `main` and requires the confirmation text `DEPLOY`;
2. records the workflow SHA;
3. verifies that the workflow SHA still equals the current remote `main`;
4. installs dependencies;
5. compiles the project;
6. runs the full unit/regression suite;
7. runs the real live-source smoke;
8. re-checks that `main` did not move while verification was running;
9. serializes releases with GitHub Actions `concurrency`, so two production releases cannot run at the same time;
10. uploads that exact checked-out workspace to the existing Railway `slp-bot` service.

If `main` moves at either SHA check, the workflow fails and must be restarted from the new head.

## One-time GitHub/Railway setup

The workflow intentionally expects a project-scoped Railway token in the GitHub Actions secret `RAILWAY_TOKEN`.

Create a Railway project token scoped to the SLP production environment and save it in the GitHub repository as Actions secret `RAILWAY_TOKEN`. Do not commit the token to this repository.

After that token is stored, ordinary coding agents do not need Railway write access at all.

Recommended GitHub repository protection for `main`:

- require a pull request before merging;
- require the `SLP v2 CI / test` status check;
- require branches to be up to date before merging;
- block force pushes;
- block branch deletion;
- do not allow bypass for automated coding agents.

Recommended GitHub `production` environment protection:

- restrict deployment to `main`;
- add a required reviewer if the account/plan supports it.

## Railway settings

For `slp-bot`:

- GitHub auto-deploy remains OFF;
- production start command remains `python main.py`;
- deployment from coding agents is forbidden;
- a production release is initiated only by the GitHub Actions release gate.

## Definition of released

A commit is released only when all of these are true:

- the release workflow succeeded;
- the released SHA is known;
- Railway reports the deployment successful;
- startup refresh completed without a fatal error;
- Telegram polling started;
- user-facing Telegram E2E is exercised for changes that affect bot behavior.

A green PR or green `main` CI by itself is not a production release.

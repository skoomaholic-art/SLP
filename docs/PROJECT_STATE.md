# SLP project state

Last updated: 2026-09-29 (Asia/Almaty)

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
- **Verification:** isolated compile/call-path test passed; full `SLP v2 CI` run #111 passed (compile, production imports and complete regression suite). Deployed Telegram verification remains pending.
- **Still open:** pass PR CI, merge only on explicit user instruction, release the resulting current `main` SHA through the production gate, then verify `/check` in Telegram.

Until production verification is completed, status is: **NOT DONE**.

## Cloud Run web candidate (2026-09-29, isolated feature branch)

- Branch: feature/cloud-run-sports-web, PR #39 (open for review, NOT MERGED).
- Adds Dockerfile-based HTTP entrypoint cloudrun_web:app; existing
  python main.py Telegram/Railway production entrypoint is unchanged.
- Reuses the parser/orchestrator and source snapshot store, with separate
  browser login, calendar, cross-channel archive list and template export.
- Web sign-in requires external user-hash and session secrets. Private
  GCS snapshot storage and approved XLSX template require separate setup.
- Manual supplier XLSX import added for Setanta Sports 1/2/KZ and Q
  League/Arena/Football, with SHA-idempotent import and per-day freshness.
- Fourteen distinct user-provided logos embedded and served by web UI;
  archive mode can show inactive source snapshots and export per-channel XLSX.
  This is NOT a complete field-level change journal.
- 25-column template export preserves pre-existing editorial cells, approved
  RU/KZ and IDs; unknown KZ remains blank for review. Approved template must
  be configured; no production claims for final editorial correctness.
- Owner Gmail OAuth code, encrypted refresh-token storage and a bounded
  read-only Gmail inbox scan exist in the feature branch. Incoming supplier
  attachments are staged for manual confirmation; text-only change messages
  are review-only, never treated as confirmed cancellations. Real Gmail
  OAuth has NOT been configured and no message has been sent.
- Fixed, templated outgoing QSport/Setanta test requests are implemented,
  but disabled by default. Only the owner's fixed work address is an allowed
  test recipient; Anton's address is not an active recipient.
- Source-revision events (added/changed/removed from current EPG) are
  append-only; editor fields/attribution are stored separately. This is
  source/audit history, not a guarantee that every supplier reschedule is
  matched to a single stable event identity.
- Six distinct VseTV web source IDs are wired to the web collector with
  LIVE evidence and MSK -> UTC+5 conversion. Their live site coverage still
  needs a real deployment check; bot parsers are not changed.
- The admin can upload an approved 25-column workbook to configured GCS,
  but the workbook has not been uploaded and translations/TEAM mapping
  still need owner editorial validation. No Cloud Run deployment occurred.
- GitHub CI must be checked for the latest candidate before merging. Production
  Railway release is independent and must NOT be triggered by web work.
  Cloud Run setup/secrets/OAuth instructions: docs/CLOUD_RUN_WEB.md.


## 2026-09-29 SLP web continuation (PR #39)

**In code, not deployed or merged:**

- Replaced the user-provided red puppet icon with a white-on-black image
  without changing its composition; favicon and web header use that image.
  Removed the duplicate PRIME/HD overlay badges. The actual KHL PRIME and
  KHL HD source logos remain separate, as do Setanta 1/2/KZ and Eurosport 1/2.
  The source panel displays exactly 14 TV channels; TVGuide remains an
  internal parser, not a fictitious 15th channel.
- Gmail scan now uses deterministic classification first. It stores
  SCHEDULE_NEW / SCHEDULE_UPDATE / SCHEDULE_CORRECTION /
  SCHEDULE_CANCELLATION / SCHEDULE_RESPONSE / OTHER / AMBIGUOUS metadata.
  The optional AI_MAIL adapter is called only for unresolved messages;
  no external AI is contacted or configured by default.
- Confirmed supplier XLSX is imported without individual approval only
  when OAuth, GCS backup and SPORT_GMAIL_AUTO_IMPORT are enabled.
  Old overlapping files, unclear channels, empty LIVE updates for a
  previously populated day and ambiguous repeated fixtures are withheld
  for review. No text-only cancellation is applied as a confirmed fact.
- Explicitly identified multi-channel Excel sheets are split into
  independent Q or Setanta source snapshots. An unlabelled sheet in a
  multi-station workbook is NOT guessed.
- AI_EDITOR has an optional local adapter for missing translation text,
  with an additional explicit opt-in for KZ auto-fill. It cannot change
  channel, LIVE or times. No local inference service is provisioned,
  so any missing unapproved KZ strings still remain blank.
- Export now derives its priority scale from the approved workbook rather
  than assuming 60000, 59990, 59980. The owner's archived example uses
  50000, 49900, 49800. Existing rows and editorial overrides are retained.
  A newer empty LIVE day cannot wipe an accepted earlier populated day.
- Offline regressions cover real supplier shapes, auto-import,
  multi-sheet workbooks, 14-channel inventory, no extra logo badges,
  local-only AI boundaries and preservation of historical data.

**Not complete:** Owner OAuth consent in the actual Google project, service
secrets and account provisioning, loading the approved XLSX, setting up
persistent GCS, authenticated 24/7 Cloud Scheduler, site deployment and
verified live broadcasts on all 14 sources. Sport+ / Qazsport incoming XLSX
require real format samples before claiming automatic parsing. No paid
cloud resource was created. Existing Telegram production is unchanged.

## 2026-09-29 continuation: legacy Excel and source coverage

- Branch `feature/cloud-run-sports-web`, PR #39; latest commit must be checked
  directly before release. This is a code-only candidate, not deployed.
- Added bounded legacy `.xls` to `.xlsx` in-memory parsing for known supplier
  layouts. Original raw bytes remain the import identity so repeat scans are
  idempotent. Gmail and rules-first mail classification now recognize `.xls`.
- Source availability combines accepted day coverage from all supplier files,
  rather than only the newest file. Tests cover source identity and the
  multi-file coverage regression.
- Unsupported supplier layouts (including not-yet-proven viju+ Sport `.xls`
  and generic QAZSPORT/SPORT+ Excel) remain unverified; do not claim
  automatic ingest until real samples are checked.
- Cloud Run deployment, GCS persistence, Gmail OAuth, Scheduler, user/secret
  provisioning and real-source correctness are still open.

## 2026-09-29 continuation: Gmail automation hardening

- Gmail classification now records provider, separate known channels, explicit
  date periods, confidence and evidence. Rules remain first; the optional
  AI_MAIL adapter is used only when rules cannot classify the message.
- Inbox synchronization stores a bounded internal-date checkpoint with overlap.
  Message and attachment failures are isolated, retried up to three times and
  quarantined after the limit. A failed message cannot block other imports or
  advance the database by silently pretending it succeeded.
- Supported multi-channel Excel files remain split into independent notices.
  Approved sender plus structural workbook fingerprints can safely resolve
  later generic filenames. Contradictory workbook identities stay in review.
- Request records now include requested/received channels, period, thread,
  answer state and delivery mode. TEST MODE remains fixed to the owner's
  approved mailbox. PRODUCTION MODE is code-ready but BLOCKED until confirmed
  supplier recipients are configured. Duplicate pending requests are rejected.
- Local verification: python -m unittest discover -s cloudrun_tests -p
  test_*.py -v passed 56 tests. This is repository verification only.
- Still BLOCKED: actual owner OAuth, real Gmail messages, approved Freedom
  Media template, GCS, Scheduler/OIDC provisioning, supplier recipient
  configuration, live Cloud Run deployment and deployed source checks.

## 2026-09-30 web configuration without choosing a host

- Render Free hosts an isolated preview of PR #39 at https://slp-web.onrender.com.
  It has working named-user sign-in, but its local SQLite disk is ephemeral,
  and auto-deploy is disabled. Do not use that preview as the only data store.
- Supplier Excel importer now resolves Dec-Jan weekly ranges from concrete
  start/end dates in the supplied filename, including independent worksheets;
  a single chronological sheet can infer December-to-January rollover.
  Synthetic regressions cover both cases; no supplier mail was read.
- Approved 25-column template upload now also supports an explicitly configured
  absolute SPORT_TEMPLATE_PATH on any host, using validated atomic replacement.
  On a temporary filesystem, the UI must warn that the workbook can be lost.
  This feature does not create or mount permanent storage.
- A real owner-provided sample named Sport_generation_Final_FIXED_FORMAT.xlsx
  was inspected outside GitHub; its priority column starts 50000, 49900,
  49800. The web export now derives the descending priority step from the
  uploaded workbook instead of imposing an earlier 60000/10 convention.
  The private workbook itself has not been committed or uploaded to Render.
- Code CI and web regression checks succeeded for the code through commit
  a8c9f9677d10c08623717939267889663038535f.
  Recheck the latest branch head's CI after later export changes.
- Gmail OAuth, vendor auto-import, provider mail contacts, persistent storage,
  24/7 scheduling, exact approved workbook upload and full live-source
  validation are NOT finished. Production Telegram release is untouched.

## 2026-09-30 verified supplier workbook formats (isolated web candidate)

- Read-only examination of the user's current QAZSPORT and SPORT+ supplier
  XLSX attachments identified two additional, different official table
  layouts. Their actual original workbook bytes are NOT in the repository.
- `services/official_supplier_excel.py` now parses explicit station headers
  for QAZSPORT HD and SPORT+ Qazaqstan. Only individual programmes bearing
  explicit direct-broadcast text are accepted. Genre labels, sports themes,
  repeats and studio LIVE text alone are not direct-match evidence.
- Kazakh-language undated SPORT+ week headers are resolved conservatively
  relative to message receipt; old/uncertain weeks fail closed. Overnight
  time slots are rolled forward once. QAZSPORT hockey involving Barys is
  excluded in the same way as the existing web event filter.
- A shared dispatcher routes manual XLSX and Gmail supplier attachments to
  this provider-specific parser only after confirming the channel INSIDE
  the workbook. The existing six Setanta/QSport parsers are unchanged.
- First official XLSX from any new sender/worksheet fingerprint stays
  pending for editor approval. Subsequent matching formats reuse the
  established confirmation and still go through overlap/ambiguity guards.
  No Gmail OAuth credentials or inbox polling were provisioned here.
- The source UI remains exactly 14 channels; accepted official supplier
  Excel is shown as a secondary data source on the QAZSPORT/SPORT+ cards,
  not as two invented additional channels.
- Synthetic regressions cover explicit direct sport, replay/studio removal,
  Barys exclusion, date inference, midnight rollover and idempotent import.
  These are code-level tests, not proof of vendor freshness in production.
- No auto-deploy to ephemeral Render, no production mail, and no Telegram
  release. Runtime imports are not persistent until a real durable backend
  is selected.

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
- Export preserves every existing approved priority exactly rather than
  assuming any single arithmetic step. The owner's actual workbook contains
  variable gaps, including 100, 25 and 9. New rows use free integer slots
  between neighboring approved rows; an exhausted gap fails closed.
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

## 2026-09-30 source and editorial regressions

- Read-only inspection of the real current Setanta Sports 1, Setanta
  Sports 2 and Setanta KZ XLSX showed their explicit LIVE prefixes match
  the existing supplier sport parser. The Setanta KZ workbook also has
  a stale June worksheet among the September/October schedules.
  The importer now bounds scope days to the concrete filename period,
  plus one day either side; this guards against importing unrelated
  historical worksheets while keeping overnight spill and supplier
  overlaps. No original supplier workbook was checked into Git.
- The original 25-column owner template has nonuniform approved priority
  gaps. Old priority cells now remain untouched. Chronologically inserted
  rows receive available unique integer priorities, with an explicit
  fail-closed case if there is no space between approved values.
- Explicit unique supplier kickoff changes preserve manual editor
  overrides on the replacement storage ID, with a transfer audit entry.
  Both original edit and historical source revision are retained.
  Same-day repeat programmes never inherit an ambiguous edit.
- Regression tests are synthetic and execute in branch CI; actual
  production LIVE source and OAuth end-to-end remain unverified.
- Render preview remains unchanged; no Telegram deployment or
  paid cloud provisioning is included.

## 2026-09-30 real-workbook layout audit

- Inspected the full owner-provided SPORT+ Qazaqstan and QAZSPORT
  worksheets locally, without uploading proprietary files to Git.
- SPORT+ first day uses B3="Уақыт" beside C3="СӘРСЕНБІ,
  30 ҚЫРКҮЙЕК"; the original untimed-header condition silently skipped
  30 September. The parser now accepts this explicit header label.
  The corresponding synthetic regression now reproduces this layout.
- SPORT+ also carries a direct Kazakh horse-racing programme using
  "БӘЙГЕ ... ТІКЕЛЕЙ ЭФИР". The sport recognizer now includes
  "Конный спорт", with an explicit regression example.
- QAZSPORT source uses Excel serial dates with date-formatted A-column
  cells, fractional B-column clocks, C titles and D durations.
  Only explicit same-row LIVE text is accepted; studio and Barys hockey
  exclusions remain unchanged.
- Source originals and the approved 25-column template are not
  committed, not uploaded to Render and not emailed onward.
  Successful CI alone does not establish production Gmail/OAuth readiness.

## 2026-09-30 - Метаданные уведомлений для персонального помощника

- В PR #39 добавлен опциональный `services/assistant_bridge.py`. После ручной или защищённой плановой синхронизации Gmail он отправляет только идентификатор уведомления, статус `pending/review` и обнаруженный канал по HTTPS с Bearer-секретом. Тела письма, сведения об отправителе, названия файлов и Excel не передаются.
- Локальный аддитивный outbox подтверждает отправку только после ответа получателя; повторный импорт и повторное письмо не порождают новую задачу. До включения интеграции текущий импорт расписаний не меняется.
- Настройка необязательная: `SPORT_ASSISTANT_NOTICE_URL` = HTTPS endpoint `/internal/slp/notice` существующего Worker, `SPORT_ASSISTANT_NOTICE_SECRET` = то же значение, что и `SLP_NOTICE_SECRET` в Worker. Нужны готовые OAuth Gmail, постоянное хранилище и запланированная синхронизация SLP; Secrets никогда не коммитить.
- Статус: изменения только в PR #39. Секреты/Cloud Scheduler/SLP Cloud Run не подтверждены, живой Telegram не переключался, внешние тесты и платные API не запускались. Production Telegram/Railway entrypoint не изменён.

## 2026-09-30 - Preview before manual supplier import

- The web editor now calls authenticated POST /api/preview-epg before
  /api/import-epg. Preview parses the same file and returns per-channel
  new, kickoff-change, absent-from-update, unchanged and ambiguous counts,
  without changing source snapshots or triggering a backup.
- The browser asks for explicit approval of each file before posting to
  the existing import route. Invalid files stop at preview; cancellation
  leaves accepted schedules unchanged. The preview displays a clear
  warning when the current database has no durable backup.
- The existing import endpoint remains available for established API
  callers, so the new browser confirmation is a UI safeguard, not a
  server-enforced two-phase transaction. A later deployment should
  consider signed previews / optimistic concurrency for multi-editor
  races. No production instance was redeployed.

## 2026-09-30 - Safety for incomplete supplier updates

- A replacement XLSX that still has LIVE events on a date but omits
  individual previously accepted fixtures now leaves that date's accepted
  source snapshot intact and reports partial_review/held_dates. A missing
  row is not a verified cancellation. Previously only fully empty dates
  were held; partially populated dates could silently erase old matches.
- A focused regression covers a two-match Setanta day updated with only
  one of those matches. Preview flags missing_from_update and import
  preserves both previously accepted matches for editorial reconciliation.
- This is a conservative fail-closed behavior: new fixtures on a held day
  are not accepted until the supplier revision is reconciled. It does
  not claim that old matches remain current indefinitely.
- Gmail OAuth, scheduler and temporary Render deployment remain untouched.

## 2026-10-05 - iptvX: provider LIVE! icon is now the live evidence

- **Original symptom:** iptvX events could not be told apart as live or
  repeat. On `epg.iptvx.one/id/<tvg-id>` the provider marks direct broadcasts
  with a `LIVE!` icon (`live.png`) inside the programme row; a repeat of the
  same match carries the identical title without the icon.
- **Root cause:** `parse_iptvx_page()` read rows with `get_text()`, which drops
  the `<img>`; every match-like row then became `live_state="candidate"` with
  `is_live_broadcast=True`, so repeats entered the LIVE schedule.
- **Changed:** `services/iptvx_sources.py` walks each day in document order
  (independent of `<p>`/`<li>`/`<div>`/table/`<br>` layout), records the icon
  per row and stores marked rows as `live_state="live"`,
  `live_evidence_method="iptvx_live_icon"`, confidence `high`. When a page has
  at least one mark, unmarked rows on that page are counted as
  `unmarked_repeats` and not stored. A page with no marks at all keeps the
  previous candidate behaviour. `IPTVX_LIVE_MARKER_POLICY=off` restores the old
  behaviour for unmarked rows. Live studio shows the provider also marks
  (`КХЛ. Подробно`, `На связи`, ...) are excluded.
- **Changed:** KHL ids were swapped. `kxl` is "КХЛ ТВ | КХЛ | KHL" and now maps
  to `KHL PRIME`; `kxl-hd` maps to `KHL HD` (page titles checked 2026-10-05).
- **Verification:** `cloudrun_tests` 129 passed, `tests` 175 passed, compile
  clean. New regression rows are copied from the real Setanta Sports and KHL
  pages of 28 Sep - 5 Oct 2026.
- **Still open / NOT verified:** the raw HTML of the pages was not available to
  the agent (only rendered text), so the layout-independent reader is proven on
  four synthetic layouts, not on a captured page. Run
  `PYTHONPATH=. python scripts/check_iptvx_live.py` from a host that can reach
  the site: every channel must show `LIVE!` > 0 on a week with broadcasts.
  The XMLTV path (`parse_iptvx_xml`, diagnostics only) is unchanged. The feed
  covers the current week only, so the upcoming horizon shrinks to almost zero
  shortly before the provider's weekly refresh. Background `iptvx-refresh`
  still answers 503 without GCS. No deployment, no Telegram bot change.

## 2026-10-05 - source cards show Excel and parsing separately

- **Original symptom:** cards read "Данные получены с Excel + парсинга" with
  one timestamp, so the editor could not tell which source feeds the grid.
- **Root cause:** "Excel" was shown whenever any file had ever been imported
  (`imported_at` set), regardless of whether it covers the current 7 days, and
  the single timestamp was the newer of the two signals.
- **Changed:** `cloudrun_ui/index.html` renders two indicators per channel,
  Excel and Парсинг, each lit only when it has events in the current window,
  each with its own event count and time; stale file, awaiting supplier, error
  and "no LIVE events" are distinct states. No server change.
- **Changed:** `services/free_apps_script.py` turns the bare
  `Apps Script: unauthorized` into a message naming the cause (bridge key
  mismatch or wrong script URL). The mismatch itself is configuration in Secret
  Manager / Script Properties and is NOT fixed by code.
- **Verification:** `cloudrun_tests` 129 passed, `tests` 175 passed, page
  script passes `node --check`. Not checked in a browser.

## 2026-10-06 - export no longer depends on the Drive bridge being up

- **Original symptom:** banner `Ошибка #201: Почта/Drive: Google Apps Script
  недоступен: HTTPError` and «Выгрузить Excel» produced no file.
- **Root cause:** `/api/export` loads the approved template on every click
  (local path -> GCS -> Apps Script). With no template in GCS and the bridge
  failing, `load_template()` raised 503, so a storage outage blocked a table
  whose data lives entirely in the SLP database. The bridge error also hid the
  HTTP status, so the cause could not be read from the banner.
- **Changed:** `cloudrun_web.py` keeps the last approved template read or
  uploaded in this process and reuses it when storage fails (`cached`); with no
  copy at all it exports the same events on a plain built-in 25-column sheet
  (`builtin`). The response carries `X-SLP-Template` and the UI states plainly
  when the approved layout was not used. `services/free_apps_script.py` reports
  `HTTP <code>` with the likely cause (403 access, 404 wrong /exec URL, 429
  quota, 5xx).
- **Verification:** `cloudrun_tests` 133 passed (4 new in
  `test_export_resilience.py`), `tests` 175 passed.
- **Not fixed by code:** the bridge itself. The built-in export does not
  contain rows and approved translations that exist only inside the template.

## 2026-10-06 - «Запросить расписание» works through the Apps Script bridge

- **Original symptom:** in bridge mode the button was always disabled:
  `/api/gmail/status` hard-coded `send_enabled=False` and the script could only
  read mail.
- **Changed (script):** `SLP_Free_Gmail_Drive.gs` gains signed
  `op=capabilities` (GET) and `op=send_request` (POST, body covered by the
  HMAC). Recipients come only from Script Properties `SLP_REQUEST_Q_TO`,
  `SLP_REQUEST_SETANTA_TO`, `SLP_REQUEST_TEST_TO`; `SLP_REQUEST_MODE` defaults
  to `test` (all letters to the test address, `[ТЕСТ]` subject). Single-use
  request id, 10-minute per-supplier limit, `all` = two letters with a
  per-supplier result. Existing operations untouched.
- **Changed (Python):** `services/free_apps_script.py` adds
  `ScriptClient.capabilities()/send_request()`, a stored capability check
  (`free_bridge_send`), `send_schedule_request()` writing the existing
  `gmail_requests` journal (test letters get status `test` and never show as
  awaiting), readable error texts, and reply matching for supplier files that
  arrive through the bridge.
- **Changed (backend/UI):** `POST /api/gmail/request` routes to the bridge when
  it is enabled (production mode requires admin); direct Gmail path unchanged.
  `/api/gmail/status` returns `send_enabled` from the last signed capabilities
  answer plus `send_reason`, `capabilities`, `send_targets`. The button title
  shows the reason; the result names supplier, period, recipient and subject.
- **Verification:** archive includes Node bridge tests for the real `.gs` source
  plus Python backend/UI regression coverage.
- **NOT verified:** nothing was sent through production. The deployed script
  must be updated and its properties set by the owner. No supplier must receive
  a letter until test mode is confirmed.


## Schedule self-check, wider ESPN cross-check, startup restore retry (2026-10-07, branch feature/schedule-anomaly-check)

- **Original symptom:** a wrong time or a leftover test row reached the
  schedule unnoticed (20-hour basketball broadcast, `ЧМ-2032`, «тест» rows,
  day/month swapped); the ESPN cross-check covered 10 leagues and only the
  first two days; one temporary Apps Script error at boot crashed the process.
- **Changed (new):** `services/schedule_anomalies.py` — read-only rules over
  the merged schedule: `duration_too_long`, `duration_too_short`,
  `channel_overlap`, `duplicate_slot`, `same_fixture_different_time`,
  `test_label`, `impossible_year`, `date_swap_suspect`. Durations are judged
  only when the end time is known, never for an estimated end.
- **Changed (backend/UI):** `GET /api/anomalies` (signed-in users; default
  period starts yesterday with no upper bound); the collect result carries a
  `schedule_anomalies` summary; new «Проверка расписания» panel in the web UI.
  Findings never change the schedule.
- **Changed (validation):** `ESPN_FEEDS` grew from 10 to 26 feeds (domestic
  cups, Conference League, Nations League, UEFA WC qualifying, Portugal,
  Netherlands, Scotland, MLS, Saudi Pro League as `ksa.1`). Requests run
  concurrently (limit 8), cover up to 8 days including the previous calendar
  day, and a feed is asked only for days that carry that sport.
- **Changed (startup):** `FreeDriveError.transient` marks network, 5xx and 429
  failures; `_restore_drive_snapshot()` retries those twice (2 s, 5 s). A
  persistent outage or a configuration fault still stops startup: the app is
  never started with an empty database.
- **Verification:** `compileall` clean; `cloudrun_tests` 177 passed, `tests`
  175 passed; local `uvicorn cloudrun_web:app` on a throwaway SQLite file:
  `/api/anomalies` returned 401 signed-out and the expected four findings
  signed-in, and the UI panel rendered them in headless Chromium. ESPN slugs
  were checked one by one against the live scoreboard endpoint.
- **NOT verified:** nothing was deployed. The rules have not been run against
  the production database, so the real false-positive rate is unknown. The
  ESPN request volume per collect grows (up to 26 feeds x 8 days) and was not
  measured against the live API from Cloud Run.

# SLP — Skoomaholic's Sports Live Parser

Production-oriented Telegram bot and TV-schedule aggregator for sports direct broadcasts in Kazakhstan.

SLP reads official channel schedules, normalizes all broadcast time to `Asia/Almaty` (UTC+5), validates source output, keeps last-good snapshots in SQLite, computes `upcoming / live / finished` centrally, and serves Telegram from accepted stored data.

## Supported channels

| Channel | Source | Mechanism |
| --- | --- | --- |
| Qazsport | `https://qazsporttv.kz/ru/program` | official static HTML, date-specific pages |
| Sport+ Qazaqstan | `https://sportplustv.kz/ru/tvguide` | official static multi-day HTML |

Playwright is intentionally **not** a runtime dependency. Both current sources expose the required schedule in server-rendered HTML, so `aiohttp + BeautifulSoup` is simpler and more reliable. If a source later becomes JS-only, add Playwright only to that provider.

## Data flow

```text
official TV source
      ↓
source parser
      ↓
SportEvent contract
      ↓
ParserOrchestrator
  ├─ per-source isolation
  ├─ retry for required current scopes
  ├─ deterministic QA
  ├─ source anomaly detection
  └─ last-good fallback
      ↓
SQLite snapshots / run history / incidents
      ↓
ScheduleService
  ├─ direct-broadcast filtering
  ├─ Asia/Almaty status calculation
  └─ multi-day schedule window
      ↓
Telegram / notifications / XLSX / diagnostics
```

Same sporting event on two TV channels is preserved as two broadcasts. Deduplication removes only true duplicates on the same channel/date/time/title.

## LIVE semantics

Broadcast state is calculated centrally from timezone-aware datetimes:

```text
upcoming: now < start
live:     start <= now < end
finished: now >= end
```

When a source does not publish an explicit end time, SLP uses a conservative sport-specific fallback. When the next TV programme is known, that programme start is used as the broadcast end.

The application timezone is always `Asia/Almaty`. Naive `now` values supplied in tests are interpreted as `Asia/Almaty`; aware values are converted to it.

## Schedule depth

The runtime no longer reads only today:

- yesterday is retained only so an overnight LIVE broadcast can stay visible after midnight;
- Qazsport is refreshed through the current published TV week and at least the nearest Monday;
- Sport+ is scanned up to 14 days ahead because its official TV guide publishes a wider window;
- Telegram reads stored snapshots through 14 days ahead.

If a future source page is not published yet, that optional date does not degrade the current feed. Current/yesterday source failures still use last-good data when available.

## Cold start

Before Telegram polling starts, SLP performs one source refresh. This prevents `/live` from answering from a brand-new empty SQLite database while the first background refresh is still running.

After that, the scheduler refreshes every four minutes by default.

## Installation

CI uses Python 3.12.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Copy the environment template and set the Telegram token outside Git:

```bash
cp .env.example .env
```

The project does not load `.env` automatically; export variables through your shell, container, service manager, Codespaces secrets, or another secret manager.

## Environment variables

Required:

```bash
BOT_TOKEN=<telegram bot token>
```

Optional:

```bash
SLP_REFRESH_INTERVAL_SECONDS=240
SLP_DB_PATH=/path/to/slp.db
SLP_LOG_LEVEL=INFO
ADMIN_IDS=123456789,987654321
OPENSERP_BIN=/path/to/openserp
OPENSERP_BASE_URL=http://openserp-api.railway.internal:7000
```

Never commit `.env`, Telegram tokens, cookies, session files, or API credentials.

## Run the bot

```bash
python main.py
```

Useful commands:

```text
/start    main menu
/today    accepted current + future direct-broadcast schedule
/live     direct broadcasts whose current state is LIVE
/check    independent web verification for one event
/health   parser/database health
/status   alias for /health
/refresh  force source refresh (admin when ADMIN_IDS is configured)
/errors   unresolved source/QA incidents (admin)
```

One provider failure does not stop the other provider. User-facing Telegram messages do not contain Python tracebacks; full exceptions remain in application logs.

## Diagnostics

Run a real end-to-end source refresh without a Telegram token:

```bash
PYTHONPATH=. python scripts/diagnose_live.py
```

It prints:

```text
CHANNEL | DATE | START | END | STATUS | LIVE | EVENT | SOURCE
...
=== LIVE NOW ===
...
=== NEXT LIVE EVENTS ===
...
```

This is the preferred smoke command when investigating “the site shows LIVE but the bot says there is nothing live”.

## Logging

Set `SLP_LOG_LEVEL=DEBUG` for skip reasons and detailed source diagnostics.

Typical INFO records include source/date, fetched and accepted counts, current status counts, `now`, timezone, run ID and schedule horizon. Source exceptions use traceback logging. Secrets and `.env` contents are never logged.

## Tests

```bash
python -m compileall -q .
python -m unittest discover -s tests -p 'test_*.py' -v
```

GitHub Actions runs compile/import/full regression on pushes and pull requests. A separate scheduled smoke workflow checks the real official source pages.

## Persistence

SQLite stores:

- active accepted source snapshots;
- historical parser-run counts;
- orchestrator runs;
- source/QA incidents.

Runtime SQLite/WAL files, logs, notification state and `.env` files are ignored by Git.

## Dependency policy

Dependencies are pinned in `requirements.txt` and upgraded only after API/CI compatibility is checked. As of September 2026, the pinned aiogram, aiohttp, Beautiful Soup and openpyxl versions are already current stable releases used by this project.

## Troubleshooting

**`/live` is empty immediately after startup**  
Check startup logs. Polling now begins only after the initial refresh attempt. If a source failed, `/health` and `/errors` show the stored incident/fallback state.

**A site event is missing**  
Run `SLP_LOG_LEVEL=DEBUG PYTHONPATH=. python scripts/diagnose_live.py`. Check whether the source marked it as a direct broadcast, whether it was parsed, and the computed `start <= now < end` window.

**A future day is missing**  
The official source may not have published it yet. Optional unpublished future dates are retried on later refreshes and do not invalidate today’s feed.

**One channel is down**  
The other channel continues refreshing. If a last-good snapshot exists for the failed source/date, SLP keeps serving it instead of replacing it with broken data.

### Separate Cloud Run web candidate

The isolated sports schedule web entrypoint is documented in
[docs/CLOUD_RUN_WEB.md](docs/CLOUD_RUN_WEB.md). It uses the root Dockerfile
and cloudrun_web.py; it does not start the Telegram bot. The web integration
is staged in PR #39, not a replacement for the existing Railway release.

# SLP — Skoomaholic Live Parser v2

Production Telegram bot for collecting and monitoring sports LIVE schedules.

SLP reads TV schedules, normalizes events to UTC+5 / `Asia/Almaty`, protects the feed from parser collapses, stores accepted snapshots in SQLite and serves Telegram from the database instead of scraping sites on every click.

## Sources

- Qazsport — `https://qazsporttv.kz/ru/program`
- Sport+ Qazaqstan — `https://www.sportplustv.kz/ru/tvguide`

Each source is isolated. A failure in one parser does not stop the other source or the Telegram bot.

## Runtime architecture

```text
Qazsport / Sport+
       ↓
source parsers
       ↓
SportEvent contract
       ↓
ParserOrchestrator
  ├─ source health checks
  ├─ deterministic QA
  └─ last-good fallback
       ↓
SQLite snapshots/history/incidents
       ↓
ScheduleService
       ↓
Telegram bot + notifications + XLSX
```

There is one production entrypoint: `main.py`.

## Main rules

- Project timezone: `Asia/Almaty` / UTC+5.
- TV broadcast time is authoritative for the TV schedule.
- Internet verification never silently rewrites TV time.
- Events after midnight are assigned to the correct calendar day.
- Same match on different TV channels is preserved; only true duplicate broadcasts are removed.
- LIVE/SOON/OVER is calculated from the accepted broadcast schedule.
- A broken parser cannot replace a healthy stored schedule with an empty snapshot.
- Telegram reads accepted SQLite snapshots; source scraping runs in the background.

## Telegram

Main menu:

- `📅 Расписание`
- `🔴 Сейчас LIVE`
- `🌐 Проверить событие`
- `📊 Система`
- `🔔 Уведомления`
- `📥 XLSX`

Commands:

```text
/start    main menu
/today    accepted schedule
/live     broadcasts live now
/check    independent web verification for one event
/health   parser/database health
/status   alias for /health
/refresh  force parser refresh (admin when ADMIN_IDS is set)
/errors   unresolved parser/QA incidents (admin when ADMIN_IDS is set)
```

`📥 XLSX` generates a real workbook in memory and sends it directly to Telegram. No placeholder and no temporary export file is written to disk.

## Background refresh

Default interval is 240 seconds (4 minutes). Change it with:

```bash
SLP_REFRESH_INTERVAL_SECONDS=240
```

The scheduler refreshes sources, runs QA/health checks, persists accepted snapshots, compares the new schedule with the previous snapshot and sends change notifications to subscribers.

## Configuration

Required:

```bash
BOT_TOKEN=<telegram bot token>
```

Optional:

```bash
SLP_REFRESH_INTERVAL_SECONDS=240
SLP_DB_PATH=/path/to/slp.db
ADMIN_IDS=123456789,987654321
OPENSERP_BIN=/path/to/openserp
```

Secrets must stay in environment variables or Codespaces secrets. Do not commit `.env` or tokens.

`ADMIN_IDS` is optional. If it is configured, `/refresh` and `/errors` are restricted to those Telegram IDs.

OpenSERP is used only for explicit independent internet checks. Parser/database operation does not depend on OpenSERP being available.

## Install

Python 3.12 is used in CI.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
python main.py
```

Expected startup log:

```text
SLP v2 started
SQLite: .../slp.db
Background refresh: every 240 sec
```

## Tests

```bash
python -m compileall -q .
python -m unittest discover -s tests -p 'test_*.py' -v
```

GitHub Actions runs compile + full regression on pull requests and pushes to `main`.

## Persistence

SQLite stores:

- accepted source event snapshots;
- parser run history;
- orchestrator runs;
- source/QA incidents.

Runtime files (`*.db`, WAL files, logs, `slp_state.json`) are ignored by Git.

## Version

Current production line: **SLP v2**.

Legacy `Step 71.2`, `Parser v1 RC` and the separate `agent_main.py` runtime are removed.

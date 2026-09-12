# SLP v2 — Parser Guardrails

SLP has one production entrypoint: `python main.py`.

The parser guardrail layer is part of the normal runtime. There is no second `agent_main.py` and no monkey-patching of the Telegram application.

## Data flow

```text
Qazsport / Sport+ Qazaqstan
        ↓
source parsers
        ↓
ParserOrchestrator
  ├─ SourceHealthAgent
  ├─ ParserQAAgent
  └─ SLPDatabase (SQLite)
        ↓
ScheduleService
        ↓
Telegram handlers
```

`ParserOrchestrator` owns source refreshes, anomaly checks, QA and last-good fallback. Accepted snapshots are persisted to SQLite. `ScheduleService` reads those accepted snapshots for Telegram, so user button presses do not scrape source websites.

## Protection rules

- parser/network exception: reject the fresh snapshot;
- a previously healthy source collapsing to zero: reject;
- a severe unexpected count collapse: reject or warn according to `SourceHealthAgent`;
- contract/time QA failure: reject;
- rejected runs keep the last accepted source snapshot active.

Agents never invent schedule data.

## Runtime

Environment variables:

```bash
BOT_TOKEN=...
SLP_REFRESH_INTERVAL_SECONDS=240
SLP_DB_PATH=/optional/path/slp.db
ADMIN_IDS=123456789,987654321
```

`ADMIN_IDS` is optional. If configured, `/refresh` and `/errors` are restricted to those Telegram user IDs.

## Diagnostics

- `/health` or `/status` — source runs, orchestrator state, SQLite counts and incidents;
- `/refresh` — manual parser refresh;
- `/errors` — unresolved parser/QA incidents.

## Tests

```bash
python -m unittest discover -s tests -p 'test_*.py' -v
```

CI compiles the entire project and runs the full regression suite on pull requests and pushes to `main`.

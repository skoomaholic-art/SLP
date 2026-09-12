# SLP Parser Agent Network v1

This branch adds a guardrail/orchestration layer around the existing SLP parsers without rewriting them.

## Runtime

Stable legacy entrypoint remains:

```bash
python main.py
```

Agent-enabled entrypoint:

```bash
python agent_main.py
```

`BOT_TOKEN` is still read by the existing `main.py`. Optional database path:

```bash
export SLP_DB_PATH=/path/to/slp.db
python agent_main.py
```

## Architecture

```text
Qazsport / Sport+
       │
       ▼
ParserOrchestrator
       │
       ├── SourceHealthAgent
       │     detects source/parser count anomalies
       │
       ├── ParserQAAgent
       │     validates SportEvent contract and time logic
       │
       └── SLPDatabase (SQLite)
             events snapshots
             parser runs
             agent runs
             incidents
```

The parsers remain deterministic Python code. Agents do not invent schedule data.

## Protection rules

For each source and requested schedule date, the orchestrator compares the fresh parser output to the previous accepted run.

- parser/network exception -> block fresh snapshot;
- previous count >= 3 and fresh count becomes 0 -> block;
- previous count >= 5 and fresh count falls to <=20% -> block;
- >=50% drop on a larger snapshot -> warning, but publication remains allowed;
- QA contract/time failure -> block.

When a run is blocked, SLP loads the last active SQLite snapshot for the same source/date. This prevents a broken HTML selector or temporary source failure from replacing a valid schedule with an empty one.

## Telegram

`agent_main.py` keeps the existing schedule, LIVE, notification and detail handlers. It monkey-patches only `main.load_schedule_events` with the orchestrated loader.

New diagnostics:

```text
/health
```

The main menu also replaces the old static Status button with `🧠 Health`.

Health output includes:

- current source status;
- fresh vs previous event count;
- fallback usage;
- last orchestrator run;
- active SQLite event count;
- latest unresolved incidents.

## Database

Default file:

```text
slp.db
```

Tables:

- `events` — active/previous source snapshots and original event JSON;
- `parser_runs` — every parser/source/date check;
- `agent_runs` — orchestrator runs;
- `incidents` — blocked source/QA failures.

Runtime SQLite files are ignored by Git.

## Tests

```bash
python -m unittest discover -s tests -p 'test_agent_network.py' -v
```

The regression suite verifies:

- collapse-to-zero detection;
- QA source mismatch rejection;
- SQLite snapshot restoration;
- orchestrator fallback to the last good source snapshot.

## Next phase

After this deterministic layer is stable, an LLM Incident/Repair Agent can be added on top of `incidents` and GitHub PRs. It should never write directly to `main`: proposed repairs must run tests first and land through a separate branch/PR.

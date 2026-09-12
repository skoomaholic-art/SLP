# SLP product contract

This is the user-visible contract. Implementation details may change; these outcomes must remain stable unless the product requirement is explicitly changed.

## Purpose

SLP is a Telegram sports schedule assistant for Kazakhstan. It aggregates official/approved TV schedule sources, normalizes time to Kazakhstan time, separates actual direct-broadcast evidence from simple EPG presence, persists accepted data, and serves current/future sports information without scraping on every user click.

## Time contract

- Canonical application timezone: `Asia/Almaty` (UTC+5).
- All user-visible event times must represent that timezone.
- Source timestamps carrying UTC semantics must be converted before status calculation.
- Status is calculated at request/reference time, not frozen forever at ingest time.

## Event state contract

For an event with normalized start/end:

- `UPCOMING`: current time is before start;
- `LIVE`: current time is greater than or equal to start and before end;
- `FINISHED`: current time is greater than or equal to end.

This temporal state is independent from whether the event has sufficient evidence to be considered a direct sports broadcast.

## Direct-broadcast contract

An event must not be labelled/returned as direct LIVE solely because it exists in an EPG.

False-positive categories such as replay, review, preview, archive/classic and comparable non-direct content must not be promoted to direct LIVE.

If the source does not provide enough direct-broadcast evidence, the event may still be a valid sports schedule candidate without becoming a direct LIVE event.

## Telegram outcomes

The current command/button contract includes:

- `/start` — usable main menu;
- `/today` — accepted current/future sports schedule;
- `/live` — only events that are temporally LIVE **and** have direct-broadcast evidence;
- `/check` — independent verification flow for one event;
- `/health` / `/status` — operational state;
- `/refresh` — forced refresh subject to admin restrictions;
- `/errors` — unresolved parser/QA incidents subject to admin restrictions.

A successful handler invocation that returns an empty result is only correct when the accepted data and filters genuinely produce no matching events. It must not silently hide refresh/database/configuration failures.

## Persistence outcomes

- User-facing schedule reads come from accepted SQLite snapshots, not a fresh scrape on every click.
- A broken fresh source result must not wipe a previously healthy accepted snapshot.
- Stored rows retain source identity, channel, titles, date/time, normalized start/end, timezone, broadcast evidence/status metadata and source URL where available.
- Duplicate removal must not collapse the same sporting event broadcast on different channels.

## Refresh outcomes

- One source failure does not stop healthy sources from updating.
- Startup attempts a refresh before Telegram polling.
- Background refresh repeats on the configured interval.
- Failures become diagnosable through logs/health/incidents rather than being silently converted into an empty schedule.

## XLSX outcomes

Exports must use the same accepted data/status semantics as Telegram at a defined reference time.

- LIVE export contains only the intended LIVE subset.
- finish/upcoming export contains the intended non-LIVE subset according to the export mode contract.
- timezone and direct-broadcast meaning remain explicit enough to audit.
- two export modes must not accidentally contain identical unfiltered rows.

## Reliability definition

SLP is considered operational only when all of these are true at the same deployed revision:

1. production process starts;
2. official sources can be refreshed or valid last-good data is available;
3. accepted snapshots exist in the deployed database;
4. temporal/direct-broadcast filtering returns correct sets;
5. Telegram handlers expose those sets;
6. repeated scheduled refreshes do not erase healthy data;
7. diagnostics identify source/configuration failures.

Green repository tests alone are necessary but not sufficient for this definition.

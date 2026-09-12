# SLP known issues

Use this file for confirmed defects and verification gaps that materially affect the product. Do not turn it into a wishlist.

## P0 — deployed end-to-end result is not yet proven

**Status:** OPEN / verification gap

**Symptom:** the user reports that the project can stall and fail to produce useful results even while repeated fixes are being made.

**Repository evidence:** baseline CI and live-source smoke are green, so repository-level checks alone do not explain or disprove the deployed symptom.

**Required next step:** execute `docs/exec-plans/active/finish-project.md` P0.1 -> P0.6 and identify the first failing boundary.

**Do not:** apply another parser/filter fix without first locating the failing boundary in source, deployment, persistence, service filtering or Telegram output.

---

## P1 — documentation/source wiring mismatch

**Status:** OPEN

**Observed:** `RuntimeParserOrchestrator` wires `qazsport`, `sportplus` and `tvguide` in production, while some high-level project documentation historically described only Qazsport and Sport+ as supported source/channel paths.

**Risk:** a coding agent can mistake lower-level/parser-wrapper files for dead or alternate implementations and create a parallel path or remove a production dependency.

**Resolution:** after P0, document exactly what `tvguide` contributes, reconcile README wording, then remove only code proven unused by repository search/tests.

---

## Closed / regression-protected

### 2026-09-12 data-correctness audit

Regression coverage exists for:

- TVGuide UTC -> `Asia/Almaty` conversion;
- separation of temporal status from direct-broadcast evidence;
- rejection of replay/review/preview/archive false positives;
- audit event examples including Tottenham–Everton, Brazil–Chile, Noche UFC and Viju Snooker Cup;
- distinct export-mode filtering.

Do not reopen this as a generic rewrite. Reopen only for a new reproducible regression with source evidence.

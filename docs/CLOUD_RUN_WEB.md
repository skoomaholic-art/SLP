# Web SLP on Google Cloud Run

This is a separate web entrypoint in the existing SLP repository, not a
replacement for the Telegram bot. The bot continues to start with
python main.py. The Cloud Run image starts uvicorn cloudrun_web:app.

## Implemented in this Cloud Run integration

- Health endpoint /healthz reports web runtime and durable GCS backup status.
- Browser sign-in with seven named accounts. Hashed credentials and the
  signing secret come from Secret Manager, never Git.
- Calendar and archive from accepted direct-sport-broadcast SLP snapshots.
- Manual Collect calls the existing SLP parser/orchestrator; missing QSport
  and Setanta triggers an explicit confirmation for partial results.
- Cross-channel grouping retains all channels in the archive and chooses one
  for the working list; never choose QAZSPORT HD for a Barys match.
- Export to the 25-column template when it is provided at deployment time.
  START = actual start minus 10 minutes. An estimated END gets plus 10 minutes.
- Optional durable storage: SQLite online backup goes to a GCS object with
  generation preconditions and is restored at startup.

Manual Setanta/QSport .xlsx uploads ARE connected to the web app:
a bounded authenticated POST /api/import-epg parses only explicit LIVE sport
rows, stores per-channel snapshots, tracks SHA-256 to prevent reimport and
records covered days. GET /api/sources reports imported filenames, actual
coverage and parser-run status. A missing-file warning is based on recorded
coverage, not on a hardcoded missing-channel list. Different channels have
different user-supplied logos, including each of the three Setanta variants.

The owner's supplied brand icon and avatar images are in
cloudrun_ui/assets/ (Skoomaholic, Дания, Вадим). The source and event UI
uses a full contain-fit logo frame: KHL PRIME and KHL HD have separate
identity labels, as do all three Setanta stations. The favicon is the
approved puppet-and-football icon, not a generated replacement.

Mail request preview config: SPORT_MAIL_TEST_TO defaults to the owner's
work mailbox (alexandr.petrossov@fmedia.kz), while
SPORT_MAIL_FUTURE_CC reserves the same address for a future CC field.
The previously supplied Anton addresses are NOT an active recipient in
the web code. This does not enable sending or Gmail OAuth.

GET /api/archive includes inactive saved source snapshots, and
GET /api/export-archive exports separate confirmed channel broadcasts.
This is NOT a complete record of every historical change: existing database
upsert can overwrite the payload for an identical storage ID.
The 25-column export now preserves existing editorial rows, translations
and IDs rather than erasing the workbook's prefilled events. Unknown Kazakh
translations remain blank for review. The exact owner's template still must
be provisioned at SPORT_TEMPLATE_OBJECT and checked against its headers.

STILL NOT CONNECTED: automatic Gmail ingestion, sending request emails,
automatic email/EPG change detection, full audit-history persistence and
background scheduling. The mail-request button remains disabled rather than
pretending to send. The existing site/ is informational, not this web app.

## On the Cloud Run build screen

GitHub: skoomaholic-art/SLP
Build type: Dockerfile
Source location: /Dockerfile
Branch: main only after this feature PR is reviewed and merged.
Service name: sport-epg
Port: the PORT environment variable (defaults to 8080)
Minimum instances: 0
Maximum instances: 1
Request concurrency: 1
Request timeout: 300 seconds for a manual refresh.

Leave Cloud Run authentication as Require authentication until the browser
access policy is configured. A normal browser sign-in to this app does NOT
bypass Cloud Run IAM. For browsers, configure IAP or another approved
authenticated frontend. Do not expose it publicly with weak old passwords.

The Dockerfile does not execute main.py or start Telegram polling, and does
not alter Railway release configuration.

## Cloud Run runtime configuration

SPORT_PUBLIC_URL: the exact HTTPS web origin, without trailing slash
SPORT_WEB_SECRET: random HMAC signing secret, at least 32 characters
SPORT_WEB_USERS: Secret Manager JSON of individually salted scrypt user hashes
SLP_DB_PATH: defaults to /tmp/slp/slp.db
SPORT_GCS_BUCKET: existing private bucket accessible by the service account
SPORT_GCS_OBJECT: optional, defaults to sport-epg/slp-web.db
SPORT_TEMPLATE_OBJECT: optional, defaults to sport-epg/template.xlsx
SPORT_TEMPLATE_PATH: alternative local Excel template path for development

Create account hashes locally using:
python scripts/cloudrun_users.py /private/path/sport-users.json

Enter fresh strong passwords interactively. Store the resulting JSON as a
Secret Manager secret, set SPORT_WEB_USERS from the secret, then delete the
temporary local JSON. Do not commit credentials, and do not paste passwords
into Cloud Build. The provisioner sets up Skoomaholic (admin), Дания (🇩🇰),
Вадим (🐈‍⬛), and USER1 through USER4 (editors). Their old disclosed weak
passwords must be replaced before deployment.

Use a least-privilege Cloud Run service account with object access limited to
the chosen bucket. Upload the exact approved 25-column Excel workbook to
SPORT_TEMPLATE_OBJECT. The repo does not contain the workbook or Gmail tokens.

When SPORT_GCS_BUCKET is absent, the app can start and warn, but Cloud Run's
local SQLite database is ephemeral and may disappear on restart. Do not treat
that mode as a permanent archive. GCS snapshots are saved after manual refresh or a successful Excel import
only. A complete production archival/retention policy needs backups
and transactional storage if more instances are required. Storage and Cloud
Run can incur costs; this PR creates no billable infrastructure.

## Browser / integration limits

First configure the approved Cloud Run edge access, then sign in with the
application account. Rotating an account hash invalidates existing sessions.
Login returns 503 until the secrets are configured.

Gmail OAuth is not configured or used by this web app. No email will be sent.
Authorized editors can temporarily import actual downloaded supplier XLSX files
with the manual upload control. Gmail attachments are not auto-ingested yet.

Existing SLP parsers and current-snapshot semantics are inherited.
No new channel/source is silently invented. The standard event view reads current accepted source snapshots; archive
mode also reads inactive source rows. This is not a field-level changelog and
should not be described as one.
Kazakh fields for events without verified translations require editorial
review rather than automatically trusting the Russian fallback.

Google Cloud Storage FUSE should never host a live SQLite database. We use
SQLite backup to object bytes. Configure maximum one Cloud Run instance and
concurrency one; generation-matched uploads fail instead of losing updates
on a stale writer. Scale-out requires a transactional database.

## Narrow verification after configuration

Confirm /healthz returns runtime=web and telegram_polling=false.
Verify login, one direct event, template XLSX, and restore after restart.
Live-source correctness and email end-to-end require separate verification.

Local development requires both requirements.txt and requirements-cloudrun.txt.
Run uvicorn cloudrun_web:app --port 8080 with the above runtime variables.

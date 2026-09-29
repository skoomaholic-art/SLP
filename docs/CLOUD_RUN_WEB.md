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

## Gmail implementation and approval boundary

The authenticated web service now has a Google OAuth authorization-code flow,
a per-admin one-time state, verification of the exact owner Gmail identity,
and an encrypted refresh token stored in the application SQLite/GCS backup.
It uses the owner's own Google OAuth credentials; **this code does not reuse
the ChatGPT-connected Gmail token**.

The Gmail UI supports manual sync, a pending XLSX review list, manual import
of confirmed Setanta/QSport XLSX, dismissal, and review of text-only change
notifications. Ambiguous attachments (e.g. a generic SPORT+ "сетка Канала.xlsx"
without a confirmed schema/timezone) must NOT be imported automatically.
Before accepting a pending XLSX, the editor can preview a real
same-channel difference (new fixtures, one-to-one kickoff changes, ambiguous
repeated fixtures, or programmes no longer listed). The diff does not update
the database. Approving an older received email is blocked if a newer
approved supplier email overlaps its dates; the user may inspect historical
source versions without rolling back the active programme.
The browser checks for new mail approximately every 15 minutes only while the
authorized application is open; continuous background polling is NOT active.

Test-only outbound request code uses exactly the owner's preapproved work
mailbox alexandr.petrossov@fmedia.kz as recipient, after the user explicitly
confirms the selected fixed template and SPORT_GMAIL_ENABLE_TEST_SEND=true.
The switch defaults to false. Anton addresses are not present in the active
recipient configuration. SPORT_MAIL_FUTURE_CC reserves the same work mailbox
as a later copy recipient; no production recipient routing is enabled.
No email was sent during development.

The token exchange, Gmail scope checks and send path need an authorized
owner run against the actual Google project to verify end-to-end behavior.

GET /api/archive includes inactive saved source snapshots, and
GET /api/export-archive exports separate confirmed channel broadcasts.
Append-only source revisions (added, changed, removed from EPG) are saved
in event_revisions; a removed listing is NOT marked as a cancelled event.
Manual editor corrections are stored in separate editorial_overrides plus
editorial_audit tables so an EPG update does not wipe editorial texts.
Browser correction fields include RU/KZ teams, subtitles, title, tournament,
start, and end time. Editing or inspecting change history needs an
authenticated editor. This is not yet an exhaustive guarantee of all
provider corrections (e.g. changed fixture ID may appear removed + added).
The 25-column export preserves existing editorial rows, translations,
IDs and supplied titles, applies stored editorial corrections, and reuses
only unambiguous approved RU/KZ TEAM/SUBTITLE mappings from the private
template (not from a generic machine translation). It escapes
potential spreadsheet formulas and sorts events in UTC+5. Unknown Kazakh
translations remain blank for review, not guessed or copied from Russian.
Priorities follow the supplied workbook's 50000, 49900 ... step.
An admin-only /api/template upload endpoint validates the 25 approved headers
and saves the original workbook in GCS with a write-generation precondition.
The exact owner's template is NOT committed to the public repository;
admin must upload it after securing GCS. A strict production completeness
check for all 25 translated fields still requires editorial signoff.

The standalone web collector now also fetches six independently
identified LIVE-only VseTV week sources: KHL PRIME (806), KHL HD (1641),
EUROSPORT 1 (535), EUROSPORT 2 (1082), МАТЧ! ПЛАНЕТА (32),
and viju+ Sport (332). It detects the source's LIVE image on the individual
programme, filters studio/replays, strips bookmaker words, normalizes
Europe/Moscow to Asia/Almaty and preserves last-good rows when a site fails.
Those six web-source real-world results are NOT yet verified after deployment.
The existing Telegram parser and its unrelated Setanta VseTV channels remain
unchanged.

STILL NOT CONNECTED OR NOT PROVEN: owner OAuth authorization, real outbound
test message, 24/7 Cloud Scheduler/OIDC job, automatic parsing of unrecognized
Sport+ mail spreadsheets, full multi-provider end-to-end LIVE correctness,
editorial signoff on translations/25 columns, Google Cloud deployment,
GCS durable backup and actual user/secret provisioning. None were performed
or represented as complete. The existing site/ is informational.

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
SPORT_GMAIL_CLIENT_ID: OAuth 2.0 Web application Client ID from owner's GCP
SPORT_GMAIL_CLIENT_SECRET: same OAuth 2.0 Client secret
SPORT_GMAIL_TOKEN_KEY: stable private Fernet symmetric key (Secret Manager)
SPORT_GMAIL_ENABLE_TEST_SEND: false (default); enable only for authorized test
SPORT_MAIL_TEST_TO: alexandr.petrossov@fmedia.kz (fixed test recipient)
SPORT_MAIL_FUTURE_CC: same address, reserved but not used for real sends
SPORT_SCHEDULER_SERVICE_ACCOUNT: optional email of dedicated OIDC job identity

Create account hashes locally using:
python scripts/cloudrun_users.py /private/path/sport-users.json

Enter fresh strong passwords interactively. Store the resulting JSON as a
Secret Manager secret, set SPORT_WEB_USERS from the secret, then delete the
temporary local JSON. Do not commit credentials, and do not paste passwords
into Cloud Build. The provisioner sets up Skoomaholic (admin), Дания (🇩🇰),
Вадим (🐈‍⬛), and USER1 through USER4 (editors). Their old disclosed weak
passwords must be replaced before deployment.

Use a least-privilege Cloud Run service account with object access limited to
the chosen bucket. The admin may upload the exact approved XLSX workbook
through the UI after GCS is configured. The repo does not contain personal
workbooks or Gmail tokens.

To activate Gmail in the existing Google Cloud project:
1. Enable Gmail API on that project and configure an OAuth consent screen;
   use an eligible owner account or a Google test-user entry as required.
2. Create a Web application OAuth client with authorized redirect URI
   https://<actual-service-origin>/api/gmail/callback .
3. Store client ID, client secret, long-lived Fernet key, web secret and
   salted users JSON in Secret Manager. Do not expose them in GitHub Actions.
4. Set SPORT_PUBLIC_URL to the exact HTTPS origin, configure the private
   GCS bucket, and authenticate to the protected UI as Skoomaholic (admin).
5. Select "Подключить Gmail владельца". OAuth checks that the returned
   Google profile is alexandr.petrossov@gmail.com. At this point sending
   is still disabled.
6. Authorize a one-time test request only if desired, by explicitly setting
   SPORT_GMAIL_ENABLE_TEST_SEND=true. A browser confirmation is required.
   Only alexandr.petrossov@fmedia.kz may receive that test request.
7. Optional: create a dedicated OIDC Cloud Scheduler job that POSTs to
   /api/jobs/gmail-sync, with the audience set to the exact URL including
   the path and SPORT_SCHEDULER_SERVICE_ACCOUNT to that job's verified
   service account. A schedule is NOT provisioned by this code.
   Scheduler, Cloud Run, Secret Manager and GCS may incur billing; get the
   owner's explicit approval before creating resources.

When SPORT_GCS_BUCKET is absent, the app can start and warn, but Cloud Run's
local SQLite database is ephemeral and may disappear on restart. Do not treat
that mode as a permanent archive. GCS snapshots are saved after accepted imports, web source refresh,
editorial changes, Gmail OAuth, inbox notice scans, test send logging and
admin actions that mutate the local database. A network interruption
between mail send and journal backup may require manual reconciliation. A complete production archival/retention policy needs backups
and transactional storage if more instances are required. Storage and Cloud
Run can incur costs; this PR creates no billable infrastructure.

## Browser / integration limits

First configure the approved Cloud Run edge access, then sign in with the
application account. Rotating an account hash invalidates existing sessions.
Login returns 503 until the secrets are configured.

Gmail features are implemented but remain inactive until the owner
configures OAuth and GCS, completes the consent screen and authorizes the
connection. Without those credentials no emails can be read or sent.
Authorized editors can always import an actual verified XLSX manually.
Gmail incoming files are staged for review and never automatically applied
until the editor explicitly accepts them. Outbound mail stays disabled until
the test-only flag and browser confirmation are both supplied.

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

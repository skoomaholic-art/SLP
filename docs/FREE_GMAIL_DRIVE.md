# SLP: Gmail + persistent Google Drive without new paid services

**Use this instead of the GCS setup script. Do NOT create a Cloud Storage
bucket, deploy Cloud Scheduler, enable paid AI, or pay an EPG provider.**

This is a free-quota solution using the owner's existing personal Gmail,
Google Apps Script and private Google Drive. Apps Script time triggers read
supplier mail **hourly**, even if the SLP web UI is closed. The SLP app pulls
archived attachment metadata and downloads only new files when you open the
site, click Check mail or use Collect. An open SLP tab checks every 15 min.

The bridge also stores encrypted/compressed SQLite state plus the editor's
approved template in the private Drive folder. Google Drive quota and Apps
Script runtime are limited: **when a free quota is reached, work stops rather
than turning into a paid subscription.** Do not buy extra Google storage.

Important: **the existing Google Cloud Run deployment is NOT guaranteed
zero-cost.** Using free Google Drive instead of paid GCS does not change the
billing behavior of Cloud Run itself. For a genuinely no-hosting-charge setup,
use the local existing SLP web server described below and, after preserving
your old data, stop the cloud deployment. Local SLP is accessible from your
own computer while it is running. Do not deploy this branch automatically to
the existing Cloud Run service until its cost settings, the backup migration,
and owner consent are reviewed.

## Before any change: protect existing data

From your still-running original SLP, export the working schedule and the
archive and retain both. An Excel archive export is NOT a complete SQLite
backup of every editorial correction. The previous Cloud Run SQLite file
lives under /tmp and **deploying a new revision can lose its contents**.
Do not assume the new Drive backup can recover old unpublished Cloud Run
/tmp data; it can only save data after the bridge is connected.

## 1. Authorize the free Gmail archiver (owner action)

1. In your personal Google account, create a project at
   https://script.google.com/home/projects/create .
2. Paste *integrations/google_apps_script/SLP_Free_Gmail_Drive.gs* as Code.gs.
3. In Apps Script select **setup** at the top and click Run. The script
   generates its own 72-character `SLP_BRIDGE_KEY` if none exists, without
   logging the key. Approve Gmail/Drive permissions for YOUR Google account.
   Review the script code before consent.
4. After the run, open Apps Script > Project settings > Script properties.
   Find `SLP_BRIDGE_KEY`. Do not paste it into ChatGPT or GitHub.
   You will copy it once into your PRIVATE local SLP setup, which
   uses it to authenticate with your Apps Script endpoint. No new Google
   OAuth client/Google API key is needed for this free bridge.
5. Deploy > New deployment > Type: Web app; Execute as: **Me**;
   Who has access: **Anyone**. Save its HTTPS `.../exec` URL privately.
   Because the web endpoint is public, its code denies all non-HMAC requests,
   validates a 90-second timestamp, checks SHA-256, never reveals unencrypted
   backup state, and never exposes attachments without a valid signature.
   Anyone with BOTH your URL and shared key could access your attachments;
   keep BOTH private. If "Anyone" is not permitted for your account, STOP.

The older Google Auth Platform Web OAuth client is not needed for this mode.
There is no SLP Gmail refresh token to expire every seven days.

## 2A. Fully no-hosting-charge route: run existing SLP locally

The existing FastAPI SLP code, editors and archive stay the same. On the
computer where SLP should run, install Python 3.12+ and clone your PRIVATE
SLP repository at this branch. From the repo folder, once:

```
python -m pip install -r requirements.txt
python -m pip install fastapi uvicorn python-multipart cryptography xlrd
python scripts/run_slp_local_free.py
```

At first startup it privately asks for the script's /exec URL, shared key,
and a new local SLP login password. The password is scrypt-hashed; secrets
are stored locally at `~/SLP_Free/local_secrets.json`. Do not sync or share
that file. The SQLite database is stored at `~/SLP_Free/slp.db` and is also
backed up in your own private Drive as an encrypted blob. The browser
interface is `http://127.0.0.1:8080`. Do not bind it to 0.0.0.0 or open
port 8080 on the public internet.

This mode uses no GCP hosting, GCS, GCP Scheduler or Gmail OAuth client.
Google's normal personal Drive/Apps Script quotas still apply; scripts stop
at their quota. Hourly Gmail archiving continues in Apps Script if your PC
is switched off. SLP shows the new data when the local web server next runs.

## 2B. OPTIONAL: keep your existing Cloud Run UI

Only if you knowingly accept its EXISTING potential billing and have first
exported/preserved data: configure the existing Cloud Run service with:

- `SPORT_FREE_SCRIPT_URL` = the Apps Script `/exec` deployment URL
- `SPORT_FREE_SCRIPT_KEY` = SAME shared key from Script Properties
- `SPORT_GCS_BUCKET` = **unset** / blank
- `SPORT_GMAIL_AUTO_IMPORT` = `true`
- `SPORT_GMAIL_ENABLE_TEST_SEND` = `false`

The shared key must be entered privately into your hosting configuration,
not into GitHub and not into a chat. Preserve your app login configuration.
After deploying this code, SLP securely restores SQLite from Drive on
startup and refuses to start if an existing backup cannot be restored.
Mismatched concurrent revisions cannot silently overwrite Drive backups.

**Do not create the GCP bucket from the previous setup instructions.**
Do not add a paid Cloud Scheduler. Apps Script performs hourly Gmail intake.

## Channels

Only the approved 14-channel SLP registry is eligible. Of the supplier
Setanta attachments, accept ONLY:

- Setanta Sports 1 Kazakhstan => SETANTA SPORTS 1
- Setanta Sports 2 Kazakhstan => SETANTA SPORTS 2
- Setanta Qazaqstan => SETANTA SPORTS KZ

The mixed supplier message is processed **per attachment**. Setanta
Sports PLUS and Setanta Kyrgyzstan attachments are ignored, not treated
as missing. The parser remains strict about an explicit LIVE broadcast,
channel identity, timezone and conflicting EPG versions. First-time
unrecognized layouts go to editor review; they do not guess a channel.

## Known limits

- Apps Script polls once per hour, NOT instant push notification. Its free
  quotas and Gmail/Drive storage availability must be monitored.
- SLP ingests new archived mail on opening its browser UI, every 15 minutes
  while open, or on explicit Collect/Check mail. It cannot wake a sleeping
  Cloud Run service for free without separate scheduler infrastructure.
- The Apps Script manifest exposes the entire archived EPG history in stable
  append order, not just the last 21 days. SLP saves its import cursor and
  processes up to 100 archived attachments per sync; click Check mail again
  while there is a backlog. Repeated imports remain idempotent.
- The SLP interface reports the bridge as connected only after a successful
  signed pull. It separately warns if the Apps Script hourly scan is not
  observed within three hours. A Google OAuth consent email by itself does
  not establish that this free archive script has been deployed.
- The Drive bridge supports encrypted SQLite backups whose compressed
  contents are <=6 MiB; larger backups fail instead of silently falling
  back to a paid service. The approved XLSX template limit is 6 MiB.
- A script deployment and approval cannot be completed by ChatGPT without
  the owner's explicit Google account authorization.
- Do NOT delete the existing SLP instance or its archive until migration
  has been personally confirmed.

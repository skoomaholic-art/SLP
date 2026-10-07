#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-sport-live-parser}"
REGION="${REGION:-europe-west1}"
SERVICE="${SERVICE:-slp-web}"
AR_REPO="${AR_REPO:-slp}"
BUCKET="${BUCKET:-${PROJECT_ID}-slp-prod}"
RUNTIME_SA_NAME="${RUNTIME_SA_NAME:-slp-runtime}"
DEPLOYER_SA_NAME="${DEPLOYER_SA_NAME:-github-slp-deployer}"
SCHEDULER_SA_NAME="${SCHEDULER_SA_NAME:-slp-scheduler}"
WIF_POOL="${WIF_POOL:-github}"
WIF_PROVIDER="${WIF_PROVIDER:-github-slp}"
GITHUB_REPOSITORY="${GITHUB_REPOSITORY:-skoomaholic-art/SLP}"

RUNTIME_SA="${RUNTIME_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
DEPLOYER_SA="${DEPLOYER_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
SCHEDULER_SA="${SCHEDULER_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

need() { command -v "$1" >/dev/null 2>&1 || { echo "Missing required command: $1" >&2; exit 1; }; }
need gcloud
need python3

gcloud config set project "$PROJECT_ID" >/dev/null

echo "==> Enabling Google Cloud APIs"
gcloud services enable   run.googleapis.com   artifactregistry.googleapis.com   iamcredentials.googleapis.com   sts.googleapis.com   secretmanager.googleapis.com   storage.googleapis.com   cloudscheduler.googleapis.com   cloudresourcemanager.googleapis.com   iam.googleapis.com

PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"

echo "==> Creating Artifact Registry repository"
if ! gcloud artifacts repositories describe "$AR_REPO" --location="$REGION" >/dev/null 2>&1; then
  gcloud artifacts repositories create "$AR_REPO"     --repository-format=docker     --location="$REGION"     --description="SLP production images"
fi

echo "==> Creating production GCS bucket"
if ! gcloud storage buckets describe "gs://$BUCKET" >/dev/null 2>&1; then
  gcloud storage buckets create "gs://$BUCKET"     --location="$REGION"     --uniform-bucket-level-access
fi
gcloud storage buckets update "gs://$BUCKET" --versioning >/dev/null

create_sa() {
  local name="$1" display="$2"
  if ! gcloud iam service-accounts describe "$name@$PROJECT_ID.iam.gserviceaccount.com" >/dev/null 2>&1; then
    gcloud iam service-accounts create "$name" --display-name="$display"
  fi
}
create_sa "$RUNTIME_SA_NAME" "SLP Cloud Run runtime"
create_sa "$DEPLOYER_SA_NAME" "SLP GitHub deployer"
create_sa "$SCHEDULER_SA_NAME" "SLP Cloud Scheduler"

echo "==> Runtime permissions"
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET"   --member="serviceAccount:$RUNTIME_SA"   --role="roles/storage.objectAdmin" >/dev/null
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET"   --member="serviceAccount:$DEPLOYER_SA"   --role="roles/storage.objectAdmin" >/dev/null

for secret in slp-web-secret slp-web-users slp-free-script-key; do
  if ! gcloud secrets describe "$secret" >/dev/null 2>&1; then
    gcloud secrets create "$secret" --replication-policy=automatic
  fi
done

if ! gcloud secrets versions list slp-web-secret --filter='state=ENABLED' --format='value(name)' | grep -q .; then
  python3 - <<'PY' | gcloud secrets versions add slp-web-secret --data-file=-
import secrets
print(secrets.token_hex(48), end="")
PY
fi

if ! gcloud secrets versions list slp-web-users --filter='state=ENABLED' --format='value(name)' | grep -q .; then
  ADMIN_PASSWORD="${SLP_ADMIN_PASSWORD:-}"
  GENERATED=0
  if [ -z "$ADMIN_PASSWORD" ]; then
    ADMIN_PASSWORD="$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(18))
PY
)"
    GENERATED=1
  fi
  ADMIN_PASSWORD="$ADMIN_PASSWORD" python3 - <<'PY' | gcloud secrets versions add slp-web-users --data-file=-
import base64, hashlib, json, os, secrets
password = os.environ["ADMIN_PASSWORD"].encode()
salt = secrets.token_bytes(16)
digest = hashlib.scrypt(password, salt=salt, n=16384, r=8, p=1, dklen=32)
encoded = "scrypt$%s$%s" % (
    base64.b64encode(salt).decode(),
    base64.b64encode(digest).decode(),
)
print(json.dumps({
    "skoomaholic": {
        "password_hash": encoded,
        "role": "admin",
    }
}, ensure_ascii=False), end="")
PY
  if [ "$GENERATED" = "1" ]; then
    echo
    echo "IMPORTANT: generated Cloud SLP admin password:"
    echo "$ADMIN_PASSWORD"
    echo "Save it now. It is not written to the repository."
    echo
  fi
fi

gcloud projects add-iam-policy-binding "$PROJECT_ID"   --member="serviceAccount:$RUNTIME_SA"   --role="roles/secretmanager.secretAccessor" >/dev/null

echo "==> GitHub deployer permissions"
for role in   roles/run.admin   roles/artifactregistry.writer   roles/cloudscheduler.admin   roles/secretmanager.secretAccessor   roles/viewer; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID"     --member="serviceAccount:$DEPLOYER_SA"     --role="$role" >/dev/null
done

for sa in "$RUNTIME_SA" "$SCHEDULER_SA"; do
  gcloud iam service-accounts add-iam-policy-binding "$sa"     --member="serviceAccount:$DEPLOYER_SA"     --role="roles/iam.serviceAccountUser" >/dev/null
done

echo "==> Workload Identity Federation for GitHub"
if ! gcloud iam workload-identity-pools describe "$WIF_POOL" --location=global >/dev/null 2>&1; then
  gcloud iam workload-identity-pools create "$WIF_POOL"     --location=global     --display-name="GitHub Actions"
fi

if ! gcloud iam workload-identity-pools providers describe "$WIF_PROVIDER"   --workload-identity-pool="$WIF_POOL" --location=global >/dev/null 2>&1; then
  gcloud iam workload-identity-pools providers create-oidc "$WIF_PROVIDER"     --location=global     --workload-identity-pool="$WIF_POOL"     --display-name="SLP GitHub Actions"     --issuer-uri="https://token.actions.githubusercontent.com"     --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref"     --attribute-condition="assertion.repository=='$GITHUB_REPOSITORY'"
fi

WIF_RESOURCE="projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${WIF_POOL}/providers/${WIF_PROVIDER}"
PRINCIPAL="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${WIF_POOL}/attribute.repository/${GITHUB_REPOSITORY}"

gcloud iam service-accounts add-iam-policy-binding "$DEPLOYER_SA"   --member="$PRINCIPAL"   --role="roles/iam.workloadIdentityUser" >/dev/null

gcloud iam service-accounts add-iam-policy-binding "$DEPLOYER_SA"   --member="serviceAccount:$DEPLOYER_SA"   --role="roles/iam.serviceAccountTokenCreator" >/dev/null

echo "==> Scheduler can invoke the public Cloud Run service with OIDC"
# The service-level roles/run.invoker binding is added by the deploy workflow
# after the service exists.

echo "==> GitHub repository variables"
if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
  gh variable set GCP_PROJECT_ID --repo "$GITHUB_REPOSITORY" --body "$PROJECT_ID"
  gh variable set GCP_PROJECT_NUMBER --repo "$GITHUB_REPOSITORY" --body "$PROJECT_NUMBER"
  gh variable set GCP_REGION --repo "$GITHUB_REPOSITORY" --body "$REGION"
  gh variable set GCP_BUCKET --repo "$GITHUB_REPOSITORY" --body "$BUCKET"
  gh variable set GCP_WIF_PROVIDER --repo "$GITHUB_REPOSITORY" --body "$WIF_RESOURCE"
  gh variable set GCP_DEPLOYER_SERVICE_ACCOUNT --repo "$GITHUB_REPOSITORY" --body "$DEPLOYER_SA"
  gh variable set GCP_RUNTIME_SERVICE_ACCOUNT --repo "$GITHUB_REPOSITORY" --body "$RUNTIME_SA"
  gh variable set GCP_SCHEDULER_SERVICE_ACCOUNT --repo "$GITHUB_REPOSITORY" --body "$SCHEDULER_SA"
  echo "GitHub variables configured."
else
  cat <<EOF

GitHub CLI is not authenticated. Set these repository variables once:
GCP_PROJECT_ID=$PROJECT_ID
GCP_PROJECT_NUMBER=$PROJECT_NUMBER
GCP_REGION=$REGION
GCP_BUCKET=$BUCKET
GCP_WIF_PROVIDER=$WIF_RESOURCE
GCP_DEPLOYER_SERVICE_ACCOUNT=$DEPLOYER_SA
GCP_RUNTIME_SERVICE_ACCOUNT=$RUNTIME_SA
GCP_SCHEDULER_SERVICE_ACCOUNT=$SCHEDULER_SA
EOF
fi

cat <<EOF

Bootstrap complete.
Project: $PROJECT_ID
Region: $REGION
Bucket: gs://$BUCKET
Runtime SA: $RUNTIME_SA
Deployer SA: $DEPLOYER_SA
WIF provider: $WIF_RESOURCE

The existing slp-free-script-key secret is preserved when it already has an
enabled version. SPORT_FREE_SCRIPT_URL is configured by the deploy workflow.
EOF

#!/usr/bin/env bash
# One-time interactive setup. Run inside the owner's Google Cloud Shell.
# This never displays, commits, emails, or uploads OAuth credentials to GitHub.
set -euo pipefail
umask 077
PROJECT="sport-live-parser"
REGION="me-central1"
SERVICE="sport-epg"
ID_SECRET="slp-gmail-client-id"
CLIENT_SECRET="slp-gmail-client-secret"
TOKEN_SECRET="slp-gmail-token-key"

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
command -v gcloud >/dev/null || die "Открой Cloud Shell в Google Cloud Console."
gcloud run services describe "$SERVICE" --project="$PROJECT" --region="$REGION" --format=json > /tmp/slp-service-$$.json || die "Не найден существующий Cloud Run sport-epg."
service_file=/tmp/slp-service-$$.json
trap 'rm -f "$service_file"' EXIT
service_field() {
  python3 -c '
import json,sys
d=json.load(open(sys.argv[1]))
t=d.get("spec",{}).get("template",{})
if sys.argv[2]=="bucket":
    env=t.get("spec",t).get("containers",[{}])[0].get("env",[])
    print(next((str(x.get("value","")) for x in env if x.get("name")=="SPORT_GCS_BUCKET"),""))
elif sys.argv[2]=="sa":
    print(t.get("spec",{}).get("serviceAccountName") or t.get("serviceAccount") or "")
else:
    print(d.get("status",{}).get("url") or d.get("uri") or "")
' "$service_file" "$1"
}
CURRENT_BUCKET="$(service_field bucket)"
BUCKET="${SLP_GCS_BUCKET:-$CURRENT_BUCKET}"
SA="$(service_field sa)"
APP_URL="$(service_field url)"
[[ -n "$APP_URL" ]] || die "Cloud Run не вернул HTTPS адрес сервиса."
if [[ -z "$SA" ]]; then
  NUMBER="$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')"
  SA="${NUMBER}-compute@developer.gserviceaccount.com"
fi
printf 'Проект: %s\nСервис: %s\nАдрес: %s\n' "$PROJECT" "$SERVICE" "$APP_URL"

# A new revision replaces the current ephemeral /tmp SQLite instance.
if [[ -z "$CURRENT_BUCKET" ]]; then
  echo 'ВНИМАНИЕ: текущая тестовая SQLite база в Cloud Run временная.'
  echo 'Перед обновлением экспортируй архив из веб-интерфейса SLP.'
  read -r -p 'Экспорт сохранён? Введи АРХИВ (иначе остановка): ' confirm
  [[ "$confirm" == 'АРХИВ' ]] || die "Ничего не изменено. Сначала сохрани архив."
fi

echo 'GCS, Secret Manager и новый Cloud Run revision могут повлечь расходы.'
read -r -p 'Разрешаешь эту настройку и возможные расходы? Введи РАЗРЕШАЮ: ' bill_ok
[[ "$bill_ok" == 'РАЗРЕШАЮ' ]] || die "Без разрешения платные изменения не выполняются."
gcloud services enable gmail.googleapis.com secretmanager.googleapis.com storage.googleapis.com \
  --project="$PROJECT" --quiet

if [[ -z "$BUCKET" ]]; then
  NUMBER="$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')"
  BUCKET="slp-epg-${NUMBER}"
  if gcloud storage buckets describe "gs://$BUCKET" --project="$PROJECT" >/dev/null 2>&1; then
    echo "Используем уже созданное приватное хранилище $BUCKET."
  else
    echo 'Для надёжного хранения почтового OAuth-токена SLP нужен Cloud Storage.'
    echo 'Cloud Storage может повлечь расходы за хранение и операции.'
    read -r -p 'Разрешаешь создать хранилище? Напиши СОЗДАТЬ: ' consent
    [[ "$consent" == 'СОЗДАТЬ' ]] || die "Ничего не развернуто без разрешения."
    gcloud storage buckets create "gs://$BUCKET" --project="$PROJECT" \
        --location="$REGION" --default-storage-class=STANDARD \
        --uniform-bucket-level-access --public-access-prevention
  fi
fi
gcloud storage buckets describe "gs://$BUCKET" --project="$PROJECT" >/dev/null \
    || die "Хранилище недоступно."
# Bucket-level permission only for the existing runtime service account.
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" \
    --member="serviceAccount:$SA" --role="roles/storage.objectAdmin" >/dev/null

secret_exists() { gcloud secrets describe "$1" --project="$PROJECT" >/dev/null 2>&1; }
create_secret_if_missing() {
  local name="$1" value="$2"
  if secret_exists "$name"; then
    echo "Секрет $name уже существует - оставлен без изменений."
  else
    printf '%s' "$value" | gcloud secrets create "$name" \
      --project="$PROJECT" --replication-policy=automatic --data-file=- >/dev/null
    echo "Секрет $name создан."
  fi
}

if ! secret_exists "$ID_SECRET" || ! secret_exists "$CLIENT_SECRET"; then
  echo 'Нужен отдельный OAuth Web client из Google Auth Platform -> Clients.'
  echo "Authorized redirect URI: ${APP_URL}/api/gmail/callback"
  echo 'Если клиента ещё нет, останови скрипт (Ctrl+C), создай его и запусти снова.'
fi
if ! secret_exists "$ID_SECRET"; then
  read -r -p 'Вставь OAuth Web CLIENT ID: ' client_id
  [[ "$client_id" == *.apps.googleusercontent.com ]] || die "Неверный формат CLIENT ID."
  create_secret_if_missing "$ID_SECRET" "$client_id"
  unset client_id
fi
if ! secret_exists "$CLIENT_SECRET"; then
  read -r -s -p 'Вставь OAuth Web CLIENT SECRET (символы не показываются): ' client_secret
  echo
  [[ -n "$client_secret" ]] || die "Пустой CLIENT SECRET."
  create_secret_if_missing "$CLIENT_SECRET" "$client_secret"
  unset client_secret
fi
if ! secret_exists "$TOKEN_SECRET"; then
  token_key="$(python3 -c 'import os,base64;print(base64.urlsafe_b64encode(os.urandom(32)).decode())')"
  create_secret_if_missing "$TOKEN_SECRET" "$token_key"
  unset token_key
fi

for name in "$ID_SECRET" "$CLIENT_SECRET" "$TOKEN_SECRET"; do
  gcloud secrets add-iam-policy-binding "$name" --project="$PROJECT" \
    --member="serviceAccount:$SA" --role="roles/secretmanager.secretAccessor" \
    --quiet >/dev/null
done
# Do not remove existing app admin/login secrets when updating OAuth.
gcloud run services update "$SERVICE" --project="$PROJECT" --region="$REGION" \
  --max-instances=1 --concurrency=1 \
  --update-secrets="SPORT_GMAIL_CLIENT_ID=$ID_SECRET:latest,SPORT_GMAIL_CLIENT_SECRET=$CLIENT_SECRET:latest,SPORT_GMAIL_TOKEN_KEY=$TOKEN_SECRET:latest" \
  --update-env-vars="SPORT_GCS_BUCKET=$BUCKET,SPORT_PUBLIC_URL=$APP_URL,SPORT_GMAIL_AUTO_IMPORT=true,SPORT_GMAIL_ENABLE_TEST_SEND=false,SPORT_GMAIL_MAIL_MODE=test" \
  --quiet
echo "Cloud Run готов к Gmail OAuth. Теперь войди в SLP -> Gmail -> Подключить Gmail владельца."
echo "Обязательно авторизуй именно аккаунт, на который поступают пересланные EPG."
echo "Потом нажми Проверить новые письма. Фоновый Scheduler на этом шаге не создавался."

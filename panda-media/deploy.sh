#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="project-017b13a1-e57e-4723-a2b"
REGION="europe-west1"
SERVICE="panda-download"
SECRET="youtube-cookies"
COOKIE_MOUNT="/secrets/youtube-cookies.txt"
VERSION_FILE=".youtube-secret-version"

cd "$(dirname "$0")"
python3 -m py_compile \
  app_v6.py \
  app_v6_runtime.py \
  app_v6_cli_runtime.py \
  app_v6_playlist_runtime.py \
  app_v6_optimized_runtime.py

gcloud config set project "$PROJECT_ID"
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com

SECRET_ARGS=()
if gcloud secrets describe "$SECRET" >/dev/null 2>&1; then
  PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
  RUNTIME_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
  gcloud secrets add-iam-policy-binding "$SECRET" \
    --member="serviceAccount:${RUNTIME_SA}" \
    --role="roles/secretmanager.secretAccessor" \
    --quiet >/dev/null

  if [[ -f "$VERSION_FILE" ]]; then
    SECRET_VERSION="$(tr -d '[:space:]' < "$VERSION_FILE")"
  else
    SECRET_VERSION="$(gcloud secrets versions list "$SECRET" --filter='state=ENABLED' --sort-by='~createTime' --limit=1 --format='value(name)')"
  fi

  if [[ -z "$SECRET_VERSION" ]]; then
    echo "ERREUR: aucune version active du secret $SECRET."
    exit 1
  fi

  SECRET_ARGS+=(--update-secrets="${COOKIE_MOUNT}=${SECRET}:${SECRET_VERSION}")
  echo "YouTube cookies: secret ${SECRET} version ${SECRET_VERSION} monté et épinglé."
else
  echo "YouTube cookies: aucun secret ${SECRET}. Les vidéos publiques seront testées sans cookies."
fi

gcloud run deploy "$SERVICE" \
  --source . \
  --region "$REGION" \
  --allow-unauthenticated \
  --execution-environment gen2 \
  --memory 8Gi \
  --cpu 2 \
  --concurrency 4 \
  --timeout 3600 \
  --min-instances 0 \
  --max-instances 1 \
  --cpu-boost \
  --no-cpu-throttling \
  "${SECRET_ARGS[@]}"

URL="$(gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)')"
echo
echo "panda.download.com · V6.6 optimized"
echo "$URL"
echo
echo "Health: ${URL}/health"
curl -fsS "${URL}/health" || true
echo

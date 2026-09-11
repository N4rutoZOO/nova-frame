#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="project-017b13a1-e57e-4723-a2b"
REGION="europe-west1"
SERVICE="panda-download"
SECRET="youtube-cookies"
COOKIE_MOUNT="/secrets/youtube-cookies.txt"

cd "$(dirname "$0")"
python3 -m py_compile app_v5.py

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
  SECRET_ARGS+=(--update-secrets="${COOKIE_MOUNT}=${SECRET}:latest")
  echo "YouTube cookies: secret ${SECRET} détecté et monté."
else
  echo "YouTube cookies: aucun secret ${SECRET}."
fi

gcloud run deploy "$SERVICE" \
  --source . \
  --region "$REGION" \
  --allow-unauthenticated \
  --memory 4Gi \
  --cpu 2 \
  --concurrency 2 \
  --timeout 3600 \
  --max-instances 3 \
  "${SECRET_ARGS[@]}"

URL="$(gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)')"
echo
echo "panda.download.com · V5"
echo "$URL"
echo
echo "Health: ${URL}/health"

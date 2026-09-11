#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="project-017b13a1-e57e-4723-a2b"
REGION="europe-west1"
SERVICE="panda-download"
COOKIE_SECRET="youtube-cookies"
COOKIE_MOUNT="/secrets/youtube-cookies.txt"
COOKIE_VERSION_FILE=".youtube-secret-version"
WORKER_SECRET="panda-youtube-worker-token"
WORKER_INSTANCE="panda-youtube-worker"
WORKER_ZONE="europe-west1-b"
WORKER_PORT="8765"

cd "$(dirname "$0")"
python3 -m py_compile \
  app_v6.py \
  app_v6_runtime.py \
  app_v6_cli_runtime.py \
  app_v6_playlist_runtime.py \
  app_v6_optimized_runtime.py \
  app_v6_worker_runtime.py \
  app_v6_ui_runtime.py \
  youtube_worker_server.py

gcloud config set project "$PROJECT_ID"
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  compute.googleapis.com

PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
RUNTIME_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

SECRET_MAPPINGS=()
ENV_ARGS=()
VPC_ARGS=()

if gcloud secrets describe "$COOKIE_SECRET" >/dev/null 2>&1; then
  gcloud secrets add-iam-policy-binding "$COOKIE_SECRET" \
    --member="serviceAccount:${RUNTIME_SA}" \
    --role="roles/secretmanager.secretAccessor" \
    --quiet >/dev/null

  if [[ -f "$COOKIE_VERSION_FILE" ]]; then
    COOKIE_VERSION="$(tr -d '[:space:]' < "$COOKIE_VERSION_FILE")"
  else
    COOKIE_VERSION="$(gcloud secrets versions list "$COOKIE_SECRET" --filter='state=ENABLED' --sort-by='~createTime' --limit=1 --format='value(name)')"
  fi
  COOKIE_VERSION="${COOKIE_VERSION##*/}"

  if [[ -z "$COOKIE_VERSION" ]]; then
    echo "ERREUR: aucune version active du secret $COOKIE_SECRET."
    exit 1
  fi

  SECRET_MAPPINGS+=("${COOKIE_MOUNT}=${COOKIE_SECRET}:${COOKIE_VERSION}")
  echo "YouTube cookies Cloud Run: ${COOKIE_SECRET}:${COOKIE_VERSION}"
else
  echo "YouTube cookies Cloud Run: aucun secret."
fi

if gcloud compute instances describe "$WORKER_INSTANCE" --zone "$WORKER_ZONE" >/dev/null 2>&1 \
  && gcloud secrets describe "$WORKER_SECRET" >/dev/null 2>&1; then
  WORKER_IP="$(gcloud compute instances describe "$WORKER_INSTANCE" --zone "$WORKER_ZONE" --format='value(networkInterfaces[0].networkIP)')"
  if [[ -n "$WORKER_IP" ]]; then
    gcloud secrets add-iam-policy-binding "$WORKER_SECRET" \
      --member="serviceAccount:${RUNTIME_SA}" \
      --role="roles/secretmanager.secretAccessor" \
      --quiet >/dev/null
    SECRET_MAPPINGS+=("PANDA_YT_WORKER_TOKEN=${WORKER_SECRET}:latest")
    ENV_ARGS+=(--update-env-vars="PANDA_YT_WORKER_URL=http://${WORKER_IP}:${WORKER_PORT}")
    VPC_ARGS+=(--network=default --subnet=default --vpc-egress=private-ranges-only)
    echo "YouTube worker: http://${WORKER_IP}:${WORKER_PORT}"
  fi
else
  echo "YouTube worker: non configuré. Lance ./setup-youtube-worker.sh d'abord."
fi

SECRET_ARGS=()
if [[ ${#SECRET_MAPPINGS[@]} -gt 0 ]]; then
  SECRET_CSV="$(IFS=,; echo "${SECRET_MAPPINGS[*]}")"
  SECRET_ARGS+=(--update-secrets="$SECRET_CSV")
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
  "${VPC_ARGS[@]}" \
  "${ENV_ARGS[@]}" \
  "${SECRET_ARGS[@]}"

URL="$(gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)')"
echo
echo "panda.download.com · V6.8 web/mobile"
echo "$URL"
echo
echo "Health: ${URL}/health"
curl -fsS "${URL}/health" || true
echo

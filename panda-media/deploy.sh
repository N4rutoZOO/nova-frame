#!/usr/bin/env bash
set -euo pipefail
PROJECT_ID="project-017b13a1-e57e-4723-a2b"
REGION="europe-west1"
SERVICE="panda-media"
cd "$(dirname "$0")"
python3 -m py_compile main.py
gcloud config set project "$PROJECT_ID"
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com
gcloud run deploy "$SERVICE" \
  --source . \
  --region "$REGION" \
  --allow-unauthenticated \
  --memory 2Gi \
  --cpu 2 \
  --concurrency 1 \
  --timeout 3600 \
  --max-instances 2
URL=$(gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)')
echo ""
echo "PANDA MEDIA ONLINE: $URL"

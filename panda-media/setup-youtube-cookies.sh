#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="project-017b13a1-e57e-4723-a2b"
SECRET="youtube-cookies"
COOKIE_FILE="${1:-youtube-cookies.txt}"

if [[ ! -f "$COOKIE_FILE" ]]; then
  echo "Fichier introuvable: $COOKIE_FILE"
  echo "Usage: ./setup-youtube-cookies.sh /chemin/vers/youtube-cookies.txt"
  exit 1
fi

FIRST_LINE="$(head -n 1 "$COOKIE_FILE" | tr -d '\r')"
if [[ "$FIRST_LINE" != "# Netscape HTTP Cookie File" && "$FIRST_LINE" != "# HTTP Cookie File" ]]; then
  echo "Le fichier doit être au format Netscape cookies.txt."
  exit 1
fi

gcloud config set project "$PROJECT_ID"
gcloud services enable secretmanager.googleapis.com

if gcloud secrets describe "$SECRET" >/dev/null 2>&1; then
  gcloud secrets versions add "$SECRET" --data-file="$COOKIE_FILE"
else
  gcloud secrets create "$SECRET" --replication-policy="automatic" --data-file="$COOKIE_FILE"
fi

echo "Secret $SECRET mis à jour."
echo "Tu peux maintenant lancer: ./deploy.sh"

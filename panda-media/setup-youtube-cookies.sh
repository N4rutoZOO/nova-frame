#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="project-017b13a1-e57e-4723-a2b"
SECRET="youtube-cookies"
COOKIE_FILE="${1:-youtube-cookies.txt}"
FILTERED_FILE="$(mktemp)"
trap 'rm -f "$FILTERED_FILE"' EXIT

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

# yt-dlp exporte souvent tous les cookies du navigateur. Pour YouTube, on ne garde
# que les entrées youtube.com afin de rester sous la limite de 64 KiB de Secret Manager.
{
  echo "# Netscape HTTP Cookie File"
  awk 'BEGIN{FS="\t"} /^#/ {next} NF>=7 && $1 ~ /(^|\.)youtube\.com$/ {print}' "$COOKIE_FILE"
} > "$FILTERED_FILE"

COOKIE_COUNT="$(awk 'BEGIN{FS="\t"} !/^#/ && NF>=7 {c++} END{print c+0}' "$FILTERED_FILE")"
SIZE_BYTES="$(wc -c < "$FILTERED_FILE" | tr -d ' ')"

echo "Cookies YouTube conservés: $COOKIE_COUNT"
echo "Taille filtrée: $SIZE_BYTES octets"

if [[ "$COOKIE_COUNT" -eq 0 ]]; then
  echo "Aucun cookie youtube.com trouvé dans le fichier exporté."
  exit 1
fi

if [[ "$SIZE_BYTES" -gt 65536 ]]; then
  echo "Le fichier filtré dépasse encore 64 KiB."
  echo "Réexporte uniquement les cookies youtube.com depuis une session YouTube fraîche."
  exit 1
fi

gcloud config set project "$PROJECT_ID"
gcloud services enable secretmanager.googleapis.com

if gcloud secrets describe "$SECRET" >/dev/null 2>&1; then
  gcloud secrets versions add "$SECRET" --data-file="$FILTERED_FILE"
else
  gcloud secrets create "$SECRET" --replication-policy="automatic" --data-file="$FILTERED_FILE"
fi

echo "Secret $SECRET mis à jour avec uniquement les cookies youtube.com."
echo "Tu peux maintenant lancer: ./deploy.sh"

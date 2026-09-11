#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="project-017b13a1-e57e-4723-a2b"
SECRET="youtube-cookies"
COOKIE_FILE="${1:-youtube-cookies.txt}"
FILTERED_FILE="$(mktemp)"
VERSION_FILE=".youtube-secret-version"
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

# Keep only youtube.com cookies. yt-dlp recommends exporting a dedicated YouTube
# session; this also keeps the Secret Manager payload below the 64 KiB limit.
{
  echo "# Netscape HTTP Cookie File"
  awk 'BEGIN{FS="\t"} /^#/ {next} NF>=7 && $1 ~ /(^|\.)youtube\.com$/ {print}' "$COOKIE_FILE"
} > "$FILTERED_FILE"

COOKIE_COUNT="$(awk 'BEGIN{FS="\t"} !/^#/ && NF>=7 {c++} END{print c+0}' "$FILTERED_FILE")"
SIZE_BYTES="$(wc -c < "$FILTERED_FILE" | tr -d ' ')"

# Account cookies normally include at least one SAPISID/PAPISID-style token.
AUTH_COOKIE_COUNT="$(awk 'BEGIN{FS="\t"} !/^#/ && NF>=7 && ($6=="SAPISID" || $6=="APISID" || $6=="__Secure-1PAPISID" || $6=="__Secure-3PAPISID") {c++} END{print c+0}' "$FILTERED_FILE")"
SESSION_COOKIE_COUNT="$(awk 'BEGIN{FS="\t"} !/^#/ && NF>=7 && ($6=="SID" || $6=="HSID" || $6=="SSID" || $6=="LOGIN_INFO" || $6=="__Secure-1PSID" || $6=="__Secure-3PSID") {c++} END{print c+0}' "$FILTERED_FILE")"

echo "Cookies YouTube conservés: $COOKIE_COUNT"
echo "Cookies d'authentification détectés: $AUTH_COOKIE_COUNT"
echo "Cookies de session détectés: $SESSION_COOKIE_COUNT"
echo "Taille filtrée: $SIZE_BYTES octets"

if [[ "$COOKIE_COUNT" -eq 0 ]]; then
  echo "ERREUR: aucun cookie youtube.com trouvé."
  exit 1
fi

if [[ "$AUTH_COOKIE_COUNT" -eq 0 ]]; then
  echo "ERREUR: l'export ne ressemble pas à une session YouTube authentifiée."
  echo "Crée une session privée/incognito dédiée, connecte-toi à YouTube, exporte les cookies youtube.com puis ferme immédiatement la fenêtre privée."
  exit 1
fi

if [[ "$SIZE_BYTES" -gt 65536 ]]; then
  echo "ERREUR: le fichier filtré dépasse encore 64 KiB."
  echo "Exporte uniquement youtube.com depuis une session privée/incognito dédiée."
  exit 1
fi

gcloud config set project "$PROJECT_ID"
gcloud services enable secretmanager.googleapis.com

if gcloud secrets describe "$SECRET" >/dev/null 2>&1; then
  gcloud secrets versions add "$SECRET" --data-file="$FILTERED_FILE" >/dev/null
else
  gcloud secrets create "$SECRET" --replication-policy="automatic" --data-file="$FILTERED_FILE" >/dev/null
fi

# Pin the exact version in the next Cloud Run revision instead of silently following
# :latest. This prevents a newly uploaded bad cookie from changing a running revision.
VERSION="$(gcloud secrets versions list "$SECRET" --filter='state=ENABLED' --sort-by='~createTime' --limit=1 --format='value(name)')"
if [[ -z "$VERSION" ]]; then
  echo "ERREUR: impossible de retrouver la version du secret créée."
  exit 1
fi
printf '%s\n' "$VERSION" > "$VERSION_FILE"
chmod 600 "$VERSION_FILE"

echo "Secret $SECRET créé avec une session YouTube authentifiée."
echo "Version épinglée pour le prochain déploiement: $VERSION"
echo "IMPORTANT: ne rouvre pas cette session privée/incognito après l'export, sinon YouTube peut faire tourner les cookies."
echo "Tu peux maintenant lancer: ./deploy.sh"

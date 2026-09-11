#!/usr/bin/env bash
set -euo pipefail

ZONE="${PANDA_WORKER_ZONE:-europe-west1-b}"
INSTANCE="${PANDA_WORKER_INSTANCE:-panda-youtube-worker}"

URL="${1:-}"
if [[ -z "$URL" ]]; then
  read -r -p "URL YouTube: " URL
fi

case "$URL" in
  http://youtube.com/*|https://youtube.com/*|http://www.youtube.com/*|https://www.youtube.com/*|http://m.youtube.com/*|https://m.youtube.com/*|http://music.youtube.com/*|https://music.youtube.com/*|http://youtu.be/*|https://youtu.be/*) ;;
  *) echo "ERREUR: colle une vraie URL YouTube (pas URL_YOUTUBE)." >&2; exit 2 ;;
esac

REMOTE_URL="$(printf '%q' "$URL")"

gcloud compute ssh "$INSTANCE" \
  --zone="$ZONE" \
  --command="set -e; mkdir -p \"\$HOME/Downloads\"; DENO=\$(command -v deno || true); JS=(); if [[ -n \"\$DENO\" ]]; then JS=(--js-runtimes \"deno:\$DENO\"); fi; yt-dlp --cookies-from-browser \"chromium:\$HOME/chrome-profile\" \"\${JS[@]}\" -f 'bv*+ba/b' --merge-output-format mp4 --embed-metadata -o \"\$HOME/Downloads/%(title)s.%(ext)s\" $REMOTE_URL; echo; echo 'Téléchargé dans:'; ls -lhtr \"\$HOME/Downloads\" | tail -5"

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
  *) echo "ERREUR: colle une vraie URL YouTube." >&2; exit 2 ;;
esac

REMOTE_URL="$(printf '%q' "$URL")"

gcloud compute ssh "$INSTANCE" \
  --zone="$ZONE" \
  --command="set -euo pipefail
mkdir -p \"\$HOME/Downloads\"

if [[ -x /opt/panda-youtube-worker/venv/bin/yt-dlp ]]; then
  YTDLP=/opt/panda-youtube-worker/venv/bin/yt-dlp
elif command -v yt-dlp >/dev/null 2>&1; then
  YTDLP=\$(command -v yt-dlp)
elif [[ -x \"\$HOME/.local/bin/yt-dlp\" ]]; then
  YTDLP=\"\$HOME/.local/bin/yt-dlp\"
else
  echo 'yt-dlp absent: installation automatique...'
  python3 -m venv \"\$HOME/.panda-ytdlp\"
  \"\$HOME/.panda-ytdlp/bin/pip\" install -q --upgrade pip 'yt-dlp[default]>=2026.07.04,<2027'
  YTDLP=\"\$HOME/.panda-ytdlp/bin/yt-dlp\"
fi

DENO=''
for CANDIDATE in \"\$HOME/.deno/bin/deno\" /usr/local/deno/bin/deno /usr/bin/deno; do
  if [[ -x \"\$CANDIDATE\" ]]; then DENO=\"\$CANDIDATE\"; break; fi
done
JS=()
if [[ -n \"\$DENO\" ]]; then JS=(--js-runtimes \"deno:\$DENO\"); fi

\"\$YTDLP\" \
  --cookies-from-browser \"chromium:\$HOME/chrome-profile\" \
  \"\${JS[@]}\" \
  -f 'bv*+ba/b' \
  --merge-output-format mp4 \
  --embed-metadata \
  -o \"\$HOME/Downloads/%(title)s.%(ext)s\" \
  $REMOTE_URL

echo
echo 'OK - téléchargé sur la VM:'
ls -lhtr \"\$HOME/Downloads\" | tail -5
"
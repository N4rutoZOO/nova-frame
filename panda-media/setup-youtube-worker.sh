#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="project-017b13a1-e57e-4723-a2b"
REGION="europe-west1"
ZONE="europe-west1-b"
INSTANCE="panda-youtube-worker"
WORKER_USER="${PANDA_WORKER_USER:-gbeerus489}"
SECRET="panda-youtube-worker-token"
PORT="8765"

cd "$(dirname "$0")"

if [[ ! -f youtube_worker_server.py ]]; then
  echo "ERREUR: youtube_worker_server.py introuvable."
  exit 1
fi

if ! gcloud compute instances describe "$INSTANCE" --zone "$ZONE" >/dev/null 2>&1; then
  echo "ERREUR: la VM $INSTANCE n'existe pas dans $ZONE."
  exit 1
fi

gcloud config set project "$PROJECT_ID" >/dev/null
gcloud services enable compute.googleapis.com secretmanager.googleapis.com >/dev/null

TOKEN="$(openssl rand -hex 32)"
if gcloud secrets describe "$SECRET" >/dev/null 2>&1; then
  printf '%s' "$TOKEN" | gcloud secrets versions add "$SECRET" --data-file=- >/dev/null
else
  printf '%s' "$TOKEN" | gcloud secrets create "$SECRET" --replication-policy=automatic --data-file=- >/dev/null
fi

env_tmp="$(mktemp)"
service_tmp="$(mktemp)"
trap 'rm -f "$env_tmp" "$service_tmp"' EXIT

cat > "$env_tmp" <<EOF
PANDA_WORKER_TOKEN=$TOKEN
PANDA_CHROME_PROFILE=/home/$WORKER_USER/chrome-profile
PANDA_WORKER_PORT=$PORT
PANDA_PLAYLIST_LIMIT=100
PANDA_WORKER_JOB_TTL=3600
PANDA_WORKER_JOBS=1
PANDA_WORKER_FRAGMENTS=2
PATH=/opt/panda-youtube-worker/venv/bin:/home/$WORKER_USER/.deno/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
EOF

cat > "$service_tmp" <<EOF
[Unit]
Description=PANDA YouTube authenticated worker
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$WORKER_USER
Group=$WORKER_USER
WorkingDirectory=/opt/panda-youtube-worker
EnvironmentFile=/etc/panda-youtube-worker.env
ExecStart=/opt/panda-youtube-worker/venv/bin/python -m uvicorn youtube_worker_server:app --host 0.0.0.0 --port $PORT
Restart=always
RestartSec=3
TimeoutStopSec=15

[Install]
WantedBy=multi-user.target
EOF

echo "Envoi du worker sur la VM..."
gcloud compute scp youtube_worker_server.py "$INSTANCE:/tmp/youtube_worker_server.py" --zone "$ZONE" >/dev/null
gcloud compute scp "$env_tmp" "$INSTANCE:/tmp/panda-youtube-worker.env" --zone "$ZONE" >/dev/null
gcloud compute scp "$service_tmp" "$INSTANCE:/tmp/panda-youtube-worker.service" --zone "$ZONE" >/dev/null

echo "Installation du service worker..."
gcloud compute ssh "$INSTANCE" --zone "$ZONE" --command="
set -e
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y python3-venv curl ca-certificates ffmpeg chromium unzip >/dev/null
mkdir -p /home/$WORKER_USER/chrome-profile /home/$WORKER_USER/Downloads
if [[ ! -x /home/$WORKER_USER/.deno/bin/deno ]]; then
  DENO_INSTALL=/home/$WORKER_USER/.deno curl -fsSL https://deno.land/install.sh | sh >/dev/null
fi
sudo mkdir -p /opt/panda-youtube-worker
sudo mv /tmp/youtube_worker_server.py /opt/panda-youtube-worker/youtube_worker_server.py
sudo chown -R $WORKER_USER:$WORKER_USER /opt/panda-youtube-worker
if [[ ! -x /opt/panda-youtube-worker/venv/bin/python ]]; then
  python3 -m venv /opt/panda-youtube-worker/venv
fi
/opt/panda-youtube-worker/venv/bin/pip install -q --upgrade pip
/opt/panda-youtube-worker/venv/bin/pip install -q 'fastapi>=0.103,<1' 'uvicorn[standard]>=0.23,<1' 'yt-dlp[default]>=2026.07.04,<2027'
sudo mv /tmp/panda-youtube-worker.env /etc/panda-youtube-worker.env
sudo mv /tmp/panda-youtube-worker.service /etc/systemd/system/panda-youtube-worker.service
sudo chown root:root /etc/panda-youtube-worker.env /etc/systemd/system/panda-youtube-worker.service
sudo chmod 600 /etc/panda-youtube-worker.env
sudo systemctl daemon-reload
sudo systemctl enable --now panda-youtube-worker
sleep 4
curl -fsS http://127.0.0.1:$PORT/health
" 

echo
SUBNET_RANGE="$(gcloud compute networks subnets describe default --region "$REGION" --format='value(ipCidrRange)')"
if [[ -z "$SUBNET_RANGE" ]]; then
  echo "ERREUR: impossible de lire le CIDR du subnet default."
  exit 1
fi

if gcloud compute firewall-rules describe panda-youtube-worker-internal >/dev/null 2>&1; then
  gcloud compute firewall-rules update panda-youtube-worker-internal \
    --allow="tcp:${PORT}" \
    --source-ranges="$SUBNET_RANGE" \
    --target-tags=panda-youtube-worker \
    --quiet >/dev/null
else
  gcloud compute firewall-rules create panda-youtube-worker-internal \
    --network=default \
    --direction=INGRESS \
    --action=ALLOW \
    --rules="tcp:${PORT}" \
    --source-ranges="$SUBNET_RANGE" \
    --target-tags=panda-youtube-worker \
    --quiet >/dev/null
fi

WORKER_IP="$(gcloud compute instances describe "$INSTANCE" --zone "$ZONE" --format='value(networkInterfaces[0].networkIP)')"
echo
echo "===================================="
echo "PANDA YOUTUBE WORKER READY"
echo "VM: $INSTANCE"
echo "Internal: http://$WORKER_IP:$PORT"
echo "Secret: $SECRET"
echo "Deno + yt-dlp + Chromium profile: OK"
echo "===================================="

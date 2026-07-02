#!/usr/bin/env bash
# One-shot provisioning for a fresh Ubuntu 24.04 droplet. Run as root:
#   curl -fsSL https://raw.githubusercontent.com/ryuoraiden/nitc-bot/main/deploy/setup.sh | bash
set -euo pipefail

REPO="https://github.com/ryuoraiden/nitc-bot.git"
APP_DIR="/opt/nitc-bot"

echo ">>> Installing packages"
apt-get update -qq
apt-get install -y -qq git python3-venv python3-pip

# Small droplets (512MB) need swap so pip installs and spikes don't OOM.
if [ ! -f /swapfile ]; then
  echo ">>> Adding 1G swap"
  fallocate -l 1G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile >/dev/null
  swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

echo ">>> Creating service user"
id -u nitcbot &>/dev/null || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin nitcbot

echo ">>> Cloning repo"
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" pull --ff-only
else
  git clone "$REPO" "$APP_DIR"
fi

echo ">>> Setting up venv"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -q --upgrade pip
"$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

mkdir -p "$APP_DIR/data"
chown -R nitcbot:nitcbot "$APP_DIR"

echo ">>> Installing systemd service"
cp "$APP_DIR/deploy/nitc-bot.service" /etc/systemd/system/nitc-bot.service
systemctl daemon-reload
systemctl enable nitc-bot >/dev/null

cat <<'EOF'

Done. Two things left:

1. Put your .env at /opt/nitc-bot/.env (scp it from your machine):
     scp .env root@YOUR_DROPLET_IP:/opt/nitc-bot/.env
   Optionally bring the existing database too:
     scp data/bot.db root@YOUR_DROPLET_IP:/opt/nitc-bot/data/bot.db
   Then fix ownership:
     chown nitcbot:nitcbot /opt/nitc-bot/.env /opt/nitc-bot/data/bot.db 2>/dev/null

2. Start it:
     systemctl start nitc-bot
     journalctl -u nitc-bot -f     # watch the logs

Updating later is:  /opt/nitc-bot/deploy/update.sh
EOF

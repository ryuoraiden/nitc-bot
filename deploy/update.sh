#!/usr/bin/env bash
# Pull the latest code and restart the bot. Run as root on the droplet.
set -euo pipefail
APP_DIR="/opt/nitc-bot"

git -C "$APP_DIR" pull --ff-only
"$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"
chown -R nitcbot:nitcbot "$APP_DIR"
systemctl restart nitc-bot
echo "Updated and restarted. Logs: journalctl -u nitc-bot -f"

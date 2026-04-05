#!/usr/bin/env bash
set -euo pipefail

export HOME="/home/commit"
export PATH="/usr/local/bin:/usr/bin:/bin:/home/commit/bin"

LOG_DIR="$HOME/.openclaw/logs"
LOG_FILE="$LOG_DIR/openclaw-gateway-autostart.log"
LOCK_FILE="/tmp/openclaw-gateway-autostart.lock"

mkdir -p "$LOG_DIR"
exec >>"$LOG_FILE" 2>&1

echo "[$(date --iso-8601=seconds)] openclaw gateway autostart begin"

sleep 15

exec /usr/bin/flock -n "$LOCK_FILE" /home/commit/bin/openclaw gateway run --allow-unconfigured

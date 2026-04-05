#!/usr/bin/env bash
set -euo pipefail

export HOME="/home/commit"
export PATH="/usr/local/bin:/usr/bin:/bin:/home/commit/bin"

LOG_DIR="$HOME/.openclaw/logs"
LOG_FILE="$LOG_DIR/cognee-autostart.log"
COMPOSE_FILE="/home/commit/cognee-mcp/docker-compose.yml"
COGNEE_DATA_ROOT="$HOME/.openclaw/cognee"

mkdir -p "$LOG_DIR"
mkdir -p \
  "$COGNEE_DATA_ROOT/api/system" \
  "$COGNEE_DATA_ROOT/api/data" \
  "$COGNEE_DATA_ROOT/api/cache"
exec >>"$LOG_FILE" 2>&1

echo "[$(date --iso-8601=seconds)] cognee autostart begin"

for _ in $(seq 1 60); do
  if /usr/bin/docker info >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

cd /home/commit/cognee-mcp
/usr/bin/docker compose -f "$COMPOSE_FILE" up -d

echo "[$(date --iso-8601=seconds)] cognee autostart complete"

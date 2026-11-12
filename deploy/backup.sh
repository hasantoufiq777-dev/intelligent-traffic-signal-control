#!/usr/bin/env bash
# Daily online backup of the gateway database; keeps the last KEEP_DAYS days.
# Cron (as the itsc user):  15 3 * * *  /srv/itsc/deploy/backup.sh >> /var/lib/itsc/backup.log 2>&1
# Settings come from the same file as the service: /etc/itsc/gateway.env (DB_PATH).
set -euo pipefail

APP_DIR="${APP_DIR:-/srv/itsc}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/itsc}"
KEEP_DAYS="${KEEP_DAYS:-14}"
ENV_FILE="${ENV_FILE:-/etc/itsc/gateway.env}"

if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi

mkdir -p "$BACKUP_DIR"
target="$BACKUP_DIR/gateway-$(date +%Y%m%d-%H%M%S).db"
cd "$APP_DIR/server"
npm run backup --silent -- "$target"          # SQLite online backup: safe while the gateway runs
gzip -9 "$target"
find "$BACKUP_DIR" -name 'gateway-*.db.gz' -mtime +"$KEEP_DAYS" -delete
echo "$(date -Is) backup ok: $target.gz"

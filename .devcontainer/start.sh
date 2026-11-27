#!/usr/bin/env bash
# Runs every time the codespace starts: builds the image if needed, starts the system, makes port
# 4100 public and prints the link and the administrator login.
set -uo pipefail
cd "$(dirname "$0")/.."

# Docker inside the codespace may need a moment after a start
for _ in $(seq 1 30); do docker info >/dev/null 2>&1 && break; sleep 2; done

echo "[start] building and starting the system (the first time takes several minutes)..."
docker compose up --build -d itsc

echo "[start] waiting for the system to report healthy..."
for _ in $(seq 1 60); do
  curl -fsS http://localhost:4100/api/health >/dev/null 2>&1 && break
  sleep 5
done

URL="https://${CODESPACE_NAME:-local}-4100.${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN:-app.github.dev}"
if [ -n "${CODESPACE_NAME:-}" ]; then
  if gh codespace ports visibility 4100:public -c "$CODESPACE_NAME" >/dev/null 2>&1; then
    PUBLIC="public"
  else
    PUBLIC="private: open the PORTS tab, right-click port 4100 -> Port Visibility -> Public"
  fi
else
  PUBLIC="(not in a codespace)"
fi

EMAIL="$(grep '^ADMIN_EMAIL=' .env | cut -d= -f2-)"
PASSWORD="$(grep '^ADMIN_PASSWORD=' .env | cut -d= -f2-)"
cat <<EOF

==================================================================
 VANET Traffic is running
   Link:      ${URL}
   Link is:   ${PUBLIC}
   Sign in:   ${EMAIL} / ${PASSWORD}
 Phone app:   open the link, then "Install app" (Android) or
              Share -> Add to Home Screen (iPhone)
 Logs:        docker compose logs -f itsc
 Show again:  bash .devcontainer/start.sh
==================================================================
EOF

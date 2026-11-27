#!/usr/bin/env bash
# Runs once when the codespace is created: writes .env (ignored by git) with the settings for
# Codespaces and a generated administrator password. An existing .env is kept.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -f .env ]; then
  echo "[setup] .env exists, keeping it"
  exit 0
fi

PASSWORD="$(head -c 18 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 16)"
# The public address of port 4100 in this codespace (the browser's Origin for sign-in and the live feed)
ORIGIN="https://${CODESPACE_NAME:-local}-4100.${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN:-app.github.dev}"

cp .env.docker.example .env
sed -i \
  -e "s|^ADMIN_EMAIL=.*|ADMIN_EMAIL=admin@itsc.local|" \
  -e "s|^ADMIN_PASSWORD=.*|ADMIN_PASSWORD=${PASSWORD}|" \
  -e "s|^HOST_PORT=.*|HOST_PORT=4100|" \
  -e "s|^COOKIE_SECURE=.*|COOKIE_SECURE=false|" \
  -e "s|^TRUST_PROXY=.*|TRUST_PROXY=1|" \
  .env
cat >> .env <<EOF

# ---- GitHub Codespaces (written by .devcontainer/setup.sh) ----
# Requests arrive through the Codespaces proxy: accept this codespace's public address as the site
CORS_ORIGIN=${ORIGIN}
EOF
chmod 600 .env
echo "[setup] wrote .env for ${ORIGIN}"

#!/usr/bin/env bash
# One-time set-up of a fresh Ubuntu server (Azure for Students, DigitalOcean, Hetzner ...) for the
# Docker deployment. Run as a user with sudo:
#   curl -fsSL https://raw.githubusercontent.com/<owner>/<repo>/main/deploy/server-setup.sh | bash -s -- <repo-url>
# or, after cloning:  bash deploy/server-setup.sh
# Then: edit ~/itsc/.env and run  docker compose -f docker-compose.prod.yml up --build -d
set -euo pipefail

REPO_URL="${1:-}"
APP_DIR="$HOME/itsc"

echo "== Docker"
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sudo sh
  sudo usermod -aG docker "$USER"
fi

echo "== Firewall: SSH, HTTP, HTTPS only"
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw --force enable

echo "== Swap (SUMO and the image build need memory on small servers)"
if ! swapon --show | grep -q .; then
  sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
  sudo mkswap /swapfile && sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

echo "== Code"
if [ -n "$REPO_URL" ] && [ ! -d "$APP_DIR" ]; then
  git clone "$REPO_URL" "$APP_DIR"
fi
if [ -d "$APP_DIR" ] && [ ! -f "$APP_DIR/.env" ]; then
  cp "$APP_DIR/.env.docker.example" "$APP_DIR/.env"
  chmod 600 "$APP_DIR/.env"
fi

IP="$(curl -fsS https://api.ipify.org || true)"
cat <<EOF

Done. Next:
  1. Log out and back in (so your user can run docker).
  2. Edit $APP_DIR/.env: ADMIN_EMAIL, ADMIN_PASSWORD and
       DOMAIN=${IP//./-}.sslip.io      (or your own domain pointing to ${IP:-this server})
  3. cd $APP_DIR && docker compose -f docker-compose.prod.yml up --build -d
  4. Open https://<DOMAIN> and sign in. Then remove ADMIN_PASSWORD from .env and run step 3 again.
EOF

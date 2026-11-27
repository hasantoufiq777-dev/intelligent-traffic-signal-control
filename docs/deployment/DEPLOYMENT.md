# Deployment guide

How to run the platform on a Linux server for real users: one machine runs nginx (HTTPS, the static
dashboard), the Node gateway and the Python + SUMO simulation it starts. The files referred to are in
[`deploy/`](../../deploy/).

What was verified, and what was not, is listed in §12.

## 1. Architecture

```
browser ──HTTPS/WSS──► nginx :443 ──► /           static dashboard (client/dist)
                                  ├──► /api/       gateway 127.0.0.1:4000 (Express)
                                  └──► /socket.io/ gateway 127.0.0.1:4000 (Socket.io, WebSocket)
                       gateway ──stdin/stdout JSON lines──► python sim/main.py ──TraCI──► SUMO
                       gateway ──► SQLite database (/var/lib/itsc/gateway.db)
```

Decisions behind this layout:

- **One server, one gateway.** The simulation is a long-running child process of the gateway, so
  the gateway must run on a machine that keeps processes alive (a VPS or a dedicated server).
  Serverless functions cannot host it. One gateway serves up to `MAX_CONNECTIONS` (1,000) viewers;
  that was load-tested ([Phase 8](../testing/PHASE_8_REPORT.md), [Phase 9](../testing/PHASE_9_REPORT.md)).
- **SQLite, not PostgreSQL.** The gateway is the only writer, and the database is a single file
  with online backup. PostgreSQL would only be needed with several gateways; it is not implemented.
- **The dashboard and the API on the same site.** Sessions use an `HttpOnly`, `Secure`,
  `SameSite=Lax` cookie, and the gateway accepts a browser request only when its `Origin` matches
  the site.
  - Serve the dashboard from the same host as the API (this guide), or from a sibling subdomain of
    the same domain. In that case set `VITE_API_URL=https://api.example.org` when building and
    `CORS_ORIGIN=https://app.example.org` on the gateway.
  - Hosting the dashboard on a different site (for example `*.vercel.app` with the API elsewhere)
    does not work: browsers do not send `SameSite=Lax` cookies across sites.
- **No Redis.** The connection limit and the Socket.io rooms are in-process. Several gateways
  behind a load balancer would need the Socket.io Redis adapter, a shared connection count and a
  shared database. That is not implemented or tested.

## 2. Server requirements

| Item | Minimum | Tested with |
|---|---|---|
| OS | Linux with systemd (Ubuntu 22.04/24.04 or Debian 12) | Windows 11 for all tests (see §12) |
| CPU / RAM | 2 vCPU / 2 GB | gateway RSS 184–258 MB at 1,000 viewers; one SUMO run ≈ 1 core at the default pace |
| Node.js | 20 LTS or newer (Argon2id needs Node ≥ 24.7; older versions use scrypt) | 24.14.1 |
| Python | 3.10+ | 3.12 |
| SUMO | 1.18+ | 1.28 |
| nginx | 1.25+ (`http2 on;` syntax) | not tested (§12) |
| Disk | 2 GB + backups | the 1.6 GB test database held 382,348 state samples over 11 days |

## 3. Install

```bash
# packages (Ubuntu)
sudo apt update
sudo apt install -y nginx python3-venv git sumo sumo-tools certbot python3-certbot-nginx
# Node.js 24 LTS from NodeSource or the official tarball; check: node -v

# service user and folders
sudo useradd --system --home /srv/itsc --shell /usr/sbin/nologin itsc
sudo mkdir -p /srv/itsc /var/lib/itsc /var/backups/itsc /etc/itsc
sudo chown itsc:itsc /srv/itsc /var/lib/itsc /var/backups/itsc

# code (a release tag or a commit you have tested)
sudo -u itsc git clone <repository url> /srv/itsc
cd /srv/itsc
sudo -u itsc git checkout <tag-or-commit>

# simulation
sudo -u itsc python3 -m venv .venv
sudo -u itsc .venv/bin/pip install -r sim/requirements.txt
SUMO_HOME=/usr/share/sumo sudo -E -u itsc .venv/bin/python sim/smoke_test.py   # SUMO + TraCI work

# gateway and dashboard
sudo -u itsc npm ci --prefix server --omit=dev
sudo -u itsc npm ci --prefix client
sudo -u itsc npm run build --prefix client        # writes client/dist
```

## 4. Configuration

```bash
sudo install -m 640 -o root -g itsc deploy/gateway.env.production.example /etc/itsc/gateway.env
sudo nano /etc/itsc/gateway.env      # ADMIN_EMAIL, ADMIN_PASSWORD (first start only)
```

[`deploy/gateway.env.production.example`](../../deploy/gateway.env.production.example) has every setting with
production values and comments. Settings that matter most:

| Variable | Production value | Why |
|---|---|---|
| `NODE_ENV` | `production` | Secure cookies and HSTS by default; a warning if `COOKIE_SECURE` is turned off |
| `HOST` / `PORT` | `127.0.0.1` / `4000` | only nginx reaches the gateway |
| `TRUST_PROXY` | `1` | client addresses (rate limits, lockout, session list) come from nginx's `X-Forwarded-For` |
| `COOKIE_SECURE` | `true` | session cookie only over HTTPS; sign-in over plain HTTP will not keep a session |
| `CORS_ORIGIN` | empty | dashboard on the same origin; list extra `https://` origins only if needed |
| `ADMIN_EMAIL` / `ADMIN_PASSWORD` | set once | first administrator; remove the password after the first start |
| `ALLOW_REGISTRATION` | `false` | administrators create accounts; can be changed in the dashboard |
| `MAX_CONNECTIONS` | `1000` | hard cap on live connections; more are refused with a clear message |
| `DB_PATH` | `/var/lib/itsc/gateway.db` | outside the code folder, so deployments never touch it |
| `SAMPLE_RETENTION_DAYS` / `AUDIT_RETENTION_DAYS` | `14` / `180` | telemetry and audit log retention; runs, results and events are kept |
| `PYTHON_BIN`, `SUMO_HOME` | the venv's python, `/usr/share/sumo` | the simulation process |
| `LIVE_LOOP` | `true` | play the playlist continuously |

All variables, with defaults, are validated at start-up ([`server/src/config.js`](../../server/src/config.js)); an
invalid value stops the gateway with a message naming the variable.

## 5. Database and migrations

Nothing to create by hand. At every start the gateway opens `DB_PATH` (creating it if missing),
switches it to WAL mode and applies any schema migrations not yet recorded in
`schema_migrations`:

| Version | Content |
|---|---|
| 1 | runs, results, events, state samples, driver trips |
| 2 | `runs.eco_fraction`, `runs.ev_routing` |
| 3 | users, sessions, password resets, audit log, settings, `runs.started_by` |
| 4 | index `state_samples.created_at` (retention) |
| 5 | `runs.demand` (traffic demand factor) |

Each migration runs once, inside a transaction. Migrations only add tables, columns and indexes,
so an older gateway can still open a newer database (tested, §9). On a large existing database the
first start of a new version can take a moment: migration 4 took 1.4 s on a 1.6 GB file.

To see the applied versions: *Administration → System* (System health) in the dashboard, or `GET /api/system` as an
administrator (`database.migrations`).

## 6. Start, stop and process management

```bash
sudo cp deploy/itsc-gateway.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now itsc-gateway
sudo systemctl status itsc-gateway
journalctl -u itsc-gateway -f                 # logs
```

The unit ([`deploy/itsc-gateway.service`](../../deploy/itsc-gateway.service)):

- restarts the gateway after a crash, 5 s later;
- raises the open-file limit to 65,535 (each live connection is a file descriptor; the default
  1,024 fails before 1,000 viewers);
- lets the service write only the database folder, `results/` and `sim/sumo/` (route files of the
  current run).

`systemctl stop` sends SIGTERM. The gateway then stops the simulation (the whole Python + SUMO
process tree), closes the sockets and the database, and exits within 10 s.

After the first start, sign in with the first administrator, change the password in *Account*,
delete the `ADMIN_PASSWORD` line from `/etc/itsc/gateway.env` and restart. Further
administrators: in the dashboard (*Administration → Users*), or on the server (asks for the password):

```bash
sudo -u itsc bash -c 'set -a; . /etc/itsc/gateway.env; set +a; cd /srv/itsc/server && npm run create-admin -- admin2@example.org "Full Name"'
```

## 7. Reverse proxy, WebSockets and HTTPS

```bash
sudo certbot certonly --nginx -d traffic.example.org
sudo cp deploy/nginx.conf /etc/nginx/sites-available/itsc      # replace traffic.example.org
sudo ln -s /etc/nginx/sites-available/itsc /etc/nginx/sites-enabled/itsc
sudo nginx -t && sudo systemctl reload nginx
```

[`deploy/nginx.conf`](../../deploy/nginx.conf):

- redirects HTTP to HTTPS (TLS 1.2/1.3);
- serves `client/dist`, with build files cached for a year and `index.html` never cached;
- proxies `/api/` and `/socket.io/`, the latter with the WebSocket upgrade headers and a 120 s
  read timeout (longer than the 25 s ping interval).

Three settings to keep:

- **`proxy_set_header Host $http_host`.** The gateway refuses a browser request whose `Origin`
  does not match `Host`. `$host` drops a non-standard port, which would make every sign-in fail.
- **`X-Forwarded-For $remote_addr`** together with `TRUST_PROXY=1`. The gateway then sees the
  real client address, and a client cannot fake it.
- **`worker_connections 4096`** and **`worker_rlimit_nofile 65535`** in `/etc/nginx/nginx.conf`.
  Each viewer uses two connections in nginx.

Open only ports 80 and 443 in the firewall (`sudo ufw allow 'Nginx Full'`); port 4000 stays local.
Certificates renew through certbot's timer; check with `sudo certbot renew --dry-run`.

## 8. Health checks and monitoring

| Check | How | Healthy |
|---|---|---|
| Liveness | `curl -fsS https://traffic.example.org/api/health` | `{"ok":true, "lifecycle": "RUNNING", ...}` (no sign-in needed) |
| Simulation | same response: `lifecycle`, `live_running` | `RUNNING`; `FAILED` after `MAX_SIM_FAILURES` failed runs in a row |
| Load and resources | *Administration → System*, or `GET /api/metrics` (administrator) | connections, CPU, memory, event-loop lag, broadcast counters, `persist_errors` |
| Logs | `journalctl -u itsc-gateway`, `/var/log/nginx/itsc.*.log` | warnings: failed runs, capacity refusals (at most one line per minute), database write errors |

An external uptime monitor can poll `/api/health` every minute and alert when it fails or reports
`FAILED`.

## 9. Backup and restore

**Backup** ([`deploy/backup.sh`](../../deploy/backup.sh)). It uses SQLite's online backup, so it is
safe while the gateway runs. Backups are gzipped, and files older than 14 days are deleted.

```bash
sudo cp -r deploy /srv/itsc/ 2>/dev/null || true          # already there after git clone
sudo -u itsc crontab -e
# 15 3 * * *  /srv/itsc/deploy/backup.sh >> /var/lib/itsc/backup.log 2>&1
```

Copy `/var/backups/itsc` to another machine as well (for example `rsync` to off-site storage); a
backup on the same disk does not survive a disk failure.

**Restore**

```bash
sudo systemctl stop itsc-gateway
sudo -u itsc cp /var/lib/itsc/gateway.db /var/lib/itsc/gateway.db.before-restore   # keep the current one
gunzip -c /var/backups/itsc/gateway-YYYYMMDD-HHMMSS.db.gz | sudo -u itsc tee /var/lib/itsc/gateway.db >/dev/null
sudo rm -f /var/lib/itsc/gateway.db-wal /var/lib/itsc/gateway.db-shm
sudo systemctl start itsc-gateway
```

A run that was in progress when the backup was taken is marked *interrupted* at start; accounts,
runs, results and events are as they were at backup time. Sessions stored in the backup stay
valid until they expire (`SESSION_TTL_HOURS`); *Administration → Sessions* lists them and signs out
any one of them.

## 10. Updating and rollback

**Update**

```bash
sudo -u itsc /srv/itsc/deploy/backup.sh                    # backup first
cd /srv/itsc && sudo -u itsc git fetch && sudo -u itsc git checkout <new-tag>
sudo -u itsc npm ci --prefix server --omit=dev
sudo -u itsc npm ci --prefix client && sudo -u itsc npm run build --prefix client
sudo -u itsc .venv/bin/pip install -r sim/requirements.txt
sudo systemctl restart itsc-gateway                       # applies new migrations
curl -fsS https://traffic.example.org/api/health
```

Viewers reconnect by themselves after the restart. In the load test, 500 viewers dropped at once
were all back within 0.34 s.

**Rollback** to the previous version:

```bash
cd /srv/itsc && sudo -u itsc git checkout <previous-tag>
sudo -u itsc npm ci --prefix server --omit=dev
sudo -u itsc npm ci --prefix client && sudo -u itsc npm run build --prefix client
sudo systemctl restart itsc-gateway
```

The database does not need to be rolled back: migrations only add tables, columns and indexes, and
an older gateway ignores what it does not know. Restore the pre-update backup (§9) only if the new
version wrote data you do not want to keep.

## 11. Security checklist

- [ ] HTTPS only; `NODE_ENV=production`, `COOKIE_SECURE=true`, `TRUST_PROXY=1`
- [ ] Gateway bound to `127.0.0.1`; firewall allows only 80/443 (and SSH)
- [ ] `ADMIN_PASSWORD` removed from `/etc/itsc/gateway.env` after the first start; file mode 640
- [ ] `ALLOW_REGISTRATION=false` unless public sign-up is wanted
- [ ] `CORS_ORIGIN` empty, or only your own `https://` origins
- [ ] Backups run daily and are copied off the server; a restore has been tried once
- [ ] `npm audit --omit=dev` in `server/` and `client/` shows no high or critical issues before each update
- [ ] OS security updates enabled (`unattended-upgrades`)

## 12. What was verified

| Item | How | Result |
|---|---|---|
| Production configuration | `tests/deploy/prodcheck.mjs` against a gateway started with `deploy/gateway.env.production.example` values (Windows paths), real SUMO, behind a local HTTPS proxy (`tests/deploy/tls-proxy.mjs`, self-signed certificate) | 8/8: security headers, CSP limited to this host, Secure/HttpOnly/SameSite cookie, other-site requests and sockets refused, JSON errors without internals, gzip and caching, browser sign-in with live cars over HTTPS/WSS |
| Online backup → restore | `deploy/backup.sh` (Git Bash) and `npm run backup` against the running production test gateway; restored copy started and signed in | integrity `ok`, migrations 1–4, accounts and runs present, in-progress run marked interrupted |
| Code rollback over a newer database | phase 8 code (`daec358`) started on the database with migration 4 | signs in, lists runs, starts and records a new run |
| Development set-up | dashboard dev server (Vite) in front of a gateway; browser smoke journey | 10/10 (this found and fixed a sign-in failure through the dev server, see the acceptance report) |

**Not verified:** a deployment on Linux, nginx itself, systemd, certbot, and a real certificate.
All tests ran on Windows 11, because this machine's Ubuntu (WSL) has no passwordless `sudo` for
installing packages. The nginx file, the systemd unit and the install commands follow the standard
documented behaviour of those tools, but they have not been run. Try them on a staging server
before relying on them.

## 13. Docker

The whole system also runs as one container: the dashboard, the Node gateway and the Python + SUMO
simulation. Files: [`Dockerfile`](../../Dockerfile), [`docker-compose.yml`](../../docker-compose.yml),
[`.env.docker.example`](../../.env.docker.example), [`.dockerignore`](../../.dockerignore).

```bash
cp .env.docker.example .env        # fill in ADMIN_EMAIL and ADMIN_PASSWORD (10+ characters)
docker compose up --build -d       # first build: a few minutes (downloads SUMO)
# open http://localhost:4100 and sign in with the admin account
docker compose logs -f             # gateway and simulation logs
docker compose down                # stop; the database and results volumes are kept
```

**What is inside the image**

| Part | Source |
|---|---|
| Dashboard | built in a `node:24` stage (`npm run build`), served by the gateway from `client/dist` |
| Gateway | `server/`, production dependencies only, runs as `node src/index.js` |
| Simulation | `sim/` on Python 3.12 with `sim/requirements.txt` |
| SUMO | `eclipse-sumo==1.28.0` from PyPI (same version as development); `SUMO_HOME=/opt/sumo` |

The container runs as an unprivileged user (`itsc`), listens on port 4000 and reports its health
from `/api/health` (`docker ps` shows `healthy`). The live playlist starts by itself
(`LIVE_LOOP=true`) with the default settings (congested, `level_vanet`).

**Data that survives a rebuild** (named volumes)

| Volume | Path in the container | Holds |
|---|---|---|
| `itsc-data` | `/app/server/data` | SQLite database: accounts, runs, audit log |
| `itsc-results` | `/app/results` | experiment results and trip files |

Backup of the database while it runs: `docker compose exec itsc node src/backup.js` (writes into
`server/data/backups/` inside the volume).

**Settings.** Everything in §4 can be set in `.env` or under `environment:` in
`docker-compose.yml`. Two differ from the server install:

- `COOKIE_SECURE=false` by default, because `http://localhost` has no HTTPS. On a public server,
  put the container behind nginx with HTTPS (§7, proxy to `127.0.0.1:4000`), then set
  `COOKIE_SECURE=true` and `TRUST_PROXY=1`, and publish the port only on localhost
  (`"127.0.0.1:4000:4000"`).
- `HOST_PORT` chooses the port on the host computer (default 4100, so `npm run dev` can keep 4000).

**Limits.** SUMO-GUI cannot open inside the container (`SIM_GUI=false`); the web map shows the
same simulation. The image is about 2 GB (SUMO and the Python science packages).

### Which way to run it

| Need | Use | Cost |
|---|---|---|
| Demo on your own screen (defence, testing) | `docker compose up -d` → http://localhost:4100 | free |
| Others open it from their own devices while your PC is on | `docker compose --profile public up -d` (Cloudflare quick tunnel, below) | free, no account, no domain |
| Online all the time | a server with `docker-compose.prod.yml` (§14) | free with student credit (Azure for Students needs no card) |

**Public link from your PC.** The `tunnel` service (profile `public`) opens a Cloudflare quick tunnel
and gives a random `https://<words>.trycloudflare.com` address:

```bash
docker compose --profile public up --build -d
docker compose logs tunnel | grep trycloudflare     # the link to share
docker compose --profile public down                # closes the link
```

The link is HTTPS (live map over WSS), changes every time the tunnel starts, and works only while
the PC and the containers are running. Share it only with people who should see the system: they
still need an account to sign in.

**Verified (25 Nov 2026, Docker 29.9 on Windows 11):** image builds; container healthy; first
admin created from `.env`; the live playlist runs the real SUMO simulation and cars appear on the
map in the browser, both on http://localhost and through the public trycloudflare link (sign-in,
live map over WSS).

## 14. Server with Docker and automatic HTTPS

[`docker-compose.prod.yml`](../../docker-compose.prod.yml) runs the system behind
[Caddy](https://caddyserver.com), which gets a free Let's Encrypt certificate, renews it and
redirects http to https ([`deploy/Caddyfile`](../../deploy/Caddyfile)). The gateway port is not
published: only Caddy reaches it. Cookies are `Secure` and `TRUST_PROXY=1` is set.

**Server.** Ubuntu 22.04 or 24.04, 2 vCPU and 4 GB RAM (SUMO uses the CPU). Free options for
students: Azure for Students (credit, no card), or DigitalOcean credit from the GitHub Student Pack
(card or PayPal to verify). Open ports 22, 80 and 443 in the provider's network settings.

**Domain.** Your own domain (an `A` record to the server's IP), or none: `sslip.io` turns the IP
into a name, e.g. `20-55-1-2.sslip.io` for `20.55.1.2`, and Caddy gets a certificate for it.

```bash
# on the server
git clone <repo-url> ~/itsc && cd ~/itsc
bash deploy/server-setup.sh          # Docker, firewall, swap, .env; prints your sslip.io name
# log out and in again, then:
nano .env                            # DOMAIN, ADMIN_EMAIL, ADMIN_PASSWORD
docker compose -f docker-compose.prod.yml up --build -d
# open https://<DOMAIN>, sign in, then remove ADMIN_PASSWORD from .env and run the line above again
```

| Task | Command (in `~/itsc`) |
|---|---|
| Update to the latest code | `git pull && docker compose -f docker-compose.prod.yml up --build -d` |
| Logs | `docker compose -f docker-compose.prod.yml logs -f` |
| Database backup | `docker compose -f docker-compose.prod.yml exec itsc node src/backup.js` |
| Stop (data kept) | `docker compose -f docker-compose.prod.yml down` |

The containers start again by themselves after a crash or a reboot. When the student credit ends,
delete the server in the provider's portal so nothing is charged.

**Verified (25 Nov 2026, on Windows 11 with `DOMAIN=localhost`, Caddy's local certificate):**
https health check, http → https redirect (308), port 4000 closed from outside, sign-in sets a
`Secure; HttpOnly; SameSite=Lax` cookie. **Not verified:** a real cloud server and a Let's Encrypt
certificate, which need a public IP.

## 15. GitHub Codespaces (cloud demo without your own server)

A codespace is a cloud machine from GitHub. The repository's
[`.devcontainer/`](../../.devcontainer/) runs the same Docker setup there and gives a public HTTPS
link. The PC does not need to stay on, and no cloud account is needed beyond GitHub.

**Start**

1. On GitHub: open the repository → **Code** → **Codespaces** → **⋯ → New with options**: choose the
   branch and the **4-core** machine → **Create codespace**.
2. Wait. The first start builds the image (about 10 minutes). The terminal then prints:
   ```
   VANET Traffic is running
     Link:      https://<codespace-name>-4100.app.github.dev
     Sign in:   admin@itsc.local / <generated password>
   ```
3. Open the link on any device, or install the phone app from it.

If the link shows as *private*: **PORTS** tab → right-click port 4100 → **Port Visibility → Public**.
To print the link and login again: `bash .devcontainer/start.sh`.

**What the set-up does**

| File | When | What |
|---|---|---|
| `.devcontainer/devcontainer.json` | creation | Ubuntu with Docker and the GitHub CLI, 4 cores / 8 GB, port 4100 |
| `.devcontainer/setup.sh` | once | writes `.env` (never committed): administrator with a generated password, `COOKIE_SECURE=false`, `TRUST_PROXY=1`, and `CORS_ORIGIN` = this codespace's public address |
| `.devcontainer/start.sh` | every start | `docker compose up --build -d itsc`, waits for health, makes port 4100 public, prints link and login |

`CORS_ORIGIN` is needed because the Codespaces proxy may pass a `Host` that is not the public address,
and the gateway accepts sign-in and the live feed only from its own site or a listed origin (tested:
403 without it, 200 with it).

**Limits**

- A codespace **stops when idle**: 30 minutes by default, at most 4 hours (GitHub → Settings →
  Codespaces → *Default idle timeout*). Visitors using the app do not count as activity. Restart it
  from GitHub → **Codespaces**; the same link and database come back.
- It uses the account's monthly Codespaces hours (a 4-core machine counts double).
- Not for 24/7 hosting: use a server (§14) for that.

**Verified locally (28 Nov 2026):** `setup.sh` writes the expected `.env` and keeps an existing one;
a gateway with the generated `CORS_ORIGIN` accepts sign-in from the Codespaces origin. **Not yet
verified:** a real codespace (needs the files on GitHub).

# Gateway (Node.js)

Express + Socket.io + SQLite gateway between the Python simulation and the React dashboard.

- **One shared live simulation.** An admin starts or stops it; every viewer watches the same stream.
- Python is spawned with an argument array (no shell). Its stdout is read line by line; each line is
  parsed as JSON inside try/catch, and bad lines are logged and ignored.
- `state` messages are throttled to at most one per second (always the latest) and sent as *volatile*,
  so a slow client skips a frame instead of building a backlog. `event`, `status`, `result` are reliable.
- Every new connection gets one `init` message: now-playing info, the latest state, the last 120
  simulated seconds of states and the last 200 events.
- Optional auto-loop playlist (`LIVE_LOOP=true`, `playlist.json`). A crashed run is followed by the
  next entry after 5 s.
- SQLite keeps runs, final results, events and one state sample per 2 simulated seconds. Runs left
  "running" by a previous gateway process are marked "interrupted" at startup.

## Run

```bash
cd server
npm install
cp .env.example .env        # set PYTHON_BIN
npm run create-admin -- you@example.org "Your Name"   # first administrator account
npm run dev:mock            # fake simulation, no SUMO needed
npm start                   # real simulation (needs SUMO_HOME and the Python requirements)
npm test                    # vitest + supertest + socket.io-client, against the mock
npm run loadtest -- --url http://127.0.0.1:4000 --viewers 1000 --ramp 60 --hold 120
```

On Windows set `PYTHON_BIN=../.venv/Scripts/python.exe`; on Linux `PYTHON_BIN=../.venv/bin/python`.

## Environment

| Variable | Default | Meaning |
|---|---|---|
| `PORT`, `HOST` | `4000`, `0.0.0.0` | Listen address |
| `PYTHON_BIN` | `python` (Windows) / `python3` | Interpreter with the sim requirements |
| `SIM_ENTRY` / `MOCK_ENTRY` | `sim/main.py` / `sim/mock_sim.py` | Entry scripts, relative to the project root |
| `USE_MOCK` | `false` | Stream the mock simulation instead of SUMO |
| `ADMIN_EMAIL`, `ADMIN_PASSWORD` | empty | First administrator, created at start-up when no administrator exists |
| `ALLOW_REGISTRATION` | `false` | Self-registration of viewer accounts (initial value; a setting at run time) |
| `NODE_ENV` | `development` | `production` turns on Secure cookies and HSTS |
| `COOKIE_SECURE` | follows `NODE_ENV` | Force the `Secure` cookie flag on or off |
| `SESSION_TTL_HOURS` | `12` | Session lifetime |
| `SESSIONS_PER_USER` | `10` | Older sessions of an account are ended beyond this number |
| `TRUST_PROXY` | `0` | Reverse proxies in front of the gateway (nginx: 1), so client addresses are right |
| `LOGIN_MAX_FAILS` / `LOGIN_LOCK_MINUTES` | `5` / `15` | Failed sign-ins before an account is locked, and for how long |
| `RATE_LIMIT_PER_MIN` | `600` | API requests per address per minute |
| `AUTH_RATE_LIMIT` | `30` | Sign-in, registration and reset attempts per address per 15 minutes |
| `RESET_LINK_MINUTES` | `60` | Lifetime of a password-reset link |
| `AUDIT_RETENTION_DAYS` | `180` | Audit entries older than this are deleted |
| `SAMPLE_RETENTION_DAYS` | `14` | State samples (telemetry) older than this are deleted; runs, results and events are kept |
| `MAX_CONNECTIONS` | `1000` | Limit on concurrent Socket.io connections; more get `connect_error` with code `capacity` |
| `MAX_SIM_FAILURES` | `3` | Failed runs in a row after which the playlist stops retrying (lifecycle `FAILED`) |
| `LIVE_LOOP` | `false` | Loop `playlist.json` forever |
| `PLAYLIST_PATH` | `./playlist.json` | Playlist entries: scenario, controller, nav, seed, penetration, loss, relay, label |
| `DB_PATH` | `./data/gateway.db` | SQLite file |
| `SUMMARY_PATH` | `../results/summary.json` | Served by `/api/experiments/summary` |
| `CORS_ORIGIN` | empty | Extra browser origins allowed to use the API and Socket.io with cookies (comma separated). Empty = same origin only |
| `SIM_STEP_DELAY` | `0.25` | Wall-clock seconds per simulated second for the live run (0 = full speed) |
| `SIM_MAX_STEPS` | scenario duration | Shorter live runs |
| `SIM_GUI` | `false` | Also open each live run in the SUMO-GUI window (desktop only). It starts by itself and closes at the end of the run. |
| `MOCK_STEP_DELAY`, `MOCK_MAX_STEPS` | `0.5`, `300` | Mock pacing and length |
| `STATE_INTERVAL_MS` | `1000` | Minimum gap between broadcast states |
| `HISTORY_SECONDS`, `EVENT_HISTORY` | `120`, `200` | History sent in `init` |
| `SAMPLE_EVERY_SIM_S` | `2` | Persist one state every N simulated seconds |
| `CRASH_RETRY_MS` | `5000` | Wait before the next playlist entry after a crash |
| `DRIVER_MODE` | `true` | One-driver mode on at start (the admin can switch it at run time) |
| `DRIVER_IDLE_MS` | `120000` | Seat freed after this long without a command |
| `DRIVER_MAX_MS` | `600000` | Longest single drive |
| `DRIVER_RESUME_MS` | `15000` | A driver who loses the connection can reclaim the seat this long (with the seat key) |
| `DRIVER_CMDS_PER_S` | `2` | Command rate limit per driver (bursts up to twice this) |
| `DRIVER_STEP_DELAY` | `0.5` | Live pacing while someone drives (real SUMO only) |
| `LOG_LEVEL` | `info` | `debug` also logs the simulation's stderr |

`SUMO_HOME` and the rest of the environment pass through to the Python process.

## Access control

Every route except `/api/health` and `/api/auth/*` needs a signed-in session. The session is an opaque
token in an `HttpOnly`, `SameSite=Lax` cookie (`itsc_sid`; `Secure` in production); only its SHA-256 is
stored. Every state-changing request must also send the session's CSRF token in `X-CSRF-Token`
(returned by sign-in and by `/api/auth/me`) and, when the browser sends an `Origin`, it must be this
site or one listed in `CORS_ORIGIN`. Passwords (10 to 72 characters) are stored as salted Argon2id hashes (scrypt on Node versions without built-in Argon2); hashing runs off the main thread. Older bcrypt hashes are accepted once and replaced at sign-in.

| Role | May |
|---|---|
| `VIEWER` | Watch the live simulation and results, follow a car, drive in one-driver mode |
| `RESEARCHER` | Also start runs; stop, pause, resume, change the speed of and restart only their own runs (and replace playlist runs) |
| `ADMIN` | Everything: any run, driver mode, SUMO-GUI, users, sessions, audit log, settings, system health |

Errors are JSON `{error, code}` with `code` one of `unauthenticated` (401), `forbidden` (403), `csrf`,
`bad_origin`, `invalid` (400, with `issues`), `rate_limited` / `locked` (429), `busy` / `not_running` (409), `not_found`. The live socket checks
the browser `Origin` too. Full list: [docs/architecture/API.md](../docs/architecture/API.md).

## API and Socket.io events

The full reference (every route with its role, the error codes and the socket events) is in
[`docs/architecture/API.md`](../docs/architecture/API.md).

## Production notes (1000+ viewers)

Load-test results are in [loadtest/REPORT.md](loadtest/REPORT.md).

**Open-file limit (Linux).** Every WebSocket is a file descriptor. Raise the limit before starting the
gateway, for example `ulimit -n 65535`, or `LimitNOFILE=65535` in a systemd unit. The default of
1024 fails before 1000 viewers. Windows has no equivalent limit.

**nginx reverse proxy** (WebSocket upgrade, cached static React build). The complete production files (HTTPS,
systemd unit, production settings, backups) are in [`deploy/`](../deploy/) and explained in
[`docs/deployment/DEPLOYMENT.md`](../docs/deployment/DEPLOYMENT.md). Minimal example:

```nginx
map $http_upgrade $connection_upgrade { default upgrade; '' close; }

upstream itsc_gateway { server 127.0.0.1:4000; keepalive 64; }

server {
    listen 80;
    server_name traffic.example.org;

    root /srv/itsc/client/dist;            # npm run build in client/

    location /assets/ {                    # hashed file names: cache for a year
        expires 1y;
        add_header Cache-Control "public, immutable";
        try_files $uri =404;
    }

    location /api/ {
        proxy_pass http://itsc_gateway;
        proxy_set_header Host $http_host;      # keeps the port: the gateway compares Origin with Host
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }

    location /socket.io/ {
        proxy_pass http://itsc_gateway;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        proxy_set_header Host $http_host;
        proxy_read_timeout 120s;           # longer than the 25 s ping interval
        proxy_buffering off;
    }

    location / {                           # single-page app fallback, never cache index.html
        add_header Cache-Control "no-cache";
        try_files $uri /index.html;
    }
}
```

Also set `worker_connections 4096;` (or higher) in nginx's `events {}` block. Each viewer uses one
connection to the client and one to the upstream.

Without nginx, the gateway also serves `client/dist` itself when that folder exists, with the same caching
(`assets/` for a year, `index.html` never). HTTP responses over 1 KB are gzip-compressed by the gateway
(`compression`); nginx can compress the static files too (`gzip on; gzip_types text/css application/javascript application/json;`).
Socket.io messages are not compressed on purpose (per-message deflate would cost CPU per viewer and per message).

**Shutdown.** SIGINT/SIGTERM stops the simulation (on Windows the whole python + SUMO process tree
is terminated), closes Socket.io and HTTP, then closes the database.

## Backup and restore

```bash
npm run backup -- backups/gateway-2026-11-12.db   # consistent copy while the gateway runs
```
Restore: stop the gateway, copy the backup over `DB_PATH` (delete the old `-wal` and `-shm` files), start it again.

## Load tests

`node loadtest/steps.js --url http://127.0.0.1:4000 --email <admin> [--steps 10,50,...,1000] [--spike 1100]
[--reconnect 500] [--logins 200] [--soak 30] [--out result.json]` (password from `LT_PASSWORD`). It signs in, opens
the connections, calls the API while holding, and reports connection success, stream and API latency, CPU, memory
and event-loop lag. Run the gateway with `AUTH_RATE_LIMIT`, `RATE_LIMIT_PER_MIN` and `SESSIONS_PER_USER` raised, since
all test traffic comes from one address. Results: `docs/testing/PHASE_8_REPORT.md` and `PHASE_9_REPORT.md`.

`node loadtest/initsize.js <url> <email> <password>` prints the size of a new viewer's first message, split by
key. Browser-side measurements: `tests/e2e/loadtime.mjs` (time until cars show, local and on an emulated
10 Mbit/s link) and `tests/e2e/render.mjs` (frames per second and long tasks per live page, optional CPU slowdown).


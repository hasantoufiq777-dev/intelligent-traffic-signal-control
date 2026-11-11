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
| `BCRYPT_ROUNDS` | `12` | Password hashing cost |
| `TRUST_PROXY` | `0` | Reverse proxies in front of the gateway (nginx: 1), so client addresses are right |
| `LOGIN_MAX_FAILS` / `LOGIN_LOCK_MINUTES` | `5` / `15` | Failed sign-ins before an account is locked, and for how long |
| `RATE_LIMIT_PER_MIN` | `600` | API requests per address per minute |
| `AUTH_RATE_LIMIT` | `30` | Sign-in, registration and reset attempts per address per 15 minutes |
| `RESET_LINK_MINUTES` | `60` | Lifetime of a password-reset link |
| `AUDIT_RETENTION_DAYS` | `180` | Audit entries older than this are deleted |
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
site or one listed in `CORS_ORIGIN`. Passwords are bcrypt hashes (10 to 72 characters).

| Role | May |
|---|---|
| `VIEWER` | Watch the live simulation and results, follow a car, drive in one-driver mode |
| `RESEARCHER` | Also start runs; stop and replace only their own runs (and replace playlist runs) |
| `ADMIN` | Everything: any run, driver mode, SUMO-GUI, users, sessions, audit log, settings, system health |

Errors are JSON `{error, code}` with `code` one of `unauthenticated` (401), `forbidden` (403), `csrf`,
`bad_origin`, `invalid` (400, with `issues`), `rate_limited` / `locked` (429), `busy` (409), `not_found`.

## API

| Method | Path | Access | Notes |
|---|---|---|---|
| GET | `/api/health` | public | `{ok, uptime_s, mock, live_running, lifecycle, viewers, max_connections}` |
| GET | `/api/auth/me` | public | `{user, csrf, expires_at, registration}`; `user` is null when not signed in |
| GET | `/api/auth/config` | public | `{registration}` |
| POST | `/api/auth/login` | public | `{email, password}` → sets the cookie, `{user, csrf}`. 401 wrong, 429 locked, 403 disabled |
| POST | `/api/auth/logout` | session | Ends the session and its live connections |
| POST | `/api/auth/register` | public | `{email, name, password}`; only when registration is open; the role is always `VIEWER` |
| POST | `/api/auth/forgot` | public | `{email}`; records a request for the administrators (same answer for unknown emails) |
| POST | `/api/auth/reset` | public | `{token, password}` with a reset link's token (single use) |
| PATCH | `/api/auth/profile` | session | `{name}` |
| POST | `/api/auth/password` | session | `{current_password, new_password}`; ends the account's other sessions |
| GET | `/api/live/status` | session | `{running, lifecycle, entry, next, loop, viewers, status}` |
| GET | `/api/live/playlist` | session | Playlist and loop flag |
| GET | `/api/options` | session | Allowed scenarios, controllers, nav modes, relay, defaults, playlist |
| GET | `/api/experiments/summary` | session | `results/summary.json`, or 404 with how to create it |
| GET | `/api/runs?limit=&offset=` | session | Stored runs, newest first, with `total` |
| GET | `/api/runs/:id?samples=N` | session | Run, result, events (up to 500), state-sample count, optional samples |
| GET | `/api/driver` | session | Driver seat and the last 20 drives with their trip reports |
| GET | `/api/sumo/gui` | session | `{available, enabled, mock, running}`; `available` only for administrators with the real simulation |
| POST | `/api/live/start` | ADMIN, RESEARCHER | Body `{scenario, controller, nav?, seed?, penetration?, loss?, relay?, eco_fraction?, ev_routing?, network?, label?}`; unknown keys are rejected. 202 `{run_id}`; 409 `busy` while another start/stop is in progress, 409 `in_use` for a researcher when another user's run is active |
| POST | `/api/live/stop` | ADMIN, RESEARCHER (own run) | Stops the run and pauses the playlist until the next start |
| POST | `/api/sumo/gui` | ADMIN | `{enabled}`: show the live run in the SUMO-GUI window on the gateway's computer (the run restarts); 409 with the mock |
| POST | `/api/driver` | ADMIN | `{enabled}` switches driver mode; switching off ends the current drive |
| POST | `/api/driver/release` | ADMIN | Ends the current drive |
| GET | `/api/users?limit=&offset=` | ADMIN | Accounts (no password data) |
| POST | `/api/users` | ADMIN | `{email, name, password, role?}` |
| PATCH | `/api/users/:id` | ADMIN | `{name?, role?, status?: active\|disabled}`; `active` also unlocks; the last active administrator is protected |
| DELETE | `/api/users/:id` | ADMIN | Not yourself, not the last administrator |
| POST | `/api/users/:id/reset-link` | ADMIN | `{path, expires_at}`: single-use password-reset link to hand to the user |
| GET | `/api/sessions` | ADMIN | Active sessions; `DELETE /api/sessions/:id` signs one out |
| GET | `/api/audit?limit=&offset=&action=` | ADMIN | Audit trail |
| GET / PUT | `/api/settings` | ADMIN | `{allow_registration}` |
| GET | `/api/metrics` | ADMIN | Connections, CPU, memory, event-loop lag, broadcast counters (used by the load test) |
| GET | `/api/system` | ADMIN | The metrics plus database status and migrations, simulation lifecycle |

**Simulation lifecycle** (`lifecycle` in `now_playing`, `/api/health`, `/api/live/status`): `STOPPED` → `STARTING` →
`RUNNING` → `STOPPING` → `STOPPED`; `FAILED` after a run that ended with an error; `RECOVERING` while the playlist waits
to retry. After `MAX_SIM_FAILURES` failed runs in a row the playlist stops until an administrator starts a run.

## Socket.io (authenticated; viewers are read-only except the one driver seat; everyone joins room `live`)

The handshake must carry the session cookie; otherwise the connection fails with `connect_error` whose
`data.code` is `unauthenticated`. Beyond `MAX_CONNECTIONS` it fails with `data.code = capacity` (existing
connections are not affected). When a session ends, its sockets are disconnected.

| Event | Payload |
|---|---|
| `init` | `{network, vehicles, now_playing, status, latest, states[], events[], result, history_seconds}` on connect |
| `state` | Protocol state message + `sent_at` (server ms timestamp), at most 1/s, volatile |
| `event` | `{type, run_id, t, kind, tls, detail}` |
| `status` | `{type, run_id, status: started/finished/stopped/error, message}` |
| `result` | Final result message |
| `now_playing` | `{running, current: {run_id, entry, source, started_at, status}, next, loop, viewers, mock}` |
| `viewers` | `{count}`, at most every 5 s when it changes |
| `network` | Road geometry of the current run (junctions, edge shapes, bounds) |
| `vehicles` | Map frame `{run_id, t, rows: [[id, x, y, angle, flags]], sent_at}`, at most 1/s, volatile, sent 150 ms after the state |
| `vehicle` | Full row of the followed car (next light, distance, fuel, CO2, advice), every map frame, only to its followers |
| `driver` | The driven car's state from the simulation (speed, mode, next light, advice, ambulance warning, fuel, CO2), every simulated second, to everyone; the final one (`finished`/`released`/`error`) carries the trip report |
| `driver_lock` | The seat `{enabled, available, busy, vid, since, idle_ms, max_ms}` when it changes (also in `now_playing.driver` and `init.driver`) |
| `driver_seat` | To the driver only: `{mine: false, reason, message}` when their drive ends (released, idle, left, admin, time_limit, run_ended, finished, error) |

Viewers can send `follow` (a vehicle id) and `unfollow`. This is a subscription to one car's details, not control of it.

One-driver mode (`src/driver.js`), all with an acknowledgement callback `{ok, error?}`:
- `driver:claim {from, to}`: take the seat with a car from one road to another (`from`/`to` are edge ids of the current
  network). Refused when driving is off, no run is live, or someone else drives. Answer: `{ok, key, idle_ms, max_ms}`.
- `driver:cmd {action: go|ease|stop, speed_kmh?}`: only from the seat holder; rate limited (`retry_ms` when refused).
- `driver:release`: hand the car back. `driver:resume <key>`: reclaim the seat after a reconnect within `DRIVER_RESUME_MS`.

The gateway passes commands to Python as JSON lines on stdin (`--driver`). SQLite table `driver_trips` keeps each drive
(run, car, roads, status, reason, report).
Volatile messages are dropped while a socket is still writing the previous one, so the map frame is kept out of the
state broadcast's tick, and follower details are sent reliably.

## Production notes (1000+ viewers)

Load-test results are in [loadtest/REPORT.md](loadtest/REPORT.md).

**Open-file limit (Linux).** Every WebSocket is a file descriptor. Raise the limit before starting the
gateway, for example `ulimit -n 65535`, or `LimitNOFILE=65535` in a systemd unit. The default of
1024 fails before 1000 viewers. Windows has no equivalent limit.

**nginx reverse proxy** (WebSocket upgrade, cached static React build):

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
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }

    location /socket.io/ {
        proxy_pass http://itsc_gateway;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        proxy_set_header Host $host;
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

Without nginx, the gateway also serves `client/dist` itself when that folder exists.

**Shutdown.** SIGINT/SIGTERM stops the simulation (on Windows the whole python + SUMO process tree
is terminated), closes Socket.io and HTTP, then closes the database.

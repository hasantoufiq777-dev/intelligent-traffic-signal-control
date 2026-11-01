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
cp .env.example .env        # set PYTHON_BIN and ADMIN_TOKEN
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
| `ADMIN_TOKEN` | empty | Required in `X-Admin-Token` for start/stop. Empty disables admin endpoints (503) |
| `LIVE_LOOP` | `false` | Loop `playlist.json` forever |
| `PLAYLIST_PATH` | `./playlist.json` | Playlist entries: scenario, controller, nav, seed, penetration, loss, relay, label |
| `DB_PATH` | `./data/gateway.db` | SQLite file |
| `SUMMARY_PATH` | `../results/summary.json` | Served by `/api/experiments/summary` |
| `CORS_ORIGIN` | `*` | Comma-separated origins for REST and Socket.io |
| `SIM_STEP_DELAY` | `0.25` | Wall-clock seconds per simulated second for the live run (0 = full speed) |
| `SIM_MAX_STEPS` | scenario duration | Shorter live runs |
| `MOCK_STEP_DELAY`, `MOCK_MAX_STEPS` | `0.5`, `300` | Mock pacing and length |
| `STATE_INTERVAL_MS` | `1000` | Minimum gap between broadcast states |
| `HISTORY_SECONDS`, `EVENT_HISTORY` | `120`, `200` | History sent in `init` |
| `SAMPLE_EVERY_SIM_S` | `2` | Persist one state every N simulated seconds |
| `CRASH_RETRY_MS` | `5000` | Wait before the next playlist entry after a crash |
| `LOG_LEVEL` | `info` | `debug` also logs the simulation's stderr |

`SUMO_HOME` and the rest of the environment pass through to the Python process.

## API

| Method | Path | Notes |
|---|---|---|
| GET | `/api/health` | `{ok, uptime_s, mock, live_running, viewers}` |
| GET | `/api/live/status` | `{running, entry, next, loop, viewers, status}` |
| GET | `/api/live/playlist` | Playlist and loop flag |
| GET | `/api/options` | Allowed scenarios, controllers, nav modes, relay, defaults, playlist |
| GET | `/api/experiments/summary` | `results/summary.json`, or 404 with how to create it |
| GET | `/api/runs?limit=&offset=` | Stored runs, newest first |
| GET | `/api/runs/:id?samples=N` | Run, result, events (up to 500), state-sample count, optional samples |
| GET | `/api/metrics` | Process CPU/memory and broadcast counters (used by the load test) |
| POST | `/api/live/start` | Admin. Body `{scenario, controller, nav?, seed?, penetration?, loss?, relay?}`; unknown keys are rejected. 202 `{run_id}` |
| POST | `/api/live/stop` | Admin. Stops the run and pauses the playlist until the next start |

## Socket.io (viewers are read-only; everyone joins room `live`)

| Event | Payload |
|---|---|
| `init` | `{now_playing, status, latest, states[], events[], result, history_seconds}` on connect |
| `state` | Protocol state message + `sent_at` (server ms timestamp), at most 1/s, volatile |
| `event` | `{type, run_id, t, kind, tls, detail}` |
| `status` | `{type, run_id, status: started/finished/stopped/error, message}` |
| `result` | Final result message |
| `now_playing` | `{running, current: {run_id, entry, source, started_at, status}, next, loop, viewers, mock}` |
| `viewers` | `{count}`, at most every 5 s when it changes |

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

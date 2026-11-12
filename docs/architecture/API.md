# API reference

The gateway's REST API and its Socket.io events. All paths are on the same origin as the dashboard.
Request bodies are JSON (at most 10 KB) and are validated; unknown keys are rejected.

## Conventions

- **Sessions.** Sign-in sets the `itsc_sid` cookie (`HttpOnly`, `SameSite=Lax`, `Secure` in production).
  Only its SHA-256 is stored on the server.
- **CSRF.** Every `POST`/`PUT`/`PATCH`/`DELETE` with a session must send the session's token in
  `X-CSRF-Token` (from `/api/auth/me` or the sign-in answer). A browser `Origin` must be this site or
  listed in `CORS_ORIGIN`.
- **Roles.** `VIEWER` < `RESEARCHER` < `ADMIN`; enforced by the gateway on every route and socket event.
- **Rate limits.** `RATE_LIMIT_PER_MIN` API requests per client address per minute; `AUTH_RATE_LIMIT`
  sign-in, registration and reset attempts per 15 minutes. Answers carry `RateLimit` headers (draft 8).
- **Errors.** `{"error": "<readable message>", "code": "<code>"}`; never a stack trace, path or SQL.

| Code | HTTP | Meaning |
|---|---|---|
| `unauthenticated` | 401 | no valid session (also a socket `connect_error`) |
| `forbidden` | 403 | the role may not do this |
| `csrf` | 403 | missing or wrong `X-CSRF-Token` |
| `bad_origin` | 403 | request or socket from another site |
| `bad_credentials` | 401 / 400 | wrong e-mail or password at sign-in (same answer for unknown e-mails); 400 for a wrong current password when changing it |
| `locked` | 429 | too many failed sign-ins; locked for `LOGIN_LOCK_MINUTES` |
| `disabled` | 403 | the account is disabled |
| `rate_limited` | 429 | too many requests from this address |
| `invalid` | 400 / 413 | body or parameters fail validation, broken JSON, body too large |
| `not_found` | 404 | unknown route, run or user |
| `exists` | 409 | the e-mail is already registered |
| `last_admin` | 409 | the last active administrator cannot be removed, demoted or disabled |
| `self` | 409 | an administrator cannot delete their own account |
| `bad_token` | 400 | reset link invalid, used or expired |
| `registration_closed` | 403 | self-registration is switched off |
| `busy` | 409 | another start or stop request is in progress |
| `in_use` | 409 | a researcher tried to replace another user's run |
| `not_running` | 409 | pause, resume, speed or restart with no run to act on |
| `capacity` | socket | `MAX_CONNECTIONS` live connections reached |
| `server_error` | 500 | unexpected failure (details only in the server log) |

## REST API

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
| GET | `/api/experiments/ga` | session | GA tuning output per target (`signals`, `aco`): `{history, evaluation}` from `results/ga/`, or null when not run |
| GET | `/api/runs?limit=&offset=` | session | Stored runs, newest first, with `total` |
| GET | `/api/runs/:id?samples=N` | session | Run, result, events (up to 500), state-sample count, optional samples |
| GET | `/api/driver` | session | Driver seat and the last 20 drives with their trip reports |
| GET | `/api/sumo/gui` | session | `{available, enabled, mock, running}`; `available` only for administrators with the real simulation |
| POST | `/api/live/start` | ADMIN, RESEARCHER | Body `{scenario, controller, nav?, seed?, penetration?, loss?, relay?, eco_fraction?, ev_routing?, network?, label?}`; unknown keys are rejected. 202 `{run_id}`; 409 `busy` while another start/stop is in progress, 409 `in_use` for a researcher when another user's run is active |
| POST | `/api/live/stop` | ADMIN, RESEARCHER (own run) | Stops the run and pauses the playlist until the next start |
| POST | `/api/live/pause` | ADMIN, RESEARCHER (own run) | 202; the simulation stops stepping until resumed; confirmed through `now_playing.playback` |
| POST | `/api/live/resume` | ADMIN, RESEARCHER (own run) | 202; continues from the same simulated second |
| POST | `/api/live/speed` | ADMIN, RESEARCHER (own run) | `{speed: 0.5 \| 1 \| 2 \| 4}` relative to the normal pacing; 202. Wall-clock pacing only: the simulated seconds are the same |
| POST | `/api/live/restart` | ADMIN, RESEARCHER (own run) | Starts the current (or last) run again from its first second with the same settings; 202 `{run_id, entry}` |
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
`data.code` is `unauthenticated`. A browser `Origin` other than this site (or a `CORS_ORIGIN` entry) fails with
`bad_origin`; clients that send no `Origin` (scripts, the load test) are not browsers and are not checked. Beyond `MAX_CONNECTIONS` it fails with `data.code = capacity` (existing
connections are not affected). When a session ends, its sockets are disconnected.

| Event | Payload |
|---|---|
| `init` | `{network, vehicles, now_playing, status, latest, states[], events[], result, history_seconds}` on connect. `latest` is the full latest state; `states` is the last `HISTORY_SECONDS` as chart rows (`run_id, t, metrics, tls{phase, queues}, comm.pdr, nav{avg_tt_nav, avg_tt_non, edge_load_cv}`) to keep the first message small |
| `state` | Protocol state message + `sent_at` (server ms timestamp), at most 1/s, volatile |
| `event` | `{type, run_id, t, kind, tls, detail}` |
| `status` | `{type, run_id, status: started/finished/stopped/error, message}` |
| `result` | Final result message |
| `now_playing` | `{running, lifecycle, current: {run_id, entry, source, started_at, status, started_by}, next, loop, viewers, mock, gui, driver, playback: {paused, speed}}`; `playback` is what the simulation confirmed (its `playback` message), not what was requested |
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

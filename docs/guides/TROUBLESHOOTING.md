# Troubleshooting

Symptoms, their usual causes and what to do. The server log comes first:

- **Linux service:** `journalctl -u itsc-gateway -f`
- **Development:** the terminal running the gateway

Set `LOG_LEVEL=debug` for more detail.

## Signing in

| Symptom | Cause | Fix |
|---|---|---|
| "Invalid email or password" | wrong credentials; the same message for unknown e-mails, on purpose | check the address; an administrator can issue a reset link |
| "Too many failed attempts" (429) | 5 failures: locked for 15 minutes; or too many attempts from one address | wait, or an administrator sets the account to *active* |
| "This account is disabled" | an administrator disabled it | ask an administrator |
| Sign-in succeeds, but you are back at the sign-in page | production mode (`COOKIE_SECURE=true`) over plain `http://`: the browser drops the `Secure` cookie | use the `https://` address; for local tests without HTTPS use `NODE_ENV=development` |
| "Request from an unknown origin was refused" (`bad_origin`) | the browser's `Origin` does not match the `Host` the gateway sees | behind nginx use `proxy_set_header Host $http_host;` (not `$host`, which drops the port); with the dashboard on another origin, add it to `CORS_ORIGIN` |
| `bad_origin` with the Vite dev server (`npm run dev`) | an old `client/vite.config.js` with `changeOrigin: true` | it must be `changeOrigin: false` (fixed in Phase 10) |
| "Missing or invalid CSRF token" | the page was open across a sign-out or the session expired | reload the page |

## Live view

| Symptom | Cause | Fix |
|---|---|---|
| *Reconnecting…* in the top bar | network drop or gateway restart | it reconnects by itself; a silent drop can take up to 45 s to notice (Socket.io heartbeat) |
| "The platform is at its limit of 1000 live connections" | `MAX_CONNECTIONS` reached | the page retries; raise the limit only with more server capacity (see the load test report) |
| Map shows *Simulation stopped* | no run is playing (stopped, or `LIVE_LOOP=false`) | start a run in *Run control* or the guided demo |
| Lifecycle *Failed* | the simulation could not start or crashed | see the next table |
| Map is empty, no roads | the gateway has not received the network yet; the run just started | wait a few seconds; if it stays empty, check the log for a failed start |
| Panels say there is no data | the current run's controller or scenario does not produce it (for example no VANET figures under fixed-time) | start a scenario that uses it; the panel text says which |
| Live view stopped after a deployment behind nginx | missing WebSocket upgrade headers or a short `proxy_read_timeout` | use the `/socket.io/` block from `deploy/nginx.conf` (timeout 120 s) |

## The simulation fails (*Failed*)

The run's message (*Run history*, and the log) reads "Simulation exited with code N: ...". Python's
own error output goes to the log only at `LOG_LEVEL=debug` (lines starting with `[sim]`). Set it,
restart, and start the run again to see the Python traceback.

| Log message | Cause | Fix |
|---|---|---|
| `Simulation process error: spawn ... ENOENT` | `PYTHON_BIN` wrong | point it at the virtual environment's interpreter (`.venv/bin/python` or `.venv\Scripts\python.exe`) |
| `[sim]` lines about `SUMO_HOME`, `sumolib` or `traci` | SUMO not installed or `SUMO_HOME` unset | install SUMO and set `SUMO_HOME`; check with `python sim/smoke_test.py` |
| "Simulation exited with code 1" | the run crashed | the `[sim]` lines before it show the Python error; run the same scenario directly with `python sim/main.py --scenario ...` |
| `[sim]` permission errors writing `sim/sumo/routes_*.rou.xml` or `results/` (Linux service) | the systemd hardening does not allow the path | keep `ReadWritePaths=... /srv/itsc/results /srv/itsc/sim/sumo` from `deploy/itsc-gateway.service` |
| "N runs failed in a row: the playlist is paused" | `MAX_SIM_FAILURES` reached: deliberately no endless restarts | fix the cause, then start a run |

Without SUMO, `USE_MOCK=true` streams a mock simulation, which is enough for checking the
dashboard.

## Research pages

| Symptom | Fix |
|---|---|
| *Results* is empty or says how to create it | `results/summary.json` is missing: `npm run sim:experiments` (long), or `npm run sim:experiments:quick` |
| *GA optimisation* shows "not run" | `results/ga/` is missing: `npm run sim:tune` |
| *Run history* cannot compare | only runs that finished with a result can be compared; stopped runs have none |

## Server and database

| Symptom | Cause | Fix |
|---|---|---|
| `EADDRINUSE` at start | another gateway already listens on `PORT` | stop it, or choose another `PORT` |
| "Invalid gateway configuration: X: ..." | a setting has an invalid value | the message names the variable; see `deploy/gateway.env.production.example` |
| "Database is not available" (503 on *System*) | disk full, file permissions, or the file was removed | check `df -h` and the owner of `DB_PATH`; the gateway recovers without a restart once writes work again |
| Database grows quickly | state samples: one complete state every 2 simulated seconds | they are deleted after `SAMPLE_RETENTION_DAYS` (14); lower it if disk is short |
| `persist_errors` above 0 on *System* | database writes failed during a run (the stream continued) | as for "Database is not available" |
| First start after an update takes a few seconds | a migration is building an index on a large database (1.4 s on 1.6 GB) | wait; it happens once |

## Development and tests

| Symptom | Fix |
|---|---|
| `npx vitest run` fails at once with "Cannot read properties of undefined (reading 'config')" on Windows | the drive letter's case differs from the project path (`e:` vs `E:`) in Git Bash; run the tests from PowerShell or cmd, or with `npm test` |
| Browser tests fail at the run-comparison step | the database has fewer than two finished runs; use a gateway whose runs have finished (for example `USE_MOCK=true MOCK_MAX_STEPS=150`) |
| Browser tests: "no Edge" | the tests drive Microsoft Edge through `playwright-core`; install Edge or change `channel` in the script |
| Load test refused after a few hundred users | rate limits: start the gateway with `AUTH_RATE_LIMIT`, `RATE_LIMIT_PER_MIN` and `SESSIONS_PER_USER` raised (all test traffic comes from one address) |

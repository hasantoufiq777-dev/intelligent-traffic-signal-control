# Failure and recovery test report

**Objective.** Every failure the platform can meet is detected, shown truthfully (never as
"running"), contained and recovered from, and no valid data is lost.
Final state: 12 November 2026 (environment: [test plan §1](TEST_PLAN.md#1-environment-all-reported-runs)).

## 1. Automated failure tests (gateway)

**Procedure.** `npm run test:server`: mainly `server/test/resilience.test.js` (18 tests), plus
lifecycle tests in `auth.test.js` and `gateway.test.js`. Failures are produced with small fake
simulation scripts that crash, never start or print garbage, with a database that is closed or
whose writes throw, and by restarting the gateway on the same database file.
**Result.** All pass on the final code (80 passed, 2 skipped).

| Failure | Expected behaviour | Actual |
|---|---|---|
| Simulation killed mid-run | lifecycle FAILED; viewers get `status: error`; run stored as `error`; no automatic restart without a playlist; an explicit start recovers | as expected |
| Simulation cannot start (missing interpreter) | start accepted, then FAILED, never RUNNING; counted in `/api/system` | as expected |
| Simulation prints invalid output (non-JSON, broken JSON, unknown types, arrays, a 200,000-character line) | ignored and counted; valid messages around them still used and stored | 5 bad lines counted; stream intact |
| Simulation exits 0 without a result | treated as a failure | as expected |
| Repeated failures with the playlist on | retry with the next entry after `CRASH_RETRY_MS`; stop after `MAX_SIM_FAILURES` (3); an administrator start recovers | exactly 3 runs, no fourth |
| Concurrent start/stop requests | the second gets 409 `busy`; no interleaving | as expected |
| Stop while the run is paused | the paused simulation stops promptly; nothing hangs | stopped within the test's 5 s bound (`playback.test.js`) |
| Pause, resume or speed with nothing running | 409 `not_running`, no state change | as expected |
| Gateway shut down mid-run | simulation process tree ended; closing twice harmless | as expected |
| Gateway restarted | accounts and sessions survive (same cookie, same role); interrupted run marked `interrupted`; lifecycle STOPPED | as expected |
| Viewer reconnects | gets the current run's snapshot with its history | as expected |
| Database unavailable | `/api/system` 503 "Database is not available", other requests 500; no internals leak; service resumes without restart | as expected |
| Every database write fails during a run (disk full) | stream continues to viewers; errors counted (`persist_errors`) and logged at most every 10 s; next run stored normally | as expected (Phase 8 defect fixed) |
| A driver's car disappears or their start fails | only that drive ends; the seat is freed | as expected (two ordering races fixed) |

## 2. Network interruptions (browser)

| Test | Expected | Actual | Status |
|---|---|---|---|
| Viewer's browser goes offline, then online (`roles.mjs`) | *Reconnecting* / *Offline* shown, then *Live* again with data | as expected. A silent drop with no data flowing is noticed through the Socket.io heartbeat (up to 45 s); the test now allows 60 s | pass |
| 500 of 1,000 viewers dropped at once (load test) | all reconnect, the limit holds | 500 / 500 within 0.34 s | pass |
| Session ended while connected (sign-out elsewhere, disabled) | live connection closed; the page shows the sign-in page | as expected (gateway tests and `roles.mjs`) | pass |
| Platform full | clear message; the page retries | `capacity` with a message (gateway tests and the load test). The dashboard's automatic retry is in `useLiveSocket.js` but was not exercised in a browser test | pass (message); retry not tested |

## 3. Backup, restore and rollback (Phase 10, manual procedure, recorded)

Run against a gateway in production mode with a live run playing.

| Step | Command | Result |
|---|---|---|
| Online backup while running | `npm run backup -- <file>` and `deploy/backup.sh` (gzip, retention) | backup written (0.8 MB; 72 KB gzipped); `PRAGMA integrity_check` = `ok`; migrations 1–4, 1 user, 1 run, 159 / 354 samples |
| Restore into a fresh gateway | copy to `DB_PATH`, start | the account signs in; the run that was in progress at backup time is listed as `interrupted` |
| Code rollback over the newer database | Phase 8 code (`daec358`) started on the database with migration 4 | signs in, lists runs, starts and records a new run; the unknown index is ignored |

The automated test `resilience.test.js › backs up a database in use and the copy restores with everything in it` additionally checks a backup taken
during use: integrity `ok`, all migrations, users whose passwords still work, and the results.

## 4. Long-running stability

30 minutes at 1,000 users (Phase 8): 0 drops, 0 errors, memory flat at 230–232 MB in the last 14
minutes, and the simulation still RUNNING at the end ([load test report](LOAD_TEST_REPORT.md)).

## 5. Limitations

- No test cut power or killed the gateway process abruptly (SIGKILL) during a database write.
  SQLite's WAL journal is designed for this, but it was not exercised.
- Disk-full was simulated by making writes throw, not by filling a real disk.
- Recovery under systemd (`Restart=on-failure`) was not exercised; no Linux machine was available.

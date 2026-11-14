# Integration test report

Final run: 12 November 2026, on the final code (environment: [test plan §1](TEST_PLAN.md#1-environment-all-reported-runs)).

## 1. Gateway (`server/test/`)

**Objective.** Every API route, socket event, role rule, lifecycle transition and failure path
works through the real Express, Socket.io and SQLite stack.
**Procedure.** `npm run test:server` (vitest). Each test starts its own gateway on a free port with
an in-memory or temporary database. The simulation is the mock, or a small fake Python script that
crashes, fails to start or prints invalid output.
**Expected.** All pass, with the real-SUMO tests skipped here (run separately, §2).
**Actual.** **82 passed, 3 skipped** (6 files; the 3 real-SUMO tests are skipped here) in 15.3 s.
**Status.** Pass.

| File | Tests | Covers |
|---|---|---|
| `auth.test.js` | 30 | sign-in, cookie flags, hashed tokens and passwords (Argon2id, bcrypt upgrade), lockout, rate limits, sessions (expiry, limit, revocation on sign-out, password change, reset, disabling), CSRF, origin checks, roles on every route, user management, last-admin protection, reset links, registration, audit trail, live sockets (unauthenticated, origin, session end), connection cap under bursts, migrations of an older database |
| `gateway.test.js` | 13 | bad simulation lines ignored; health, options, playlist and status; start needs a researcher or administrator and a valid body; one run broadcast to all viewers, late viewers get history, the result is stored; road map and car positions to everyone, car details only to followers; stop; SUMO-GUI guard; simulation arguments; GA and summary endpoints; playlist advance and recovery from a crashed run; runs left running marked interrupted |
| `driver.test.js` | 9 | one-driver seat: command validation; one viewer at a time, only their commands, rate limit, trip stored; drive to the destination; administrator switches driving off; drive ends when the run is replaced; reply ordering (two races found and fixed); seat freed after the idle time and when the driver leaves after the reconnect grace |
| `resilience.test.js` | 18 | simulation crash, failed start, invalid output, exit without result, gateway restart, database unavailable and failing writes, hostile input, path traversal, stored XSS, socket abuse, concurrency, retention, backup and restore |
| `playback.test.js` | 7 | platform events (run started, finished, connection limit) reach viewers and the run's history; pause, resume, speed and restart: refused to viewers and when nothing runs; the paused state is the one the simulation confirmed and simulated time stands still; only the speeds 0.5/1/2/4; restart from the first second; a researcher controls only their own run; audit entries; a paused run stops promptly |
| `snapshot.test.js` | 5 | history rows, HTTP compression, security headers, the traffic demand setting (validation, simulation argument, stored with the run) |
| `ambulance.test.js` | 5 | ambulance on demand (added after the final run, 14 November 2026): refused when nothing runs, to viewers and with invalid roads; dispatched into a fixed-time run with priority added, events in order (sent, preemption start and end, arrived), stored events and audit; dispatched trips in the stored result; the junctions' own priority with `adaptive_ev`, sent while paused, at most 3 on the road; a researcher only into their own run |

## 2. Gateway with real SUMO (`server/test/e2e.real.test.js`)

**Objective.** The gateway drives the real Python + SUMO simulation end to end.
**Procedure.** `npm run test:e2e` (`E2E_REAL=1`).
**Expected.** All pass.
**Actual.** **3 passed** in 23.3 s.
**Status.** Pass.

| Test | Checks |
|---|---|
| Ambulance run | an ambulance scenario on the grid: states, V2I alerts, preemption, the result stored |
| Driven car | a viewer drives a car through the grid; the trip report is stored |
| Playback | a real SUMO run is paused (simulated time stands still for 1.5 s), set to 4× and resumed: it then runs above 15 simulated seconds per second, and the history has no missing second |
| Ambulance on demand (14 November 2026) | an ambulance sent into a real fixed-time grid run: priority added at J0–J3, green at all 3 junctions it crosses, arrival, no collision; events and trip in the stored result. With it: **4 passed** |

## 3. Integration of the parts in the browser suites

The browser suites ([E2E report](E2E_TEST_REPORT.md)) also exercise the full chain:

- dashboard → gateway → Python → SUMO, against a gateway with real SUMO;
- the live map's car positions changing between frames;
- the module pages filling from the real run's data.

## 4. Defects found by these tests (all fixed, with a test that reproduces them)

| Phase | Defect |
|---|---|
| 2–3 | A late report from the simulation ended the next driver's drive |
| 8 | A second ordering of the same race; replies are now matched in order |
| 8 | Two administrators creating the same account at once got a 500 instead of 409 |
| 8 | A failing database write stopped a run's live stream |
| 8 | bcrypt hashing on the main thread blocked the server (2 sign-ins per second) |
| 9 | The retention query read the whole sample table (now indexed; test checks the plan) |
| 10 | The live socket did not check the browser's origin |
| after 10 | Test race, not a platform defect: the restart test read the first state as soon as the run was *RUNNING*, which the simulation's *started* message sets before its first state. It failed about 1 run in 3 once a sixth test file added load; it now waits for the first state (5 full runs passed) |

## 5. Limitations

- The mock simulation does not produce road speeds, incidents or ambulance routes. Those paths are
  covered by the real-SUMO tests and the browser suites, not by this suite.
- Tests run on Windows. The process-tree shutdown has a Windows branch (`taskkill`) and a POSIX
  branch; only the Windows branch was exercised.

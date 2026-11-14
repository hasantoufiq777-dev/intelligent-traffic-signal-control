# Test Report — Phase 8 (complete system testing)

Dates: 2026-11-12. Machine: Windows 11, Intel Core i7-12700H (20 threads), 15.7 GB RAM, Node 24.14.1,
Python 3.12, SUMO 1.28, Microsoft Edge driven by Playwright, axe-core 4.14. Every figure was measured
in these runs. Raw data: `docs/testing/data/` (`load-steps.json`, `load-steps-bcrypt.json`,
`load-soak.json`, `a11y.json`).

## 1. Summary

| Suite | Command | Result |
|---|---|---|
| Python simulation | `python -m pytest -q` | **129 passed** (10 min 32 s) |
| Gateway (mock simulation) | `npm --prefix server test` | **69 passed**, 2 skipped (the real-SUMO tests below); 22 new |
| Gateway with real SUMO | `npm run test:e2e` | **2 passed** |
| Dashboard unit tests | `npm --prefix client test` | **13 passed** |
| Browser journey | `node tests/e2e/smoke.mjs` | **10 / 10** |
| Every page | `node tests/e2e/pages.mjs` | **22 / 22** |
| Roles (viewer, researcher, administrator) | `node tests/e2e/roles.mjs` | **14 / 14** |
| Responsive (18 pages × 6 widths) | `node tests/e2e/responsive.mjs` | **108 / 108**, no overflow, no console errors |
| Accessibility (axe, WCAG 2.0–2.2 A/AA) | `node tests/e2e/a11y.mjs` | **0 violations in 45 audits**; keyboard checks passed |
| Dependency audit (production) | `npm audit --omit=dev` | **0 vulnerabilities** in server, dashboard and root (after the fix in 3.4) |
| Load: 10 → 1000 users, reconnect, spike, sign-ins | `node server/loadtest/steps.js` | section 4 |
| Soak: 1000 users for 30 minutes | `node server/loadtest/steps.js --steps 1000 --soak 30` | section 4.3 |
| Dashboard load time | `node tests/e2e/loadtime.mjs` | met on the local network; **not met** on a 10 Mbit/s link (section 5) |

## 2. What the new gateway tests cover (`server/test/resilience.test.js`, additions in `auth.test.js`, `driver.test.js`)

| Area | Checked behaviour |
|---|---|
| Worker crash | The simulation is killed mid-run: lifecycle FAILED, viewers get `status: error`, the run is stored as `error`, nothing restarts on its own without a playlist, an explicit start recovers |
| Start failure | A missing Python interpreter: the start is accepted, then reported FAILED (never shown as running); `/api/system` counts the failure |
| Invalid output | Non-JSON, broken JSON, unknown types, arrays and a 200 000-character line are ignored and counted (5 bad lines); the valid messages around them are used and stored |
| Exit without result | Exit code 0 but no result: treated as a failure |
| Gateway shutdown mid-run | The simulation process is gone after `close()`; closing twice is harmless |
| Gateway restart | Accounts and sessions survive (same cookie, same role); the interrupted run is marked `interrupted`; lifecycle STOPPED |
| Reconnect | A reconnecting viewer gets the current run's snapshot with its history |
| Database unavailable | `/api/system` answers 503 "Database is not available", other requests a plain 500; no stack trace, path or SQL text reaches the client; normal service resumes without a restart |
| Database write failure during a run | Every write fails (disk full): viewers keep receiving the stream, errors are counted (`persist_errors`) and logged at most every 10 s, no crash; the next run is stored normally |
| Hostile input | Oversized body 413, broken JSON 400, negative seed, path in `network`, shell text in `scenario`, prototype keys: all 400, nothing started; unknown routes 404; paging limits clamped; SQL text in a filter or id has no effect |
| Stored XSS | Script text in a user name is stored and returned as JSON data with `nosniff`, never as HTML |
| Path traversal | `..`, encoded `..%2f`, `%2e%2e` requests never return `.env`, `package.json` or the database file |
| Socket abuse | A viewer's driver commands, forged resume keys, injection text, wrong payload types and made-up event names change nothing and do not disconnect the server |
| Concurrency | 25 users sign in and read at once (all 200); two administrators create the same account at once: exactly one 201 and one 409 |
| Password hashes | Argon2id with its own salt per user; an old bcrypt hash works once and is replaced at sign-in; malformed stored hashes never match; 24 parallel sign-ins do not stall other requests |
| Driver replies in order | A drive ended before its car was named, then a new drive: the late reply is matched to the old drive and the new drive goes on; a failed start ends only its own drive |
| Retention | State samples older than `SAMPLE_RETENTION_DAYS` (14) are deleted at start-up and every 6 h; runs, results and events are kept |
| Backup and restore | `npm run backup` copies the database while it is in use; the copy opens with integrity `ok`, all migrations, the users (passwords work) and the results |
| Indexes | Session, email, events, samples, audit and run-list lookups use indexes (query plans checked) |

## 3. Defects found and fixed

### 3.1 Password hashing blocked the server (performance, security availability)
The first stepped load test measured **2 sign-ins per second**: 50 simultaneous sign-ins took 25 s,
and the event-loop lag reached 110 ms, because the pure-JavaScript bcrypt ran on the main thread.
Hashing now uses Node's built-in Argon2id (19 MiB, 2 passes; scrypt on Node versions without it)
on the thread pool, at most two at a time. Result: **200 simultaneous sign-ins in 13.2 s
(15.2 per second)**, and an API request during that burst answered in 1.5 ms. Existing bcrypt hashes
still work and are upgraded at the next sign-in.

### 3.2 Two administrators creating the same account at once got a 500
Both passed the "email free?" check before either insert. The database's unique constraint now
decides, and the second request gets 409 "already exists".

### 3.3 Driver seat: a late reply could end the next driver's drive (second race)
Phase 2–3 fixed one case. Under full-suite load a different ordering still failed once in seven runs:
the simulation's reply to a failed or ended start was taken for the new drive. Replies are now
matched to starts in the order the starts were sent. Two new tests drive the exact sequence; the full
gateway suite then passed 8 times in a row.

### 3.4 Dashboard dependency with known vulnerabilities
`npm audit` reported 3 high-severity advisories in React Router 6.30.2 (open redirect leading to XSS)
and, after updating within version 6, 2 moderate ones fixed only in version 7. The dashboard now uses
React Router 7.18.5: 0 production vulnerabilities, and all browser suites pass on it. (The remaining
advisories are in development-only tools — the test runner and the development server — and do not
reach the built site.)

### 3.5 Database writes could stop a run's stream
A failing write (for example a full disk) threw inside the message handler. Writes during a run are
now guarded: the stream continues and the errors are counted and logged.

### 3.6 Accessibility (axe found 4 rule violations on the first audit, 27 occurrences)

| Rule | Where | Fix |
|---|---|---|
| Colour contrast | White on the blue primary buttons, dark theme: 3.98 : 1 | Darker accent `#0b72b0`: 5.19 : 1 (and 3.3 : 1 against the background for focus and selection marks) |
| Button name / label mismatch | The account button on phones showed only an initial | The name stays readable for screen readers on phones (visually hidden, not removed) |
| ARIA on plain elements | Queue bars and map layer groups | `role="img"` / `role="group"` |
| Scrollable region without keyboard access | Formula boxes, wide tables on phones, the event list | Formulas wrap; tables and the event list can be focused and scrolled from the keyboard |
| Focus ring (keyboard check) | Map junctions had only a border highlight | A real focus ring as well |

After the fixes: **0 violations in 45 audits** (18 signed-in pages in the dark and light themes, 3
signed-out pages in both themes, 3 pages at 390 px). Keyboard: the skip link is first and moves
focus to the content, all 18 navigation links are reachable, every focus stop has a visible ring,
and the map zooms and pans from the keyboard.

## 4. Load and capacity (measured)

Gateway with the mock simulation (`USE_MOCK=true`, a live run playing), `MAX_CONNECTIONS=1000`,
rate limits raised because all test traffic comes from one address. Every user is signed in. Each
step: N live connections, then 20 s of holding while the test calls the API (status, run list,
session, options) at about 32 requests per second. The test clients run on the same machine, so
network latency is not included and the clients' own CPU use competes with the server.

### 4.1 Steps

| Users | Connected | Connect p95 (ms) | Drops | Stream latency p50 / p95 / p99 (ms) | API p50 / p95 / p99 (ms) | API errors | Gateway CPU (one core) | Memory RSS | Event-loop lag p99 | Outgoing |
|---|---|---|---|---|---|---|---|---|---|---|
| 10 | 10 (100 %) | 47 | 0 | 1 / 3 / 4 | 2.3 / 4.3 / 5.5 | 0 | 7.0 % | 77 MB | 16 ms | 30 KB/s |
| 50 | 50 (100 %) | 140 | 0 | 3 / 9 / 12 | 2.2 / 4.4 / 7.9 | 0 | 4.8 % | 82 MB | 16 ms | 164 KB/s |
| 100 | 100 (100 %) | 197 | 0 | 6 / 11 / 13 | 2.2 / 4.2 / 10.8 | 0 | 6.2 % | 91 MB | 16 ms | 341 KB/s |
| 250 | 250 (100 %) | 160 | 0 | 12 / 24 / 29 | 2.1 / 5.1 / 23.6 | 0 | 4.8 % | 106 MB | 50 ms | 883 KB/s |
| 500 | 500 (100 %) | 160 | 0 | 20 / 43 / 51 | 2.3 / 19.3 / 44.9 | 0 | 7.3 % | 141 MB | 45 ms | 1.7 MB/s |
| 750 | 750 (100 %) | 173 | 0 | 36 / 68 / 77 | 2.4 / 41.2 / 65.9 | 0 | 10.5 % | 150 MB | 30 ms | 2.6 MB/s |
| 1000 | 1000 (100 %) | 156 | 0 | 43 / 81 / 88 | 2.2 / 55.1 / 83.5 | 0 | 9.1 % | 210 MB | 29 ms | 3.5 MB/s |

Every connected user received one state per second (0.98–1.0). Against the targets in the brief:
API p95 below 500 ms (**55 ms** at 1000 users), p99 below 1000 ms (**84 ms**), at least 99 %
successful connections (**100 %** at every step), no dropped connections.

### 4.2 Reconnection, spike, sign-in burst

| Test | Result |
|---|---|
| Reconnection: 500 of 1000 users dropped at once and reconnected immediately | 500 / 500 back within 2.8 s (connect p95 2.6 s); 1000 connected afterwards |
| Spike: 1100 connections opened at the same moment | 1000 accepted, **100 refused with `capacity`**, 0 failed; all accepted within 5.7 s; 0 drops afterwards; API p95 59 ms; event-loop lag p99 499 ms during the burst |
| Sign-in burst before the fix (bcrypt) | 50 at once: all succeeded, 25.4 s in total, **2 per second** |
| Sign-in burst after the fix (Argon2id) | 200 at once: all succeeded, 13.2 s in total, **15.2 per second**, API answered in 1.5 ms during the burst |

### 4.3 Soak: 1000 users for 30 minutes

| Measure (once a minute, 30 samples) | Result |
|---|---|
| Users connected | 1000 in every sample |
| Dropped connections / API errors / storage errors | 0 / 0 / 0 |
| Stream latency p95, worst minute | 147 ms (p99 170 ms) |
| API latency p95 / p99, worst minute | 92 ms / 147 ms |
| Memory RSS | 237 MB at minute 1, then 122–232 MB; flat at 230–232 MB over the last 14 minutes |
| JavaScript heap | Rises and falls between 33 and 86 MB with garbage collection; no upward trend |
| Event-loop lag p99, worst minute | 54 ms |
| Simulation at the end | Still RUNNING |

Memory and queues stayed bounded under this load.

## 5. Dashboard load time

Time from signing in (or opening the page while signed in, with an empty cache) until the live map
shows cars; three runs each.

| Network | Sign-in to cars | Fresh page load to cars | Target (3 s) |
|---|---|---|---|
| Local | 0.72–0.81 s | 0.60–0.66 s | met |
| 10 Mbit/s with 40 ms extra latency (emulated) | 5.76–6.31 s | 5.61–6.14 s | **not met** |

Cause, measured: the gateway sends the main script uncompressed (315 KB, about 100 KB compressed),
and a new viewer's first message carries the last 120 simulated seconds of state history, 363 KB,
also uncompressed. This is the first item for Phase 9 (optimisation), with a new measurement after.

## 6. Security checks done in this phase

Access control, session handling, CSRF, rate limits and lockout were tested in Phase 2–3 (27 tests,
still passing). Added here: stored XSS, path traversal, SQL text in parameters, oversized and
malformed bodies, prototype-pollution keys, socket events by an unauthorised user, the
duplicate-account race, behaviour when the database fails, and the production dependency audit.
Not done: an external penetration test or an automated dynamic scanner (for example OWASP ZAP);
HTTPS behaviour was tested through the `COOKIE_SECURE` setting, not on a real certificate.

## 7. Known limits of these results

- The load tests use the mock simulation (the real SUMO run costs the same gateway work per viewer,
  but SUMO's own CPU is not included) and run the clients on the same machine.
- The connection limit is per gateway process; several gateway instances would need a shared
  counter (not built; a single instance carried 1000 users at about 10 % of one core).
- Password-reset links are handed over by an administrator; there is no mail server.

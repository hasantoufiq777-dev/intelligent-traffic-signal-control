# Phase 9 report: profiling and optimisation

Date: 12 November 2026. Every figure below was measured on the machine described in §6; the raw data
is in [`data/`](data/). Each change has a before and an after measured the same way.

## 1. Summary

| Problem found by profiling | Change | Before | After |
|---|---|---|---|
| Dashboard slow to show cars on a slow link (missed target from Phase 8) | Smaller first socket message; HTTP compression | 5.6–9.2 s | 1.1–1.9 s (target 3 s: **met**) |
| A new viewer's first message (`init`) carried 120 full states | History keeps only the fields the charts read | 350 KB | 64 KB |
| Main script and API sent uncompressed | gzip for HTTP responses over 1 KB | 315 KB | 101 KB on the wire |
| Hashed build files re-checked every hour | `assets/*` cached for a year, `immutable` | `max-age=3600` | `max-age=31536000, immutable` |
| Telemetry retention read the whole sample table (382,348 rows, 1.6 GB) at start-up and every 6 h, blocking the server | Index on `state_samples.created_at` (migration 4) | 1.1–3.9 s blocked | 0.1 ms |

Side effects measured under load (1,000 signed-in users, mock simulation, same settings as Phase 8):
API p95 55 → 15.5 ms, live-stream p95 81 → 34 ms, 500 reconnects all back in 2.8 → 0.34 s,
a 1,100-connection spike fully admitted in 5.7 → 0.7 s.

Profiled and left unchanged, because the measurement showed no problem: the browser rendering of every
live page (§4) and the remaining database queries (§3).

## 2. Load time on a slow link

**Objective.** After sign-in the live map shows moving cars within 3 s on a 10 Mbit/s link.

**Procedure.** `node tests/e2e/loadtime.mjs <url> <email> <password>`: Edge, cold cache, three runs per
condition, time from clicking *Sign in* (and from a fresh page load of a signed-in user) until the
first car is drawn. The throttled condition uses Chrome network emulation: 10 Mbit/s each way, 40 ms
latency. Gateway with a real SUMO run (grid 2×2).

**Diagnosis.** A request timeline (Chrome DevTools Protocol, throttled) showed where the time went:

| Before | at |
|---|---|
| page script (315 KB) finished | 0.69 s |
| socket opened | 0.80 s |
| first socket message (256 KB at that moment) received | 4.63 s |
| cars drawn | 4.81 s |

The script was not the main cost. The first socket message was: Chrome's emulation passes WebSocket
data much more slowly than HTTP (about 67 KB/s here). Real links do not usually treat WebSockets
like that, but the message was large either way. 97 % of it was `states`, the last 120 simulated
seconds, each a full state of about 2.9 KB. Of that, 1.9 KB was the per-junction block with
signal timing (SPaT for every approach), signal letters and estimated queues, plus road speeds and
eco data. The history charts read only time, metrics, each junction's phase and queues, the VANET
delivery ratio and three navigation averages; the complete latest state is sent beside the history
and is unchanged.

**Changes.**
- `historyRow()` in [`server/src/liveChannel.js`](../../server/src/liveChannel.js): the gateway stores
  history as chart rows (about 0.5 KB per state). Live `state` broadcasts are unchanged.
- [`compression`](https://www.npmjs.com/package/compression) 1.8.2 for HTTP (1.8.1 has a high-severity
  advisory, GHSA-vc2v-76pw-4v95; `npm audit --omit=dev` reports 0 vulnerabilities after).
  Socket.io stays uncompressed on purpose: per-message deflate would compress every broadcast once
  per viewer.
- Long-term caching of the content-hashed files under `assets/`; `index.html` stays `no-cache`.

**Result** ([`data/phase9-loadtime.json`](data/phase9-loadtime.json)):

| Condition | Measure | Before | After |
|---|---|---|---|
| Local | sign-in → cars | 747 / 770 / 762 ms | 715 / 656 / 647 ms |
| Local | cold load → cars | 627 / 680 / 663 ms | 632 / 636 / 625 ms |
| 10 Mbit/s + 40 ms | sign-in → cars | 5,752 / 5,761 / 5,790 ms | 1,161 / 1,155 / 1,145 ms |
| 10 Mbit/s + 40 ms | cold load → cars | 5,641 / 5,650 / 9,158 ms | 1,443 / 1,918 / 1,894 ms |

First-message size, full 120-state history (`node server/loadtest/initsize.js`):

| | Before | After |
|---|---|---|
| whole message | 349.6 KB | 64.3 KB |
| `states` | 341.3 KB | 55.6 KB |
| one history state | ≈ 2.9 KB | ≈ 0.46 KB |

Main script on the wire: 315,401 → 104,334 bytes (gzip). Status: **pass** (target 3 s).

## 3. Database

`EXPLAIN QUERY PLAN` for the gateway's queries on the real database (809 runs, 262,748 events,
382,348 state samples, 1.6 GB):

| Query | Plan |
|---|---|
| run list (paged) | index `idx_runs_started` + covering index on results |
| events of a run | index `idx_events_run` |
| samples of a run | index `idx_samples_run` |
| session by token, user by e-mail | unique indexes |
| audit retention | index `idx_audit_at` |
| driver trips (latest 20) | rowid order, stops at the limit |
| **sample retention** | **full table scan** |

The sample retention (added in Phase 8) ran at start-up and every 6 h on the event loop. On this
database it took 1,223 / 1,087 ms warm and 3,946 ms cold, and found nothing to delete (all samples
were younger than 14 days), so the cost grows with the table and buys nothing until data ages out.

**Change.** Migration 4 adds `idx_samples_created` on `state_samples(created_at)`.
**After** (copy of the same database): `pruneSamples` 0.1 / 0.0 / 0.0 ms. One-time cost: building the
index took 1,423 ms when the 1.6 GB database was opened by the new version. A test checks that the
retention query uses the index after migrating an older database.

## 4. Browser rendering

`node tests/e2e/render.mjs <url> <email> <password> 20 [cpu slowdown]`: 20 s on each live page while a
real SUMO run plays (congested scenario, up to 52 cars on the map); frames per second and main-thread
long tasks (over 50 ms) ([`data/phase9-render.json`](data/phase9-render.json)).

| Page | fps | Long tasks | fps, CPU slowed 4× | Long tasks, CPU slowed 4× |
|---|---|---|---|---|
| Live traffic (map) | 60 | 0 | 60.1 | 0 |
| Live overview (5 charts) | 60.1 | 0 | 56.8 | 20 in 20 s, mean 79 ms, longest 103 ms |
| Signals | 60.1 | 0 | 59.9 | 0 |
| VANET | 60.1 | 0 | 60 | 0 |
| Navigation | 60 | 0 | 60 | 0 |

The only long tasks are on the live overview with the CPU slowed 4×: one per second, when the five
history charts redraw. At normal speed that is about 20 ms, under the 50 ms threshold, and the page
still renders 57 fps when slowed. Chart animations were already off. No change was made; if weaker
devices matter, redrawing these charts every 2 s instead of every second would halve the cost.

## 5. Regression checks after the changes

| Suite | Result |
|---|---|
| Gateway tests (`npm test` in `server/`) | 72 passed, 2 skipped (real-SUMO tests, run separately) |
| Dashboard unit tests | 13 passed |
| Browser smoke journey | all passed |
| Every page and its controls (`pages.mjs`) | all passed |
| Roles (`roles.mjs`) | all 14 passed |
| Responsive, 18 pages × 6 widths | 108 / 108, no overflow, no console errors |
| Accessibility (axe, WCAG 2.2 AA), 45 audits | 0 violations; keyboard checks pass |
| Load: 100 / 500 / 1,000 users, spike 1,100, 500 reconnects | 100 % connected; capacity refusals exactly 100; 0 failures |

Two test-script faults found while re-running, both fixed:

- **Viewer reconnect step in `roles.mjs`.** The step waited 30 s for the *Reconnecting* badge. Going
  offline does not close an open WebSocket. With the simulation stopped, no data flows, so the client
  notices only when a heartbeat is missed, which takes up to 45 s (25 s ping interval plus 20 s
  timeout). The step now waits 60 s and always restores the network.
- **Failure screenshots.** The script wrote them into the working directory; they are now written
  only when `SHOTS=<dir>` is set.

The run-comparison steps need two finished runs in the database. On a brand-new database they
cannot pass until two runs have finished, so `pages.mjs` and `roles.mjs` were run against a gateway
whose mock runs finish in seconds. The other suites ran against a real SUMO run.

## 6. Environment

Windows 11 Pro, Intel Core i7-12700H (20 threads), 15.7 GB RAM, Node 24.14.1, Microsoft Edge (headless,
through playwright-core), SUMO 1.28. Server and test clients on the same machine, so network latency
is only what the emulation adds.

## 7. Limits of these measurements

- The slow link is emulated by the browser, not a real network. Its unusually slow WebSocket
  throughput makes it a pessimistic case for the socket message.
- The road network is the 2×2 grid with at most about 50 cars; a much larger network was not profiled.
- Load figures use the mock simulation (as in Phase 8) and clients on the same machine.

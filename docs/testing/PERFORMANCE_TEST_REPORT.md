# Performance test report

Front-end and back-end performance outside the load tests ([load test report](LOAD_TEST_REPORT.md)).
The optimisation work with every before/after measurement is in the [Phase 9 report](PHASE_9_REPORT.md);
this report gives the final state and how to measure it. Final runs: 12 November 2026, on the final
code (commit `608564b`) and build (Vite 8.3.8), with a real SUMO run (environment: [test plan §1](TEST_PLAN.md#1-environment-all-reported-runs)).

## 1. Time until the live map shows cars

**Objective.** After sign-in the live map shows moving cars within 3 s, also on a slow connection.
**Procedure.** `node tests/e2e/loadtime.mjs <url> <email> <password>`. Edge with an empty cache,
three runs per condition. It measures from clicking *Sign in*, and from a fresh page load of a
signed-in user, until the first car is drawn. The slow condition is Chrome's network emulation at
10 Mbit/s each way with 40 ms latency.
**Expected.** Under 3 s in both conditions.
**Actual** ([`data/final-loadtime.json`](data/final-loadtime.json)):

| Condition | Sign-in → cars | Fresh load → cars | Phase 8 (before optimisation) |
|---|---|---|---|
| Local | 0.64–0.66 s | 0.62–0.64 s | 0.72–0.81 s / 0.60–0.66 s |
| 10 Mbit/s + 40 ms | 1.14–1.18 s | 1.39–1.41 s | 5.76–6.31 s / 5.61–6.14 s |

**Status.** Pass. In Phase 8 the slow-link case failed; Phase 9 found the cause (a 350 KB first
socket message and uncompressed scripts) and fixed it.

## 2. Payload sizes

| Item | Size | How measured |
|---|---|---|
| Main script | 302 KB, **sent as 94–97 KB** (gzip) | build output; `prodcheck.mjs` |
| Largest lazy chunk (charts) | 381 KB, 101 KB gzip; loaded only by pages with charts | build output |
| First socket message with a full 120 s history | **64 KB** (63.8 KB on the final code; was 350 KB) | `node server/loadtest/initsize.js` |
| Live stream per viewer | about 3.1 KB/s (3.1 MB/s for 1,000 viewers) | load test, outgoing bandwidth |

Build files have content hashes and are cached for a year (`immutable`); `index.html` is never
cached. Every page is loaded on demand (route-level code splitting).

## 3. Rendering

**Objective.** Live pages stay smooth while data arrives every second.
**Procedure.** `node tests/e2e/render.mjs <url> <email> <password> 20 [slowdown]`: 20 s per page,
counting frames per second and main-thread tasks over 50 ms.
**Actual** ([`data/final-render.json`](data/final-render.json); the 4× slowdown from Phase 9,
[`data/phase9-render.json`](data/phase9-render.json)):

| Page | fps | Long tasks | CPU slowed 4×: fps | CPU slowed 4×: long tasks |
|---|---|---|---|---|
| Live traffic (map) | 60 | 0 | 60.1 | 0 |
| Live overview (5 charts) | 59.9 | 0 | 56.8 | 20 in 20 s (mean 79 ms) |
| Signals | 60.1 | 0 | 59.9 | 0 |
| VANET | 60 | 0 | 60 | 0 |
| Navigation | 60.1 | 0 | 60 | 0 |

**Status.** Pass. The only long tasks appear with a 4× slower CPU on the chart page, once per
second when the five charts redraw. At normal speed that is about 20 ms, under the 50 ms threshold.

## 4. Server

| Measure | Result | Source |
|---|---|---|
| API latency at 1,000 users | p50 2.8 ms, p95 20.3 ms, p99 40.8 ms | load test |
| Event-loop lag at 1,000 users | p99 17 ms | load test |
| CPU / memory at 1,000 users | 9.8 % of one core / 215 MB | load test |
| Sign-ins in parallel | 15.2 per second without blocking other requests (Argon2id, 2 at a time off the main thread) | Phase 8 |
| Database queries | all use an index or the rowid; retention 1.1–3.9 s → 0.1 ms with the Phase 9 index | Phase 9 |

## 5. How to run the performance tests

```bash
# a gateway with a live run (real SUMO) and an account
cd server && LIVE_LOOP=true ADMIN_EMAIL=perf@example.org ADMIN_PASSWORD='<10+ characters>' npm start
# from the project root, in another terminal
node tests/e2e/loadtime.mjs http://localhost:4000 perf@example.org '<password>'
node tests/e2e/render.mjs   http://localhost:4000 perf@example.org '<password>' 20      # add 4 for a 4x slower CPU
node server/loadtest/initsize.js http://localhost:4000 perf@example.org '<password>'
```

The browser scripts drive Microsoft Edge through `playwright-core`; `npm install` at the project
root installs it.

## 6. Limitations

- The slow link is emulated by the browser, and that emulation passes WebSocket data unusually
  slowly (about 67 KB/s measured), which makes it a pessimistic case.
- The road network is the 2×2 grid with at most about 50 cars. A much larger network would draw
  more map elements; it was not profiled.
- Only Edge (Chromium) was measured.

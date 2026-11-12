# Test plan

Inventory of every test suite, the environment it runs in, and how to run it. Approach and
priorities: [test strategy](TEST_STRATEGY.md).

## 1. Environment (all reported runs)

| Item | Value |
|---|---|
| Machine | Windows 11 Pro 10.0.26300, Intel Core i7-12700H (20 threads), 15.7 GB RAM |
| Runtime | Node.js 24.14.1, Python 3.12, SUMO 1.28 |
| Browser | Microsoft Edge (headless) through playwright-core 1.65; axe-core 4.14 |
| Network | server and clients on the same machine; slow-link tests use Chrome network emulation (10 Mbit/s, 40 ms) |
| Gateways under test | started for each run with a fresh database in a temporary folder and a test administrator from `ADMIN_EMAIL`/`ADMIN_PASSWORD`; real SUMO, or the mock (`USE_MOCK=true`) where stated |

## 2. Inventory

| # | Suite | Location | Tests | Run with | Needs |
|---|---|---|---|---|---|
| 1 | Python simulation | `sim/tests/` (22 files) | 138 | `npm run test:py` | Python venv, SUMO |
| 2 | Gateway integration | `server/test/` (auth, gateway, driver, resilience, playback, snapshot) | 82 | `npm run test:server` | — |
| 3 | Gateway with real SUMO | `server/test/e2e.real.test.js` | 3 | `npm run test:e2e` | SUMO |
| 4 | Dashboard unit | `client/src/lib/*.test.js` | 13 | `npm run test:client` | — |
| 5 | Browser journey | `tests/e2e/smoke.mjs` | 10 steps | `node tests/e2e/smoke.mjs <url> <email> <password>` | running gateway, Edge |
| 6 | Every page and control | `tests/e2e/pages.mjs` | 24 steps | same arguments | two finished runs in the database |
| 7 | Roles | `tests/e2e/roles.mjs` | 14 steps | same arguments, administrator account | a gateway the test may stop and start |
| 8 | Responsive | `tests/e2e/responsive.mjs` | 18 pages × 6 widths | same + output folder | — |
| 9 | Accessibility | `tests/e2e/a11y.mjs` | 45 audits + keyboard | same + output JSON | — |
| 10 | Accessibility during ambulance priority | `tests/e2e/a11y-ambulance.mjs` | 2 audits | same; starts the `emergency` scenario | real SUMO |
| 11 | Load time | `tests/e2e/loadtime.mjs` | 12 timings | same | — |
| 12 | Rendering | `tests/e2e/render.mjs` | 5 pages × 20 s | same + seconds + CPU slowdown | — |
| 13 | Load | `server/loadtest/steps.js` | steps, spike, reconnect, logins, soak | see [load test report](LOAD_TEST_REPORT.md) | raised rate limits |
| 14 | First-message size | `server/loadtest/initsize.js` | — | `<url> <email> <password>` | — |
| 15 | Production configuration | `tests/deploy/prodcheck.mjs` | 8 checks | `<https url> <email> <password> [--insecure]` | gateway in production mode behind HTTPS |
| 16 | Screenshots | `tests/e2e/shots.mjs` | — | for the reports | — |
| 17 | Live details | `tests/e2e/live-details.mjs` | 4 steps | `<url> <email> <password>`; starts a navigation run | real SUMO |

`npm test` at the project root runs suites 1, 2 and 4.

## 3. Order of a full run

1. Suites 1–4: no browser needed.
2. Start a gateway with real SUMO and `LIVE_LOOP=true`; run 5, 8, 9, 10, 11, 12, 17.
3. Start a gateway with `USE_MOCK=true MOCK_MAX_STEPS=150 LIVE_LOOP=true` (runs finish within
   seconds); once two runs have finished, run 6 and 7.
4. On the same mock gateway, with `AUTH_RATE_LIMIT`, `RATE_LIMIT_PER_MIN` and `SESSIONS_PER_USER`
   raised, run 13.
5. Start a gateway with the values of `deploy/gateway.env.production.example` behind HTTPS (locally:
   `tests/deploy/tls-proxy.mjs` with a self-signed certificate) and run 15.

## 4. Reports

| Report | Content |
|---|---|
| [UNIT_TEST_REPORT](UNIT_TEST_REPORT.md) | suites 1 and 4 |
| [INTEGRATION_TEST_REPORT](INTEGRATION_TEST_REPORT.md) | suites 2 and 3 |
| [E2E_TEST_REPORT](E2E_TEST_REPORT.md) | suites 5, 6, 7, 10 and the production browser check |
| [SECURITY_TEST_REPORT](SECURITY_TEST_REPORT.md) | security tests across all suites, dependency audits |
| [PERFORMANCE_TEST_REPORT](PERFORMANCE_TEST_REPORT.md) | suites 11, 12, 14, database query plans; how to run them |
| [LOAD_TEST_REPORT](LOAD_TEST_REPORT.md) | suite 13; how to run it |
| [RESPONSIVENESS_REPORT](RESPONSIVENESS_REPORT.md) | suites 8, 9 and 10 |
| [FAILURE_RECOVERY_REPORT](FAILURE_RECOVERY_REPORT.md) | failure and recovery tests, backup, restore, rollback |
| [REGRESSION_TEST_REPORT](REGRESSION_TEST_REPORT.md) | research-result regression and the final full run |
| [FINAL_ACCEPTANCE_REPORT](FINAL_ACCEPTANCE_REPORT.md) | every acceptance criterion with its evidence |
| Phase reports | [2–3](PHASE_2_3_REPORT.md), [4–5](PHASE_4_5_REPORT.md), [6–7](PHASE_6_7_REPORT.md), [8](PHASE_8_REPORT.md), [9](PHASE_9_REPORT.md): what each phase found and fixed |

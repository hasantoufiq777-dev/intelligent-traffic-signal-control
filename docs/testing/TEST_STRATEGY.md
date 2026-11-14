# Test strategy

How the platform is tested and why. The concrete inventory, environment and commands are in the
[test plan](TEST_PLAN.md); the results are in the reports listed there.

## 1. Goals, in priority order

1. **The research stays correct.** The web work must not change any simulation result.
2. **Data reaching the screen is real and accurate.** What the map and panels show is what the
   simulation produced, for the run that is actually playing.
3. **Access control holds.** Every role rule is enforced by the server, not only hidden in the UI.
4. **The live service is reliable.** Failures of the simulation, the database or the network are
   reported and recovered from, never shown as "running".
5. **It carries the required load.** 1,000 concurrent signed-in users, with the limit enforced.
6. **It is usable.** Every page on every screen size, by keyboard and screen reader.

## 2. Levels

| Level | Tool | What it covers | Simulation used |
|---|---|---|---|
| Unit (Python) | pytest | controllers, VANET channel, preemption, navigation, incidents, GLOSA, GA, work zone, live protocol, **zero-regression against stored baselines** | real SUMO |
| Unit (dashboard) | vitest | pure logic: congestion rules, GLOSA advice, vehicle flags | none |
| Integration (gateway) | vitest + supertest + socket.io-client | every REST route and socket event through the real Express/Socket.io stack, with a real SQLite database (in memory or a temporary file) | mock simulation, or fake processes that crash or print garbage |
| End-to-end (gateway + SUMO) | vitest (`E2E_REAL=1`) | an ambulance run with V2I preemption and a driven car, through the real Python and SUMO | real SUMO |
| Browser | playwright-core driving Microsoft Edge | user journeys per role, every page and its controls, sign-in to live map | real SUMO (journeys, layout, accessibility), mock (run comparison needs finished runs) |
| Responsive and accessibility | Playwright + axe-core 4.14 | 18 pages × 6 widths for overflow and errors; WCAG 2.0–2.2 A/AA rules in both themes; keyboard operation | real SUMO |
| Load | custom Node script (`server/loadtest/steps.js`) | stepped load to 1,000 users, spike, mass reconnect, sign-in burst, 30-minute soak | mock (the gateway's work per viewer is the same) |
| Performance | Playwright, CDP throttling, query plans | load time, first-message size, rendering, database queries | real SUMO |
| Production configuration | `tests/deploy/prodcheck.mjs` | headers, cookies, origin rules, errors, HTTPS/WSS sign-in in production mode | real SUMO |

**Why a mock simulation for most gateway tests.** It is deterministic and fast, and it produces the
same message types as the real one. The real path is still covered: by the real-SUMO end-to-end
tests, by the browser suites (which run against real SUMO), and by the Python suite.

## 3. Risk-based focus

| Risk | Tests aimed at it |
|---|---|
| Web changes alter research results | `sim/tests/test_regression.py` (12 runs, identical to baseline); live-mode run compared with `results/raw.csv`; diff of `sim/` since the last research commit |
| UI shows "running" when it is not | lifecycle tests (crash, failed start, exit without result, gateway restart); browser checks of the now-playing bar |
| Privilege escalation | 401/403 on every route per role; self-promotion; last-admin protection; socket events from unauthorised users; browser journeys per role |
| Session theft or forgery | hashed tokens, cookie flags, CSRF, origin checks on API and socket, revocation on sign-out, password change, reset and disabling |
| Overload | connection cap under bursts; event-loop blocking (found: bcrypt); bounded memory over 30 minutes |
| Races | concurrent start/stop (409), duplicate-account race (found), driver-seat ordering (found twice) |
| Data loss | backup while running, restore, rollback over a newer schema, interrupted-run marking |

## 4. Rules

- **No fabricated results.** Every number in a report comes from a run recorded in that report,
  with the raw data in `docs/testing/data/` where it is large.
- **Defects are fixed at the cause**, and a test that reproduces them is added.
- **After every phase**, all suites are re-run; a phase ends only with no failing test.
- **A flaky test is a finding.** Several "intermittent" failures were real races (driver seat,
  reconnect timing) and were fixed in the code or the test, not retried away.
- **Limitations are stated** where a test could not be done (see each report's last section).

## 5. Entry and exit criteria

- **Entry, for a phase's tests:** the code builds, and the previous phase's suites pass.
- **Exit, for the project:** every suite passes on the final code, every acceptance criterion in
  the brief has evidence or a documented reason
  ([final acceptance report](FINAL_ACCEPTANCE_REPORT.md)), and no known critical defect is open.

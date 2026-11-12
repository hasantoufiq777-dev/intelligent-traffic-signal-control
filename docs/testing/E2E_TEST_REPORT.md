# End-to-end test report

Browser tests through the whole stack: Microsoft Edge (headless, playwright-core) → dashboard →
gateway → Python → SUMO. Final run: 12 November 2026, on the final code (environment:
[test plan §1](TEST_PLAN.md#1-environment-all-reported-runs)). Each script fails on any console
error or uncaught page error, including Content-Security-Policy violations.

## 1. Main journey (`tests/e2e/smoke.mjs`)

**Objective.** A user can sign in, see live traffic, move between the main pages and sign out.
**Environment.** Gateway with real SUMO, playlist running.
**Expected / actual.** 10 of 10 steps / **10 of 10 passed**. **Status: pass.**

Steps:

1. Redirects to the sign-in page.
2. Refuses a wrong password.
3. Signs in and shows the live map with cars.
4. Cars move between frames.
5. Opens Live overview.
6. Opens Results.
7. Opens Run control.
8. Opens Administration.
9. Administration lists users and system health.
10. Signs out and loses access (the API then answers 401).

The same journey also passed through the Vite development server (`npm run dev`) after the
Phase 10 fix to its proxy (§5).

## 2. Every page and its controls (`tests/e2e/pages.mjs`)

**Objective.** Every page shows its real content without errors, and the key interactions work.
**Environment.** Gateway with the mock simulation, after two runs had finished (needed for the
comparison).
**Expected / actual.** 24 of 24 steps / **24 of 24 passed**. **Status: pass.**

| Steps | Checks |
|---|---|
| 16 pages | Live overview, Signals, VANET, Emergency, Navigation, Incidents, Work zone, Eco-driving, Events, Results, GA optimisation, Run history, Documentation, Guided demo, Run control, Administration: each opens with its own content |
| Work-zone results | the evaluated rows from `results/summary.json` are shown |
| GA page | both targets (signals, ACO) with real numbers from `results/ga/` |
| CSV download | results download as a CSV file |
| Run comparison | two stored runs compared side by side |
| Guided demo | step 3 starts its scenario and the map shows that run |
| Pause to explain | the demo pauses the run: *Paused* (as confirmed by the simulation), the map says at which second, the clock stands still; *Resume* continues |
| Speed and restart | Run control sets 2× (shown as *2× speed*) and back to 1×; *Restart from the beginning* starts a new run with the same settings |
| Notifications | the bell counts important events and opens the event centre |

## 3. Roles (`tests/e2e/roles.mjs`)

**Objective.** Each role sees and may do exactly what it should, in the browser and at the API.
**Environment.** Mock-simulation gateway. The test creates a researcher, a second researcher and a
viewer as the administrator, and deletes them at the end.
**Expected / actual.** 14 of 14 / **14 of 14 passed**. **Status: pass.**

| Role | Checks |
|---|---|
| Administrator | creates a researcher and a viewer |
| Viewer | sees monitoring and research, not run control or administration |
| | opening `/admin` or `/administration` directly gets a refusal page |
| | the server refuses the viewer's start, stop, pause, resume, speed, restart, user, settings, audit and driver-switch requests (403) |
| | the guided demo says starting needs another role and shows no start button |
| | can follow a car and read its details |
| | reconnects after the connection drops |
| Researcher | sees run control but not administration |
| | starts a run from run control; it plays and is recorded as started by them |
| | another researcher cannot stop (403) or replace (409) it; the administrator can |
| | compares runs in the run history |
| | stops their own run |
| Administrator | disabling the viewer signs them out at once; sign-in then says the account is disabled |
| | a password-reset link sets a new password once; the second use is refused |

## 4. Accessibility during ambulance priority (`tests/e2e/a11y-ambulance.mjs`)

**Objective.** The map stays accessible while an ambulance has priority. The general audit only
sees that state by chance, and in the final run it once caught a defect there (§5).
**Procedure.** Starts the `emergency` scenario, waits until a junction shows *AMBULANCE*, then runs
axe (WCAG 2.0–2.2 A/AA) in the light and dark themes.
**Expected / actual.** No violations, and the junction's name mentions the ambulance / **no
violations in either theme**; name "Junction J0 ambulance priority. Show details". **Status: pass.**

## 5. Production configuration in the browser (`tests/deploy/prodcheck.mjs`, last check)

**Objective.** With the production settings, a user can sign in over HTTPS and watch the live map,
with the strict CSP and `Secure` cookies.
**Environment.** Gateway started with the values of `deploy/gateway.env.production.example`
(`NODE_ENV=production`, `COOKIE_SECURE=true`, `TRUST_PROXY=1`), real SUMO. In front of it ran
`tests/deploy/tls-proxy.mjs` with a self-signed certificate, standing in for nginx.
**Expected / actual.** Cars shown after sign-in and after a reload, no CSP violations, cookie
`Secure` and `HttpOnly` in the browser / **as expected** (8/8 checks, [`data/final-prodcheck.txt`](data/final-prodcheck.txt)).
**Status.** Pass.

## 6. Defects found by the end-to-end tests

| Phase | Defect | Fix |
|---|---|---|
| 4–5 | Mobile-only buttons visible on desktop; dropdown 6 px wider than the page at 390 px; slow but empty roads coloured as congested | CSS and congestion rule |
| 6–7 | Administration page titled "Run control"; VANET estimate column repeated the true queue under non-VANET controllers | exact path match; dash when not applicable |
| 8 | Accessibility: 27 occurrences of 4 rules (contrast, name mismatch, ARIA on plain elements, unreachable scroll regions) | see [responsiveness report](RESPONSIVENESS_REPORT.md) |
| 10 | **Sign-in through the Vite dev server refused** (`bad_origin`): the proxy rewrote `Host` | `changeOrigin: false`; the journey passes through Vite |
| 10 | **Junction name did not include the visible *AMBULANCE* note** (axe, only during preemption) | note drawn outside the button; name says "ambulance priority"; dedicated test |

Test-script faults fixed along the way (not product defects):

- the reconnect step waited 30 s, while a stopped simulation sends no data, so the client can
  take up to 45 s (heartbeat) to notice;
- failure screenshots were written into the project folder.

## 7. Limitations

- Only Microsoft Edge (Chromium) was driven. Firefox and Safari were not tested, so there is no
  test of WebKit-specific behaviour (for example CSP matching of `ws:` / `wss:`).
- The run-comparison steps need two finished runs, so they run against a mock-simulation gateway.
  The other suites use real SUMO.
- No visual-regression baseline (pixel comparison); layout is checked for overflow and errors,
  and screenshots were reviewed by hand in Phases 4–8.

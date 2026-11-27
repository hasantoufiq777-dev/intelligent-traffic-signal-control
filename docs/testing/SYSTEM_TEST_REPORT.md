# System test report

Run: 27 November 2026, against the Docker deployment (environment: [test plan §1](TEST_PLAN.md#1-environment-all-reported-runs);
Docker 29.9, image built from this repository, real SUMO 1.28 inside the container).

## 1. Purpose and scope

The unit and integration tests check parts. The **system test** checks the finished, deployed system
as a whole from the outside, the way a user and the browser meet it: one running deployment, the
real simulation and SUMO, the live feed over Socket.io, the access rules and the run history.

| | |
|---|---|
| **Test type** | black-box system test (functional, security and safety requirements) |
| **Script** | [`tests/system/system.mjs`](../../tests/system/system.mjs) |
| **Target** | any running deployment: Docker (`http://localhost:4100`), dev mode (`http://localhost:4000`) or a server (`https://…`) |
| **Needs** | an administrator account; the real simulation (not `USE_MOCK`) |
| **Run** | `npm run test:system -- <url> <admin email> <admin password>` |
| **Report file** | set `SYSTEM_REPORT=<file.json>` to save every check with its result and duration |
| **Effect** | starts its own run (`normal`, `level_vanet`, grid 2×2, seed 7) and stops it; a playlist that was playing is interrupted |

## 2. Test cases and results

| ID | Requirement checked | Procedure | Expected | Result |
|---|---|---|---|---|
| S01 | The deployment is up and uses the real simulation | `GET /api/health` | 200, `ok`, `mock: false` | **Pass** |
| S02 | The dashboard is served | `GET /` | 200 with the React root element | **Pass** |
| S03 | Security headers | headers of `GET /` | `Content-Security-Policy` with `default-src 'self'`, `X-Content-Type-Options: nosniff` | **Pass** |
| S04 | No data without signing in | `GET /api/runs` without a session | 401 | **Pass** |
| S05 | Wrong passwords are refused | sign in with a wrong password | 401 | **Pass** |
| S06 | An administrator can sign in | sign in | 200, role `ADMIN`, `HttpOnly` session cookie, CSRF token | **Pass** |
| S07 | Changes need the CSRF token | `POST /api/live/stop` without the token | 403 | **Pass** |
| S08 | The default controller is offered | `GET /api/options` | contains `level_vanet` | **Pass** |
| S09 | Live feed for signed-in users | Socket.io connection with the session cookie | `init` message | **Pass** |
| S10 | An administrator starts a simulation | `POST /api/live/start` | 202 with a run id | **Pass** (`live-mwz7gk11-425a27`) |
| S11 | Cars appear on the live map | wait for `vehicles` of this run | at least one car | **Pass** (first car at t = 5 s) |
| S12 | Cars move | positions of the same cars 10 simulated seconds apart | at least one car moved > 1 m | **Pass** (6 of 6 cars moved in 11 s) |
| S13 | Signals change by themselves (dynamic control) | signal states per junction from `state` messages | ≥ 3 different states at a junction | **Pass** (4 junctions, up to 5 states) |
| S14 | **Safety:** never green for crossing roads | every received `state`: N/S green and E/W green at the same time | none | **Pass** (40 updates, 4 junctions, no conflict) |
| S15 | The live status is correct | `GET /api/live/status` | running, this run id, controller `level_vanet` | **Pass** |
| S16 | An administrator stops the run | `POST /api/live/stop` | `stopped: true` | **Pass** |
| S17 | Runs are recorded | `GET /api/runs/<id>` | 200, the run with status `stopped` | **Pass** |
| S18 | Signing out ends the session | `POST /api/auth/logout`, then `GET /api/runs` | 401 | **Pass** |

**Total: 18 / 18 passed.** Raw results: [`data/system_test_docker.json`](data/system_test_docker.json).

## 3. Defects found

None in the system. The first run of the new script showed three failures, all in the test itself,
and the script was corrected:

| Check | Cause | Correction |
|---|---|---|
| S12 | measured when only one car was on the network, and it was waiting at a red light | waits for 5 cars and compares 10 simulated seconds later; reads the column positions from the message's `fields` |
| S14 | judged after 11 signal updates; the gateway sends at most about one per second | listens until 40 updates have arrived |
| S15 | read the controller from the wrong level of the status answer | reads `entry.entry.controller` and also checks the run id |

## 4. How the system test relates to the other levels

| Level | What | Where | Report |
|---|---|---|---|
| Unit | single functions and classes (Python, gateway, dashboard) | `sim/tests/`, `server/test/simProcess.test.js` and others, `client/src/**/*.test.js` | [UNIT_TEST_REPORT](UNIT_TEST_REPORT.md) |
| Integration | gateway through its real HTTP and socket stack | `server/test/` | [INTEGRATION_TEST_REPORT](INTEGRATION_TEST_REPORT.md) |
| **System** | **the deployed system as a whole** | **`tests/system/system.mjs`** | **this report** |
| Acceptance / end-to-end | user journeys in a real browser | `tests/e2e/` | [E2E_TEST_REPORT](E2E_TEST_REPORT.md), [FINAL_ACCEPTANCE_REPORT](FINAL_ACCEPTANCE_REPORT.md) |

## 5. Limitations

- One deployment on one machine (Windows 11 with Docker Desktop). A cloud server was not tested.
- The safety check (S14) covers the signal states the gateway broadcast during the test (about one
  per second). Every simulation step is checked by the Python safety tests (`test_safety.py`,
  `test_clearance.py`).
- The test signs in as an administrator. The rules for the other roles are covered by
  `server/test/auth.test.js` and the browser suite `tests/e2e/roles.mjs`.

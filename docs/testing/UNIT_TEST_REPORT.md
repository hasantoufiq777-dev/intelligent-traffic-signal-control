# Unit test report

Final run: 12 November 2026, on the final code (environment: [test plan §1](TEST_PLAN.md#1-environment-all-reported-runs)).

## 1. Python simulation (`sim/tests/`)

**Objective.** The simulation's algorithms behave as specified, and the stored research results are
reproduced exactly.
**Procedure.** `npm run test:py` (pytest, quiet), real SUMO.
**Expected.** All tests pass; the regression test reproduces its 12 baseline runs exactly.
**Actual.** **138 passed**, 1 warning (from the property-testing library), in 7 min 42 s.
**Status.** Pass.

| File | Tests | Covers |
|---|---|---|
| `test_adaptive.py` | 4 | adaptive controller: no conflicting greens, minimum and maximum green, no starvation of an approach |
| `test_safety.py` | 2 | conflicting-green detection; the safe yellow → all-red → green sequence |
| `test_collisions.py` | 1 | no collisions in the evaluated scenarios |
| `test_vanet.py` | 6 | delivery probability falls with distance; no delivery before the delay; RSU queue estimate near the true queue; same seed same result; equipped-car selection; stale beacons expire |
| `test_preemption.py`, `test_preemption_grid.py`, `test_vanet_preemption.py`, `test_ev_only_green.py` | 5 + 6 + 5 + 6 | ambulance preemption: exact and over V2I/V2V with loss and relay; only the ambulance's approach green; recovery |
| `test_ev_router.py` | 6 | ambulance route choice around closures and congestion |
| `test_navigation.py` | 9 | A\*, ACO, the traffic map, rerouting |
| `test_incident.py` | 14 | incident detection from stopped cars, closure, reaction |
| `test_accident_zone.py` | 6 | shared-lane one-way control, clearance, ambulance turn, no opposing conflicts |
| `test_eco.py` | 11 | GLOSA advice (go, slow down for green, stop), message age, no advice when timing is uncertain or preempted, SPaT from fixed and adaptive controllers, lower CO₂ and stops, reproducibility |
| `test_ga_tuner.py` | 13 | genetic algorithm operators, held-out evaluation |
| `test_evaluate.py`, `test_metrics.py` | 7 + 3 | multi-seed evaluation and statistics; trip-info parsing and per-step metrics |
| `test_grid.py` | 3 | runs on the 2×2 network with fixed and adaptive control; route alternatives file |
| `test_driver.py` | 7 | one-driver mode in the simulation |
| `test_live_protocol.py` | 6 | the JSON-lines live protocol from the mock and the real simulation; incident, VANET alert and reroute events; a clear error for a missing parameters file |
| `test_playback.py` | 5 | pause, resume and speed (Phase 10): applied and confirmed, invalid speeds ignored, driver commands kept while paused, the pause ends when the run stops, and **a paused and re-speeded real SUMO run gives the same states, second by second, and the same results as an undisturbed one** |
| `test_live_extras.py` | 4 | demand factor scales every flow and is the identity at 1; 2× demand gives more than twice the cars of 0.5×; jitter is the standard deviation of delivered delays (incremental); the live state carries jitter and the latest ACO decision (probabilities sum to 1) **and the run's results equal those of a run without live output** |
| `test_dispatch.py` | 9 | ambulance on demand (added 14 November 2026): wrapping keeps the running controller; priority added only where missing; invalid requests change nothing; **an ambulance sent mid-run gets green at every junction it crosses** with fixed and with VANET adaptive control (events in order, on the map, no collision, no missed preemption); **it is faster with priority than the same ambulance without**; `adaptive_ev`'s own priority and a chosen route; at most 3 on the road; work-zone run stays conflict-free; a run without a dispatch is unchanged second by second |
| `test_regression.py` | 1 |
| `test_city.py` | 12 | city network check (every class reaches every corner, no dead ends, J0–J3 links unchanged); vehicle factory; city trips; **lane permissions in routing**; **A\* heuristic admissible and A\* cost equal to Dijkstra** on 40 random pairs with random congestion; distance search equal to SUMO's shortest path; **route explorer: shortest not fastest, congestion changes the fastest**; planner and controller factories; fuel curves; speed advice never above the limit, the leader or traffic ahead, and avoids a stop at red; **a real city run: 0 collisions, every vehicle on a lane its class may use, 35–65 % of ordinary vehicles equipped, the driven truck never above the limit when 180 km/h is asked** | **12 reference runs identical to `regression_baseline.json`** |

(Counts are test functions; pytest's 138 includes parametrised cases.)

**Re-run after adding ambulance on demand (14 November 2026): 148 passed** (138 + 10 in
`test_dispatch.py`), 1 warning, in 13 min 50 s, with other suites running at the same time. The
zero-regression test still reproduces its 12 baseline runs exactly.

**Re-run after the supervisor enhancements (15 November 2026): 160 passed** (148 + 12 in `test_city.py`), in
13 min 53 s. The zero-regression test still reproduces its 12 baseline runs exactly, after the route-planner
(Strategy) and controller-factory refactors.

**Defect found by these tests (Phase 10, fixed before release):** driver commands that arrived while
a run was paused were put in a list that was then replaced, so they were lost. The fix takes the
result first and then keeps it (`sim/commands.py`, `wait_while_paused`).

## 2. Dashboard logic (`client/src/lib/`)

**Objective.** The pure functions behind the map and panels decide correctly.
**Procedure.** `npm run test:client` (vitest).
**Expected.** All pass.
**Actual.** **13 passed** (3 files) in 0.5 s.
**Status.** Pass.

| File | Tests | Covers |
|---|---|---|
| `congestion.test.js` | 4 | a road counts as congested only with a queue *and* a low speed (a defect found in Phase 4–5: slow but empty roads were coloured) |
| `glosa.test.js` | 5 | the advice text and colour from the simulation's SPaT figures |
| `vehicles.test.js` | 4 | vehicle kind from the flag bits (equipped, eco, ambulance, driven, advised) |
| `roads.test.js` | 2 | entry and exit roads offered for a driven car and a sent ambulance (added 14 November 2026; 15 passed) |

## 3. Gateway units

The gateway is tested through its real HTTP and socket stack ([integration report](INTEGRATION_TEST_REPORT.md)).
`server/test/snapshot.test.js` holds four focused tests added in Phases 9–10:

- the history row keeps exactly the fields the charts read (2 tests);
- HTTP compression applies only above 1 KB;
- the security headers: CSP limited to this host, HSTS only in production, a malformed `Host`
  neutralised.

## 4. Limitations

- The dashboard's React components have no component-level unit tests. They are covered by the
  browser suites (every page, every role, every width, accessibility), which exercise them with
  real data.
- The Python suite needs SUMO and takes about 7 minutes; it is run in full after every phase.

## 5. Update, 27 November 2026: full re-run and new unit tests

All unit suites were run again on the current code (Docker deployment and the dev gateway running at
the same time, which is why the Python suite took longer).

| Suite | Command | Result | Time |
|---|---|---|---|
| Python simulation | `npm run test:py` | **249 passed** (238 in the full run + 11 new, run separately), 1 warning | 25 min 16 s |
| Gateway | `npm run test:server` | **96 passed**, 4 skipped (the real-SUMO tests, run with `npm run test:e2e`) | 20 s |
| Dashboard | `npm run test:client` | **44 passed** (9 files) | 0.4 s |

**New unit tests** for code that had none:

| File | Tests | Covers |
|---|---|---|
| `sim/tests/test_sumo_connect.py` | 6 | starting SUMO and connecting TraCI (the "map not loading" fix): libsumo needs no port; SUMO gets a free port; TraCI connects to `127.0.0.1`, never `localhost`; each attempt has a 3 s limit and the default is restored; the open connection has no limit; retry messages go to stderr, not the JSON stream; a failed connection kills SUMO and reports the error |
| `sim/tests/test_sensing_vanet.py` | 5 | the VANET sensing provider: queues, waiting times, ambulance alerts and speeds come from the roadside units; only fully blocked roads into *this* junction count as blocked; `update` steps the VANET emulation |
| `server/test/simProcess.test.js` | 5 | the gateway's simulation process (a Node script stands in for Python): protocol lines become messages, other output is reported separately, empty lines ignored; exit codes; `send()` writes one JSON line and refuses after exit; `stop()` ends the process and marks it stopped; a program that cannot start reports exit −1 instead of crashing the gateway |
| `client/src/lib/format.test.js` | 7 | number, speed, percentage, p-value and time formatting; missing values shown as a dash |
| `client/src/hooks/usePersistentState.test.js` | 6 | page state kept between pages: initial value, restore from `sessionStorage` under `itsc-ui:`, broken saved text, no storage available, sign-out clears only this app's keys |

The whole deployed system is tested separately: [system test report](SYSTEM_TEST_REPORT.md) (18/18).

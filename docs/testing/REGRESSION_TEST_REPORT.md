# Regression test report

**Objective.** Building the platform (Phases 1–10) changed none of the simulation's behaviour or
research results, and nothing that worked in one phase broke in a later one.

## 1. The simulation is unchanged

### 1.1 Code

`git diff --stat c7a0317 HEAD -- sim/ results/` covers everything from the last research commit
(the accident work zone) to the final platform commit:

```
 sim/commands.py                  | 64 +++++++++++++++++++++++++++
 sim/live_vehicles.py             |  5 ++-
 sim/main.py                      |  5 +++
 sim/mock_sim.py                  | 17 ++++---
 sim/navigation/navigator.py      | 25 ++++++++++-
 sim/runner.py                    | 43 +++++++++++++++---
 sim/scenarios/generate_routes.py | 19 ++++++++
 sim/tests/test_live_extras.py    | 69 +++++++++++++++++++++++++++++
 sim/tests/test_playback.py       | 95 ++++++++++++++++++++++++++++++++++++++++
 sim/vanet/channel.py             | 18 ++++++++
 10 files changed, 348 insertions(+), 12 deletions(-)
```

- **Live display output** (`live_vehicles.py`, part of `runner.py`; Phases 4–7): road speeds,
  incidents with their detection state, ambulance routes, the VANET range, each car's road and
  destination. No control decision reads them.
- **Playback** (`commands.py`, `mock_sim.py`, part of `runner.py`; Phase 10): pause, resume and
  speed change only the wall-clock delay; SUMO is not stepped while paused. `test_playback.py`:
  identical states and results with and without pausing.
- **Measured jitter and route decisions** (`vanet/channel.py`, `navigation/navigator.py`; Phase 10):
  read-only statistics and a record of the latest route search, added to the live state. No extra
  random numbers are drawn. `test_live_extras.py`: the results equal those of a run without live
  output.
- **Demand factor** (`generate_routes.py`, `main.py`, `runner.py`; Phase 10): a new run setting
  that multiplies the scenario's flows; at the default 1 the scenario config is passed unchanged,
  which the zero-regression test confirms.
- **Results.** No file under `results/` changed.
- **Not committed.** `sim/scenarios/navigation.yaml` has a local edit that is not committed and was
  not made by this work.

### 1.2 Zero-regression test

`sim/tests/test_regression.py` reruns 12 reference runs (normal and congested traffic × fixed and
adaptive control × 3 seeds) and requires results **identical** to `regression_baseline.json`.
Passed in every full Python run, including the final one (138 passed, 12 November 2026).

### 1.3 Live mode gives the stored results (Phase 4–5)

`emergency`, `adaptive_ev_vanet`, seed 1, 10 % loss, relay on, run in live mode (so the new output
code runs), compared with `results/raw.csv`:

| Metric | Stored | Live mode |
|---|---|---|
| Ambulance travel time | 59.0 s | 59.0 s |
| Average travel time | 93.23 s | 93.23 s |
| Average waiting time | 12.85 s | 12.85 s |
| Maximum queue | 6 | 6 |

### 1.4 The published figures match the stored results

The brief lists the project's existing results. Each one was compared with `results/summary.json`
(10 seeds per configuration), which the Results page displays:

| Result (brief) | Brief | `summary.json` |
|---|---|---|
| Normal: fixed / adaptive / VANET 100 % / VANET 50 % waiting | 19.4 / 12.1 / 11.9 / 15.7 s | 19.39 / 12.11 / 11.9 / 15.7 s (−37.6 %, −38.7 %, −18.9 %) |
| Congested: fixed / adaptive / VANET 100 % / VANET 50 % waiting | 37.4 / 26.9 / 22.2 / 20.2 s | 37.4 / 26.9 / 22.2 / 20.2 s (−28.1 %, −40.8 %, −46.0 %) |
| Ambulance: baseline / exact preemption / VANET 10 % loss relay / GA-tuned | 87.6 / 65.2 / 65.6 / 66.5 s | 87.6 / 65.2 / 65.6 / 66.5 s; missed preemptions 0 |
| Work zone: closure / alternating / + A\* / + A\* + EV route (trip, waiting, ambulance) | 102.1, 19.4, 66.2 / 95.4, 15.2, 74.2 / 94.4, 15.1, 71.5 / 95.3, 15.1, 60.5 | 102.1, 19.4, 66.2 / 95.3, 15.2, 74.2 / 94.4, 15.1, 71.5 / 95.3, 15.1, 60.5; conflicts 0, collisions 0 |

One value differs in the last digit: the alternating configuration's trip time is 95.347 s in
`summary.json`, which rounds to 95.3; the brief gives 95.4 (its source rounded differently). No
stored result was changed. The brief's "Alternating + A\* + EV route" row is the
`alternating+ev_routing` configuration in `summary.json`.

## 2. Suite results across the phases

| Suite | Phase 2–3 | Phase 4–5 | Phase 6–7 | Phase 8 | Phase 9 | **Final** |
|---|---|---|---|---|---|---|
| Python | 129 | 129 | not re-run (no `sim/` change) | 129 | not re-run (no `sim/` change) | **138** (129 + 9 Phase 10) |
| Gateway | 46 + 2 skipped | 46 | 47 | 69 | 72 | **82** + 3 skipped |
| Gateway with real SUMO | 2 | — | — | 2 | — | **3** |
| Dashboard unit | 9 | 13 | 13 | 13 | 13 | **13** |
| Browser journey | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 | **10/10** (also through the dev server) |
| Every page | — | — | 22/22 | 22/22 | 22/22 | **24/24** |
| Roles | — | — | — | 14/14 | 14/14 | **14/14** |
| Responsive | — | ✓ | ✓ | 108/108 | 108/108 | **108/108** |
| Accessibility | — | — | — | 0 in 45 | 0 in 45 | **0 in 45**, + 0 in 2 during ambulance priority |
| Production configuration | — | — | — | — | — | **8/8** |

The test counts only grew. No test was removed or weakened. One step's timeout was raised, with
the reason recorded: the reconnect step in `roles.mjs`, because of the heartbeat timing.

## 3. Final full run

Run on 12 November 2026. The last code change was commit `f2c9d15` (jitter, route decisions, demand,
platform events). The Python, gateway, real-SUMO, browser and accessibility suites ran after it; the
load test, load time, rendering and production check ran on `608564b`, the commit before (§4).

| Suite | Result |
|---|---|
| `npm run test:py` | 138 passed (7 min 42 s) |
| `npm run test:server` | 82 passed, 3 skipped |
| `npm run test:e2e` | 3 passed |
| `npm run test:client` | 13 passed (Vitest 5.1) |
| smoke / pages / roles / live details | 10/10, 24/24, 14/14, 4/4 |
| responsive / accessibility / ambulance accessibility | 108/108, 0 violations in 45 audits, 0 in 2 |
| load time / rendering | 1.14–1.41 s on the slow link; 60 fps on every live page |
| load (10 → 1,000, spike, reconnect) | 100 % connected; API p95 20.3 ms at 1,000; exactly 1,000 of 1,100; 500/500 back in 0.34 s |
| production check | 8/8 |
| dependency audit (production) | 0 vulnerabilities in server, dashboard and root |

## 4. Limitations

- The full Python suite was not re-run after Phases 6–7 and 9, because no file under `sim/` changed
  in those phases. It was run in Phases 2–3, 4–5, 8 and in the final run.
- The 30-minute soak test was run in Phase 8 and not repeated on the final code (see the load test
  report).
- The load test, load time, rendering profile and production check ran on `608564b`. The last
  commit adds a few fields to the state (jitter, one route decision) and events at run start and
  end; it does not change the broadcast path, the first message's history rows, or the headers.

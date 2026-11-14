# How the project works, module by module

This guide follows the project in the order it was built. For each module it covers:

- what the module does;
- how it works, step by step;
- the files that hold it;
- where to see it in the web app;
- how it is tested.

Exact formulas, parameters and the full results are in
[PROJECT_OVERVIEW.md](PROJECT_OVERVIEW.md) and [method.md](method.md).

---

## The big picture

```
 Browser (React dashboard)
      │  HTTPS (REST)  +  WebSocket (live stream)
      ▼
 Node gateway (server/)  ── SQLite database (accounts, runs, results, events)
      │  starts the simulation as a child process
      │  stdin: commands (pause, speed, drive, ambulance)
      │  stdout: one JSON line per message (state, map frame, event, result)
      ▼
 Python simulation (sim/)  ── TraCI ──►  SUMO (moves the cars)
```

**SUMO** moves every car (following, braking, lane changes, stopping at red). **Python** decides
everything else: the signals, the VANET messages, the routes, the ambulance priority and the speed
advice. The **gateway** runs one shared simulation and streams it to every viewer. The
**dashboard** draws it.

### One simulated second (`sim/runner.py`)

Each simulated second the runner does these steps, in this order:

1. **Incidents.** Crash vehicles are placed or removed.
2. **VANET.** Cars send beacons, ambulances send alerts, the radio channel delivers or loses them,
   and the roadside units update their estimates.
3. **Signal controllers.** Each junction keeps or changes its green.
4. **Accident zone.** The zone controller runs (zone network only).
5. **Routing.** Ambulance route choice and navigation.
6. **Eco-driving.** Speed advice.
7. **Dashboard commands.** Pause, speed, the driven car, a sent ambulance.
8. **Live output.** The state, the map frame and events are printed.
9. **SUMO steps once.** Collisions are checked and the run is paced to wall-clock time.

---

## Module 0–1: Environment, network and traffic

**What it does.** It sets up SUMO, the road networks and the traffic.

**How it works:**

- **Networks:**
  - `net.xml`: one signalised junction, J0.
  - `grid2x2.net.xml`: the main network. Four junctions J0–J3, 250 m apart, 2 lanes each way,
    50 km/h.
  - `grid2x2_zone.net.xml`: the same grid with an 80 m work zone.
- **Scenarios.** Each scenario is a YAML file that gives traffic flows in vehicles per hour.
  `generate_routes.py` turns the flows into a SUMO route file with a seeded random process, so the
  same seed always gives the same traffic.
- **The ambulance vehicle type** is defined in every route file:
  - acceleration 3.5 m/s², deceleration 6 m/s²;
  - may drive up to 1.3× the speed limit.
- **Demand setting.** A *demand* factor (0.25–2×) multiplies every flow. At 1× the flows are passed
  through unchanged.

**Files:** `sim/sumo/`, `sim/scenarios/*.yaml`, `sim/scenarios/generate_routes.py`

**In the app:** *Run control*: scenario, network and demand.

**Tested by:**
- `test_grid.py`;
- the demand test in `test_live_extras.py` (2× demand gives more than twice the cars of 0.5×).

---

## Module 2: Simulation runner, metrics, fixed-time baseline

**What it does.** It runs one simulation from start to end and measures it.

**How it works:**

- **The runner** (`runner.py`) does the following:
  - starts SUMO;
  - builds the controllers;
  - runs the per-second loop above.
- **SUMO's own signal timer is switched off.** A light changes only when Python says so.
- **Every change is made safe.** It always goes green → **yellow 3 s** → **all-red 2 s** → the
  other green (`BaseController.set_phase_safe`).
- **Conflicting greens are refused.** Every signal state is checked before it is applied.
- **Fixed-time controller:** 30 s of green for north–south, then 30 s for east–west. This is the
  baseline.
- **Results.** At the end, `metrics.py` reads SUMO's trip file and reports:
  - average waiting, travel and lost time;
  - throughput and the longest queue;
  - ambulance travel time;
  - fuel and CO₂;
  - collisions.

**Files:** `sim/runner.py`, `sim/main.py`, `sim/controllers/base.py`, `sim/controllers/fixed.py`,
`sim/analytics/metrics.py`

**In the app:** start any scenario with controller `fixed`.

**Tested by:** `test_safety.py`, `test_metrics.py`, `test_collisions.py`

---

## Module 3: Live stream and mock simulator

**What it does.** It lets the dashboard watch a run while it plays.

**How it works:**

- With `--live`, the simulation prints one JSON line per message:
  - `status` (started, finished, stopped, error);
  - `state` (every second: signals, queues, metrics, VANET, navigation);
  - `vehicles` (the map frame);
  - `event` (preemption, alert, incident, reroute and others);
  - `result` (the final metrics).
- **The mock simulator** (`mock_sim.py`) prints the same kind of messages without SUMO. The gateway
  tests use it, and so do quick demos.

**Files:** `sim/runner.py` (`emit_json_line`, `protocol_event`), `sim/live_vehicles.py`,
`sim/mock_sim.py`, `sim/mock_traffic.py`

**Tested by:** `test_live_protocol.py`

---

## Module 4 / 4b: Adaptive signal control and the sensing layer

**What it does.** The green follows the queues instead of a fixed clock.

**How it works** (`AdaptiveController`):

1. Every **5 s** it compares the north–south queue (N + S) with the east–west queue (E + W).
2. It switches early when any of these holds:
   - the waiting side has at least **5 more cars**;
   - the served side is empty and the other side has cars;
   - an approach has been red for **60 s** (the starvation limit).
3. Every green lasts between **10 s and 50 s**.
4. An approach blocked by an incident counts as having no demand.

**The controller never reads SUMO directly.** It asks a `StateProvider`:

- `OracleStateProvider`: exact counts from SUMO;
- `VanetStateProvider`: what the roadside units estimated from radio messages (Module 6).

The same controller code therefore runs on perfect data or on VANET data.

**Files:** `sim/controllers/adaptive.py`, `sim/sensing.py`

**In the app:** *Signals* page (phase history, green lengths); the countdowns on the map.

**Result:** waiting time −37.6 % (normal traffic) and −28.1 % (congested) against fixed-time.

**Tested by:** `test_adaptive.py` (no conflicting greens, min/max green, no starvation)

---

## Module 5: 2×2 grid

**What it does.** It moves from one junction to a small city grid, so routes and multi-junction
effects exist.

**How it works:** each of the four junctions has its own controller instance. Routes cross one to
three junctions. `route_alternatives.py` checks that alternative routes really exist.

**Files:** `sim/sumo/grid2x2.net.xml`, `sim/sumo/generate_grid.py`,
`sim/scenarios/route_alternatives.py`

**In the app:** the live map, which shows the grid by default.

**Tested by:** `test_grid.py`

---

## Module 6: VANET emulation

**What it does.** It simulates the cars' radio network, so the signals see only what the radio
delivers.

**How it works:**

- **Equipped cars.** A seeded share of cars (the *penetration*, 20–100 %) has a radio. Ambulances
  and the driven car always have one.
- **Beacons.** Each equipped car sends one every second: position, speed and lane.
- **Radio channel:**
  - range 300 m;
  - the loss probability grows with distance squared, from a base loss up to a maximum loss;
  - delay 10 ms plus 0–5 ms of jitter;
  - seeded, so a run repeats exactly.
- **Roadside unit (RSU)**, one per junction:
  - keeps the latest beacon of each car; beacons expire after 3 s;
  - queue per approach = cars slower than 1 m/s within 100 m, scaled by 1/penetration to count the
    cars without a radio.
- **Traffic management centre (TMC).** Wired to all RSUs, it builds a network-wide map of road
  speeds and incidents.
- **The `adaptive_vanet` controller** is the Module 4 controller running on these estimates.

**Files:** `sim/vanet/channel.py`, `nodes.py`, `network.py`, `messages.py`, `sensing_vanet.py`

**In the app:** *VANET* page:
- packet delivery ratio, loss, delay and measured jitter;
- estimated against true queues;
- the RSU ranges on the map (*Layers*).

**Result:** with 100 % of cars equipped, waiting time −38.7 % (normal) and −40.8 % (congested).

**Tested by:**
- `test_vanet.py`: delivery falls with distance; the queue estimate is close to the true queue;
  the same seed gives the same result;
- the jitter test in `test_live_extras.py`.

---

## Module 7: Ambulance detection and preemption (exact data)

**What it does.** It gives the ambulance green at each junction before it arrives.

**How it works** (`PreemptionController`, which wraps the adaptive controller):

1. **NORMAL.** Adaptive control runs as usual. Each second the junction checks the ambulances heading
   to it, looking at most 2 junctions ahead.
2. When an ambulance's ETA is **≤ 18 s**, it moves to **TRANSITION**: yellow, then all-red.
3. **EV_GREEN.** Only the **ambulance's approach** gets green, for all its turns, so the queue in
   front of it clears at once. All other approaches stay red.
4. **Holding the green:**
   - held until the ambulance has passed, plus **5 s** clearance;
   - if the ambulance changes route and now arrives from another side, the green moves there;
   - safety limit: **90 s**.
5. **RECOVERY.** 10 s of normal operation through safe changes, then back to NORMAL.

Every preemption is recorded. `missed_preemptions` counts the junctions an ambulance crossed
without having had priority there.

**Files:** `sim/controllers/preemption.py`

**In the app:**
- *Emergency* page;
- the map: *AMBULANCE* at the junction, and the ambulance's remaining route;
- events: *Preemption started / ended*.

**Result:** ambulance trip 87.6 s → **65.2 s**.

**Tested by:** `test_preemption.py`, `test_preemption_grid.py`, `test_ev_only_green.py`

---

## Module 8: Preemption over V2I / V2V

**What it does.** The junction learns about the ambulance only through radio messages, as it would
in reality.

**How it works:**

1. **The ambulance sends an `EV_ALERT`** every 0.5 s: position, speed and route.
2. **The alert reaches the RSUs:**
   - directly (V2I);
   - with **relay** on, also passed on by other equipped cars (V2V), up to 3 hops, with duplicate
     suppression.
3. **The RSU forwards it** over the wired backbone to the junctions on the ambulance's route.
4. **Between alerts**, the RSU estimates the position by dead reckoning. An alert older than 2 s
   expires.
5. **The preemption controller** (Module 7) runs on these received alerts.

**Files:** `sim/vanet/alerts.py`, `sim/vanet/sensing_vanet.py`

**In the app:** controller `adaptive_ev_vanet`; events *Alert sent / delivered / lost*.

**Result:** with 10 % loss and relay on: **65.6 s**, almost the same as with exact data; 0 missed
preemptions.

**Tested by:** `test_vanet_preemption.py`

---

## Module 9: Navigation with A* and ACO

**What it does.** Navigating cars choose the fastest route using the TMC's live traffic map.

**How it works:**

- **Who navigates.** 30 % of the equipped cars. Each asks for a route every ~30 s, or after
  passing a junction.
- **Road cost** = length ÷ current speed (smoothed) + **8 s per traffic light**. A closed road
  costs infinity.
- **A\*.** The exact fastest route; its heuristic is straight-line distance ÷ maximum speed.
- **ACO (ant colony optimisation):**
  - ants walk the road graph and pick each turn with probability ∝ τ^α · (1/cost)^β;
  - the pheromone τ evaporates by ρ, and the best ants deposit Q/cost;
  - it returns up to **3 routes with probabilities**, so cars spread over alternatives instead of
    all taking the same road;
  - it falls back to A\* if no ant arrives.
- **Switching route.** A car switches only for a route at least **5 %** faster, at most once per
  15 s.
- **Ambulance routing** (`ev_router.py`, `--ev-routing on`):
  - the ambulance plans with A\* at departure, on each new road and when a closure appears;
  - it counts no signal wait, because preemption gives it green.

**Files:** `sim/navigation/graph.py`, `astar.py`, `aco.py`, `navigator.py`, `ev_router.py`

**In the app:** *Navigation* page:
- the latest route decision, with ACO's alternatives, probabilities and the route chosen;
- reroute events.

**Result:**
- in the incident scenario, A\* navigating cars were −8.2 % faster;
- ambulance routing with a closure ahead: 113.7 s → 63.3 s.

**Tested by:** `test_navigation.py`, `test_ev_router.py`

---

## Module 10: Incidents detected through VANET

**What it does.** A crash blocks a road; the network notices it from the cars' messages and reacts.

**How it works:**

- **The crash.** Stopped "blocker" vehicles block one lane, or every lane (a closure), for a set
  time.
- **Detection.** An RSU flags the road when a car has been stopped on it for 30 s, more than 30 m
  from the stop line. Queues at a red light sit at the stop line, so they are not flagged.
  - The flag goes to the TMC.
  - It clears 10 s after the evidence is gone.
- **Reaction:**
  - navigation avoids the road;
  - drivers who reach a closed road turn away;
  - the signals give no green to an approach that cannot move.

**Files:** `sim/scenarios/incident.py`, the incident code in `sim/vanet/nodes.py` and `network.py`

**In the app:** *Incidents* page; the map shows *not yet detected* → *detected*; events.

**Result:** detection takes ~33 s.

**Tested by:** `test_incident.py`

---

## Module 11: Genetic-algorithm tuning

**What it does.** It searches for better parameter values instead of guessing them.

**How it works:**

- **What it tunes:**
  - the signal/VANET parameters (min/max green, queue threshold, decision interval, ETA threshold,
    message intervals);
  - the ACO parameters (α, β, ρ, ants, iterations, Q, elite).
- **The GA:**
  - tournament selection, BLX-α crossover, Gaussian mutation, elitism;
  - a cache of evaluated parameter sets;
  - parallel workers, each running SUMO.
- **Train / test split.** It is tuned on seeds 101–103 and checked on the unseen seeds 1–10.
- **Output.** `results/ga/best_params_*.yaml`, used by the `_tuned` controllers and `aco_tuned`.

**Files:** `sim/optimizer/ga_tuner.py`, `results/ga/`

**In the app:** *GA tuning* page.

**Result:**
- ACO fitness +34.6 % (p = 0.002), and route search 3–4× faster;
- the signal tuning gave ~4 %, which is not significant.

**Tested by:** `test_ga_tuner.py`

---

## Module 12: Multi-seed evaluation

**What it does.** It turns single runs into reliable comparisons.

**How it works:**

1. Each configuration is run on **10 seeds**.
2. For each configuration, the means and confidence intervals are computed.
3. Configurations are compared with paired Wilcoxon tests.
4. The results go to `results/summary.json` and `raw.csv`, with plots.

**Files:** `sim/analytics/evaluate.py`, `vanet_experiments.py`, `report.py`, `plots.py`

**In the app:** *Results* page (tables and charts per suite).

**Tested by:**
- `test_evaluate.py`;
- `test_regression.py`: **12 reference runs must give exactly the stored results**, so no later
  change can quietly alter the research.

---

## Module 13: Node gateway

**What it does.** It connects the simulation to many browsers.

**How it works:**

- **One shared simulation.** `LiveChannel` owns the Python process:
  - it validates every line (zod);
  - it keeps the latest state, 120 s of chart history and 200 events;
  - it broadcasts to everyone.
- **Pacing:**
  - the state and the map frame at most once per second, latest-wins (a slow client skips a frame
    instead of queueing it);
  - events and results are sent reliably.
- **Storage (SQLite):** runs, results, events, state samples (one every 2 s) and driver trips.
- **Playlist.** Runs play one after another. After a crash it retries; after 3 failures in a row
  it stops.
- **Lifecycle:** STOPPED → STARTING → RUNNING → STOPPING; or FAILED.

**Files:** `server/src/liveChannel.js`, `app.js`, `db.js`, `protocol.js`, `simProcess.js`

**Tested by:** `server/test/gateway.test.js`, `resilience.test.js`

---

## Modules 14–15: Dashboard and eco-driving (GLOSA)

### Dashboard

**How it works:**

- **The live map** draws each car at the position and angle SUMO reports, animated between frames.
  It shows:
  - the lights and their countdowns;
  - queues and congestion;
  - closures and ambulance routes;
  - RSU ranges and the work zone.
- **Module pages:** *Signals*, *VANET*, *Emergency*, *Navigation*, *Incidents*, *Work zone*,
  *GLOSA*.
- **Other pages:** *Results*, *GA tuning*, *Run history*.

### Eco-driving (GLOSA)

**What it does.** Cars get speed advice so they reach the light on green instead of stopping.

**How it works:**

1. Each RSU broadcasts **SPaT** (signal phase and timing) every second.
2. An eco car within 300 m computes when it would arrive at the stop line, and gets one of four
   pieces of advice:
   - **go**: it arrives on green, so nothing changes;
   - **slow**: target speed = distance ÷ time until green, so it arrives as the light turns green;
   - **stop**: the needed speed would be below 5 m/s, so it drives normally and stops;
   - **none**: the timing is too uncertain (adaptive control, or a preemption).
3. After the junction, the car returns to normal speed.

**Files:** `sim/vanet/glosa.py`; `client/src/`

**In the app:** *GLOSA* page (eco cars against the others); the eco tile.

**Result:** with all cars eco and fixed-time signals:
- stops −40.5 %;
- waiting −30.6 %;
- CO₂ −2.9 %.

**Tested by:** `test_eco.py`, `glosa.test.js`, `congestion.test.js`, `vehicles.test.js`

---

## Later work 1: One-driver mode

**What it does.** One viewer at a time drives a car inside the shared run.

**How it works:**

1. **Start the drive.** The viewer picks a start road and a destination. The gateway sends a
   `drive_start` command on stdin, and Python adds the car.
2. **Drive it** with **Go**, **Ease off** (10–50 km/h) and **Stop** (keys G / E / S). SUMO's safety
   rules stay on, so the car cannot run a red light or crash.
3. **While driving**, the driver sees:
   - speed advice;
   - the next light;
   - ambulance warnings ("behind" or "crossing").
4. **At the end**, a trip report: time, distance, stops, fuel, CO₂ and advice followed, compared
   with other cars on the same route.
5. **The seat is freed** on arrival, hand-back, 2 min idle, 10 min maximum, leaving the page, the
   end of the run, or by the admin.

**Files:** `sim/driver.py`, `server/src/driver.js`, `client/src/components/DrivePanel.jsx`

**In the app:** *My drive*. Administrators can switch it off in *Run control*.

**Tested by:** `test_driver.py`, `server/test/driver.test.js`

---

## Later work 2: Accident work zone (alternating one-way traffic)

**What it does.** A crash leaves one lane for both directions; the two sides take turns on it.

**How it works** (`ZoneController`):

- **The crash.** It blocks both westbound lanes and one eastbound lane of the J2–J3 road for 360 s.
- **Activation.** The zone switches on when the TMC marks that road blocked (VANET detection).
- **Taking turns:**
  1. One side gets green.
  2. Yellow 3 s.
  3. All-red until the shared lane is **empty**. Entry and exit detectors count this, not the
     radio.
  4. The other side gets green.
- **Turn length:** 8 s + 2.5 s per queued car, at most 40 s. A side that has waited 90 s goes next.
- **Ambulance.** Its side gets the next turn at once.
- **Routing.** Routes, navigation and ambulance routing all count the expected wait at the zone.

**Files:** `sim/controllers/zone.py`, `sim/scenarios/accident_zone.py`,
`sim/sumo/grid2x2_zone.net.xml`

**In the app:** *Work zone* page; the map shows the shared lane, its direction arrows and the zone
signals.

**Result:** against closing the road:
- waiting −21.6 %;
- 0 times with both directions in the lane;
- 0 collisions.

**Tested by:** `test_accident_zone.py`

---

## The web platform (Phases 1–10 of the platform brief)

### Accounts, roles and security

- **Sign-in.** E-mail and password, with Argon2id password hashes.
- **Session.** A random token in an `HttpOnly` cookie; only its hash is stored.
- **Roles:**

  | Role | Can do |
  |---|---|
  | **Viewer** | Watch, follow a car, drive |
  | **Researcher** | Also start and stop their own runs |
  | **Administrator** | Everything: users, sessions, audit, settings |

- **Every write request is protected:**
  - a CSRF token;
  - an origin check;
  - input validation;
  - rate limits.
- **Account lockout** after repeated failed sign-ins.
- **Security headers:** CSP (limited to this host) and HSTS in production.
- **Connection limit:** at most **1,000** live connections.
- **Audit log** of sign-ins, account changes, runs and refused actions.

**Files:** `server/src/auth.js`, `password.js`, `app.js`

**Pages:** *Account*, *Administration*

**Tested by:**
- `server/test/auth.test.js` (30 tests);
- `tests/e2e/roles.mjs`;
- the security report.

### Playback (pause, speed, restart)

**How it works:**

1. The gateway writes `{"cmd":"pause"}`, `{"cmd":"resume"}` or `{"cmd":"speed","speed":2}` to the
   simulation's stdin.
2. `Playback` (`sim/commands.py`) changes **only the wall-clock pacing**. While paused, SUMO is not
   stepped, so the results stay identical.
3. The simulation confirms each change with a `playback` message, and viewers see the confirmed
   state.

**Tested by:**
- `test_playback.py`: a paused and re-speeded run is identical, second by second;
- `playback.test.js`.

### Guided teacher demo

A 13-step demo (`/demo`). Each step:
- explains what is happening;
- starts its scenario;
- can **pause to explain**.

### Performance and load

- **Smaller first message.** It went from 350 KB to **64 KB**: compact history rows, gzip, and
  long caching of the static files.
- **Faster data retention.** An index was added for it.
- **Load test:** 1,000 signed-in viewers, 100 % connected, API p95 20.3 ms.

**Reports:** `docs/testing/` (performance, load, Phase 9).

### Platform events

The gateway adds these events to the event centre:
- run started, finished, stopped and failed;
- the playlist halted;
- the connection limit reached.

---

## Newest: send an ambulance at any time (`sim/dispatch.py`)

**What it does.** An administrator, or the researcher who started the run, presses **Send an
ambulance** in *Run control* at any moment of a run. It works for **every controller and scenario**,
and the ambulance gets green at every junction it crosses.

**How it works:**

1. **The request.** `POST /api/live/ambulance`, with no body or with `{from, to}`. It is checked
   by `AmbulanceSchema` and sent to Python on stdin as `{"cmd":"ambulance"}`. It also works while
   paused: the ambulance then starts when the run resumes.
2. **The route** (`AmbulanceDispatcher`):
   - *automatic*: the entry-to-exit crossing through the **most signalised junctions** (3 on the
     grid);
   - *chosen*: the given roads, checked and routed by SUMO.
3. **Priority** (first ambulance only):
   - controllers that already have priority (`adaptive_ev*`) are left as they are;
   - `fixed`, `adaptive` and `adaptive_vanet` are each wrapped in the **preemption controller**
     (Module 7). It runs the existing controller unchanged until the ambulance is within 18 s,
     then gives it the safe ambulance green.
   - With `adaptive_vanet`, the junctions learn about the ambulance from its V2I alerts (Module 8).
4. **The ambulance is added** to SUMO at once (`amb_dispatch_1`, `_2`, …). At most 3 drive at the
   same time.
5. **Events:**
   - `ambulance_dispatched` (route, junctions, how priority is given);
   - `preempt_start` / `preempt_end` at each junction;
   - `ambulance_arrived` (travel time, "green given at 3 of 3 junctions");
   - or `ambulance_rejected` (unknown road, no route, too many on the road).
6. **The result** gains `dispatched_ambulances`, `dispatched_avg_travel_time`,
   `dispatched_priority_added` and `dispatched_trips` (per ambulance: route, times, junctions,
   where it got green). *Run history* shows them as a table.
7. **A run without a dispatch is unchanged.** The dispatcher draws no random numbers and touches
   nothing until a command arrives.

**Measured in a real run** (2×2 grid, fixed-time, sent at second 20):

| | Value |
|---|---|
| Route | left1J2 → J1bottom1, through J2, J3, J1 |
| Travel time | 59 s |
| Green given | at 3 of 3 junctions |
| Collisions | 0 |

The same ambulance **without** priority was slower (checked by a test).

**Files:**
- `sim/dispatch.py`;
- `sim/controllers/preemption.py` (`inner=`);
- the dispatch wiring in `sim/runner.py`;
- `MockAmbulances` in `sim/mock_sim.py`;
- `server/src/app.js` and `protocol.js`;
- `client/src/pages/Admin.jsx` (`AmbulanceDispatch`), `client/src/pages/Research.jsx`
  (`DispatchedTrips`), `client/src/lib/roads.js`.

**Tested by:**

| Test | Count | Checks |
|---|---|---|
| `sim/tests/test_dispatch.py` | 10 | including "faster with priority" and "unchanged without a dispatch" |
| `server/test/ambulance.test.js` | 5 | refusals, events in order, stored result, roles |
| `e2e.real.test.js` | 1 | real SUMO |
| `tests/e2e/ambulance.mjs` | 6 browser steps | the button, the map, arrival, the event centre |

---

## Where each module is in the app

| Page | Module(s) |
|---|---|
| *My drive* (home: live map) | all live modules on the map; one-driver mode |
| *Live overview* | junction cards, VANET and navigation panels, live charts |
| *Signals* | 2, 4 (and 7 during preemption) |
| *VANET* | 6 |
| *Emergency* | 7, 8, ambulance routing |
| *Navigation* | 9 |
| *Incidents* | 10 |
| *Work zone* | accident work zone |
| *GLOSA* | eco-driving |
| *Results*, *GA tuning* | 11, 12 (stored research results) |
| *Run history*, *Events* | stored runs, comparisons, the event centre |
| *Demo* | guided 13-step teacher demo |
| *Run control* | start/stop, playback, **send an ambulance**, driver mode |
| *Administration*, *Account* | users, sessions, audit, settings; own password and sessions |

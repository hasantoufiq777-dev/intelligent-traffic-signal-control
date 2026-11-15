# VANET-Based Intelligent Traffic Signal Control — Project Overview

**This is the summary of the whole project** (updated 15 November 2026): what it is, how every part
works, what was built in each module and in the web platform, the results, and how to run and
deploy it. A step-by-step explanation per module is in [HOW_IT_WORKS.md](HOW_IT_WORKS.md); all
documents are listed in [README.md](README.md). For deeper technical detail see
[method.md](method.md) (design and maths), [../PROJECT_CONTEXT.md](../PROJECT_CONTEXT.md)
(interfaces and protocol), [results_discussion.md](results_discussion.md) and
[../results/summary.md](../results/summary.md) (all evaluated numbers).

---

## 1. What the project is

A traffic simulation in which traffic lights, ambulances, navigation and speed advice are controlled
by information that cars exchange over a **VANET** (Vehicular Ad hoc Network: car-to-car V2V and
car-to-roadside V2I radio). It answers questions such as:

- Do traffic lights that react to real queues beat fixed-time lights, and does that still hold when
  the queue information comes over a lossy radio network and not every car has a radio?
- How much faster does an ambulance get through when lights turn green for it?
- Can navigation, incident detection, eco-driving advice and an accident work zone be run from the
  same VANET data?

Everything runs in simulation and is shown live on a web dashboard that many people can watch at once.

| Part | Technology |
|---|---|
| Traffic simulator | **SUMO 1.28** (Eclipse), controlled through **TraCI** / libsumo |
| Control logic, VANET emulation, algorithms | **Python** (`sim/`) |
| Gateway server | **Node.js** — Express, Socket.io, SQLite (better-sqlite3), zod (`server/`) |
| Dashboard | **React** + Vite, Recharts (`client/`) |
| Statistics and plots | NumPy, SciPy, Matplotlib |

There is no OMNeT++/Veins: the wireless network is emulated in Python (range, distance-based loss,
delay, jitter, relaying).

---

## 2. Architecture

```
SUMO (moves the cars)  <-- TraCI -->  Python simulation (sim/)
                                        - signal controllers, VANET, navigation, eco-driving,
                                          incidents, accident zone, driver mode
                                        | JSON lines on stdout (one burst per simulated second)
                                        v
                       Node.js gateway (server/)  -- SQLite (runs, results, events, drives)
                                        | Socket.io (one shared stream) + REST API
                                        v
                       React dashboard (client/)  -- map, cards, charts, results, driving
```

- The gateway starts the Python simulation as a child process and loops through a **playlist** of
  scenarios (`server/playlist.json`). There is one shared simulation; every viewer watches it.
- Python prints protocol messages (`network`, `state`, `vehicles`, `event`, `driver`, `playback`,
  `status`, `result`); the gateway validates, stores and forwards them to every connected browser.
- The gateway sends commands to Python on stdin: pause, resume, speed, drive a car, send an ambulance.
- Everyone signs in; roles decide who may do what (section 13).
- A **mock simulator** (`sim/mock_sim.py`) produces the same messages without SUMO, for development.

### Repository layout

```
sim/
  main.py, runner.py        CLI and the simulation loop
  sensing.py                StateProvider interface (oracle = exact SUMO data)
  controllers/              base (safe switching), fixed, adaptive, preemption, zone
  vanet/                    channel, messages, nodes (RSU/TMC), network, alerts, glosa, sensing_vanet
  navigation/               graph, astar, aco, navigator, ev_router
  scenarios/                YAML scenarios, route generator, incident, accident_zone
  sumo/                     networks: net.xml (single), grid2x2.net.xml, grid2x2_zone.net.xml
  optimizer/ga_tuner.py     genetic algorithm tuning
  analytics/                metrics, evaluate (multi-seed suites), plots, report
  driver.py                 one-driver mode
  commands.py               dashboard commands on stdin; playback (pause, resume, speed)
  dispatch.py               ambulance sent from the dashboard at any moment
  live_vehicles.py          per-vehicle map rows, fuel/CO2 tracking
  mock_sim.py, mock_traffic.py   SUMO-free stand-in
  tests/                    pytest suite (148 tests)
server/src/                 gateway: app (routes), auth, password, liveChannel, driver, db, protocol,
                            simProcess, backup, createAdmin, config
server/test/, loadtest/     gateway tests (vitest), load and size measurements
client/src/                 dashboard: pages, components, hooks, lib
tests/e2e/, tests/deploy/   browser tests (Playwright + Edge), production-configuration check
deploy/                     production settings, systemd unit, nginx site, backup script
docs/                       this file, HOW_IT_WORKS.md, method.md, results_discussion.md,
                            architecture/, deployment/, guides/, testing/, research/
results/                    summary.json/.md, plots, GA output, raw runs (raw.csv, git-ignored)
```

---

## 3. The road network and the cars

**Networks**
- `net.xml`: one signalised junction (J0), used in the early modules.
- `grid2x2.net.xml`: the main network. Four signalised junctions **J0, J1, J2, J3** 250 m apart,
  2 lanes per direction, 50 km/h, entry roads on all sides.
- `grid2x2_zone.net.xml`: the same grid with an 80 m accident work zone on the J2–J3 road (section 12).

**Traffic demand** comes from scenario YAML files (`sim/scenarios/*.yaml`): origin–destination flows
in vehicles per hour, generated with a seeded random arrival process, so each seed gives a different
but repeatable traffic pattern.

| Scenario | Purpose |
|---|---|
| `normal` | Moderate traffic |
| `congested` | Heavy traffic |
| `emergency` | Traffic plus an ambulance |
| `navigation` | Flows with equal-length alternative routes, for routing |
| `incident` | A road closure in the middle of the run |
| `ev_incident`, `ev_congested` | Ambulance with a closure ahead / a jammed corridor |
| `accident_zone` | Crash on J2–J3 handled by alternating one-way traffic |

**How cars drive (SUMO's job).** The project never moves cars by hand; SUMO's driving models do:
- Car following (Krauss model): each car keeps a safe gap (2.5 m when stopped), accelerates at up
  to 2.6 m/s², brakes normally at up to 4.5 m/s², and has small random speed variations.
- **Stopping at red:** a car approaching a red light brakes to stop at the stop line; at yellow it
  stops if it comfortably can, otherwise it drives on.
- **Starting at green:** the queue moves off one car after another.
- **Lane changes:** cars move to the lane they need for their next turn, or around a blockage.
- **Ambulance** vehicle type: accel 3.5 m/s², decel 6 m/s², may drive up to 1.3× the speed limit.
- Collision checking (lanes and junctions) is on in every run; every run reports `collisions`
  (0 in all evaluated scenarios).

---

## 4. The simulation loop (one simulated second)

`sim/runner.py` repeats, every simulated second:

1. **Incidents:** place or remove crash vehicles if one is scheduled.
2. **VANET:** connected cars send beacons, ambulances send alerts, the channel delivers or loses
   them, RSUs update their estimates, the TMC updates its map, incident flags are raised/cleared.
3. **Signal controllers:** each junction keeps or changes its green.
4. **Accident zone controller** (zone network only).
5. **Ambulance route choice** and **navigation** (route changes).
6. **Eco-driving** speed advice.
7. **Driver mode** commands.
8. **Live output** (dashboard messages) and per-step metrics.
9. **SUMO advances** one second; collisions are checked; live runs are paced
   (0.25 s of real time per simulated second = 4× real time; 2× while someone drives).

At the end, trip statistics are read from SUMO's tripinfo output: average waiting time, time loss,
travel time, throughput, queues, ambulance travel time, fuel and CO₂ (HBEFA4 emission model).

---

## 5. Traffic signal control

### 5.1 Safe changeover (all controllers)
```
green (north–south) -> yellow 3 s -> all red 2 s -> green (east–west) -> ...
```
- Python sets the light state directly; SUMO's own timer is disabled, so a light changes only when
  the controller says so.
- Every applied state is checked for conflicting greens and refused if unsafe.
- Two phases per junction: north–south green and east–west green (each with its turns).

### 5.2 Controllers

| Controller | How it decides |
|---|---|
| `fixed` | 30 s green per direction, always (baseline) |
| `adaptive` | Every 5 s compare the north–south and east–west queues (exact, from SUMO). Green lasts 10–50 s. Switch early when the other side has ≥ 5 more cars waiting, when the served side is empty (≤ 1 car) and the other has cars, or when an approach has been red 60 s (starvation limit). A blocked approach (incident) counts as no demand. |
| `adaptive_vanet` | Same rule, but queues come from RSU estimates built from beacons |
| `adaptive_ev` | Adaptive + ambulance preemption with exact ambulance positions |
| `adaptive_ev_vanet` | Adaptive over VANET + preemption driven by received ambulance alerts |
| `adaptive_ev_vanet_tuned` | Same, with GA-tuned parameters |

### 5.3 Ambulance preemption
1. The ambulance broadcasts an **EV_ALERT** every 0.5 s (position, speed, route).
2. RSUs receive it directly (V2I) or relayed car to car (V2V, up to 3 hops); the receiving RSU forwards
   it over the wired backbone to the junctions on the ambulance's route. Between alerts, RSUs estimate
   its position by dead reckoning; an alert older than 2 s expires.
3. When a junction expects the ambulance within **18 s**, it switches (yellow, all red) to **green for
   the ambulance's approach only**, so the queue in front of it clears.
4. It holds that green until the ambulance has passed, then recovers to normal adaptive control.
   Safety limit: 90 s. If the ambulance changes approach (new route), the green follows it.

**Ambulance at any moment** (`sim/dispatch.py`). *Send an ambulance* in Run control adds an ambulance
to the running simulation at once, with any scenario and controller. The route is automatic (the
crossing through the most junctions) or chosen. Controllers without priority of their own (`fixed`,
`adaptive`, `adaptive_vanet`) are wrapped in the preemption controller for the rest of the run.
Measured in a real fixed-time grid run: 3 junctions, 59 s, green at 3 of 3, no collision; slower
without priority (tested).

### 5.4 Relation to published work
The controllers are rule-based (queue-actuated control with min/max green and a starvation limit,
plus standard emergency preemption); they do not implement a specific paper. Compared with Cai et al.,
"An Intersection Signal Control Mechanism Assisted by Vehicular Ad Hoc Networks", Electronics 2019:
this project shares the RSU/VANET data collection idea, but does not implement far-vehicle multi-hop
collection (ICP), island estimation, forward simulation, per-flow waiting time, Fixed-FA or Dynamic-FA,
or 12 conflict-free phase pairs. It does add things that paper leaves out: safety intervals and a
starvation limit, partial radio penetration, realistic packet loss and emergency priority.

---

## 6. The VANET emulation (`sim/vanet/`)

| Element | Behaviour |
|---|---|
| **Equipped cars (OBU)** | A seeded share of cars (penetration 20–100%) has a radio; ambulances and the driven car always do |
| **Beacon** | Every connected car, once a second: position, speed, lane |
| **Channel** | Range 300 m. Loss probability = base_loss + (max_loss − base_loss)·(d/R)², max 0.2 by default; delay 10 ms + jitter 5 ms; seeded, so runs repeat exactly |
| **RSU** (one per junction) | Keeps the latest beacon per car (stale after 3 s). Queue per approach = cars slower than 1 m/s within 100 m, × 1/penetration. Mean speed per road |
| **TMC** (traffic management centre) | Wired to all RSUs: network-wide map of road speeds and incidents |
| **EV_ALERT** | Ambulance alerts, V2I + optional V2V relay (duplicate suppression, hop limit) |
| **INCIDENT_FLAG** | RSU → TMC when stopped vehicles indicate a blockage |
| **SPaT** | RSU → cars: signal phase and timing, for eco-driving |

---

## 7. Navigation (Module 9) — `sim/navigation/`

- A share of connected cars (30% by default) navigates. Every ~30 s, or after passing a junction, a
  navigating car asks for the fastest route.
- **Road cost** = length / current speed from the TMC map (smoothed over ~60 s) + 8 s per traffic
  light; closed roads cost infinite.
- **A\*** (`astar.py`): exact fastest route with a Euclidean distance / max-speed heuristic
  (equivalent to Dijkstra with the heuristic off).
- **ACO — ant colony optimisation** (`aco.py`): ants walk the road graph choosing turns with
  probability ∝ τ^α · (1/cost)^β; pheromone evaporates (ρ) and the best ants deposit Q/cost;
  pheromone is kept per destination. Returns the 3 best routes with selection probabilities, so cars
  spread over alternatives instead of all taking the same road. Falls back to A\* if no ant arrives.
- A car switches route only if the new one is ≥ 5% faster and not more than once per 15 s.
- **Ambulance route choice** (`ev_router.py`, `--ev-routing on`): the ambulance plans with A\* at
  departure, on each new road and on a new closure; it counts no signal wait (preemption gives it
  green) and switches only for a clearly faster route. Preemption follows the new route.

---

## 8. Incidents (Module 10)

- **Ground truth:** a crash is simulated by stopped "blocker" vehicles on one lane (`stop`) or all
  lanes (`closure`) for a set time (`sim/scenarios/incident.py`).
- **Detection:** an RSU flags a road when a car has been stopped on it for 30 s more than 30 m from
  the stop line (red-light queues sit at the stop line, so they are not flagged): `lane_blocked` or
  `edge_blocked`. The flag goes to the TMC; it clears 10 s after the evidence disappears.
- **Reaction:** navigation avoids the road; drivers reaching a closed road turn away (local detour);
  the signal controller gives no green to an approach that cannot move.
- Detection delay in the evaluation: about 33 s.

---

## 9. Eco-driving with GLOSA (Module 14) — `sim/vanet/glosa.py`

GLOSA = Green Light Optimal Speed Advisory.
- Each RSU broadcasts SPaT once a second (when its lights turn green and for how long).
- An **eco car** within 300 m computes its arrival at the stop line:
  - **go** — it arrives during green: no change;
  - **slow** — it would arrive at red: target speed = distance / time until green (+1 s margin),
    applied with `setSpeed`, so it arrives as the light turns green without stopping;
  - **stop** — the needed speed is below 5 m/s: drive normally and stop;
  - **none** — timing too uncertain (e.g. adaptive or preemption), no fresh SPaT, or a queue ahead.
- After the junction the car returns to normal speed (`setSpeed(-1)`). SUMO still enforces safe gaps
  and red lights.
- Fuel and CO₂ are tracked per car (HBEFA4 rates) and compared eco vs other cars.

---

## 10. Genetic algorithm tuning (Module 11) — `sim/optimizer/ga_tuner.py`

- **Targets:** (a) signal/VANET parameters (min/max green, queue threshold, decision interval, ETA
  threshold, alert and beacon intervals); (b) ACO parameters (α, β, ρ, ants, iterations, Q, elite).
- **GA:** tournament selection, BLX-α crossover, bounded Gaussian mutation, elitism, a cache of
  evaluated chromosomes, parallel workers each running SUMO.
- Tuned on seeds 101–103, checked on held-out seeds 1–10.
- Results: ACO fitness improved 34.6% (p = 0.002) and its route search became 3–4× faster;
  signal-parameter tuning gave a small, not significant gain (~4%).

---

## 11. One-driver mode (Module 15+) — `sim/driver.py`, `server/src/driver.js`

- One viewer at a time takes a car into the live run (from road A to road B) and drives it with
  **Go**, **Ease off** (10–50 km/h) and **Stop** (keys G / E / S).
- SUMO's safety rules stay on: the driver cannot run a red light or hit another car.
- The driver sees speed advice (GLOSA), the next light, ambulance warnings (V2V, "behind" or
  "crossing"), and at the end a trip report: time, distance, stops, fuel, CO₂, advice followed,
  compared with other cars on the same route.
- Seat rules: freed on arrival, hand-back, 2 min idle, 10 min max, leaving (15 s reconnect grace),
  run end or by the admin; commands are rate-limited. The driven car is excluded from result metrics.

---

## 12. Accident work zone (alternating one-way traffic)

Network `grid2x2_zone`: the J2–J3 road is split at ZW (x = 340) and ZE (x = 420). A contraflow edge
`J3J2.cf` lies on the eastbound outer lane and is fed only from the inner westbound lane. ZW and ZE are
signals owned by the zone controller (`sim/controllers/zone.py`). The four grid junctions keep exactly
the connections of `grid2x2`.

**Scenario:** at 150 s a crash blocks both westbound lanes and its debris the inner eastbound lane
(for 360 s); one lane is left for both directions.

| Step | Rule |
|---|---|
| Activation | When the TMC marks the westbound zone road fully blocked (VANET detection, ~33 s); off again when it clears |
| Turns | green(d) → yellow 3 s → all red until the shared lane is empty (≥ 2 s) → other side |
| Safety | The next side never gets green before the lane and both end junctions are empty — counted by entry/exit detectors, not radio |
| Turn length | 8 s + 2.5 s per queued car (max 40 s); ends early if its side is empty and the other waits; a side waiting 90 s goes next |
| Queues | From beacons received by the zone's roadside unit (range, loss, 1/penetration) |
| Ambulance | Its side gets the next turn: the other side's green ends at once, the lane clears, its side turns green until it has entered |
| Routing | Westbound routes moved onto the shared lane; navigation and ambulance routing see the expected wait as extra cost |
| Trapped cars | Cars caught behind the crash before detection are guided into the shared lane before the next westbound turn |
| `closure` mode (baseline) | Westbound road closed at J3; traffic detours over other roads |

---

## 13. The dashboard and gateway

**Gateway (`server/`)**
- Starts runs, loops the playlist (crash retry; stops after 3 failures in a row), validates every
  message (zod), stores runs, results, events, state samples and driver trips in SQLite.
- One shared Socket.io stream: state and map frame at most once per second (latest-wins), events
  and results reliably; first message ~64 KB. Load-tested at 1,000 signed-in viewers (API p95
  20.3 ms); at most 1,000 connections.
- Run lifecycle STOPPED → STARTING → RUNNING → STOPPING (or FAILED); **playback**: pause, resume,
  speed 0.5×–4×, restart (wall-clock pacing only, results unchanged); **send an ambulance**.
- REST API (full list in [architecture/API.md](architecture/API.md)): health, live status and
  control, options, runs, results, experiments summary, GA, driver, accounts, sessions, audit,
  settings, system health.

**Accounts and security**
- E-mail + password (Argon2id); session token in an `HttpOnly` cookie, only its hash stored.
- Roles: **viewer** (watch, follow and drive a car), **researcher** (also start, stop and control
  own runs), **administrator** (everything, users, sessions, audit, settings).
- CSRF token and origin check on writes and live sockets, input validation, rate limits, account
  lockout, security headers (CSP limited to this host, HSTS in production), audit log.

**Dashboard (`client/`)**
- **My drive** (home): live map, follow a car (next light, countdown, advice, fuel), drive a car,
  eco-driving summary, accident-zone card, events.
- **Live overview:** junction cards (signal, queues true vs estimated), VANET and navigation panels,
  live charts.
- **Module pages:** Signals, VANET (with measured jitter), Emergency, Navigation (latest A\*/ACO
  decision with probabilities), Incidents, Work zone, GLOSA.
- **Research:** Results (from `results/summary.json`), GA tuning, Run history (compare two runs;
  ambulances sent), Events (event centre).
- **Demo:** guided 13-step teacher demonstration with pause to explain.
- **Run control:** start/stop with all options (including demand 0.25–2×), playback, send an
  ambulance, driver mode, SUMO-GUI. **Administration** and **Account** pages.
- Works from 360 px to 1920 px wide; accessibility audits with 0 violations.

**Map details**
- Roads drawn wider than real so they are visible; signal heads coloured by state.
- Cars drawn from the front bumper backwards at real length + 1.5 m, so queued cars never overlap.
- Headings animate the short way (no 360° spins on turns).
- Each car has a stable paint colour (12) and body style (sedan, hatchback, SUV, pickup, sports) from
  its id; a roof dot marks connected (blue) or eco (green) cars; the ambulance is a white van with a
  red cross and light bar; the driven car is purple.
- Accident zone overlay: blocked lanes, crash and debris markers, the shared lane with arrows for the
  current direction, both zone signals, and the closure line in closure mode.

---

## 14. Results (10 seeds each, mean; p-values from paired Wilcoxon tests)

**Signal control (vs fixed-time)**

| Configuration | Normal: waiting | Congested: waiting |
|---|---|---|
| Fixed-time | 19.4 s | 37.4 s |
| Adaptive (exact queues) | 12.1 s (−37.6%) | 26.9 s (−28.1%) |
| Adaptive over VANET, 100% connected | 11.9 s (−38.7%) | 22.2 s (−40.8%) |
| Adaptive over VANET, 50% connected | 15.7 s (−18.9%) | 20.2 s (−46.0%) |

**Ambulance (emergency scenario, vs adaptive without preemption: 87.6 s)**

| Configuration | Ambulance trip |
|---|---|
| Preemption, exact positions | 65.2 s (−25.6%) |
| Preemption over VANET, 10% loss, relay on | 65.6 s (−25.1%) |
| GA-tuned VANET preemption | 66.5 s (−24.1%); average waiting of all cars also −24% |

Missed preemptions: 0 in all configurations.

**Ambulance route choice (vs scenario route)**

| Scenario | Route off → on |
|---|---|
| Closure ahead (`ev_incident`) | 113.7 s → 63.3 s (−44.3%) |
| Congested corridor (`ev_congested`) | 74.1 s → 64.5 s (−13.0%) |
| Normal (`emergency`) | no significant change |

**Navigation (30% of cars navigating)**

| Scenario | Effect |
|---|---|
| `navigation` | A\*: navigating cars −2.1%, traffic spread more evenly (edge-load CV −11%); ACO similar spread, slower search |
| `incident` | A\*: navigating cars −8.2%, all cars −2.9%; ACO-tuned −7.7% / −3.2% |

**Eco-driving (fixed-time signals, normal traffic)**

| Eco share | CO₂ per km | Stops per car | Waiting |
|---|---|---|---|
| 0% | 253.9 g | 1.45 | 19.4 s |
| 60% | −1.2% | −25% | −19.6% |
| 100% | −2.9% | −40.5% | −30.6% |

With adaptive signals the timing is too unpredictable for advice, so eco-driving has no effect there.

**Accident work zone (vs closing the westbound road)**

| Configuration | Avg trip | Avg waiting | Ambulance trip |
|---|---|---|---|
| Closure | 102.1 s | 19.4 s | 66.2 s |
| Alternating | 95.4 s (−6.6%) | 15.2 s (−21.6%) | 74.2 s (+12.1%) |
| Alternating + A\* navigation | 94.4 s (−7.5%) | 15.1 s (−22.1%) | 71.5 s (+8.0%) |
| Alternating + ambulance route choice | 95.3 s (−6.6%) | 15.1 s (−22.3%) | 60.5 s (−8.6%) |

Zero times with both directions in the shared lane, zero collisions.

---

## 15. Project timeline (modules and later work)

| Module | Work |
|---|---|
| 0 | Project context and directory structure |
| 1 | Environment, single-junction network, route generation |
| 2 | Simulation runner, metrics, fixed-time baseline |
| 3 | Live JSON stream and mock simulator |
| 4 / 4b | Adaptive signal controller; sensing abstraction (StateProvider), protocol v2 |
| 5 | 2×2 signalised grid, route-alternatives check |
| 6 | Emulated VANET layer (channel, RSUs, TMC), VANET-sensed adaptive control |
| 7 | Ambulance detection and preemption (exact data) |
| 8 | Preemption over V2I/V2V (alerts, relay, backbone, dead reckoning) |
| 9 | Navigation with A\* and ACO on the VANET traffic map |
| 10 | Incidents detected through VANET, rerouting and signal reaction |
| 11 | GA tuning of signal/VANET and ACO parameters |
| 12 | Multi-seed evaluation suites with statistics and plots |
| 13 | Node.js gateway (live channel, playlist, SQLite, REST, Socket.io) |
| 14–15 | React dashboard (user panel, live overview, results, admin), eco-driving (GLOSA), ambulance-only preemption green, navigation cost fix, final evaluation |
| Later | Ambulance route choice and a preemption deadlock fix; one-driver mode; map fixes (front-bumper drawing, collision check, no spin on turns, varied car colours and body styles); accident work zone with alternating one-way traffic |
| Platform phases 1–10 | Accounts, roles and security; shared live run with lifecycle; live map and module pages; research dashboards and event centre; guided teacher demo; security, accessibility and 1,000-user load tests; performance work (first message 350 → 64 KB); playback controls; measured jitter, ACO route decisions, demand setting, platform events; deployment guide and files; user, admin, teacher and troubleshooting guides; test strategy, plan and twelve test reports |
| November 2026 | Ambulance sent at any moment from Run control, with priority added for every controller (`sim/dispatch.py`) |

**Fixes found along the way (documented in method.md):** navigation cost model (signal wait per
junction), preemption deadlock when an ambulance changes approach, two lanes merging into the
contraflow lane (emergency stop and a rear-end crash), cars trapped behind the crash blocking the
zone entry (gridlock), junction-link direction for unknown road names.

---

## 16. Running the project

**Install** (needs SUMO, Python 3.12, Node.js 20; `SUMO_HOME` set):
```bash
npm run setup          # .venv + Python requirements + npm install (server, client)
```

**Live demo**
```bash
npm run dev:mock       # gateway + dashboard with the mock simulation (no SUMO)
npm run dev            # same with real SUMO   -> http://localhost:5173
npm start              # build the dashboard and serve everything from the gateway (port 4000)
```

**One simulation from the command line**
```bash
python sim/main.py --scenario <normal|congested|emergency|incident|navigation|ev_incident|ev_congested|accident_zone> \
    --controller <fixed|adaptive|adaptive_vanet|adaptive_ev|adaptive_ev_vanet|adaptive_ev_vanet_tuned> \
    [--network single|grid2x2|grid2x2_zone] [--nav none|astar|aco|aco_tuned] [--seed 1] \
    [--penetration 0.5] [--loss 0.1] [--range 300] [--relay on|off] [--nav-fraction 0.3] \
    [--eco-fraction 0.6] [--ev-routing on|off] [--gui] [--live] [--step-delay 0.25] [--driver]
```

**Experiments and tests**
```bash
npm run sim:experiments          # full evaluation, 10 seeds -> results/summary.json, summary.md, plots
python sim/analytics/evaluate.py --suite zone --workers 8     # one suite
npm run sim:tune                 # GA tuning (signals, then ACO)
npm test                         # Python (148), gateway (87) and client (15) tests
npm run test:e2e                 # end-to-end with real SUMO
```

**Configuration:** `sim/config.yaml` (controller, VANET, navigation, eco, zone, GA, evaluation
parameters) and `server/.env` (port, Python path, accounts and security, playlist, pacing, driver mode).

**Sharing and deployment**
- Same network: `HOST=0.0.0.0`, open `http://<PC-IP>:4000`.
- Temporary public link: `cloudflared tunnel --url http://127.0.0.1:4000` (works while the PC runs).
- Permanent: Linux server with SUMO + Python + Node, systemd service, nginx reverse proxy with
  WebSocket upgrade and HTTPS: [deployment/DEPLOYMENT.md](deployment/DEPLOYMENT.md) and the files in
  `deploy/`. Set `NODE_ENV=production`, `TRUST_PROXY=1` and create the administrator account
  (`npm run create-admin` in `server/`).
  Serverless hosts do not fit (long-running process and WebSockets).

---

## 17. Quality and safety checks

- **148 Python tests** (controllers, VANET, preemption, navigation, incidents, GA, evaluation,
  driver mode, collisions, accident zone, playback, live extras, ambulance on demand), **87 gateway
  tests** (+ 4 with real SUMO), **15 dashboard tests**.
- Browser tests in Edge: journey, every page, every role, live details, ambulance button,
  6 screen widths (108/108), accessibility (0 violations in 45 audits).
- Security, load (1,000 users), performance, failure and recovery, backup/restore/rollback and a
  production-configuration check: see [testing/](testing/) and the
  [final acceptance report](testing/FINAL_ACCEPTANCE_REPORT.md).
- **Zero-regression test:** 12 reference runs must reproduce the stored results exactly.
- Every run records SUMO collisions (lanes and junctions): 0 in all evaluated scenarios.
- Accident zone: a test checks that both entry signals are never green together and that both
  directions are never in the shared lane together.
- New features were checked to leave earlier results unchanged (stored runs reproduce exactly).

---

## 18. Limitations

- Simulation only; the VANET is emulated (no OMNeT++/Veins radio model).
- Two-phase signals per junction (north–south / east–west); no pedestrians. Phase-order
  optimisation such as ACO phase scheduling (Rida et al., 2020, ASTL) is therefore not used; ACO is
  applied to route choice instead.
- Web platform: tested on Windows with Edge; the Linux/nginx deployment, Firefox/Safari, screen
  readers and an external security scan were not run; no e-mailed password resets or two-factor
  sign-in.
- Small 2×2 grid: few alternative routes, which limits what navigation and ACO can gain.
- Eco-driving needs predictable signal timing (works with fixed-time, not adaptive).
- Driver mode is a demo feature; one driver at a time; live runs with a driver are not evaluated.
- The accident zone exists on one road (J2–J3) of its own network variant; the mock simulator has no
  zone. Cars guided past the crash are moved across by TraCI (a short sideways jump on the map).

---

## 19. Repository and version control

- GitHub: `hasantoufiq777-dev/intelligent-traffic-signal-control`, branch `main` (modules 0–15);
  branch `modules-10-13` holds the later work and the web platform (pushed 15 November 2026, not
  yet merged into `main`).
- Modules 0–15 and ambulance route choice: authored by Toufiq Hasan.
- Later work (npm fix, `client/src/lib` tracking, driver mode, map fixes, car styles, accident zone,
  web platform phases 1–10, ambulance on demand): authored by ShahariarEmon112.
- Commit messages carry no AI co-author lines.
- `sim/scenarios/navigation.yaml` now adds an ambulance (amb1, departs at 80 s); the stored
  navigation results were measured before this change.

---

## 20. Glossary

| Term | Meaning |
|---|---|
| VANET | Vehicular ad hoc network: cars and roadside units exchanging radio messages |
| V2V / V2I | Vehicle-to-vehicle / vehicle-to-infrastructure communication |
| OBU | On-board unit: the radio in a car |
| RSU | Roadside unit: the radio at a junction |
| TMC | Traffic management centre, wired to all RSUs |
| Penetration | Share of cars that have a radio |
| PDR | Packet delivery ratio |
| Preemption | Turning lights green for an ambulance |
| SPaT | Signal phase and timing message |
| GLOSA | Green light optimal speed advisory (eco-driving) |
| A\* / ACO | Shortest-path search / ant colony optimisation |
| GA | Genetic algorithm (parameter tuning) |
| TraCI | SUMO's remote-control interface |
| Starvation limit | Longest time an approach may stay red while it has cars waiting |

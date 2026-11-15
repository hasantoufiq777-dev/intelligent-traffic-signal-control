# Implementation summary: what was added and how it works

This document explains the work added in November 2026 and how each part works inside:
- the ambulance at any time;
- the supervisor's enhancements;
- the redesign of the city network.

It also explains the algorithms underneath that the new features use: signal control, A\*, ACO, the
VANET and the genetic algorithm.

Related documents: [HOW_IT_WORKS.md](HOW_IT_WORKS.md) (every module, step by step),
[PROJECT_OVERVIEW.md](PROJECT_OVERVIEW.md) (the whole project), [architecture/DESIGN_PATTERNS.md](architecture/DESIGN_PATTERNS.md),
[architecture/ENHANCEMENT_PLAN.md](architecture/ENHANCEMENT_PLAN.md) (plan, risks, measured outcome).

---

## Contents

1. [The tasks that were added](#1-the-tasks-that-were-added)
2. [How the system fits together](#2-how-the-system-fits-together)
3. [Traffic light control](#3-traffic-light-control)
4. [Ambulance priority and the ambulance at any time](#4-ambulance-priority-and-the-ambulance-at-any-time)
5. [The VANET](#5-the-vanet)
6. [Routing: A\*, its heuristic, ACO and the route explorer](#6-routing-a-its-heuristic-aco-and-the-route-explorer)
7. [The city network redesign](#7-the-city-network-redesign)
8. [Vehicle types and lane restrictions](#8-vehicle-types-and-lane-restrictions)
9. [The 3D driver perspective](#9-the-3d-driver-perspective)
10. [The traffic control view (2D map)](#10-the-traffic-control-view-2d-map)
11. [Driving controls and the fuel-efficient speed](#11-driving-controls-and-the-fuel-efficient-speed)
12. [Eco-driving (GLOSA) and the genetic algorithm](#12-eco-driving-glosa-and-the-genetic-algorithm)
13. [Design patterns](#13-design-patterns)
14. [How it was verified](#14-how-it-was-verified)
15. [Limits](#15-limits)

---

## 1. The tasks that were added

| # | Task | Result | Main files |
|---|---|---|---|
| 1 | Send an ambulance at any moment | *Send an ambulance* in Run control; priority at every junction for every controller | `sim/dispatch.py`, `server/src/app.js`, `client/src/pages/Admin.jsx` |
| 2 | Realistic road network with route alternatives | Non-uniform town `grid2x2_city`: main, secondary, collector and local roads | `sim/sumo/generate_city_net.py`, `sim/scenarios/city.yaml` |
| 3 | A\* with a correct heuristic, alternatives shown | Class-aware A\*, distance search, route explorer | `sim/navigation/astar.py`, `graph.py`, `route_explorer.py` |
| 4 | VANET penetration 50 % | Default 50 %; connected vehicles counted and marked | `sim/main.py`, `server/src/protocol.js`, `TrafficMap.jsx` |
| 5 | Design patterns | Strategy (planners), Factory (controllers, planners, vehicle types), documented Adapter, State, Observer | `sim/navigation/planners.py`, `sim/controllers/factory.py`, `sim/vehicles.py` |
| 6 | One colour for eco vehicles | `--eco` (#16a34a) on the map, in the 3D view and in the legend | `client/src/styles.css`, `CarShape.jsx`, `DriverView3D.jsx` |
| 7 | Vehicle types | Car, eco car, motorcycle, bus, truck, ambulance as real SUMO types | `sim/vehicles.py` |
| 8 | Lane widths and restrictions | Widths 3.6–3.0 m; permissions per class, enforced by SUMO | `sim/sumo/generate_city_net.py` |
| 9 | Driver perspective and control view | 3D view with three.js and the 2D map, switchable | `client/src/components/DriverView3D.jsx`, `pages/UserPanel.jsx` |
| 10 | Speed control and advice | Accelerate, brake, slow down, stop, desired speed; recommendation with reasons | `sim/driver.py`, `client/src/components/DrivePanel.jsx` |
| 11 | Fuel-efficient speed | From fuel curves measured with SUMO's HBEFA4 model | `sim/analytics/fuel_curves.py`, `sim/eco_speed.py` |
| 12 | Tests and documentation | 160 Python, 89 gateway, 15 dashboard tests; browser and accessibility suites; these documents | `sim/tests/test_city.py`, `tests/e2e/city.mjs`, `docs/` |

**The research is untouched.** Everything was built so that the research results stay valid:
- the research network `grid2x2` and its scenarios are unchanged;
- a 12-run zero-regression test confirms the stored results are reproduced exactly.

---

## 2. How the system fits together

```
 Browser (React)
   Traffic control view (SVG map) | Driver perspective (three.js 3D) | panels, route explorer, driving
        │  HTTPS (REST)  +  WebSocket (Socket.io)
        ▼
 Node gateway (server/)        one shared simulation for every viewer, accounts and roles, SQLite
        │  stdin: commands (pause, speed, drive, ambulance, routes)
        │  stdout: one JSON line per message (state, vehicles, event, driver, routes, result)
        ▼
 Python simulation (sim/)      signals, VANET, routing, ambulance priority, advice
        │  TraCI
        ▼
 SUMO                          moves every vehicle (car following, lane changes, safety)
```

**Every simulated second,** `sim/runner.py`:

1. **Incidents:** places or removes crash vehicles.
2. **VANET:** steps the radio network (beacons, alerts, roadside units).
3. **Signals:** steps every junction controller.
4. **Work zone:** runs its controller (zone network only).
5. **Routing:** ambulance routing and navigation.
6. **Eco-driving:** GLOSA speed advice.
7. **Commands from the dashboard:** playback, driving, a sent ambulance, route searches.
8. **Live messages:** prints the state, the map frame and events.
9. **SUMO:** advances one second; collisions are checked.

**How the dashboard reaches the simulation:**
- **Commands** go to the Python process's standard input as JSON lines, for example
  `{"cmd": "routes", "from": "gEJ1", "to": "kcgS0", "vclass": "truck"}`.
- **Replies** come back on standard output, for example a `routes` message with the same request id.
- **The gateway** passes the reply only to the viewer who asked.

---

## 3. Traffic light control

**Who decides.** Python does: SUMO's own signal timer is switched off, and every junction has a
controller object.

### 3.1 Safe changeover (all controllers): `sim/controllers/base.py`

```
green (one direction) → yellow 3 s → all-red 2 s → green (other direction)
```

- **City scenario:** the all-red is 4 s, because its junctions are 30–40 m wide.
- **Conflict check:** every signal state is checked before it is applied. A state where north–south
  and east–west are green together is refused.

### 3.2 The controllers

| Controller | Decision rule |
|---|---|
| `fixed` | 30 s green per direction, always |
| `adaptive` | rule below, on exact queue counts from SUMO |
| `adaptive_vanet` | the same rule, on queue estimates from the roadside units (VANET) |
| `adaptive_ev`, `adaptive_ev_vanet` | the adaptive rule plus ambulance preemption (section 4) |

**The adaptive rule** (`sim/controllers/adaptive.py`). Every 5 s it compares
Q_NS = queue N + queue S with Q_EW = queue E + queue W:

- **Minimum and maximum green:** a green lasts at least 10 s and at most 50 s.
- **It switches to the other direction when:**
  - the other direction has waited 60 s (starvation limit); or
  - the served direction has ≤ 1 vehicle and the other has some; or
  - the other direction has at least 5 more vehicles waiting.
- **Blocked approaches:** an approach blocked by an incident counts as no demand.

### 3.3 Signal programs in the city network

The generator writes each junction's program itself, as 4 phases:

| Phase | Signals |
|---|---|
| 0 | north–south green |
| 1 | north–south yellow |
| 2 | east–west green |
| 3 | east–west yellow |

- **Left turns are permissive** (shown as `g`: they give way to oncoming traffic).
- **Why the generator writes them:** with 3-lane roads SUMO would otherwise add its own left-turn
  phases, and the controllers expect exactly these four.
- **Which links belong to which axis** is found from each lane's geometry.

### 3.4 Results

| Network | Fixed-time waiting | Adaptive waiting | Adaptive over VANET |
|---|---|---|---|
| Research grid, normal traffic (10 seeds) | 19.4 s | 12.1 s (−37.6 %) | 11.9 s (100 % equipped) |
| City network (5 seeds) | 27.3 s | 19.8 s (−27 %) | 22.4 s (50 % equipped) |

---

## 4. Ambulance priority and the ambulance at any time

### 4.1 Preemption: `sim/controllers/preemption.py`

Each junction runs this state machine:

```
NORMAL ──(ambulance ETA ≤ 18 s)──► TRANSITION ──► EV_GREEN ──(passed + 5 s)──► EXIT ──► RECOVERY (10 s) ──► NORMAL
            yellow, all-red          green on the ambulance's        yellow, all-red
                                     approach only
```

1. **Detection.** Each second the junction looks at the ambulances that will cross it within their
   next 2 junctions. The data comes either:
   - exactly from SUMO; or
   - from their radio alerts (VANET controllers).
2. **Trigger.** When the nearest ambulance is ≤ 18 s away, the green ends safely (yellow, then
   all-red).
3. **EV_GREEN.** **Only the ambulance's approach** gets green, so the queue in front of it clears.
4. **End.** The green is held until it has passed, plus 5 s. If it changes route, the green moves to
   its new approach. Safety limit: 90 s.
5. **Recovery.** Normal control resumes; the waiting direction is served first.

### 4.2 Ambulance at any time: `sim/dispatch.py`

1. **Request.** Run control calls `POST /api/live/ambulance`. The gateway checks the role and writes
   `{"cmd": "ambulance"}` (optionally with `from` and `to`) to the simulation.
2. **Route.** `AmbulanceDispatcher` chooses the route. Automatically, it tries every pair of an entry
   road and an exit road with SUMO's router and keeps the one through the most signalised junctions.
3. **Priority.** If the run's controller has no ambulance priority of its own (fixed, adaptive,
   adaptive_vanet), every junction's controller is **wrapped** in a `PreemptionController`. The
   wrapper runs the original controller unchanged until an ambulance is near.
4. **Departure.** The ambulance is added to SUMO at once; at most 3 sent ambulances drive at a time.
5. **Reports:**
   - events: `ambulance_dispatched`, `preempt_start` / `preempt_end` per junction, and
     `ambulance_arrived` with the travel time and "green given at 3 of 3 junctions";
   - result fields: `dispatched_*`.

**Measured:** on the fixed-time grid, the ambulance crossed 3 junctions in 59 s with green at all 3.
Without priority, the same ambulance was slower (tested).

---

## 5. The VANET

The radio network is emulated in Python, in `sim/vanet/`.

| Part | How it works |
|---|---|
| Equipped vehicles | Each ordinary vehicle is chosen by a seeded hash of its id: about 50 % by default. Ambulances, eco cars and the driven vehicle always have a radio |
| Beacons | Every equipped vehicle sends position, speed and lane once a second |
| Channel | Range 300 m. Loss probability P(loss) = base_loss + (max_loss − base_loss)·(d/300)², 0.2 at the edge of range; delay 10 ms + 0–5 ms jitter; seeded, so runs repeat exactly |
| Roadside unit (one per signalised junction) | Keeps each vehicle's latest beacon (forgotten after 3 s). Queue per approach = vehicles slower than 1 m/s within 100 m, × 1/penetration (to count the unequipped ones). Also the mean speed per road |
| Traffic management centre | Joins the roadside units' road speeds and incident flags into one map, used by navigation |
| Ambulance alerts | Every 0.5 s. Received by a roadside unit directly (V2I), or passed on by up to 3 equipped vehicles (V2V relay) |

---

## 6. Routing: A\*, its heuristic, ACO and the route explorer

### 6.1 The road graph: `sim/navigation/graph.py`

- **Nodes and links:** the graph's nodes are **roads** (SUMO edges); its links are the turns allowed
  from one road into the next.
- **Vehicle classes:** for every class (car, motorcycle, bus, truck, ambulance) the graph stores which
  turns that class may make: a lane it may use must connect to a lane it may use. That is how trucks
  never get a route over a local road, or a left turn at a signal in the city.
- **Cost of a road** e, in seconds:

  ```
  c(e) = length(e) / max(v_e, 1 m/s)  +  8 s if e ends at a traffic light
  c(e) = ∞                                 if e is closed
  ```

  v_e is the road's current speed from the VANET traffic map, smoothed over about one signal cycle,
  or its speed limit when there is no report.

### 6.2 A\* and its heuristic: `sim/navigation/astar.py`

A\* expands the road with the smallest

```
f(n) = g(n) + h(n)
```

- **g(n):** the real cost so far, the sum of c(e) along the path.
- **h(n), the heuristic:**

  ```
          √((x_n − x_goal)² + (y_n − y_goal)²)
  h(n) = ────────────────────────────────────────
                       v_max
  ```

  - (x_n, y_n) is the end point of road n, and (x_goal, y_goal) the end point of the destination road;
  - v_max is the highest speed limit in the network (16.67 m/s = 60 km/h in the city, 13.89 m/s on the
    research grid).

**Why it is correct.** h never overestimates the real remaining time (it is *admissible*):
- no route is shorter than the straight line;
- no vehicle is faster than v_max;
- the real cost only adds slow speeds and signal waits.

A\* therefore returns the cheapest route, and it is checked against Dijkstra (h = 0) on 40 random pairs
with random congestion.

**Distance search.** The same A\* with cost = length in metres and h = the straight-line distance in
metres. It finds the shortest route; seconds and metres are never mixed.

**Example (car, from the east to S0):**
- the shortest route (791 m) takes the 30 km/h local road and needs 75 s;
- the fastest (802 m) stays on the 3-lane main road through J0 and needs 67 s.

### 6.3 ACO (ant colony optimisation): `sim/navigation/aco.py`

1. **The ants walk.** Ants start on the current road. At each junction an ant picks the next road v
   with probability

   ```
   P(v) ∝ τ(u,v)^α · (1 / c(v))^β        (α = 1, β = 2 by default; GA-tuned: α 0.49, β 4.50)
   ```

   τ is the pheromone on the turn u→v, kept separately for each destination.
2. **After each iteration:**
   - **evaporation:** τ ← max(τ_min, (1 − ρ)·τ), with ρ = 0.1;
   - **deposit:** the best k (3) routes add Q / cost, with Q = 100 and τ capped at τ_max.
3. **The answer:** the 3 cheapest distinct routes, each with probability ∝ 1/cost. The car picks one
   at random by those probabilities, so cars spread over alternatives instead of all taking the same
   road. If no ant arrives, A\* is used instead.

### 6.4 When navigating cars switch: `sim/navigation/navigator.py`

- **Who navigates:** 30 % of equipped vehicles.
- **When they search:** every 30 s, after each junction, or at once if an incident is reported on their
  route.
- **When they switch:** only if the new route is at least 5 % faster, and at most once per 15 s.

### 6.5 Route explorer: `sim/navigation/route_explorer.py`

Asked from the Routes tab, it uses the same A\* and the same costs, on the current traffic map:
1. A\* on time gives the **fastest now**.
2. A\* on distance gives the **shortest**.
3. **Alternatives:** each road of the fastest route is closed in turn and A\* is run again, keeping the
   cheapest distinct routes.
4. **Each route** is reported with its distance, free-flow time, current time, signals and congestion,
   with the fastest and the shortest marked.
5. **A\*'s own figures** are reported too: h at the start (straight-line distance ÷ v_max) and the
   roads expanded.

### 6.6 Ambulance routing: `sim/navigation/ev_router.py`

- **Method:** A\* for the emergency class, with no signal wait counted (preemption gives it green).
- **When it searches:** at departure, on each new road and when a closure appears.
- **When it switches:** only for a clearly faster route.

---

## 7. The city network redesign

**Built by** `sim/sumo/generate_city_net.py`, with SUMO's netconvert. **What it builds:** a
non-uniform town with four signalised junctions, J0–J3, at irregular spacings.

| Road class | Lanes each way | Widths | Speed | Who may use it |
|---|---|---|---|---|
| Main (W–J0–J1–E, with a bend) | 3 | 3.6 / 3.5 / 3.3 m | 60 km/h | all; inner lane no trucks or buses |
| Secondary (north–south, J2–J3) | 2 | 3.4 / 3.2 m | 50 km/h | all; inner lane no trucks or buses |
| Collector (W2–J2, J3–E3) | 2 | 3.25 / 3.0 m | 40 km/h | all; inner lane no trucks or buses |
| Local sub-roads (bends, stop signs) | 1 | 3.0 m | 30 km/h | cars, motorcycles, ambulances |

**How it is built:**

1. **Plain files.** Nodes and edges are written as XML: lane widths and permissions per lane, curve
   points for the bends, and node types (signal, stop-controlled T-junction, exit).
2. **First netconvert run.** It works out which turn each pair of roads is (right, straight, left).
3. **Explicit lane use at the signals,** like lane markings:
   - right turns only from the right lane;
   - left turns only from the inner lane;
   - straight on from each lane into the matching lane.

   **Why this was needed:** left to itself, SUMO gave trucks a left turn from the *right* lane, across
   the cars going straight beside it.
4. **Second netconvert run** with those connections.
5. **4-phase signal programs** written for J0–J3 (section 3.3), and the final network built.
6. **`check()`** verifies:
   - every exit is reachable;
   - no road is a dead end for any class;
   - each signalised junction has four legs and four phases;
   - turns come only from the allowed lanes;
   - the road classes really differ.

### Why trucks and buses never turn left at a signal

**Measured** in plain SUMO at 1.6× demand, 4 runs:

| Setup | Junction collisions |
|---|---|
| Cars and motorcycles only | **0** |
| Full mix, heavy vehicles allowed to turn left | **17**, all a bus or truck turning left across oncoming traffic in a 30–40 m junction |
| Larger accepted gap, no driver imperfection, faster acceleration, or a 0.5 s step | 9–22: none of these helped |
| **Truck routes** (inner lanes closed to heavy vehicles) | **0** |

- **The rule:** heavy vehicles go straight or turn right at the signals.
- **Which trips:** they are generated only between exits they can legally reach, 32 of the 56 pairs.
- **With the controllers:** 0 collisions in 30 runs (3 controllers × 2 demand levels × 5 seeds).

**Metrics** are recalculated by `sim/analytics/city_eval.py` into `results/city/`. The research grid
is untouched.

**City versions of the scenarios** (for live demonstrations; the research results stay on `grid2x2`):

| Scenario | What happens | Measured (seed 1, 900 s, 0 collisions) |
|---|---|---|
| `city` | Mixed traffic on the redesigned town | waiting: fixed 27.3 s, adaptive 19.8 s (5 seeds) |
| `city_emergency` | Two ambulances cross the town (east → S0, west → N3); use `adaptive_ev_vanet`, ambulance routing on | ambulance trip 73.0 s with priority, 100.5 s without (−27 %); 0 missed preemptions |
| `city_incident` | A crash blocks the main road's middle lane between J0 and J1 for 300 s | detected over the VANET after 33 s; 12 vehicles rerouted |
| `city_navigation` | 40 % more traffic, so the alternative roads matter; use `astar` or `aco` | ACO: waiting 28.8 s, against 31.2 s without navigation |

The crash blocks one lane rather than closing the road:
- **why not a full closure:** trucks and buses would have no legal detour there (they never turn left at
  a signal), and this project never teleports stuck vehicles;
- **what still happens:** traffic squeezes past in the other two lanes, and navigating cars, warned over
  the VANET, take other roads.

The app's playlist opens with these city runs.


---

## 8. Vehicle types and lane restrictions

`sim/vehicles.py` holds one catalogue. `VehicleTypeFactory` turns it into SUMO `<vType>` elements and
into the summary the dashboard draws from:

| Category | SUMO class | Size | Accel / decel | Emission model (HBEFA4) |
|---|---|---|---|---|
| Car | passenger | 4.5 × 1.8 m | 2.6 / 4.5 m/s² | petrol car Euro 4 |
| Eco car | passenger | 4.3 × 1.8 m | 2.0 / 4.0 | petrol car Euro 6d |
| Motorcycle | motorcycle | 2.2 × 0.9 m | 3.5 / 6.5 | motorcycle ≤ 250 cc Euro 5 |
| Bus | bus | 12.0 × 2.55 m | 1.2 / 4.0 | city bus 15–18 t Euro VI |
| Truck | truck | 10.0 × 2.5 m | 1.0 / 4.0 | rigid truck 14–20 t Euro VI |
| Ambulance | emergency | 6.5 × 2.1 m | 3.5 / 6.0 | petrol car Euro 4 |

- **Speed factor ≤ 1:** vehicles never exceed the limit (ambulances excepted).
- **Gap acceptance:** heavy vehicles accept a 2.5 s gap when crossing priority traffic, others 1.5 s.
- **Lane permissions are enforced by SUMO itself:**
  - trips are routed by SUMO for each vehicle's class;
  - a city test checks every vehicle in every frame is on a lane its class may use.

---

## 9. The 3D driver perspective

**File:** `client/src/components/DriverView3D.jsx`, built with **three.js** (version 0.170.0).

- **Loading:** the view is loaded only when *Driver perspective* is opened, so the rest of the
  dashboard stays small. The 3D code is a separate 125 KB gzipped file.
- **Data:** it is built from the same messages as the map, so both views always show the same
  simulation.

**Coordinates.** SUMO uses x east and y north (metres). three.js uses x right, y up, z towards the
viewer. So a SUMO point (x, y) becomes **(x, 0, −y)**. A SUMO heading of θ degrees (clockwise from
north) becomes a rotation of **−θ** about the vertical axis.

**The static scene,** built once per road map:

1. **Ground:** a large grass plane.
2. **Roads:**
   - every lane is a flat ribbon of triangles along its **SUMO lane shape** at its **real width**;
   - the ribbon is built by moving each point sideways along the local normal, by half the width;
   - ribbons are merged into one mesh per surface type: asphalt, local road, restricted lane.
3. **Markings:**
   - dashed lines between lanes (3 m dash, 6 m gap);
   - a solid line between the two directions;
   - both as thin ribbons slightly above the road.
4. **Junctions:** the polygon of SUMO's own junction shape.
5. **Buildings:** boxes on free ground (more than 26 m from any road), with heights and positions from
   a hash of their grid position, so they are always the same. They are drawn as one
   `InstancedMesh`, a single draw call.
6. **Signal heads:**
   - a pole, a box and three bulbs at each stop line, beside the road, facing the approaching drivers;
   - each bulb's colour is updated from the live signal state of its approach.

**Vehicles.**
- **Models:** one per vehicle, built from boxes at its category's **real size**:
  - car: body and cabin;
  - truck: cab and cargo box;
  - bus: body and window band;
  - motorcycle: narrow body and rider;
  - ambulance: white with a red stripe and a light bar.
- **Colours:** every eco vehicle has the one eco colour; the driven vehicle is purple.
- **Placement:** SUMO's position is the front bumper, so the model's centre is placed half a length
  behind it.

**Smooth movement.** Positions arrive once a second. Each frame (about 60 per second) every vehicle
is moved linearly from its previous to its new position. Its heading turns the short way round.

**Camera.**
- **Driver's seat:** at the ego vehicle, about 0.8 × its height above the road (a truck sees from
  higher than a car), looking 30 m ahead. The ego vehicle itself is hidden.
- **Behind the vehicle:** half a length plus 9 m behind and 3 m above it, looking 18 m ahead of it.
- **Smoothing:** the camera follows smoothly (each frame it moves 20 % of the way to its target).

**Speed display.** It shows speed, the posted limit (round sign), category, lane, the light ahead with
its distance and colour, and the efficient speed. The data comes from the followed vehicle's detail
row, or for your own vehicle from the driver messages.

**A problem found and fixed.** In headless testing the grass showed through the road.
- **The cause:** layers only 2 cm apart, and a coarse depth buffer (also found on some GPUs).
- **The fix:**
  - layers moved clearly apart: ground −0.25 m, junctions −0.05 m, road 0, markings +0.04 m;
  - the ground is pushed back in depth (polygon offset);
  - the near clipping plane raised to 0.5 m.

  The browser test now checks for road-coloured pixels in the lower half of the view.

---

## 10. The traffic control view (2D map)

**File:** `client/src/components/TrafficMap.jsx` (SVG).

**Research grids:** the space around each straight road line is stretched 3×, so roads are wide
enough to see while junctions and stop lines stay aligned.

**City network:** drawn at its **real geometry** instead, because irregular junctions and bends make
stretching impossible:
- every lane as a path along its SUMO shape, at its real width;
- dashed markings between lanes, a yellow centre line, a direction arrow in each lane;
- SUMO's junction shapes;
- lanes closed to heavy vehicles hatched, and local roads in a lighter grey.

**Vehicles:**
- drawn by category (car shapes, motorcycle, bus, truck, ambulance);
- every eco vehicle is green (`--eco`);
- a blue roof dot marks a vehicle with a radio;
- vehicles glide between frames.

**Overlays:**
- signal heads with countdowns;
- queue counts;
- congestion colouring;
- incidents;
- ambulance routes;
- the route explorer's alternatives (the selected route on top);
- the counter "VANET: x of y vehicles connected · penetration 50 %".

**Bandwidth.** The gateway sends each map frame as compact rows `[id, x, y, angle, flags, kind]`.
`kind` is `null` for an ordinary car, which keeps the frames small for 1,000 viewers.

---

## 11. Driving controls and the fuel-efficient speed

### 11.1 Controls: `sim/driver.py`, `DrivePanel.jsx`

**Taking a vehicle:** you choose car, eco car, motorcycle, bus or truck, a start road and a
destination. The driven vehicle is a copy of that category's SUMO type (`driven_<category>`), so it
keeps its size, lane permissions and fuel model.

| Control | Key | Effect |
|---|---|---|
| Accelerate | W / ↑ | target speed +5 km/h |
| Brake | X / ↓ | −5 km/h (stop below 5) |
| Slow down | E | to 60 % of the current speed |
| Stop | S / Space | brake to a stop |
| Normal | G | SUMO's own speed up to the limit |
| Desired-speed slider | | hold at most this speed |

**Safety.** Every input becomes a *maximum* speed, sent with `setSpeed`, capped at the lane's posted
limit. SUMO's default safety checks stay on:
- safe gaps;
- comfortable braking;
- stopping at red lights;
- only the lanes this vehicle may use.

A test asks for 180 km/h and checks the vehicle never goes above the limit.

### 11.2 Estimated fuel-efficient speed: `sim/eco_speed.py`

1. **Fuel curves.** `sim/analytics/fuel_curves.py` drives each category alone on a straight test road
   in SUMO, using SUMO's own HBEFA4 model. It records:
   - fuel and CO₂ per metre at steady speeds from 10 to 90 km/h;
   - fuel per second when idling;
   - fuel, time and distance to speed up from a stop to each speed.

   The results are stored in `sim/data/fuel_curves.json`.
2. **Hard limits** on the recommended speed:
   - the posted limit and the vehicle's top speed;
   - the vehicle ahead (within 60 m and slower): speed ahead + (gap − 15 m)/10;
   - congestion (traffic ahead below 60 % of the limit): traffic speed + 2 m/s.
3. **Cost of each candidate speed v,** in 0.5 m/s steps, over the distance d to the next light (or the
   next 300 m):

   ```
   cost(v) = fuel_per_metre(v) · d
           + [if the light would be red on arrival]
               idle fuel × waiting time + extra fuel to speed up again from a stop
           + 1.5 × idle fuel rate × travel time
   ```

   - **Whether the light is red on arrival** comes from its timing, received over the VANET (SPaT).
   - **The time term** stops the advice from slowing the vehicle right down just to save a little fuel.
4. **The cheapest speed is recommended:**
   - "keep this speed" if within 1 m/s of the current speed, otherwise "speed up" or "slow down";
   - with its reasons;
   - fuel, CO₂ and time for now against recommended;
   - a fuel-against-speed chart.

   It is labelled an **estimate**.

**Example.** A car at 50 km/h, 200 m from a red light that turns green in 20 s, is advised 35 km/h. It
arrives on green instead of stopping: 15.9 mL instead of 36.6 mL, in the same time.

---

## 12. Eco-driving (GLOSA) and the genetic algorithm

### GLOSA (Green Light Optimal Speed Advisory): `sim/vanet/glosa.py`

1. **Broadcast.** Each roadside unit sends the signal timing (SPaT) every second.
2. **Advice.** An eco vehicle within 300 m computes its arrival time and gets one of four pieces of
   advice:
   - **go:** it arrives on green;
   - **slow:** target speed = distance ÷ time until green, so it arrives as the light turns green;
   - **stop:** the needed speed would be below 5 m/s;
   - **none:** the timing is too uncertain (adaptive control or preemption).

**Result** (all cars eco, fixed-time lights): stops −40.5 %, waiting −30.6 %, CO₂ −2.9 %.

### Genetic algorithm: `sim/optimizer/ga_tuner.py`

1. **Candidates.** Each candidate is a complete set of settings:
   - signals: minimum and maximum green, queue threshold, decision interval, ambulance ETA trigger;
   - or ACO: α, β, ρ, ants, iterations, Q, elite.
2. **Fitness, lower is better:**
   - signals: waiting + 0.5 × ambulance travel time + 20 per missed preemption;
   - ACO: navigating cars' trip time plus penalties.

   It is measured in real SUMO runs on seeds 101–103.
3. **Evolution,** over 12 generations of 12 candidates:
   - tournament selection (best of 3);
   - BLX-α crossover;
   - Gaussian mutation;
   - the 2 best kept unchanged.
4. **Fair check** on the unseen seeds 1–10.

**Results:**
- ACO: fitness +34.6 % (p = 0.002), route search 3–4× faster;
- signals: about 4 %, not significant.

---

## 13. Design patterns

| Pattern | Where |
|---|---|
| **Strategy** | Signal controllers share one interface. Route planners (`AStarPlanner` for time or distance, `AcoPlanner`) share `plan()` |
| **Factory** | `create_controller()` (name → controller class); `make_planner()` (mode → planner); `VehicleTypeFactory` (catalogue → SUMO types and dashboard data) |
| **Adapter** | `StateProvider` (exact SUMO data or VANET estimates behind one interface); `SimProcess` (Python process → gateway events) |
| **State** | Preemption, signal changeover, run lifecycle, driver seat, work-zone turns |
| **Observer** | Gateway: simulation messages → live channel → Socket.io rooms (all viewers; followers of one vehicle) |
| **Singleton** (limited) | One live channel and configuration per gateway, created in the composition root; the fuel curves cached once. A global Singleton was avoided because the tests start several gateways |

Details, reasons and class relationships: [architecture/DESIGN_PATTERNS.md](architecture/DESIGN_PATTERNS.md).

---

## 14. How it was verified

| Check | Result |
|---|---|
| Python tests (including the 12-run zero-regression test) | 160 passed |
| City tests (`test_city.py`) | 12: network check; lane permissions; A\* equal to Dijkstra; heuristic never overestimates; A\* agrees with SUMO's router on every exit pair; explorer; planners; fuel advice; a real run (0 collisions, every vehicle on an allowed lane, 35–65 % equipped, driver capped at the limit) |
| Gateway tests | 89 passed (+ 4 with real SUMO) |
| Dashboard unit tests | 15 passed |
| Browser: city, routes, truck driving, 3D view, view switch | 9/9 |
| Browser: ambulance on demand | 6/6 |
| Layout at 6 widths (1920–360 px) | 108/108 |
| Accessibility (WCAG 2.2 AA, axe) | 0 violations: 45 general audits + 8 audits of the new screens, light and dark |
| Collision sweep, city network | 0 in 30 runs |

---

## 15. Limits

- **Roadside units** sit only at J0–J3. Roads more than 300 m from them report no speed, and their
  route cost uses the speed limit.
- **The fuel-efficient speed is an estimate,** not a proven optimum. The network is flat, so road
  gradient is not modelled.
- **Trucks and buses never turn left at a signal** in the city: 32 of the 56 exit pairs have a legal
  truck route.
- **Junction shapes in 3D are flat polygons;** there are no pedestrians, kerbs or road signs except the
  speed limit on the display.
- **Tested environment:** Windows with Edge. The Linux deployment, Firefox, Safari and screen readers
  were not tested.

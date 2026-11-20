# VANET-Based Intelligent Traffic Control: Short Summary

A SUMO traffic simulation in which traffic lights, ambulances, navigation and driver advice are
controlled by information that vehicles exchange over an emulated **VANET** (V2V car-to-car and
V2I car-to-roadside radio). A web platform shows every run live. The full detail is in
[PROJECT_OVERVIEW.md](PROJECT_OVERVIEW.md) and [IMPLEMENTATION_SUMMARY.md](IMPLEMENTATION_SUMMARY.md).
All the numbers are in [results_discussion.md](results_discussion.md).

## 1. Stack

| Part | Technology |
|---|---|
| Simulator | SUMO 1.28, controlled every second through TraCI |
| Control, VANET, algorithms | Python (`sim/`) |
| Gateway | Node.js: Express, Socket.io, SQLite, zod (`server/`) |
| Dashboard | React and Vite; 2D SVG map; 3D driver view in three.js (`client/`) |
| Tests | pytest, Node test runner, Vitest, Playwright (Edge), axe-core (WCAG 2.2 AA) |

## 2. The VANET: partial or full

- **Penetration** is the share of vehicles with a radio, from 0 to 1. Below 1 the network is
  **partial**; at 1.0 it is **full**. The live app uses 0.5 by default; the experiments use 0.2, 0.5,
  0.8 and 1.0. Ambulances always have a radio.
- **Radio model:** 300 m range, loss that grows with the square of the distance, delay with jitter,
  and optional V2V relay.
- **Vehicles (OBU):** send BEACONs (position, speed) and EV_ALERTs (ambulances).
- **Roadside units (RSU):** one per junction. Each estimates its queues from the beacons it hears,
  scaled up by 1/penetration.
- **Traffic Management Centre (TMC):** builds a traffic map from all RSUs (road speeds, incidents).
  Ambulances and navigating cars use this map.

## 3. Algorithms and where they are used

| Algorithm | What it does | Used in |
|---|---|---|
| **Dynamic (default mode)** | Every second scores each road from its queue, waiting time, density, arrivals and how full the road ahead is; green to the higher-scoring direction for 5–45 s sized to its queue; extends, ends early (empty road, blocked road ahead, a car waiting 40 s) or holds the green when nobody waits on red | `dynamic` (exact measures), `dynamic_vanet` (RSU beacons) |
| **Fixed-time** | Fixed green times | Baseline in every scenario |
| **Adaptive** | Gives green by measured queues, within minimum and maximum green and a limit on how long a direction may wait | `adaptive` (exact queues), `adaptive_vanet` (RSU estimates) |
| **Preemption** | Ambulance near (ETA threshold): safe yellow, all-red, then green for it | `adaptive_ev*`, emergency scenarios, ambulance on demand |
| **Zone control** | Alternating one-way traffic on a shared lane; next green only once the lane is empty | `accident_zone` |
| **A\*** | Fastest route on live road costs; never overestimates the time left, so routes are optimal | Navigation `astar`, ambulance routing, route explorer, city scenarios |
| **Ant colony (ACO)** | Pheromone-based search; returns several good routes and spreads cars over them (anti-herding) | Navigation `aco` and `aco_tuned` (navigation, incident, congested scenarios) |
| **Genetic algorithm** | Offline tuning: keeps the best parameter sets each generation, blends them and adds small random changes | Tunes the signal parameters (`*_tuned` controllers) and the ACO parameters (`aco_tuned`) |
| **GLOSA** | Signal timing over I2V, then advice to go, slow down for the next green, or stop | Eco-driving cars |
| **Incident detection** | An equipped car stopped for 30 s raises an incident flag at the TMC | Incident scenarios |
| **Fuel-efficient speed** | Tries speeds in 0.5 m/s steps; picks the lowest cost of fuel, red-light stops and time | Driver advice (`sim/eco_speed.py`) |
| **Car following** | Fixed time gap to the car ahead (details below) | My car, Drive panel, 3D view (`sim/car_following.py`) |

A\* uses these costs:
- each road costs `length / current speed + expected wait at the signal`, and closed roads cost infinity;
- the heuristic is `h = straight-line distance / highest speed limit`.

Car following works like adaptive cruise control (ACC):
- desired gap `s* = 2.5 m + T·v`; target speed `v_lead + 0.25·(gap − s*)`;
- the time gap T is 1.5 s with radar only, and 1.0 s when both cars have a radio (cooperative ACC);
- with a radio the car ahead also sends its acceleration, so the car behind starts braking before the gap shrinks.

Scenarios:
- `normal`, `congested`, `emergency`, `incident`, `navigation`, `ev_incident`, `ev_congested`,
  `accident_zone` and `city`;
- networks: the 2×2 grid, the grid with an accident zone, and a four-junction town with main,
  secondary, collector and local roads and lane rules per vehicle type.

## 4. What the driver sees and how it is measured

Each value is read from SUMO every second:
- **Speed and acceleration:** `getSpeed` and `getAcceleration`.
- **Distance:** `getDistance`.
- **Fuel and CO₂:** `getFuelConsumption` and `getCO2Emission`, summed over the trip. They come from
  the **HBEFA4** emission model, one emission class per vehicle type.
- **Stops:** counted from the speed.
- **Car ahead and car behind:** `getLeader` and `getFollower`, within 100 m.

The car's panel shows these values and the next signal with its timing. The 3D driver view shows the
same data. The driver can take a car and drive it: go, ease off, stop, or follow the car ahead (key F).

## 5. Platform features

- **Live map:** many people can watch one run at once. Roles are admin, operator and viewer. Run
  control sets the scenario, controller, navigation, penetration, loss, relay and seed. A playlist
  runs scenarios one after another.
- **Ambulance on demand:** adds an ambulance at any moment. Every signal then gets preemption.
- **Route explorer:** shows the fastest, shortest and alternative A\* routes with their costs.
- **Watching a car:** follow any car (My car), drive it, or watch from the 3D driver perspective.
- **Run history:** past runs are stored in SQLite with their results.

## 6. Key results (10 seeds, mean values, significance by Wilcoxon test)

| Topic | Result |
|---|---|
| Adaptive vs fixed, normal demand | Waiting time down 38 % (exact queues); with VANET 39 % at full penetration, 19 % at 50 %, 28 % worse at 20 % |
| Adaptive VANET, congested demand | 31–46 % better than fixed at every penetration |
| Ambulance preemption | Travel time down about 25 % (87.6 s to 65.2 s), even with 50 % packet loss; 0 junctions missed |
| Ambulance route choice | Closure ahead: 44 % faster with preemption; preemption only gives 113.7 s, both together 63.3 s |
| Navigation | Navigating cars 0–12 % faster than the others; network-wide gains only with an incident (A\* −5.9 %) |
| A\* vs ACO | Routes equally good on this small network; A\* searches under 1 s per run, tuned ACO about 15× slower |
| GLOSA (fixed signals) | Stops down 40 %, CO₂ down 2.9 %, waiting time down from 19.4 s to 13.5 s |
| Incident detection | Closures detected in 80–100 % of runs, after about 33 s |
| GA tuning | ACO: 35–44 % better fitness on held-out seeds; signals: waiting time 9.2 s (24 % better than adaptive) |
| City network (5 seeds, 50 % penetration) | Waiting: fixed 27.3 s, adaptive 19.8 s, adaptive_vanet + A\* 21.5 s; 0 collisions |

## 7. Limits

- **Emulated channel:** no MAC contention, interference or obstacles (no OMNeT++/Veins).
- **Small networks:** few alternative routes, so navigation gains are small.
- **Ambulances:** they obey red lights, and other cars form no rescue lane.
- **Fuel and CO₂:** model estimates, not measurements.

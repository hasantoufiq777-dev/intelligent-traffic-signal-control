# Supervisor enhancements: plan and risks

Plan written before implementation (15 November 2026). Decisions taken with the project owner:

- The richer network, vehicle types and lane rules go into a **new network variant**. `grid2x2` and
  every stored research result stay unchanged.
- The driver perspective is drawn in **real 3D with three.js**, loaded only when it is opened.

## 1. What exists and what changes

| Area | Now | Change |
|---|---|---|
| Network | `grid2x2`: 4 signalised junctions, dead-end entry roads, one car type, equal lanes | New `grid2x2_city`. The same four junctions J0–J3 with their links unchanged (the controllers assume 4 legs). Around them: an outer arterial ring (60 km/h, wide lanes), corner gates, and residential short-cuts (30 km/h, narrow, no trucks or buses). This gives at least two routes between any gates, of different length, speed and congestion |
| Vehicles | `car` and `emergency` | City vehicle types from one catalogue (a factory): motorcycle, car, eco car, bus, truck, ambulance. Each has its own size, speeds, acceleration, SUMO class and HBEFA4 emission class. City trips are routed by SUMO per vehicle class, so lane permissions are respected |
| Lanes | 2 equal lanes | Lane widths by road type; permissions per class; the residential streets are closed to trucks and buses. The map draws widths and marks restricted lanes |
| Penetration | Default 100 % | Default **50 %** for live runs (command line, gateway, Run control). Ambulances and eco cars are always equipped. Research runs pass their penetration explicitly and are unaffected |
| Eco cars | Random paint, green roof dot | One fixed colour everywhere: map, driver view, details, legend |
| A\* | Inside navigation only | Also a **route explorer**: choose origin and destination; the simulation's own A\* returns the fastest route now, the shortest distance and further alternatives, with distance, free-flow time, current estimated time and signal count. All drawn on the map |
| Driver | Go / Ease off / Stop, 2D map | Traffic-control view (the existing map) and a **3D driver perspective** from the same SUMO frames. Controls: accelerate, brake, slow down, stop, desired speed. Always capped at the road limit, with SUMO's safety checks kept |
| Speed advice | GLOSA for eco cars | For the driven car: maintain / increase / reduce, with reasons (limit, light ahead, vehicle ahead, congestion), and an **estimated fuel-efficient speed** from fuel curves measured with SUMO's HBEFA4 model for that vehicle type |
| Design patterns | Present but undocumented: adapter (StateProvider), state machines, observer (gateway events) | Formalise where it helps: Strategy (route planners), Factory (controllers, vehicle types). Document every pattern with its classes and reasons |

## 2. Order of work (each step tested before the next)

1. City network generator; verify it builds, every gate is reachable from every gate for each class, and there are no dead ends.
2. Vehicle catalogue and city routes; a SUMO run without errors, teleports or collisions.
3. Penetration default 50 %; equipped count and share on the dashboard.
4. A\* route explorer (simulation command, gateway, map); a test showing that the shorter route is not always the faster one.
5. Fuel curves and the speed recommendation; driver controls.
6. 3D driver view and view switching.
7. Eco colour, vehicle shapes, lane drawing, legend.
8. Design patterns refactor and documentation.
9. Full test run: Python (including zero-regression), gateway, dashboard, browser, accessibility, responsive.

## 3. Risks and how they are handled

| Risk | Handling |
|---|---|
| Research results change | Nothing in `grid2x2`, `net.xml` or their scenarios changes; the 12-run zero-regression test must still pass |
| Lane permissions make a route impossible (no teleporting in this project, so a stuck vehicle blocks a road) | Generator checks reachability per vehicle class; trips are routed by SUMO with the vehicle class; a city run is checked for teleports, collisions and stuck vehicles |
| Priority junctions on the ring gridlock under heavy demand | Moderate default demand; congestion test run |
| Wider lanes change junction geometry at J0–J3 | Signal link order checked to be identical to `grid2x2` |
| 3D view slows phones | three.js loaded only in the driver view; frames come once per second and are interpolated; pixel ratio capped |
| Fuel-efficient speed presented as exact | Called an *estimated efficient speed*; it comes from SUMO's own model, sampled per vehicle type |
| Two A\* implementations drifting apart | The explorer calls the same `astar_search` as navigation |

## 4. Outcome (15 November 2026)

All nine steps were implemented and tested; details in [HOW_IT_WORKS.md](../HOW_IT_WORKS.md#supervisor-enhancements-november-2026).

| Check | Result |
|---|---|
| City network build check | every class reaches every corner, no dead ends, J0–J3 links as in grid2x2 |
| Collision sweep (3 controllers × 2 demands × 5 seeds, 900 s each) | 0 collisions in 30 runs (13–21 before the lane rules and geometry fixes) |
| Python suite | 160 passed, zero-regression included |
| Gateway suite | 89 passed; real SUMO 4 passed |
| Dashboard unit tests | 15 passed |
| Browser: `city.mjs` | 9/9 (city map, 50 % chip, eco colour, route explorer, truck routes, truck driving with advice, 3D view, view switch) |
| Browser: smoke, ambulance, responsive | passed; 108/108 page × width combinations |
| Accessibility | 0 violations in the 45 general audits and in 8 audits of the new states (light and dark) |

**Defects found while building, all fixed:**
- junction collisions of buses and trucks: geometry, lane rules, gap acceptance;
- A\* routing trucks onto streets closed to them: class-aware road graph;
- the 3D road hidden by the ground with a coarse depth buffer: layer heights and depth offset.

**Limits:**
- Roadside units sit at J0–J3 only. Roads more than 300 m from them report no speed, and route costs
  there use the speed limit.
- The fuel-efficient speed is an estimate (cost per candidate speed, not a global optimum). The
  network is flat, so road gradient is not modelled.
- Heavy vehicles may not turn left at the signals: they follow truck routes, which cover 32 of the 56 exit
  pairs of the redesigned town (section 5).
- The 3D view draws junctions as discs, not their exact SUMO shapes.

## 5. Network redesign (non-uniform four-junction town)

Decided with the project owner: redesign `grid2x2_city`, keep `grid2x2` and every research result.

| Requirement | How it is met |
|---|---|
| Main road and sub-roads | 3-lane main road W–J0–J1–E; 2-lane secondary and collector roads; 1-lane local sub-roads with bends, joining at stop-controlled T-junctions |
| Non-uniform geometry | Irregular junction spacing; bends on the main road, J2–J3, the collectors and every local road |
| Real lane differences | Lane counts 3 / 2 / 2 / 1, widths 3.6–3.0 m, limits 60 / 50 / 40 / 30 km/h, all in SUMO; the map draws lanes from SUMO's lane shapes at their real widths |
| Vehicle restrictions | Local roads: cars, motorcycles, ambulances; inner lanes: no trucks or buses; lane use at signals fixed (right from the right lane, left from the inner lane) |
| A\* | Class-aware graph; A\* finds a route exactly where SUMO's router does (tested on every exit pair) |
| Valid routes | Network check plus SUMO routing every trip for its class; heavy trips only on legal pairs |
| Metrics recalculated | `sim/analytics/city_eval.py` → `results/city/` |

**Found while building, fixed:**
1. **Right-lane left turns.** SUMO gave trucks a left turn from the right lane (across straight traffic).
   Fixed by explicit lane use at the signals.
2. **Heavy-vehicle left turns.**
   - **The finding:** buses and trucks turning left across oncoming traffic in these 30–40 m junctions
     collided (plain SUMO, 1.6× demand: 0 with cars only, 17 with the full mix).
   - **What didn't help:** vehicle parameters and a 0.5 s step (9–22).
   - **The fix:** truck routes, so heavy vehicles never turn left at a signal. 0 collisions in plain
     SUMO, and 0 in 30 controller runs (3 controllers × 2 demands × 5 seeds).

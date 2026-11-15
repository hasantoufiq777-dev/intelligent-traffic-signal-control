# Design patterns

Where each pattern is used, why, what problem it solves, and how the classes relate. Patterns were
applied only where they remove real branching or coupling. Some were already in the code and are
documented here; three were introduced in November 2026 (marked **new**).

| Pattern | Where | Status |
|---|---|---|
| [Strategy](#strategy) | Signal controllers; route planners (A\*, ACO, shortest distance) | controllers existing, planners **new** |
| [Factory](#factory) | Controller factory, route-planner factory, vehicle-type factory | **new** |
| [Adapter](#adapter) | `StateProvider` (exact SUMO data or VANET estimates); `SimProcess` (Python process ↔ gateway events) | existing |
| [State](#state) | Preemption state machine; simulation lifecycle; driver seat and drive; zone turns | existing |
| [Observer](#observer) | Gateway: simulation output → live channel → Socket.io rooms (viewers, followers) | existing |
| [Singleton](#singleton) | Fuel curves cache; one live channel and configuration per gateway | existing (composition root) |

---

## Strategy

**Signal controllers.**
- **Where:** `sim/controllers/base.py` (`BaseController`), `fixed.py`, `adaptive.py`, `preemption.py`, `zone.py`.
- **Why:** each junction is controlled by one of several interchangeable algorithms. They share one
  interface: `step(t)`, current phase, SPaT, queues.
- **Problem solved:** the runner steps every junction the same way, whatever the method, and adding a
  controller does not touch the simulation loop.
- **Relationships:** `FixedTimeController`, `AdaptiveController` and `PreemptionController` derive from
  `BaseController`, which holds the safe changeover (yellow, all-red, conflict check).
  `PreemptionController` also *wraps* another controller, a Decorator-like composition: it runs the
  inner controller until an ambulance comes. Thanks to this, an ambulance sent from the dashboard can
  give priority to a running fixed-time or adaptive controller (`sim/dispatch.py`).

**Route planners (new).**
- **Where:** `sim/navigation/planners.py`.
- **Why:** the navigator, the route explorer and the ambulance router all need "a route from road A
  to road B for this vehicle class on the current traffic map", by different methods.
- **Problem solved:** the navigator used to branch on `mode == "astar"` / `"aco"`. Now it calls
  `self.planner.plan(...)` and receives a `PlanResult` (path, cost, alternatives, chosen,
  expansions). The explorer uses the same planners, so there is one A\*, not two.
- **Relationships:**

  ```
  RoutePlanner (protocol: plan(start, goal, speed_map, closed, vclass) -> PlanResult)
   ├─ AStarPlanner(graph, cost="time")      fastest: h = straight line / v_max
   ├─ AStarPlanner(graph, cost="distance")  shortest: h = straight line
   └─ AcoPlanner(AcoRouter, rng)            k routes with probabilities, one drawn
  VehicleNavigator ──uses──> RoutePlanner   (chosen once by make_planner(mode))
  ```

## Factory

| Factory | File | Creates | Why |
|---|---|---|---|
| `create_controller(name, ...)` | `sim/controllers/factory.py` (**new**; was an if/elif chain in the runner) | the controller class for a name, from the registry `CONTROLLERS` | One table instead of name checks. The `*_vanet` names map to the same class as their exact-data twin: the difference is the injected provider (Adapter), not the logic |
| `make_planner(mode, ...)` | `sim/navigation/planners.py` (**new**) | the planner for a navigation mode; `None` for "none" | Unknown modes fail clearly; a new method is one class plus one registry entry |
| `VehicleTypeFactory` | `sim/vehicles.py` (**new**) | SUMO `<vType>` elements for route files, and the catalogue summary sent to the dashboard | One source for size, class, speeds and emission model of each category. SUMO, the map, the 3D view and the fuel advice therefore always agree on what a vehicle is |

## Adapter

**`StateProvider`** (`sim/sensing.py`, `sim/vanet/sensing_vanet.py`):
- **What it adapts:** two very different sources behind one interface (`get_queues`, `get_ev_info`,
  `get_edge_speeds`):
  - `OracleStateProvider` reads SUMO through TraCI;
  - `VanetStateProvider` reads what the roadside units estimated from radio messages.
- **Problem solved:** the same controller code runs on exact data or on VANET data, which is the
  core comparison of the research.

**`SimProcess`** (`server/src/simProcess.js`):
- **What it adapts:** the Python child process (stdout JSON lines, stdin commands) into Node events
  (`message`, `bad_line`, `exit`) and a `send(cmd)` method.
- **Problem solved:** the gateway never deals with pipes or line splitting.

## State

| State machine | File | States |
|---|---|---|
| Ambulance preemption (per junction) | `sim/controllers/preemption.py` | NORMAL → TRANSITION → EV_GREEN → EXIT → RECOVERY → NORMAL |
| Signal changeover | `sim/controllers/base.py` | STABLE → YELLOW → ALL_RED → STABLE |
| Simulation lifecycle | `server/src/liveChannel.js` | STOPPED → STARTING → RUNNING → STOPPING; FAILED, RECOVERING |
| Driver seat and drive | `server/src/driver.js`, `sim/driver.py` | free → claimed → waiting → driving → ended (arrived, released, idle, left, admin, run ended) |
| Work-zone turns | `sim/controllers/zone.py` | green(d) → yellow → clearing → green(other) |

**Why it fits:** each object behaves differently in each state, and only some transitions are allowed.
For example, an ambulance green can only start after yellow and all-red, and a run cannot be paused
unless it is RUNNING. Named states make those rules explicit and testable.

## Observer

**Where:** the gateway.
- `SimProcess` emits `message` events, and `LiveChannel` subscribes.
- `LiveChannel` publishes to Socket.io rooms: `live` for every viewer, `veh:<id>` for the followers of
  one vehicle.
- The dashboard subscribes with `socket.on(...)`.

**Problem solved:** the simulation does not know who watches. One run is broadcast to up to 1,000
viewers, and a vehicle's details go only to the viewers who follow it.

On the Python side, components (controllers, VANET, navigation, dispatcher, driver) queue their
events, and the runner collects them each second with `pop_events()`, a pull-based variant. This was
kept because the order of events within a simulated second must be deterministic.

## Singleton

The project avoids global singletons. Instead:
- **One instance per process, by construction.** The gateway creates exactly one configuration
  object and one `LiveChannel` in `createGateway()` (the composition root) and passes them down, so
  tests can create several gateways side by side.
- **A lazily initialised module cache** for read-only data: the fuel curves in `sim/eco_speed.py`
  (`curves()`), loaded once per process on first use.

A true Singleton class was not introduced. It would make the gateway tests (which start many gateways
in one process) impossible and would not solve a real problem here.

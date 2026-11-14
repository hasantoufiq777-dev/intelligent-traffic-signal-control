# Test Report — Phases 4–5 (design system, layout, traffic map)

Date: 2026-11-12. Same machine as `PHASE_2_3_REPORT.md` (Windows 11, i7-12700H, Node 24.14.1,
Python 3.12, SUMO 1.28, Microsoft Edge driven by Playwright). All figures were measured in these runs.

## 1. What changed

**Layout and design system (Phase 4)**
- Application frame: collapsible sidebar with grouped navigation (Monitor, Research, Control, System),
  top bar with the page title, connection state, theme switch and user menu. Below 900 px the sidebar
  becomes a drawer opened from the top bar; it closes on navigation, on Escape and on the backdrop.
- Dark navy theme by default (light and system themes remain), one set of colour tokens, inline SVG
  icons, "Skip to content" link, visible focus rings, reduced-motion support.
- Form controls never grow past their column.

**Traffic map (Phase 5)** — `client/src/components/TrafficMap.jsx`
- Zoom (buttons, Ctrl + scroll, pinch, `+`/`-` keys), pan (drag, arrow keys), fit to network.
- Layer switches, remembered per browser: vehicles, queue counts, signal countdowns, congestion,
  accidents and closures, ambulance routes, roadside units, radio range, road names.
- Signals drawn above the cars with the seconds left when the controller can predict them; number of
  waiting vehicles per approach; junctions are selectable and show "AMBULANCE" during preemption.
- Congestion per road direction from the observed speed **and** the queue (a car waiting at a red
  light is not congestion); blocked roads with a warning marker and "not yet detected" until the
  roadside units report them; the remaining route of every ambulance; roadside units and their range.
- A banner on the map when the simulation is not running or the connection is lost, so an old
  picture is never mistaken for live traffic.
- Side panel with tabs: Overview (accident zone, incidents and closures, ambulances, events),
  Junction (phase, signal, countdown, true and VANET-estimated queue per approach, controller,
  ambulance ETA), Vehicle (road, destination, speed, next light, advice, fuel, CO2, driving), Eco-driving.
- Status tiles: vehicles, average speed, average waiting, congestion, active incidents, ambulances,
  VANET delivery, throughput. A dash is shown when the simulation did not send a value.

**Simulation live output (display only)** — `sim/runner.py`, `sim/live_vehicles.py`
- New optional keys: `state.edge_speeds`, `state.incidents {known, actual, detection}`,
  `state.ev_routes`, `state.metrics.avg_speed_kmh`; `network.vanet {active, range_m, penetration}`,
  `network.edges[].speed/length`; vehicle detail fields `road`, `to`.
- No control code was touched. The mock simulation does not send these keys; the pages then show a dash.

## 2. Results

| Check | Command | Result |
|---|---|---|
| Python simulation | `python -m pytest -q` | **129 passed** |
| Gateway | `npm --prefix server test` | **46 passed**, 2 skipped (real-SUMO tests) |
| Dashboard unit tests | `npm --prefix client test` | **13 passed** (4 new: congestion rules) |
| Dashboard build | `npm --prefix client run build` | succeeded |
| Browser journey | `node tests/e2e/smoke.mjs …` | **10 of 10 steps passed** |
| Responsive | `node tests/e2e/responsive.mjs …` | **36 page/width combinations**, no horizontal overflow, no console errors |
| Regression of a stored result | section 3 | identical |

**Responsive check.** Pages `/`, `/live`, `/results`, `/admin`, `/administration`, `/account` at
1920, 1440, 1024, 768, 390 and 360 px, plus `/login` and `/forgot-password` at 1440 and 360 px. For
each: no horizontal page overflow; above 900 px the sidebar is visible, at or below it the sidebar is
a closed drawer with a menu button; the drawer opens, navigates and closes. The first run found one
defect, fixed and re-run: on `/admin` at 390 px the controller dropdown was 6 px wider than the page.

**Visual check with real SUMO** (screenshots reviewed):
- `accident_zone` at t = 302 s: blocked westbound lanes, shared lane with "westbound turn" and its
  direction arrows, zone signals, the ambulance's route, signal countdowns and queue counts.
- `ev_incident` at t = 266 s: road J1J3 marked closed, the ambulance rerouted via J0J2 and J2J3 with
  its route drawn, junction J2 showing ambulance priority.
- Defects found and fixed in this pass: mobile-only buttons visible on desktop; congestion colouring
  marked ordinary red-light stops as heavy (rule now needs a queue); duplicate incident labels over
  the accident zone; a label overlapping a queue count.

## 3. Regression of research results

`emergency`, `adaptive_ev_vanet`, seed 1, loss 0.1, relay on, run in **live mode** (so the new output
code runs) and compared with `results/raw.csv`:

| Metric | Stored | Now |
|---|---|---|
| Ambulance travel time | 59.0 s | 59.0 s |
| Average travel time | 93.23 s | 93.23 s |
| Average waiting time | 12.85 s | 12.85 s |
| Maximum queue | 6 | 6 |

## 4. Python suite

`python -m pytest -q`: **129 passed** in 14 min 43 s (the live simulation was running at the same time, which slows it). The
live-protocol, driver and incident tests (28) were also run on their own after the change: all passed.

## 5. Not covered yet

- Automated accessibility audit (axe) and screen-reader testing: planned for Phase 8. Done by hand
  here: keyboard reachability of navigation, map (arrows, +/-, Enter on junctions), tabs and forms.
- Selecting a car from the keyboard: cars are selectable by pointer and through the "Follow"
  buttons and pickers in the Vehicle tab, not by tabbing through every car on the map.
- The mock simulation has no road speeds, incidents or ambulance routes.

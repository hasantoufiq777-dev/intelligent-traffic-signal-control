# Test Report — Phases 6–7 (module pages, research dashboards, guided demo)

Date: 2026-11-12. Same machine and tools as the earlier reports. All figures were measured in these runs.

## 1. What was added

| Page | Path | Content (all from the live stream or stored results) |
|---|---|---|
| Signals | `/signals` | Every junction: phase, signal, countdown and queue per approach, controller; phase history strip of the last two simulated minutes; waiting vehicles per junction; preemption events |
| VANET | `/vanet` | Range, share of connected cars, delivery ratio, loss, delay, relay hops, alert lead time, missed preemptions; roadside-unit table (true queue, estimate, error); alert message counts and activity |
| Emergency | `/emergency` | Active ambulances with ETA, distance and speed; remaining route; per-junction priority; preemption and reroute events |
| Navigation | `/navigation` | Mode, navigating cars, trip times, road-load spread; how a route is chosen (cost, ACO rule, switching rule); route-change events |
| Incidents | `/incidents` | Active incidents with their detection state, planned clearance and time left, timeline |
| Work zone | `/work-zone` | Zone state per side, safety count, expected waits, the rules, the evaluated results table (from `summary.json`), timeline |
| Eco-driving | `/glosa` | Eco summary, SPaT table per junction and approach, advice for the followed car, fuel and CO2 |
| Events | `/events` | Simulation state and all events with category and "important only" filters; a bell in the top bar counts new important events |
| GA optimisation | `/ga` | Per target: fitness before and after, convergence chart, default and tuned parameters, check on held-out seeds with p-values (new endpoint `GET /api/experiments/ga`) |
| Run history | `/runs` | Stored runs (paged); result of one run or a side-by-side comparison of two |
| Results | `/results` | Existing tables and charts, now with CSV download of the shown suite and JSON download of everything |
| Documentation | `/docs` | Problem, objectives, architecture diagram, method, each algorithm with its formula, evaluation, results, limitations, future work, technologies |
| Guided demo | `/demo` | Thirteen steps in the order asked for; a scenario step starts its run (administrators and researchers), says what to watch and when, and shows the live map |

Rules kept: no page invents data. When the simulation does not send a value the page shows a dash or
says why (for example "no VANET messages with this controller", "navigation is off in this run"). The
navigation page states that individual ACO alternatives and their probabilities are not streamed.

## 2. Results

| Check | Command | Result |
|---|---|---|
| Gateway | `npm --prefix server test` | **47 passed**, 2 skipped (1 new: GA endpoint, including an unreadable file and the 401 without a session) |
| Dashboard unit tests | `npm --prefix client test` | **13 passed** |
| Dashboard build | `npm --prefix client run build` | succeeded |
| Page check | `node tests/e2e/pages.mjs …` | **22 of 22 steps passed** |
| Browser journey | `node tests/e2e/smoke.mjs …` | **10 of 10 steps passed** |
| Responsive | `node tests/e2e/responsive.mjs …` | **108 page/width combinations** (18 pages × 6 widths), no horizontal overflow, no console errors |
| Python simulation | not re-run | No file under `sim/` changed in these phases (last full run: 129 passed, Phase 4–5 report) |

**Page check (real SUMO, Edge).** Each of the 16 sidebar links opens its page with the right title and
its real content. Further steps: the work-zone table shows the evaluated rows (for example 95.3 s for
alternating control with ambulance route choice); the GA page shows the stored figures for both targets
(684 and 339 simulations); the results table downloads as a CSV with the expected header and rows; two
stored runs can be compared; the demo starts step 3 from its button, reports "this scenario is playing"
and the map shows moving cars of that run; the bell opens the event centre.

## 3. Defects found and fixed

- The Administration page showed the title "Run control" (its path starts with `/admin`). The match is now exact.
- With a controller that does not use the VANET, the "VANET estimate" column repeated the true queue. It now shows a dash.

## 4. Not covered yet (Phase 8)

- The same journeys as a researcher and as a viewer in the browser (the role rules themselves are covered by the gateway tests).
- Automated accessibility audit; security scan; load steps from 10 to 750 users and the soak test.
- The mock simulation sends no road speeds, incidents or ambulance routes, so those parts stay empty with it.

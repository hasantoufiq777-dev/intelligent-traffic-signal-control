# Teacher demonstration guide

How to present the project to a teacher or examiner with the built-in **Guided demo** (`/demo`):
thirteen steps from the problem to the results. A full walk-through takes 20–25 minutes; a 5-minute
version is in §4.

## 1. Before the session

1. Start the platform: `npm start` from the project folder (it builds the dashboard and starts the
   gateway with real SUMO), or use the deployed site.
2. For a demonstration, set `LIVE_LOOP=false` in `server/.env`, so that only the runs you start
   play. With the loop on, the playlist continues after each run.
3. Sign in as a **researcher** or **administrator**. Viewers can follow the demo, but only these
   two roles can start its scenarios.
4. Check that the research pages have data:
   - *Results* needs `results/summary.json` (`npm run sim:experiments`, which takes long; the
     repository already contains it);
   - *GA optimisation* needs `results/ga/`.
5. Optional, on a desktop: *Open in SUMO-GUI* in the top bar shows SUMO's own window beside the
   dashboard.
6. Optional: a second screen or browser window signed in as a viewer, to show that everyone watches
   the same simulation.

## 2. The thirteen steps

Each step in `/demo` explains what is happening, lists what to point at, and, for steps with a
scenario, has a *Start this scenario* button. While a step's scenario plays, **Pause to explain**
freezes it, for every viewer, at the current simulated second; *Resume* continues from there. In
*Run control* you can also play a run at 0.5× to 4× speed or restart it from the beginning. The live map on the demo page shows the run that is
really playing. The simulated times below assume the default pacing (4 simulated seconds per
wall-clock second).

| # | Step | Starts | Point at | Say |
|---|---|---|---|---|
| 1 | Project overview | — (opens Documentation) | the problem, architecture diagram, formulas | Signals, ambulance priority, navigation and speed advice all use information that cars exchange over an emulated VANET; SUMO simulates the traffic |
| 2 | The road network | — (opens the map) | *Layers → VANET radio range*; click a junction | 2×2 grid, junctions 250 m apart, two lanes each way, a roadside unit with 300 m range at each junction |
| 3 | Normal traffic, fixed-time | `normal`, `fixed` | countdowns from 30 s; cars waiting at red with the cross road empty | The baseline wastes green time |
| 4 | Congested traffic | `congested`, `fixed` | queues growing; roads turning amber/red; the Congestion tile | The same plan cannot cope with heavy traffic |
| 5 | Adaptive signal control | `congested`, `adaptive_vanet` | greens of different lengths (Signals page) | Every 5 s the queues of both directions are compared; green 10–50 s; nobody waits more than 60 s. Evaluated: waiting −40.8 % against fixed in congestion (100 % equipped) |
| 6 | VANET monitoring | `normal`, `adaptive_vanet`, 50 % equipped, 10 % loss | delivery ratio, loss, delay; estimated vs true queue (VANET page) | The controller sees only what the network delivers, and the estimate is scaled up for the unequipped cars |
| 7 | Ambulance preemption | `emergency`, `adaptive_ev_vanet`, 10 % loss, relay on | pink route; *AMBULANCE* at each junction ahead; *Preemption started* events (Emergency page) | Alerts reach the junctions over V2I/V2V; only the ambulance's approach turns green, via yellow and all-red. Evaluated: 87.6 s → 65.6 s with 10 % loss, 0 missed preemptions |
| 8 | Accident detection | `incident`, A\* navigation | road J0→J1 marked *not yet detected* at 150 s, detected at about 183 s (Incidents page) | Nobody reports the crash; a roadside unit notices cars stopped for 30 s, and navigating cars then avoid the road |
| 9 | Road closure and the ambulance | `ev_incident`, route choice on | J1→J3 closes at 150 s; at about 242 s the pink route changes | The ambulance plans around the closure using the traffic centre's map before it reaches it |
| 10 | Contraflow at an accident | `accident_zone` | from about 183 s the shared lane with direction arrows, both ends red while it clears; ambulance's turn at about 300 s; normal again at about 530 s (Work zone page) | One open lane shared one direction at a time, with turn lengths from the queues. Evaluated: 0 opposing-direction conflicts, 0 collisions |
| 11 | Navigation | `navigation`, ACO | trip time of navigating cars vs the others (Navigation page) | A\* sends everyone to the best route; ACO spreads cars over good alternatives |
| 12 | GA and ACO tuning | — (opens GA optimisation) | convergence curve; held-out check | The GA tuned parameters offline; ACO improved 35–44 % on seeds it had never seen; the signal parameters by 1.5–3.8 %, not significant |
| 13 | Research results | — (opens Results) | means, spread, change against baseline, p-values; CSV download | All comparisons over 10 seeds with paired statistical tests |

## 3. Questions to expect

| Question | Where the answer is |
|---|---|
| What problem does it solve? Why a VANET? | *Documentation → Problem and objectives*, *VANET*; [`docs/PROJECT_OVERVIEW.md`](../PROJECT_OVERVIEW.md) §1 |
| How exactly does the adaptive controller decide? | *Documentation → Signal control* (formula); Signals page during step 5 |
| How is the ambulance given green safely? | *Documentation → Ambulance priority*; the event log in step 7 |
| How are the results calculated? Are they significant? | *Documentation → Evaluation*; *Results* (10 seeds, paired Wilcoxon tests); [`docs/method.md`](../method.md) |
| Is the map real data or an animation? | Real: each car is drawn at the position SUMO reports, once per second; *Follow* a car and compare with SUMO-GUI |
| What are the limitations? | *Documentation → Limitations*: emulated radio channel (no MAC layer), small grid, fuel and CO₂ from a model, results are simulation results, not field measurements |
| How many people can use it? | Up to 1,000 at once, load-tested ([load test report](../testing/LOAD_TEST_REPORT.md)) |
| Is it secure? | Roles enforced on the server, sessions, CSRF and origin checks ([security report](../testing/SECURITY_TEST_REPORT.md)) |

The numbers quoted in the table come from the stored experiments (`results/summary.json`). They
describe these simulated scenarios, not guaranteed real-world performance.

## 4. Five-minute version

1. **Step 2:** the map and a junction (30 s).
2. **Steps 3 → 5:** fixed signals, then adaptive in congestion; point at the queues (1.5 min).
3. **Step 7:** the ambulance; wait for *AMBULANCE* at the junctions (1.5 min).
4. **Step 10:** contraflow; jump to it about 45 s after starting so the shared lane is active (1 min).
5. **Step 13:** one Results table with its p-value (30 s).

## 5. If something goes wrong

| Symptom | Quick fix |
|---|---|
| A step's *Start* button is missing | You are signed in as a viewer; sign in as a researcher or administrator |
| The map says *Simulation stopped* or *Failed* | Start the step again; if it fails again, see [troubleshooting](TROUBLESHOOTING.md) |
| No ambulance yet | It enters at 120 simulated seconds, about 30 s after the start |
| SUMO is not installed on the presentation computer | Set `USE_MOCK=true` in `server/.env` and restart: a mock simulation streams the same kinds of data (no road speeds, incidents or ambulance routes) |
| The Results page is empty | `results/summary.json` is missing: run `npm run sim:experiments` beforehand |

# PROJECT CONTEXT (v2)

Project: VANET-based Intelligent Traffic Signal Control System (web-based).
Main tier: an emulated VANET (vehicles with OBUs, roadside units (RSUs) at junctions, V2V and V2I messages with range, loss, and delay). Traffic signals, emergency-vehicle preemption, and navigation act on information delivered by the VANET.
Optimization: A* heuristic search and ant colony optimization (ACO) for routing, a genetic algorithm (GA) for offline parameter tuning.
Simulation: SUMO + Python (TraCI/libsumo), 2x2 signalized grid (single junction used for early tests). Web layer: Node.js (Express + Socket.io) gateway + React dashboard. No OMNeT++: the network is emulated in Python.

## Tech rules
- Python 3.10+, SUMO 1.18+, Node.js 18+, React 18 with Vite, Recharts, socket.io, better-sqlite3.
- Use traci for GUI/debug and libsumo for headless experiments, behind one wrapper.
- Never hardcode paths. Read SUMO_HOME and other settings from env or config files. Parameters live in YAML (sim/config.yaml, scenarios/*.yaml), not constants.
- Fixed random seeds. Every random component (channel loss, jitter, equipped-vehicle selection, ACO, GA) uses a seeded RNG derived from the run seed.
- Allowed Python deps: pyyaml, numpy, pandas, scipy, matplotlib, pytest, networkx (tests only). A* and ACO are implemented by hand.
- Safety rule: signals must never jump to a conflicting green. Always yellow, then all-red, then green.
- Python writes logs to stderr ONLY. stdout is reserved for the JSON-lines protocol.
- Controllers read state ONLY through a StateProvider (oracle or VANET), never directly from TraCI.
- Do not modify files outside the scope of the current module unless needed; if you do, say so.

## Repo structure (keep exactly)
project/
  PROJECT_CONTEXT.md
  README.md
  sim/
    sumo/             net files, routes, sumocfg
    sensing.py        StateProvider, OracleStateProvider
    vanet/            channel.py, messages.py, nodes.py, network.py, sensing_vanet.py, alerts.py
    navigation/       graph.py, astar.py, aco.py, navigator.py
    controllers/      base.py, fixed.py, adaptive.py, preemption.py
    optimizer/        ga_tuner.py
    analytics/        metrics.py, plots.py, evaluate.py, vanet_experiments.py
    scenarios/        *.yaml, generate_routes.py, route_alternatives.py, incident.py
    runner.py  main.py  mock_sim.py  config.yaml  requirements.txt
  server/             Node.js
  client/             React (Vite)
  results/            summary.json, summary.md, plots
  docs/

## Names used everywhere
- Controllers: fixed, adaptive (oracle sensing), adaptive_vanet (VANET-sensed), adaptive_ev (oracle sensing + oracle preemption), adaptive_ev_vanet (VANET sensing + V2I/V2V preemption), adaptive_ev_vanet_tuned (same with GA-tuned parameters).
- Navigation modes (--nav): none, astar, aco, aco_tuned.
- Scenarios: normal, congested, emergency, incident, navigation. Networks: single, grid2x2. Junction ids on the grid: J0, J1, J2, J3.

## Interfaces
- StateProvider (sim/sensing.py): get_queues(tls_id), get_waiting_times(tls_id), get_ev_info(tls_id), get_edge_speeds(), name.
- BaseController(tls_id, params, provider); step(sim_time); helpers get_queue_lengths(), get_current_phase(), set_phase_safe(target_phase).
- run_simulation(scenario, controller, seed, params, gui=False, **opts) -> results dict with: avg_waiting_time, avg_time_loss, avg_travel_time, ev_travel_time, max_queue, throughput_veh_per_hr, seed, controller, scenario, plus VANET fields: pdr, avg_latency_ms, avg_hops, missed_preemptions, ev_alert_delivery_time, queue_estimate_mae; navigation fields: nav_mode, nav_avg_travel_time, nonnav_avg_travel_time, edge_load_cv, route_search_expansions, route_search_ms, reroute_count.
- Metrics CSV columns: time, tls_id, phase, queue_N, queue_S, queue_E, queue_W, n_vehicles, ev_present, queue_est_N, queue_est_S, queue_est_E, queue_est_W.

## CLI contract
python sim/main.py --run-id r1 --scenario navigation --controller adaptive_ev_vanet --nav astar --seed 1 [--live] [--gui] [--params file.yaml] [--aco-params file.yaml] [--penetration 0.5] [--nav-fraction 0.3] [--loss 0.1] [--range 300] [--relay on|off]
Exit code 0 on success, non-zero on error. SIGTERM closes SUMO cleanly and emits a status line.

## VANET model (emulated)
- Nodes: OBU on each equipped vehicle (fraction = penetration, chosen by seeded hash of the vehicle id; emergency vehicles are always equipped), RSU at each junction, a TMC reachable from RSUs over a wired lossless backbone (fixed small delay).
- Channel: success needs distance <= range; loss probability = base_loss + (max_loss - base_loss) * (d / range)^2; delay = base_delay + jitter (ms). Delivered on the sim step covering the delay.
- Messages: BEACON (vehicle id, position, speed, edge, timestamp; every beacon_interval), EV_ALERT (EV id, position, speed, route edges, ETA; every alert_interval; TTL and hop count; relayed by vehicles when relay=on), INCIDENT_FLAG (edge id, reason; created by RSUs from beacons with near-zero speed).
- RSU estimates per approach: queue (equipped vehicles below a speed threshold inside the queue zone, scaled by 1/penetration), mean speed per edge. Beacons expire after stale_timeout.
- TMC keeps the traffic map: per-edge mean speed and density from RSU reports.

## Navigation
- Road graph is an EDGE graph built from the SUMO net (successors from edge.getOutgoing()) so routes are always valid in SUMO.
- Edge cost = length / max(mean_speed_from_VANET_map, v_min); fallback to free-flow speed; closed edges = infinity.
- A*: heuristic = euclidean distance / max_speed (admissible). A zero heuristic must give exactly Dijkstra. Count node expansions.
- ACO: ants build paths on the edge graph with probability proportional to tau^alpha * eta^beta (eta = 1 / edge cost), avoiding loops. After each iteration: evaporation tau *= (1 - rho), deposit Q / path_cost from the best ants. Returns routes with selection probabilities so navigating vehicles spread over good alternatives.
- Navigating vehicles are a fraction (nav-fraction) of equipped vehicles; they reroute every nav_interval and when passing a junction, with validated setRoute.

## Live protocol (Python stdout, one JSON object per line, flushed immediately)
{"type":"status","run_id":"r1","status":"started|finished|error","message":"..."}
{"type":"state","run_id":"r1","t":142,"tls":{"J0":{"phase":"EV_GREEN","queues":{"N":4,"S":7,"E":3,"W":6},"queues_est":{"N":3,"S":6,"E":3,"W":6},"signal":{"N":"r","S":"r","E":"G","W":"G"}}},"ev":[{"id":"amb1","tls":"J0","eta":11.4,"dist":170,"speed":15,"alert_age":0.8}],"metrics":{"avg_wait":12.4,"n_vehicles":38,"throughput":1240},"comm":{"pdr":0.97,"latency_ms":42,"penetration":0.5,"sent":1200,"lost":36},"nav":{"mode":"astar","navigating":120,"avg_tt_nav":210.4,"avg_tt_non":240.1,"max_edge_load":14,"edge_load_cv":0.42}}
{"type":"event","run_id":"r1","t":140,"kind":"preempt_start|preempt_end|incident_start|incident_end|alert_sent|alert_delivered|alert_lost|reroute","tls":"J0","detail":"..."}
{"type":"result","run_id":"r1","metrics":{ all fields from the results dict },"seed":1,"controller":"adaptive_ev_vanet","scenario":"emergency"}
Rate limits: "state" at most once per sim second; alert_* events are sampled (at most 5 per second).

## Working style for the agent
- Plan briefly, implement, then run the tests/commands yourself in the terminal.
- At the end, report what you built, exact commands to run, expected output, assumptions, and what you did not test.

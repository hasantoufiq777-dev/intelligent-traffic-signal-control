# PROJECT CONTEXT

Project: "Intelligent Traffic Signal Control System" as a web-based system.
Core: SUMO + Python (TraCI/libsumo) simulation with three controllers (fixed-time baseline, adaptive, adaptive + emergency-vehicle preemption), a GA parameter tuner, and multi-seed evaluation. Web layer: Node.js (Express + Socket.io) gateway that spawns the Python simulation and streams live state to a React dashboard. No OMNeT++.

## Tech rules
- Python 3.10+, SUMO 1.18+, Node.js 18+, React 18 with Vite, Recharts, socket.io, better-sqlite3.
- Use `traci` for GUI/debug and `libsumo` for headless experiments, behind one wrapper so code works with both.
- Never hardcode paths. Read SUMO_HOME and other settings from env or config files. Scenario and controller parameters live in YAML, not constants in code.
- Fixed random seeds for reproducibility. Type hints and short docstrings in Python; clear module boundaries in JS.
- Allowed Python deps: pyyaml, numpy, pandas, scipy, matplotlib, pytest. Do not add other dependencies without saying why.
- Safety rule: signals must never jump to a conflicting green. Always yellow, then all-red, then green.
- Python writes logs to stderr ONLY. stdout is reserved for the JSON-lines protocol below.
- Do not modify files outside the scope of the current module unless needed to make it work; if you do, say so.

## Repo structure (keep exactly)
project/
  PROJECT_CONTEXT.md
  README.md
  sim/
    sumo/            net.xml, routes.rou.xml, sim.sumocfg, detectors
    controllers/     base.py, fixed.py, adaptive.py, preemption.py
    optimizer/       ga_tuner.py
    analytics/       metrics.py, plots.py, evaluate.py
    scenarios/       *.yaml (normal, congested, emergency, incident), generate_routes.py, incident.py
    runner.py        run_simulation(scenario, controller, seed, params, gui=False) -> dict
    main.py          CLI entry point
    mock_sim.py      fake simulator emitting the live protocol (no SUMO needed)
    config.yaml
    requirements.txt
  server/            Node.js: Express + Socket.io + SQLite
  client/            React (Vite)
  results/           summary.json, summary.md, plots
  docs/

## Shared interfaces
- controllers/base.py: class BaseController with __init__(self, tls_id: str, params: dict) and step(self, sim_time: float) -> None. Helpers: get_queue_lengths(), get_current_phase(), set_phase_safe(target_phase).
- runner.run_simulation returns a dict with: avg_waiting_time, avg_time_loss, avg_travel_time, ev_travel_time, max_queue, throughput_veh_per_hr, seed, controller, scenario.
- Metrics CSV columns: time, tls_id, phase, queue_N, queue_S, queue_E, queue_W, n_vehicles, ev_present.
- Controller names used everywhere: fixed, adaptive, adaptive_ev, adaptive_ev_tuned.
- Scenario names used everywhere: normal, congested, emergency, incident.

## CLI contract
python sim/main.py --run-id r1 --scenario emergency --controller adaptive_ev --seed 1 [--live] [--gui] [--params file.yaml]
Exit code 0 on success, non-zero on error. Handle SIGTERM by closing SUMO cleanly and emitting a status line.

## Live protocol (Python stdout, one JSON object per line, flushed immediately)
{"type":"status","run_id":"r1","status":"started|finished|error","message":"..."}
{"type":"state","run_id":"r1","t":142,"tls":{"J0":{"phase":"EV_GREEN","queues":{"N":4,"S":7,"E":3,"W":6},"signal":{"N":"r","S":"r","E":"G","W":"G"}}},"ev":[{"id":"amb1","tls":"J0","eta":11.4,"dist":170,"speed":15}],"metrics":{"avg_wait":12.4,"n_vehicles":38,"throughput":1240}}
{"type":"event","run_id":"r1","t":140,"kind":"preempt_start|preempt_end|incident_start|incident_end","tls":"J0","detail":"..."}
{"type":"result","run_id":"r1","metrics":{"avg_waiting_time":0,"avg_time_loss":0,"avg_travel_time":0,"ev_travel_time":0,"max_queue":0,"throughput_veh_per_hr":0},"seed":1,"controller":"adaptive_ev","scenario":"emergency"}

## Working style for the agent
- Plan briefly, then implement, then run the tests/commands yourself in the terminal.
- At the end, report: what you built, exact commands to run it, what output to expect, what you assumed, and what you did not test.

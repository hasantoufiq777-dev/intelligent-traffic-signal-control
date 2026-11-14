"""Safety check: SUMO reports no collisions (on lanes or inside junctions) in the scenarios,
with every controller type that changes signals or speeds: preemption, eco-driving speed advice,
navigation reroutes, incidents and ambulance routing."""

from __future__ import annotations

import pytest

from sim.runner import run_simulation

CASES = [
    ("congested", "adaptive_vanet", None, {}),
    ("emergency", "adaptive_ev_vanet", {"network": "grid2x2"}, {}),
    ("navigation", "adaptive_vanet", None, {"nav": "astar", "eco_fraction": 0.6}),
    ("ev_incident", "adaptive_ev_vanet", None, {"ev_routing": True}),
]


@pytest.mark.parametrize("scenario,controller,params,kw", CASES, ids=[c[0] for c in CASES])
def test_no_vehicle_collides(scenario, controller, params, kw) -> None:
    r = run_simulation(scenario=scenario, controller=controller, seed=1, params=params, max_steps=500,
                       run_id=f"t_col_{scenario}", **kw)
    assert r["collisions"] == 0

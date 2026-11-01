"""Regression tests for the grid preemption bugs fixed after Module 12.

1. EV distance and approach are computed along the EV's route (not from its current lane or a
   straight line); junctions not ahead on the route get no EV info.
2. While the EV is inside a junction SUMO may already have advanced the route index; the
   junction being crossed comes from the internal lane id.
3. Controllers hold every phase they set (setPhaseDuration), so SUMO's static program cannot
   advance a phase behind the controller.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Dict

import pytest

from sim.controllers.base import BaseController
from sim.runner import run_simulation
from sim.tests.test_safety import MockTrafficLightAPI as MockTraCI
from sim.vanet.nodes import distance_along_route, internal_lane_junction

# Toy map: A -> J0 -> B -> J1 -> C -> exit
EDGES = {"A": ("J0", 200.0), "B": ("J1", 250.0), "C": ("J9", 100.0), "X": ("J2", 250.0)}
ROUTE = ["A", "B", "C"]
TLS = {"J0", "J1", "J2"}


def test_distance_along_route_on_normal_edges() -> None:
    assert distance_along_route("J0", ROUTE, 0, "A", 50.0, EDGES, TLS) == (150.0, "A", 0)
    # J1 is one signalised junction further: A remainder + all of B
    assert distance_along_route("J1", ROUTE, 0, "A", 50.0, EDGES, TLS) == (400.0, "B", 1)
    assert distance_along_route("J2", ROUTE, 0, "A", 50.0, EDGES, TLS) is None  # not on route
    assert distance_along_route("J0", ROUTE, 1, "B", 10.0, EDGES, TLS) is None  # already passed


@pytest.mark.parametrize("route_index", [0, 1])  # SUMO may report either while inside J0
def test_distance_inside_a_junction(route_index: int) -> None:
    assert internal_lane_junction(":J0_10_0") == "J0" and internal_lane_junction("A") is None
    assert distance_along_route("J0", ROUTE, route_index, ":J0_10", 3.0, EDGES, TLS) == (0.0, "A", 0)
    assert distance_along_route("J1", ROUTE, route_index, ":J0_10", 3.0, EDGES, TLS) == (250.0, "B", 0)


def test_controller_holds_the_phases_it_sets() -> None:
    api = type("Api", (), {})()
    api.trafficlight = MockTraCI()
    ctrl = BaseController("J0", sumo_api=api)
    ctrl._apply_phase(ctrl.PHASE_EW_GREEN)
    assert api.trafficlight.phase_duration == BaseController.HOLD_PHASE_S


def _run(ctrl: str, seed: int, tmp_path: Path, **kw: Any) -> Dict[str, Any]:
    out = tmp_path / f"{ctrl}_{seed}_{kw.get('loss', 0)}"
    return run_simulation("emergency", ctrl, seed=seed, params={"network": "grid2x2", **kw.pop("params", {})},
                          run_id=f"t_pg_{ctrl}_{seed}", max_steps=400, metrics_csv_path=out / "m.csv",
                          output_dir=out, **kw)


@pytest.fixture(scope="module")
def grid_runs(tmp_path_factory) -> Dict[str, Any]:
    tmp = tmp_path_factory.mktemp("pgrid")
    runs: Dict[str, Any] = {}
    for seed in (1, 5):
        runs[("adaptive", seed)] = _run("adaptive", seed, tmp)
        runs[("adaptive_ev", seed)] = _run("adaptive_ev", seed, tmp)
        runs[("adaptive_ev_vanet", seed)] = _run(
            "adaptive_ev_vanet", seed, tmp, loss=0.0, params={"vanet": {"base_delay_ms": 0.0, "jitter_ms": 0.0}})
    runs["dir"] = tmp
    return runs


def test_grid_preemption_speeds_up_the_ev(grid_runs) -> None:
    for seed in (1, 5):
        base = grid_runs[("adaptive", seed)]["ev_travel_time"]
        pre = grid_runs[("adaptive_ev", seed)]["ev_travel_time"]
        print(f"\nseed {seed}: EV travel time adaptive {base} s -> adaptive_ev {pre} s")
        assert 0 < pre < base
        assert grid_runs[("adaptive_ev", seed)]["missed_preemptions"] == 0


def test_grid_vanet_preemption_matches_oracle(grid_runs) -> None:
    for seed in (1, 5):
        oracle = grid_runs[("adaptive_ev", seed)]["ev_travel_time"]
        vanet = grid_runs[("adaptive_ev_vanet", seed)]["ev_travel_time"]
        assert abs(vanet - oracle) <= 4.0, f"seed {seed}: VANET {vanet} vs oracle {oracle}"


def test_only_junctions_on_the_ev_route_are_preempted(grid_runs) -> None:
    on_route = {"J0", "J1", "J3"}  # bottom0J0 -> J0J1 -> J1J3 -> J3top1
    for ctrl in ("adaptive_ev", "adaptive_ev_vanet"):
        files = list(grid_runs["dir"].glob(f"{ctrl}_1_*/preemption_events_*.csv"))
        assert files, f"{ctrl}: no preemption happened"
        rows = list(csv.DictReader(open(files[0], encoding="utf-8")))
        assert {r["tls_id"] for r in rows} == on_route
        assert all(float(r["duration"]) < 60.0 for r in rows), "green held far longer than needed"

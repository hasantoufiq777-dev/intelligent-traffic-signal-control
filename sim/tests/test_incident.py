"""Tests for Module 10: incidents detected through VANET.

Unit tests (no SUMO): RSU incident rule, TMC incident map, backbone delay, adaptive
controller ignoring a fully blocked approach.
Integration tests (SUMO, grid2x2 incident scenario): detection delay at full and low
penetration, navigating vehicles avoiding the blocked edge, EV preemption with an
incident on the EV's route, reproducibility.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Set

import pytest

from sim.controllers.adaptive import AdaptiveController
from sim.sensing import StateProvider
from sim.vanet.messages import BeaconMessage
from sim.vanet.nodes import RSUNode, TMC

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Edge "A" runs from (0,0) to (200,0) with 2 lanes; edge "B" has 1 lane.
EDGE_INFO = {"A": ((200.0, 0.0), 2), "B": ((0.0, 200.0), 1)}


def _rsu() -> RSUNode:
    rsu = RSUNode(tls_id="J9", pos=(200.0, 0.0), queue_speed_threshold=1.0, stale_timeout=3.0)
    rsu.configure_incident_detection(EDGE_INFO, detect_time=30.0, clear_time=10.0, stopline_exclusion=30.0)
    return rsu


def _beacon(vid: str, x: float, speed: float, edge: str = "A", lane: int = 0, t: float = 0.0) -> BeaconMessage:
    return BeaconMessage(msg_id=f"{vid}_{t}", sender_id=vid, pos=(x, 0.0), speed=speed,
                         edge_id=edge, lane_id=f"{edge}_{lane}", timestamp=t)


def _feed(rsu: RSUNode, t0: int, t1: int, beacons_fn) -> List[Any]:
    """Feed beacons every second from t0 to t1 and collect the RSU's flag messages."""
    msgs: List[Any] = []
    for t in range(t0, t1 + 1):
        for b in beacons_fn(float(t)):
            rsu.receive_beacon(b, float(t))
        msgs.extend(rsu.detect_incidents(float(t)))
    return msgs


# ----------------------------------------------------------------------------- unit tests
def test_rsu_flags_lane_blocked_after_detect_time() -> None:
    rsu = _rsu()
    msgs = _feed(rsu, 0, 29, lambda t: [_beacon("v1", 100.0, 0.0, t=t)])
    assert msgs == [], "must not flag before incident_detect_time"
    msgs = _feed(rsu, 30, 31, lambda t: [_beacon("v1", 100.0, 0.0, t=t)])
    assert len(msgs) == 1 and msgs[0].kind == "lane_blocked" and msgs[0].edge_id == "A"


def test_rsu_flags_edge_blocked_when_all_lanes_stopped() -> None:
    rsu = _rsu()
    msgs = _feed(rsu, 0, 31, lambda t: [_beacon("v1", 100.0, 0.0, lane=0, t=t),
                                        _beacon("v2", 100.0, 0.0, lane=1, t=t)])
    assert [m.kind for m in msgs] == ["edge_blocked"]


def test_rsu_ignores_red_light_queue_at_stop_line() -> None:
    rsu = _rsu()
    # Stopped 60 s, but only 10 m from the edge end: a signal queue, not an incident
    msgs = _feed(rsu, 0, 60, lambda t: [_beacon("v1", 190.0, 0.0, t=t)])
    assert msgs == []


def test_rsu_ignores_vehicle_that_moves() -> None:
    rsu = _rsu()
    # Stops for 20 s, moves, stops again for 20 s: never 30 s continuously
    def beacons(t: float):
        speed = 5.0 if 20 <= t < 22 else 0.0
        return [_beacon("v1", 100.0, speed, t=t)]
    assert _feed(rsu, 0, 42, beacons) == []


def test_rsu_clears_flag_after_clear_time() -> None:
    rsu = _rsu()
    _feed(rsu, 0, 31, lambda t: [_beacon("v1", 100.0, 0.0, t=t)])
    msgs = _feed(rsu, 32, 60, lambda t: [_beacon("v1", 150.0, 10.0, t=t)])
    assert [m.kind for m in msgs] == ["cleared"]
    assert "A" not in rsu.flagged_edges


def test_low_penetration_misses_unequipped_stop() -> None:
    """An RSU only knows what equipped vehicles report: no beacons, no detection."""
    rsu = _rsu()
    assert _feed(rsu, 0, 60, lambda t: []) == []


def test_tmc_merges_rsu_reports() -> None:
    tmc = TMC()
    rsu_a, rsu_b = _rsu(), _rsu()
    rsu_b.tls_id = "J8"
    flag_a = _feed(rsu_a, 0, 31, lambda t: [_beacon("v1", 100.0, 0.0, t=t)])[0]
    flag_b = _feed(rsu_b, 0, 31, lambda t: [_beacon("v1", 100.0, 0.0, lane=0, t=t),
                                            _beacon("v2", 100.0, 0.0, lane=1, t=t)])[0]
    assert tmc.receive_incident_flag(flag_a, 32.0)["after"] == "lane_blocked"
    assert tmc.receive_incident_flag(flag_b, 32.0)["after"] == "edge_blocked"
    clear_b = _feed(rsu_b, 32, 50, lambda t: [])[-1]
    assert clear_b.kind == "cleared"
    # J9 still reports a lane blockage, so the edge stays in the map
    assert tmc.receive_incident_flag(clear_b, 51.0)["after"] == "lane_blocked"
    assert tmc.get_incidents() == {"A": "lane_blocked"}


def test_backbone_delivers_flag_on_next_step() -> None:
    """INCIDENT_FLAGs travel over the backbone and are never used before delivery."""
    from sim.vanet.network import VanetNetwork

    net = VanetNetwork(sumo_api=None, tls_ids=[], config={"backbone_delay_ms": 5.0}, seed=1)
    flag = _feed(_rsu(), 0, 31, lambda t: [_beacon("v1", 100.0, 0.0, t=t)])[0]
    net.incident_backbone_queue.append((31.0 + net.backbone_delay_s, flag))
    net._step_incident_detection(31.0)
    assert net.get_incidents() == {}
    net._step_incident_detection(32.0)
    assert net.get_incidents() == {"A": "lane_blocked"}


class _FakeProvider(StateProvider):
    def __init__(self, queues: Dict[str, int], blocked: Set[str]) -> None:
        self.queues = queues
        self.blocked = blocked

    @property
    def name(self) -> str:
        return "fake"

    def get_queues(self, tls_id: str) -> Dict[str, int]:
        return dict(self.queues)

    def get_waiting_times(self, tls_id: str) -> Dict[str, float]:
        return {d: 0.0 for d in "NSEW"}

    def get_ev_info(self, tls_id: str) -> List[Dict[str, Any]]:
        return []

    def get_edge_speeds(self) -> Dict[str, float]:
        return {}

    def get_blocked_approaches(self, tls_id: str) -> Set[str]:
        return set(self.blocked)


@pytest.mark.parametrize("blocked, expect_switch", [(set(), True), ({"W"}, False)])
def test_adaptive_never_greens_a_blocked_approach(blocked: Set[str], expect_switch: bool) -> None:
    provider = _FakeProvider({"N": 0, "S": 0, "E": 0, "W": 20}, blocked)
    ctrl = AdaptiveController("J1", params={"min_green": 10, "max_green": 500, "starvation_limit": 30},
                              provider=provider)
    switched = False
    for t in range(1, 120):
        ctrl.step(float(t))
        if ctrl.is_in_transition() or ctrl.current_phase != ctrl.PHASE_NS_GREEN:
            switched = True
    assert switched == expect_switch


# ---------------------------------------------------------------------- integration tests
def _run(tmp_path: Path, tag: str, **kw: Any) -> Dict[str, Any]:
    from sim.runner import run_simulation

    incident = kw.pop("incident", {})
    return run_simulation(
        scenario="incident",
        controller=kw.pop("controller", "adaptive_vanet"),
        seed=kw.pop("seed", 1),
        run_id=f"t_inc_{tag}",
        params={"incident": incident},
        metrics_csv_path=tmp_path / f"{tag}.csv",
        **kw,
    )


@pytest.fixture(scope="module")
def stop_runs(tmp_path_factory) -> Dict[float, Dict[str, Any]]:
    tmp = tmp_path_factory.mktemp("inc_stop")
    return {
        pen: _run(tmp, f"stop_{pen}", incident={"type": "stop"}, penetration=pen, max_steps=330)
        for pen in (1.0, 0.2)
    }


def test_full_penetration_detects_closure_quickly(tmp_path: Path) -> None:
    r = _run(tmp_path, "closure_full", penetration=1.0, max_steps=230)
    print(f"\nclosure, penetration 1.0: detection delay {r['incident_detection_delay']} s, "
          f"kind {r['incident_detected_kind']}, false flags {r['incident_false_flags']}")
    assert r["incident_detected"] is True
    assert r["incident_detection_delay"] <= 40.0  # detect_time 30 s + beacon/backbone latency
    assert r["incident_detected_kind"] == "edge_blocked"
    assert r["incident_false_flags"] == 0
    assert r["incident_local_detours"] > 0


def test_low_penetration_detection_is_slower_or_missed(stop_runs) -> None:
    hi, lo = stop_runs[1.0], stop_runs[0.2]
    print(f"\nstopped vehicle: penetration 1.0 -> detected={hi['incident_detected']} "
          f"delay={hi['incident_detection_delay']} s; penetration 0.2 -> detected={lo['incident_detected']} "
          f"delay={lo['incident_detection_delay']} s")
    assert hi["incident_detected"] is True
    assert hi["incident_detection_delay"] <= 40.0
    assert (not lo["incident_detected"]) or lo["incident_detection_delay"] >= hi["incident_detection_delay"]


def test_navigating_vehicles_avoid_blocked_edge(tmp_path: Path) -> None:
    r = _run(tmp_path, "stop_nav", incident={"type": "stop"}, nav="astar",
             nav_fraction=0.5, penetration=1.0, max_steps=330)
    n_nav = r["incident_nav_entries_after_detection"]
    n_non = r["incident_nonnav_entries_after_detection"]
    print(f"\nentries onto the blocked edge after detection: navigating={n_nav}, non-navigating={n_non}")
    assert r["incident_detected"] is True
    assert n_non > 0, "scenario should send non-navigating traffic over the incident edge"
    assert n_nav * 3 <= n_non


def test_ev_preemption_works_with_incident_on_route(tmp_path: Path) -> None:
    csv_path = PROJECT_ROOT / "results" / "preemption_events_incident_adaptive_ev_vanet_s1.csv"
    if csv_path.exists():
        csv_path.unlink()
    r = _run(tmp_path, "ev", controller="adaptive_ev_vanet", penetration=1.0, max_steps=600)
    print(f"\nEV travel time with closure on its route: {r['ev_travel_time']} s")
    assert r["incident_detected"] is True
    assert r["ev_travel_time"] > 0.0, "EV must reach its destination"
    assert csv_path.exists(), "EV preemption must still trigger"
    assert len(csv_path.read_text(encoding="utf-8").strip().splitlines()) >= 2


def test_incident_runs_are_reproducible(tmp_path: Path) -> None:
    keys = ("incident_detection_delay", "incident_local_detours", "avg_travel_time", "pdr")
    a = _run(tmp_path, "rep_a", penetration=0.5, max_steps=220)
    b = _run(tmp_path, "rep_b", penetration=0.5, max_steps=220)
    assert {k: a[k] for k in keys} == {k: b[k] for k in keys}

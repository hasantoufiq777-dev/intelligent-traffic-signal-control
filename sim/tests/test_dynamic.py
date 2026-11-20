"""Dynamic congestion-aware controller: scores, green length, switching rules and safety.

The traffic monitor is replaced by fixed measurements, so these tests need no SUMO.
"""

from __future__ import annotations

from typing import Dict

import pytest

from sim.controllers.dynamic import DynamicController
from sim.controllers.factory import create_controller
from sim.controllers.traffic_monitor import ApproachStats

PARAMS = {"min_green": 5, "max_green": 45, "yellow_time": 3, "all_red_time": 2, "decision_interval": 1,
          "startup_lost_s": 2.0, "headway_s": 2.0, "fairness_wait_s": 40}


def road(d: str, waiting: float = 0.0, vehicles: float = None, down: str = "clear", down_occ: float = None,
         down_space: float = 60.0, mean_wait: float = 0.0, max_wait: float = 0.0, near: float = 0.0,
         flow: float = 0.0, closed: bool = False) -> ApproachStats:
    occ = {"clear": 0.1, "busy": 0.5, "blocked": 0.9}[down] if down_occ is None else down_occ
    veh = waiting if vehicles is None else vehicles
    return ApproachStats(direction=d, roads=[f"in{d}"], lanes=2, waiting=waiting, vehicles=veh,
                         density=veh / 0.458, mean_wait_s=mean_wait, max_wait_s=max_wait, flow_vpm=flow,
                         near_stopline=near, down_roads=[f"out{d}"], down_worst=f"out{d}", down_occ=occ,
                         down_space=down_space, down_status=down, closed=closed)


class Feed:
    """Measurements the controller sees; change .stats between steps."""

    def __init__(self, stats: Dict[str, ApproachStats]) -> None:
        self.stats = stats

    def observe(self, t, closed_approaches=None, incidents=None):
        return dict(self.stats)


def make(stats: Dict[str, ApproachStats]) -> DynamicController:
    c = DynamicController("J0", params=dict(PARAMS))
    c.monitor = Feed(stats)  # type: ignore[assignment]
    return c


def all_roads(**kw: ApproachStats) -> Dict[str, ApproachStats]:
    out = {d: road(d) for d in "NSEW"}
    out.update(kw)
    return out


def run(c: DynamicController, seconds: int, t0: int = 0) -> int:
    for i in range(seconds):
        c.step(float(t0 + i + 1))
    return t0 + seconds


def test_factory_names():
    assert isinstance(create_controller("dynamic", "J0", PARAMS, provider=None), DynamicController)
    assert isinstance(create_controller("dynamic_vanet", "J0", PARAMS, provider=None), DynamicController)


def test_score_queue_first_downstream_and_closure():
    c = make(all_roads())
    base = c.score(road("N", waiting=10))
    assert c.score(road("N", waiting=20)) > base  # longer queue, higher priority
    assert c.score(road("N", waiting=10, mean_wait=30)) > base  # waiting time adds priority
    assert c.score(road("N", waiting=10, down="blocked")) < base  # blocked downstream lowers it
    assert c.score(road("N", waiting=10, closed=True)) == 0.0  # a closed road gets no green


def test_clear_downstream_beats_blocked_bigger_queue():
    """The paper-style example: road A 15 waiting but blocked downstream, road B 8 waiting and clear."""
    c = make(all_roads())
    a = c.score(road("N", waiting=15, down="blocked"))
    b = c.score(road("E", waiting=8, down="clear"))
    assert b > a


def test_green_length_follows_demand():
    c = make(all_roads())
    c.stats = all_roads(N=road("N", waiting=2))
    light, _ = c.green_need("NS")
    c.stats = all_roads(N=road("N", waiting=12))
    moderate, _ = c.green_need("NS")
    c.stats = all_roads(N=road("N", waiting=60))
    heavy, _ = c.green_need("NS")
    assert light < moderate < heavy
    assert light == pytest.approx(2.0 + 2.0 * 1)  # 2 vehicles on 2 lanes: one per lane
    c._plan("NS")
    assert c.planned == c.max_green  # capped at max_green
    assert c.tier == "long"


def test_downstream_space_and_blockage_shorten_the_green():
    c = make(all_roads())
    c.stats = all_roads(N=road("N", waiting=20, down="busy", down_space=4))
    need, note = c.green_need("NS")
    assert need == pytest.approx(2.0 + 2.0 * 2)  # only the 4 vehicles that fit are released
    assert note and "reduced" in note and "outN" in note
    c.stats = all_roads(N=road("N", waiting=20, down="blocked"))
    need, note = c.green_need("NS")
    assert need == 0.0 and "blocked" in note
    c._plan("NS")
    assert c.planned == c.min_green


def test_never_ends_before_min_green_and_gaps_out_when_empty():
    c = make(all_roads(E=road("E", waiting=6)))  # NS green, its roads empty, cars waiting on EW
    t = run(c, 4)
    assert c.serving() == "NS"  # min_green 5 s not reached yet
    run(c, 1, t)
    assert c.is_in_transition() and c.transition_state == "YELLOW"
    assert any("ended early" in e["text"] for e in c.log)


def test_vehicles_near_stop_line_keep_the_green():
    c = make(all_roads(N=road("N", waiting=0, vehicles=3, near=2), E=road("E", waiting=2)))
    run(c, 8)
    assert c.serving() == "NS"  # still arriving at the line: no gap-out, green extended


def test_fairness_ends_a_busy_green():
    stats = all_roads(N=road("N", waiting=30), E=road("E", waiting=1, max_wait=10))
    c = make(stats)
    t = run(c, 10)
    assert c.serving() == "NS"
    stats["E"] = road("E", waiting=1, max_wait=41)
    run(c, 1, t)
    assert c.is_in_transition()
    assert any("waited" in e["text"] for e in c.log)
    run(c, 6, t + 1)  # yellow 3 s + all-red 2 s, then East–West
    assert c.serving() == "EW"
    assert "prioritised: longest waiting time" in c.reason or any("prioritised" in e["text"] for e in c.log)


def test_no_green_for_an_empty_road():
    c = make(all_roads(N=road("N", waiting=3)))  # nobody on East–West
    run(c, 60)
    assert c.serving() == "NS" and not c.is_in_transition()
    assert any("skipped" in e["text"] for e in c.log)


def test_switch_goes_through_yellow_and_all_red():
    c = make(all_roads(E=road("E", waiting=6)))
    states = []
    for i in range(1, 20):
        c.step(float(i))
        states.append(c.transition_state if c.is_in_transition() else c.serving())
    i_y = states.index("YELLOW")
    assert states[i_y:i_y + 3] == ["YELLOW"] * 3
    assert states[i_y + 3:i_y + 5] == ["ALL_RED"] * 2
    assert states[i_y + 5] == "EW"


def test_max_green_with_steady_demand_on_both():
    stats = all_roads(N=road("N", waiting=80, near=3), E=road("E", waiting=5))
    c = make(stats)
    states = [None]
    for i in range(1, 60):
        c.step(float(i))
        states.append(c.serving())
    green_s = sum(1 for s in states if s == "NS")
    assert green_s <= c.max_green + c.decision_interval


def test_live_info_shape():
    c = make(all_roads(N=road("N", waiting=4), E=road("E", waiting=2)))
    run(c, 3)
    info = c.get_live_info()
    assert info["serving"] == "NS"
    assert set(info["roads"]) == set("NSEW")
    assert {"waiting", "queue_m", "density", "mean_wait_s", "down_status", "score", "signal"} <= set(info["roads"]["N"])
    assert info["planned_s"] >= 5 and info["remaining_s"] is not None
    assert info["reason"]

"""Queue levelling in stages (most -> second -> third -> fourth) and green ahead of a car on a free road."""

from __future__ import annotations

from sim.controllers.leveling import LevelingController
from sim.controllers.traffic_monitor import ApproachStats, TrafficMonitor
from sim.tests.test_leveling import Feed, PARAMS, road

FREE = dict(PARAMS, free_flow=True, tier_tolerance=1, level_tolerance=0)


def make(feed, serving="N", params=FREE):
    c = LevelingController("J0", params=dict(params))
    c.monitor = feed
    c.current_phase = c._code(serving)
    return c


def coming(d, eta, vehicles=1):
    s = road(d, waiting=0, vehicles=vehicles)
    s.approaching, s.approach_eta_s = float(vehicles), float(eta)
    return s


def test_first_stage_goes_down_to_the_second_queue():
    c = make(Feed(N=12, E=7, S=4, W=1))
    c.step(1.0)
    assert c.target_queue == 7


def test_level_roads_go_down_to_the_third_queue():
    c = make(Feed(N=7, E=7, S=4, W=1))
    c.step(1.0)
    assert c.target_queue == 4 and c._stage_peers == {"E"}


def test_then_to_the_fourth_and_finally_to_zero():
    c = make(Feed(N=4, E=4, S=4, W=1))
    c.step(1.0)
    assert c.target_queue == 1
    c = make(Feed(N=2, E=2, S=2, W=2))
    c.step(1.0)
    assert c.target_queue == 0


def test_green_does_not_stop_at_a_level_peer():
    feed = Feed(N=7, E=7, S=3)
    c = make(feed)
    c.step(1.0)
    feed.stats["N"] = road("N", 5)
    for t in range(2, 12):
        c.step(float(t))
    assert c.serving() == "N"  # still above the third queue (3), though East has 7
    feed.stats["N"] = road("N", 3)
    c.step(12.0)
    assert c.is_in_transition() and c._target_group() == "E"


def test_a_road_that_grows_past_the_green_takes_over():
    feed = Feed(N=12, E=2)
    c = make(feed)
    c.step(1.0)
    feed.stats["N"] = road("N", 6)
    feed.stats["W"] = road("W", 9)
    for t in range(2, 10):
        c.step(float(t))
        if c.is_in_transition():
            break
    assert c.is_in_transition() and c._target_group() == "W"


def test_green_goes_ahead_of_a_car_on_a_free_road():
    feed = Feed()
    feed.stats["E"] = coming("E", 9.0)
    c = make(feed)
    c.step(1.0)  # no minimum green on an empty road
    assert c.is_in_transition() and c._target_group() == "E"


def test_two_cars_the_first_to_arrive_is_served_first():
    feed = Feed()
    feed.stats["N"] = coming("N", 4.0)
    feed.stats["E"] = coming("E", 9.0)
    c = make(feed)
    c.step(1.0)
    assert c.serving() == "N" and not c.is_in_transition()


def test_empty_network_keeps_the_green_without_changing():
    c = make(Feed())
    for t in range(1, 120):
        c.step(float(t))
    assert c.serving() == "N" and not c.is_in_transition()


def test_empty_green_gives_way_at_once_to_a_waiting_car():
    c = make(Feed(E=1))
    c.step(1.0)
    assert c.is_in_transition() and c._target_group() == "E"


class Lane:
    halting = {"n0": 1}

    @classmethod
    def getLastStepHaltingNumber(cls, lane):
        return cls.halting.get(lane, 0)


class Api:
    lane = Lane


def test_stop_line_detector_reveals_a_car_without_a_radio():
    m = TrafficMonitor("J0", None, [], params={"stopline_detector": True})
    m.api = Api
    s = ApproachStats(direction="N", roads=["n"], lanes=1)
    m._apply_stopline_detector(s, "N", ["n0"], 10.0)
    m._apply_stopline_detector(s, "N", ["n0"], 25.0)
    assert s.stopline_occupied and s.waiting == 1.0 and s.max_wait_s == 15.0
    clear = ApproachStats(direction="N", roads=["n"], lanes=1)
    Lane.halting = {}
    m._apply_stopline_detector(clear, "N", ["n0"], 26.0)
    assert not clear.stopline_occupied and "N" not in m._occupied_since
    Lane.halting = {"n0": 1}

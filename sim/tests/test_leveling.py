"""Queue levelling: longest queue first, down to the next one, level roads repeat, the rest is freed."""

from __future__ import annotations

from sim.controllers.factory import create_controller
from sim.controllers.leveling import LevelingController
from sim.controllers.traffic_monitor import ApproachStats

PARAMS = {"min_green": 5, "max_green": 45, "yellow_time": 3, "all_red_time": 2, "fairness_wait_s": 60}


def road(d, waiting=0.0, vehicles=None, near=0.0, max_wait=0.0, down="clear", closed=False):
    veh = waiting if vehicles is None else vehicles
    return ApproachStats(direction=d, roads=[f"in{d}"], lanes=2, waiting=waiting, vehicles=veh, near_stopline=near,
                         max_wait_s=max_wait, down_roads=[f"out{d}"], down_worst=f"out{d}", down_status=down, closed=closed)


class Feed:
    def __init__(self, **queues):
        self.stats = {d: road(d, queues.get(d, 0)) for d in "NSEW"}

    def observe(self, t, closed_approaches=None, incidents=None):
        return dict(self.stats)


def make(feed, serving="E"):
    c = LevelingController("J0", params=dict(PARAMS))
    c.monitor = feed
    c.current_phase = c._code(serving)
    return c


def run(c, seconds, t0=0):
    for i in range(seconds):
        c.step(float(t0 + i + 1))
    return t0 + seconds


def until_green(c, limit=12):
    for i in range(limit):
        c.step(1000.0 + i)
        if c.serving():
            return c.serving()
    return None


def test_factory_names():
    assert isinstance(create_controller("level", "J0", PARAMS, provider=None), LevelingController)
    assert isinstance(create_controller("level_vanet", "J0", PARAMS, provider=None), LevelingController)


def test_busiest_road_goes_first_alone():
    feed = Feed(N=12, E=7, W=3)
    c = make(feed, "S")
    run(c, 5)  # S has nothing: after the minimum green it hands over
    assert until_green(c) == "N"


def test_green_ends_when_level_with_second_road():
    feed = Feed(N=12, E=7)
    c = make(feed, "N")
    run(c, 8)
    assert c.serving() == "N"  # still above 7
    feed.stats["N"] = road("N", 7)
    c.step(9.0)
    assert c.transition_state == "YELLOW"
    assert until_green(c) == "E"


def test_level_facing_roads_go_together():
    feed = Feed(N=8, S=8, E=2)
    c = make(feed, "E")
    run(c, 5)
    assert until_green(c) == "NS"


def test_the_last_cars_are_freed_before_changing():
    feed = Feed(W=3)
    c = make(feed, "W")
    run(c, 30)
    assert c.serving() == "W" and not c.is_in_transition()  # nobody else waits: W keeps the green


def test_cars_still_at_the_line_are_not_cut_off():
    feed = Feed(N=2, E=2)
    feed.stats["N"] = road("N", waiting=0, vehicles=4, near=4)
    c = make(feed, "N")
    run(c, 7)
    assert c.serving() == "N"


def test_a_long_wait_overrides_the_queue_order():
    feed = Feed(N=20, E=1)
    c = make(feed, "N")
    run(c, 6)
    feed.stats["E"] = road("E", 1, max_wait=61)
    c.step(7.0)
    assert c.transition_state == "YELLOW"
    assert until_green(c) == "E"


def test_blocked_road_ahead_gives_way():
    feed = Feed(N=10, E=2)
    feed.stats["N"] = road("N", 10, down="blocked")
    c = make(feed, "N")
    run(c, 5)
    assert c.is_in_transition()


def test_every_change_passes_yellow_then_all_red():
    feed = Feed(N=9, E=1)
    c = make(feed, "E")
    states = []
    for i in range(1, 16):
        c.step(float(i))
        states.append(c.transition_state if c.is_in_transition() else c.serving())
    y = states.index("YELLOW")
    assert states[y:y + 3] == ["YELLOW"] * 3 and states[y + 3:y + 5] == ["ALL_RED"] * 2 and states[y + 5] == "N"


def test_spat_gives_exact_green_only_during_a_change():
    feed = Feed(N=9, E=1)
    c = make(feed, "E")
    run(c, 5)
    assert c.is_in_transition()
    assert c.get_spat()["N"]["next_green_min_s"] == c.get_spat()["N"]["next_green_max_s"] is not None
    until_green(c)
    assert c.get_spat()["E"]["next_green_min_s"] is None

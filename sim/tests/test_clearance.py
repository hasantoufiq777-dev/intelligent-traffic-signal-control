"""Queue levelling: computed yellow, all-red until the junction is empty, hard limits for unseen roads."""

from __future__ import annotations

import pytest

from sim.controllers.leveling import LevelingController
from sim.tests.test_leveling import Feed, PARAMS, road

CLEAR = dict(PARAMS, dynamic_clearance=True, all_red_min_s=1, all_red_max_s=4, yellow_min_s=3,
             yellow_reaction_s=1.0, yellow_decel=3.05, max_red_s=60)


class Provider:
    """Stands in for the VANET provider: the controller only reads its name here."""

    name = "vanet"
    vanet_net = None

    def get_blocked_approaches(self, tls_id):
        return set()

    def get_incidents(self):
        return {}


def make(feed, serving="E", params=CLEAR, provider=None):
    c = LevelingController("J0", params=dict(params), provider=provider)
    c.monitor = feed
    c.current_phase = c._code(serving)
    return c


def start_change(c):
    c.set_phase_safe(c._code("N"))


def seconds_in(c, state):
    n = 0
    while c.transition_state == state and n < 20:
        c.update_transitions(1.0)
        n += 1
    return n


@pytest.mark.parametrize("kmh, expected", [(30, 3), (50, 4), (60, 4), (80, 5)])
def test_yellow_follows_the_kinematic_formula(kmh, expected):
    c = make(Feed())
    v = kmh / 3.6
    needed = 1.0 + v / (2 * 3.05)

    class Lane:
        @staticmethod
        def getMaxSpeed(_):
            return v

    class Api:
        lane = Lane

    c.sumo_api = Api
    c.monitor.in_lanes = {"E": ["e0"]}
    assert c._road_yellow_times()["E"] == expected >= needed


def test_pair_uses_the_longer_yellow():
    c = make(Feed())
    c._yellow_by_road = {"N": 3.0, "S": 5.0}
    assert c._yellow_for("NS") == 5.0


def test_all_red_ends_as_soon_as_the_junction_is_empty():
    c = make(Feed())
    c._yellow_by_road = {"E": 4.0}
    c._junction_clear = lambda: True
    start_change(c)
    assert seconds_in(c, "YELLOW") == 4
    assert seconds_in(c, "ALL_RED") == 1
    assert c.serving() == "N"


def test_all_red_waits_for_a_car_still_inside():
    c = make(Feed())
    answers = iter([False, False, True])
    c._junction_clear = lambda: next(answers)
    start_change(c)
    seconds_in(c, "YELLOW")
    assert seconds_in(c, "ALL_RED") == 3


def test_all_red_never_exceeds_the_safety_limit():
    c = make(Feed())
    c._junction_clear = lambda: False
    start_change(c)
    seconds_in(c, "YELLOW")
    assert seconds_in(c, "ALL_RED") == 4
    assert c.serving() == "N"


def test_no_conflicting_green_during_clearance():
    c = make(Feed())
    c._junction_clear = lambda: False
    start_change(c)
    seen = []
    for _ in range(9):
        seen.append(c.serving())
        c.update_transitions(1.0)
    assert seen[:7] == [None] * 7  # yellow and all-red: no road is green


def test_spat_gives_the_window_during_clearance():
    c = make(Feed())
    c._yellow_by_road = {"E": 4.0}
    c._junction_clear = lambda: False
    start_change(c)
    n = c.get_spat()["N"]
    assert (n["next_green_min_s"], n["next_green_max_s"]) == (5.0, 8.0)


def test_max_green_is_hard_when_sensing_is_partial():
    feed = Feed(E=1)  # looks like nobody waits on the other roads
    c = make(feed, "E", provider=Provider())
    c.last_green = {d: 40.0 for d in c.last_green}
    for i in range(1, 46):
        c.step(40.0 + i)
        if c.is_in_transition():
            break
    assert c.is_in_transition() and i <= 45


def test_a_road_that_looks_empty_still_gets_a_turn():
    feed = Feed(E=5)
    c = make(feed, "E", provider=Provider())
    c.last_green = {"N": 0.0, "S": 50.0, "E": 50.0, "W": 50.0}
    for i in range(1, 10):
        c.step(52.0 + i)
        if c.is_in_transition():
            break
    assert c.is_in_transition() and c._target_group() == "N"


def test_exact_sensing_keeps_the_green_when_nobody_is_there():
    c = make(Feed(W=3), "W")
    for i in range(1, 70):
        c.step(float(i))
    assert c.serving() == "W"

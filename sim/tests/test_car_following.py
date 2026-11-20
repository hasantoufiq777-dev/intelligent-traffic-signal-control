"""Car following: advice from the vehicle ahead (sim/car_following.py) and the live rows."""

from sim.car_following import (STANDSTILL_GAP_M, TIME_GAP_ACC_S, TIME_GAP_CACC_S, follow_advice)
from sim.live_vehicles import FLAG_EQUIPPED, VEHICLE_FIELDS, add_follow_advice

LIMIT = 13.9  # 50 km/h


def test_free_road_drives_at_the_limit():
    a = follow_advice(10.0, LIMIT)
    assert a["state"] == "free" and a["speed"] == round(LIMIT, 1) and a["ttc"] is None


def test_leader_too_far_counts_as_free_road():
    assert follow_advice(10.0, LIMIT, 5.0, 0.0, 150.0)["state"] == "free"


def test_same_speed_at_the_desired_gap_keeps_the_speed():
    v = 10.0
    gap = STANDSTILL_GAP_M + TIME_GAP_ACC_S * v
    a = follow_advice(v, LIMIT, v, 0.0, gap)
    assert a["state"] == "keep" and abs(a["speed"] - v) < 0.2
    assert a["desired_gap"] == round(gap, 1) and a["time_gap"] == round(gap / v, 1)


def test_slower_leader_means_slow_down_and_a_time_to_collision():
    a = follow_advice(13.0, LIMIT, 8.0, 0.0, 40.0)
    assert a["state"] == "slow" and a["speed"] < 13.0
    assert a["ttc"] == round(40.0 / 5.0, 1)


def test_closing_fast_is_a_brake_warning():
    a = follow_advice(14.0, LIMIT, 4.0, 0.0, 25.0)  # ttc 2.5 s
    assert a["state"] == "brake" and a["ttc"] < 4.0 and a["speed"] < 14.0


def test_stopped_vehicle_ahead():
    a = follow_advice(8.0, LIMIT, 0.0, 0.0, 20.0)
    assert a["state"] == "stopped" and a["speed"] <= 8.0


def test_stopped_vehicle_far_ahead_while_closing_fast():
    assert follow_advice(12.2, LIMIT, 0.0, 0.0, 71.0)["state"] == "stopped"  # 5.8 s to reach it
    assert follow_advice(3.0, LIMIT, 0.0, 0.0, 71.0)["state"] != "stopped"  # 24 s away: not yet


def test_leader_pulling_away_means_speed_up_within_the_limit():
    a = follow_advice(6.0, LIMIT, 13.0, 1.0, 60.0)
    assert a["state"] == "speed_up" and 6.0 < a["speed"] <= round(LIMIT, 1)


def test_too_close_at_equal_speed():
    a = follow_advice(12.0, LIMIT, 12.0, 0.0, 8.0)
    assert a["state"] == "too_close" and a["speed"] < 12.0


def test_cooperative_reacts_to_hard_braking_before_the_gap_shrinks():
    v = 10.0
    gap = STANDSTILL_GAP_M + TIME_GAP_ACC_S * v
    radar = follow_advice(v, LIMIT, v, -4.0, gap, cooperative=False)
    v2v = follow_advice(v, LIMIT, v, -4.0, gap, cooperative=True)
    assert radar["state"] == "keep" and radar["mode"] == "acc"  # a radar sees no change yet
    assert v2v["state"] == "brake" and v2v["mode"] == "cacc" and v2v["speed"] < radar["speed"]


def test_cooperative_keeps_a_shorter_gap():
    v = 10.0
    assert follow_advice(v, LIMIT, v, 0.0, 30.0, cooperative=True)["desired_gap"] == round(
        STANDSTILL_GAP_M + TIME_GAP_CACC_S * v, 1)


def test_target_never_negative_or_above_the_limit():
    for args in [(0.0, LIMIT, 0.0, -3.0, 0.0), (13.9, LIMIT, 20.0, 3.0, 99.0), (5.0, LIMIT, 0.0, 0.0, 1.0)]:
        for coop in (False, True):
            s = follow_advice(*args, cooperative=coop)["speed"]
            assert 0.0 <= s <= round(LIMIT, 1)


def _row(vid, speed, flags, acc, leader, gap):
    vals = dict(id=vid, x=0, y=0, angle=0, speed=speed, flags=flags, tls=None, approach=None, dist=None, co2_g=0,
                fuel_ml=0, advice=None, adv_speed=None, green_in=None, road="e", to="x", kind="car", lane=0,
                limit=LIMIT, acc=acc, leader=leader, gap=gap)
    n = VEHICLE_FIELDS.index("gap") + 1
    return [vals[k] for k in VEHICLE_FIELDS[:n]]


def test_live_rows_get_the_follow_fields():
    rows = [_row("a", 10.0, FLAG_EQUIPPED, -4.0, None, None),
            _row("b", 10.0, FLAG_EQUIPPED, 0.0, "a", 17.5),  # both equipped: cooperative, sees a's braking
            _row("c", 10.0, 0, 0.0, "b", 17.5),  # not equipped: radar only
            _row("d", 8.0, 0, 0.0, "incblk_1", 20.0),  # the stopped incident vehicle (not in the rows)
            _row("e", 8.0, 0, 0.0, "gone", 20.0)]  # leader not known
    add_follow_advice(rows)
    f = {r[0]: dict(zip(VEHICLE_FIELDS, r)) for r in rows}
    assert all(len(r) == len(VEHICLE_FIELDS) for r in rows)
    assert f["a"]["follow"] is None
    assert f["b"]["follow"] == "brake"
    assert f["c"]["follow"] == "keep"
    assert f["d"]["follow"] == "stopped"
    assert f["e"]["follow"] is None and f["e"]["follow_speed"] is None

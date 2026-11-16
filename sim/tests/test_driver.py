"""One-driver mode (sim/driver.py): commands, the driven car in SUMO, advice, ambulance warning,
trip report, and that the driven car stays out of navigation, eco-driving and the run results."""

from __future__ import annotations

import contextlib
import io
import json
from typing import Any, Dict, List

from sim.commands import CommandReader
from sim.runner import run_simulation


class Script:
    """Command source for run_simulation: commands keyed by simulation second."""

    def __init__(self, plan: Dict[int, List[Dict[str, Any]]]) -> None:
        self.plan = {k: list(v) for k, v in plan.items()}
        self.t = 0

    def poll(self) -> List[Dict[str, Any]]:
        self.t += 1
        return self.plan.pop(self.t, [])


def _live_run(plan: Dict[int, List[Dict[str, Any]]], **kw: Any):
    """Run a live simulation with scripted driver commands; return (result, protocol messages)."""
    args = dict(scenario="emergency", controller="adaptive_ev_vanet", seed=1, max_steps=320,
                params={"network": "grid2x2"}, run_id="t_drv")
    args.update(kw)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        result = run_simulation(live=True, driver=True, driver_commands=Script(plan), **args)
    msgs = [json.loads(line) for line in out.getvalue().splitlines() if line.startswith("{")]
    return result, msgs


def _driver_msgs(msgs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [m for m in msgs if m["type"] == "driver"]


def test_command_reader_parses_json_lines_and_skips_bad_ones() -> None:
    stream = io.StringIO('{"cmd": "drive", "mode": "go"}\nnot json\n\n[1, 2]\n{"no_cmd": 1}\n{"cmd": "drive_end"}\n')
    reader = CommandReader(stream)
    reader._thread.join(timeout=2)
    assert reader.poll() == [{"cmd": "drive", "mode": "go"}, {"cmd": "drive_end"}]
    assert reader.poll() == []


def test_drive_stop_go_and_arrive_with_a_trip_report() -> None:
    plan = {
        100: [{"cmd": "drive_start", "from": "bottom0J0", "to": "J3top1", "step_delay": 0}],
        125: [{"cmd": "drive", "mode": "stop"}],
        135: [{"cmd": "drive", "mode": "go"}],
        150: [{"cmd": "drive", "mode": "ease", "speed": 6.0}],
        165: [{"cmd": "drive", "mode": "go"}],
    }
    result, msgs = _live_run(plan)
    assert result["collisions"] == 0  # stopping and easing off never make anyone crash
    drv = _driver_msgs(msgs)
    by_t = {m["t"]: m for m in drv if m["status"] == "driving"}

    assert drv[0]["status"] == "waiting" and drv[0]["vid"] == "drv1"
    # Stop: the car brakes (within its limits) to a standstill and the stop is counted
    assert by_t[124]["speed_kmh"] > 30
    assert by_t[126]["speed_kmh"] > 0  # no instant stop: SUMO keeps the braking limit
    assert by_t[133]["speed_kmh"] == 0.0 and by_t[133]["stops"] == 1
    # Ease off to 6 m/s = 21.6 km/h
    assert by_t[160]["mode"] == "ease" and by_t[160]["speed_kmh"] == 21.6
    # The driven car is on the map with the driver flag (32) and an OBU (16)
    rows = [r for m in msgs if m["type"] == "vehicles" for r in m["rows"] if r[0] == "drv1"]
    assert rows and all(r[5] & 32 and r[5] & 16 for r in rows)

    final = [m for m in drv if m["status"] == "finished"]
    assert len(final) == 1
    rep = final[0]["report"]
    assert rep["finished"] and rep["from"] == "bottom0J0" and rep["to"] == "J3top1"
    assert 900 < rep["distance_m"] < 1100 and rep["stops"] >= 1
    assert rep["co2_g"] > 0 and rep["fuel_ml"] > 0 and rep["co2_g_per_km"] > 100
    # Cars with the same origin and destination finished during the run, so they are the comparison
    assert rep["compare"]["group"] == "same_route" and rep["compare"]["n"] >= 1
    kinds = [m["kind"] for m in msgs if m["type"] == "event"]
    assert "driver_start" in kinds and "driver_end" in kinds
    # No driver messages after the trip ended
    assert drv[-1]["status"] == "finished"


def test_ambulance_crossing_the_route_warns_the_driver() -> None:
    plan = {100: [{"cmd": "drive_start", "from": "bottom0J0", "to": "J3top1", "step_delay": 0}],
            150: [{"cmd": "drive", "mode": "ease", "speed": 6.0}]}
    _, msgs = _live_run(plan)
    drv = _driver_msgs(msgs)
    warned = [m for m in drv if m.get("amb")]
    assert warned, "the ambulance (amb1, departs at 120 s via J1 to J3) should be announced"
    assert {m["amb"]["where"] for m in warned} == {"crossing"}
    assert {m["amb"]["junction"] for m in warned} == {"J3"}
    assert all(m["amb"]["dist_m"] <= 200 for m in warned)
    assert [m for m in drv if m["status"] == "finished"][0]["report"]["amb_warnings"] == 1


def test_hand_back_releases_the_car_to_normal_traffic() -> None:
    plan = {100: [{"cmd": "drive_start", "from": "left0J0", "to": "J3right1", "step_delay": 0}],
            110: [{"cmd": "drive", "mode": "stop"}],
            125: [{"cmd": "drive_end", "reason": "idle"}]}
    _, msgs = _live_run(plan)
    drv = _driver_msgs(msgs)
    released = [m for m in drv if m["status"] == "released"]
    assert len(released) == 1 and "No input" in released[0]["message"]
    assert released[0]["report"]["finished"] is False
    assert all(m["t"] <= 125 for m in drv)  # nothing after the hand back
    # The car drives on (no longer held at standstill) and leaves the network
    later = [r for m in msgs if m["type"] == "vehicles" and m["t"] > 130 for r in m["rows"] if r[0] == "drv1"]
    assert later and max(r[4] for r in later) > 5.0
    assert not [m for m in msgs if m["type"] == "vehicles" and m["t"] == 320 and any(r[0] == "drv1" for r in m["rows"])]


def test_bad_start_is_reported_and_a_second_car_is_refused() -> None:
    plan = {20: [{"cmd": "drive_start", "from": "nowhere", "to": "J3top1"}],
            30: [{"cmd": "drive_start", "from": "bottom0J0", "to": "J3top1", "step_delay": 0},
                 {"cmd": "drive_start", "from": "left0J0", "to": "J3right1"}],
            40: [{"cmd": "drive", "mode": "warp"}, {"cmd": "unknown"}]}
    _, msgs = _live_run(plan, max_steps=60)
    drv = _driver_msgs(msgs)
    errors = [m for m in drv if m["status"] == "error"]
    assert errors[0]["vid"] == "" and "Unknown road" in errors[0]["message"]
    assert "already being driven" in errors[1]["message"]
    # The bad commands at 40 s change nothing; the run ends with the car still driven
    assert [m for m in drv if m["t"] == 45][0]["mode"] == "go"
    assert drv[-1]["status"] == "released" and "run ended" in drv[-1]["message"]


def test_glosa_advice_with_fixed_time_signals_and_followed_share() -> None:
    plan = {60: [{"cmd": "drive_start", "from": "bottom0J0", "to": "J3top1", "step_delay": 0}]}
    _, msgs = _live_run(plan, controller="fixed", scenario="navigation", max_steps=260, params=None)
    drv = [m for m in _driver_msgs(msgs) if m["status"] == "driving"]
    advice = {m["advice"]["advice"] for m in drv}
    assert advice & {"go", "slow", "stop"}, "fixed-time signals have predictable timing, so advice is given"
    slow = [m for m in drv if m["advice"]["advice"] == "slow"]
    for m in slow:
        assert 0 < m["advice"]["speed_kmh"] <= m["limit_kmh"]


def test_driven_car_is_not_part_of_the_run_results(tmp_path) -> None:
    from sim.analytics.metrics import parse_tripinfo, parse_tripinfo_emissions

    xml = tmp_path / "tripinfo.xml"
    xml.write_text(
        '<tripinfos>'
        '<tripinfo id="c1" vType="car" duration="100" waitingTime="10" timeLoss="20" routeLength="1000">'
        '<emissions CO2_abs="200000" fuel_abs="80000"/></tripinfo>'
        '<tripinfo id="drv1" vType="driven" duration="500" waitingTime="300" timeLoss="400" routeLength="1000">'
        '<emissions CO2_abs="900000" fuel_abs="400000"/></tripinfo>'
        '</tripinfos>', encoding="utf-8")
    m = parse_tripinfo(xml_path=xml, sim_duration=600, max_queue=0)
    assert m["avg_travel_time"] == 100 and m["avg_waiting_time"] == 10
    em = parse_tripinfo_emissions(xml)
    assert em["co2_g_per_km"] == 200.0


def test_follow_mode_drives_behind_the_car_ahead_and_reports_both_neighbours() -> None:
    """Car following (sim/car_following.py): the driven car sees the vehicle ahead and the one behind,
    gets a speed for following at a safe gap, and in "follow" mode drives at it (adaptive cruise)."""
    plan = {100: [{"cmd": "drive_start", "from": "bottom0J0", "to": "J3top1", "step_delay": 0}],
            102: [{"cmd": "drive", "mode": "follow"}]}
    result, msgs = _live_run(plan, scenario="congested", controller="adaptive")
    assert result["collisions"] == 0
    drv = [m for m in _driver_msgs(msgs) if m["status"] == "driving"]
    assert drv and all("ahead" in m and "behind" in m and "follow" in m for m in drv)
    assert all(m["mode"] == "follow" for m in drv if m["t"] > 103)
    with_lead = [m for m in drv if m["ahead"]]
    assert with_lead, "in congested traffic the driven car has a vehicle ahead"
    for m in with_lead:
        a, f = m["ahead"], m["follow"]
        assert a["id"] != m["vid"] and 0 <= a["gap_m"] <= 100 and a["speed_kmh"] >= 0
        assert f["state"] in ("keep", "slow", "brake", "stopped", "speed_up", "too_close")
        assert 0 <= f["speed_kmh"] <= m["limit_kmh"] + 0.5
    # a vehicle behind (when one is within 100 m) gets its own advice for following the driven car
    assert all(m["behind"]["follow"] and m["behind"]["gap_m"] <= 100 and m["behind"]["follow_speed_kmh"] <= m["limit_kmh"] + 0.5
               for m in drv if m["behind"])
    # The vehicles rows carry acceleration, the leader and the gap, and the follow advice
    vm = [m for m in msgs if m["type"] == "vehicles" and m["rows"]][-1]
    f = vm["fields"]
    rows = [dict(zip(f, r)) for r in vm["rows"]]
    led = [r for r in rows if r["leader"]]
    assert led and all(r["gap"] is not None and isinstance(r["acc"], float) for r in led)
    known = {r["id"] for r in rows}
    assert all(r["follow"] for r in led if r["leader"] in known)

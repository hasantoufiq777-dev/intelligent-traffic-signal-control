"""Live playback from the dashboard (sim/commands.py Playback): pause, resume and speed change only
wall-clock pacing, are confirmed on the protocol, and leave the run's results exactly the same."""

from __future__ import annotations

import contextlib
import io
import json
from typing import Any, Dict, List

from sim.commands import Playback
from sim.runner import run_simulation


class Script:
    """Command source: the n-th poll returns plan[n] (polls happen every simulated second, and every
    0.1 s while paused)."""

    def __init__(self, plan: Dict[int, List[Dict[str, Any]]]) -> None:
        self.plan = {k: list(v) for k, v in plan.items()}
        self.n = 0

    def poll(self) -> List[Dict[str, Any]]:
        self.n += 1
        return self.plan.pop(self.n, [])


def _playback(step_delay: float = 0.25):
    sent: List[Dict[str, Any]] = []
    return Playback(step_delay, sent.append, "r1"), sent


def test_playback_commands_are_applied_and_confirmed_others_pass_through_in_order() -> None:
    pb, sent = _playback()
    drive_a, drive_b = {"cmd": "drive", "mode": "go"}, {"cmd": "drive", "mode": "stop"}
    others = pb.split([drive_a, {"cmd": "pause"}, drive_b, {"cmd": "speed", "speed": 2}], t=5)
    assert others == [drive_a, drive_b]
    assert pb.paused and pb.speed == 2.0 and pb.step_delay == 0.125
    assert [(m["type"], m["paused"], m["speed"], m["t"]) for m in sent] == [("playback", True, 1.0, 5), ("playback", True, 2.0, 5)]
    pb.split([{"cmd": "resume"}], t=6)
    assert not pb.paused and sent[-1]["paused"] is False


def test_invalid_speeds_are_ignored_without_confirmation() -> None:
    pb, sent = _playback()
    for bad in (0, 3, -1, "fast", None, 1000):
        pb.split([{"cmd": "speed", "speed": bad}], t=1)
    assert pb.speed == 1.0 and pb.step_delay == 0.25 and sent == []


def test_waiting_while_paused_ends_on_resume_and_keeps_driver_commands_for_the_next_second() -> None:
    pb, _ = _playback()
    pb.split([{"cmd": "pause"}], t=10)
    drive = {"cmd": "drive", "mode": "ease", "speed": 5}
    reader = Script({2: [drive], 4: [{"cmd": "resume"}]})
    pb.wait_while_paused(reader, t=10, should_stop=lambda: False, tick=0.001)
    assert not pb.paused and reader.n == 4
    assert pb.split([], t=11) == [drive]  # held while paused, delivered with the next second


def test_waiting_while_paused_ends_when_the_run_is_stopped() -> None:
    pb, _ = _playback()
    pb.split([{"cmd": "pause"}], t=3)
    calls = {"n": 0}

    def stop() -> bool:
        calls["n"] += 1
        return calls["n"] > 3

    pb.wait_while_paused(Script({}), t=3, should_stop=stop, tick=0.001)
    assert pb.paused  # still paused, but the loop returned so the run can stop


def _run(plan: Dict[int, List[Dict[str, Any]]]):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        result = run_simulation("normal", "fixed", seed=1, live=True, driver=True, driver_commands=Script(plan),
                                max_steps=150, params={"network": "grid2x2"}, run_id="t_pb", step_delay=0.0)
    msgs = [json.loads(line) for line in out.getvalue().splitlines() if line.startswith("{")]
    return result, msgs


def test_pausing_and_changing_speed_leaves_the_results_unchanged() -> None:
    plain, plain_msgs = _run({})
    # pause at the 30th poll, three empty polls while paused, resume, then two speed changes
    played, msgs = _run({30: [{"cmd": "pause"}], 34: [{"cmd": "resume"}], 60: [{"cmd": "speed", "speed": 4}],
                         90: [{"cmd": "speed", "speed": 0.5}]})
    acks = [m for m in msgs if m["type"] == "playback"]
    assert [(a["paused"], a["speed"]) for a in acks] == [(True, 1.0), (False, 1.0), (False, 4.0), (False, 0.5)]
    assert acks[0]["t"] == acks[1]["t"]  # no simulated time passed while paused
    keys = ("avg_waiting_time", "avg_travel_time", "avg_time_loss", "throughput_veh_per_hr", "max_queue")
    assert {k: played[k] for k in keys} == {k: plain[k] for k in keys}
    # the same states, second by second
    states = lambda ms: [(m["t"], m["metrics"]) for m in ms if m["type"] == "state"]  # noqa: E731
    assert states(msgs) == states(plain_msgs)

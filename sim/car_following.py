"""Car following: what the vehicle ahead is doing and the speed to drive behind it.

Every vehicle knows the vehicle directly ahead in its lane (SUMO: traci.vehicle.getLeader): its
speed, its acceleration and the gap between them. From that it gets a target speed under a
constant time-gap policy, as adaptive cruise control (ACC) does:

    desired gap  s* = S0 + T * v                      (S0 = 2.5 m standstill gap, T = time gap)
    target speed v_t = v_lead + K_GAP * (gap - s*)  [+ K_ACC * a_lead when the leader's acceleration is known]

- ACC (radar only): the follower measures the leader's speed and the gap, T = 1.5 s.
- CACC (both vehicles have an OBU): the leader's acceleration also arrives by V2V beacon, so the
  follower reacts to a braking leader at once (feed-forward K_ACC * a_lead) and can keep a
  shorter time gap, T = 1.0 s.

Closing in on a slower leader, the target is also capped at the highest speed from which the
follower can still brake comfortably (B_COMFORT) to the leader's speed within the spare gap:
v <= sqrt(v_lead^2 + 2 * B_COMFORT * (gap - s*)).

The time to collision (TTC = gap / closing speed) flags a dangerous approach. The result is
advice for the dashboard and, in the "follow" driving mode, the speed the driven car is given
(sim/driver.py). SUMO's own car-following model still keeps every vehicle safe.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Optional

LOOKAHEAD_M = 100.0  # leaders further than this are ignored (free road)
STANDSTILL_GAP_M = 2.5
TIME_GAP_ACC_S = 1.5
TIME_GAP_CACC_S = 1.0
K_GAP = 0.25  # 1/s: how fast the gap error is closed
K_ACC = 1.0  # s: feed-forward of the leader's acceleration (CACC only)
B_COMFORT = 1.5  # m/s2: comfortable braking
HARD_BRAKE = -2.5  # m/s2: the leader is braking hard
TTC_WARN_S = 4.0  # closing in on the leader this fast is dangerous
TTC_STOPPED_S = 8.0  # a stopped vehicle ahead reached within this: time to stop behind it
STOPPED = 0.5  # m/s
SPEED_BAND = 1.0  # m/s: a target within this of the current speed means "keep your speed"

STATES = ("free", "keep", "slow", "brake", "stopped", "speed_up", "too_close")


def follow_advice(speed: float, limit: float, leader_speed: Optional[float] = None,
                  leader_acc: Optional[float] = None, gap: Optional[float] = None,
                  cooperative: bool = False) -> Dict[str, Any]:
    """Advice for a vehicle at `speed` (m/s) on a road with `limit` (m/s) behind a leader.

    leader_speed, gap: None when no vehicle is within LOOKAHEAD_M (free road).
    leader_acc: used only when `cooperative` (received by V2V); a radar cannot measure it.
    Returns {state, speed (target m/s), desired_gap (m), time_gap (s), ttc (s), mode}.
    """
    speed, limit = max(0.0, float(speed)), max(0.0, float(limit))
    if leader_speed is None or gap is None or gap > LOOKAHEAD_M:
        return {"state": "free", "speed": round(limit, 1), "desired_gap": None, "time_gap": None, "ttc": None,
                "mode": None}
    leader_speed, gap = max(0.0, float(leader_speed)), max(0.0, float(gap))
    acc = float(leader_acc) if cooperative and leader_acc is not None else 0.0
    t_gap = TIME_GAP_CACC_S if cooperative else TIME_GAP_ACC_S
    desired = STANDSTILL_GAP_M + t_gap * speed
    target = leader_speed + K_GAP * (gap - desired) + K_ACC * acc
    if leader_speed < speed:  # closing in: still able to brake gently to the leader's speed in the spare gap
        target = min(target, math.sqrt(leader_speed ** 2 + 2 * B_COMFORT * max(0.0, gap - desired)))
    if gap <= STANDSTILL_GAP_M:
        target = min(target, leader_speed)  # never close in further at the standstill gap
    target = min(max(0.0, target), limit)
    closing = speed - leader_speed
    ttc = gap / closing if closing > 0.1 else None
    time_gap = gap / speed if speed > STOPPED else None

    if leader_speed < STOPPED and (gap < max(30.0, desired * 2) or (ttc is not None and ttc < TTC_STOPPED_S)):
        state = "stopped"
    elif (cooperative and acc <= HARD_BRAKE) or (ttc is not None and ttc < TTC_WARN_S):
        state = "brake"
    elif gap < desired * 0.7:
        state = "too_close"
    elif target < speed - SPEED_BAND:
        state = "slow"
    elif target > speed + SPEED_BAND:
        state = "speed_up"
    else:
        state = "keep"
    return {"state": state, "speed": round(target, 1), "desired_gap": round(desired, 1),
            "time_gap": None if time_gap is None else round(time_gap, 1),
            "ttc": None if ttc is None else round(ttc, 1), "mode": "cacc" if cooperative else "acc"}

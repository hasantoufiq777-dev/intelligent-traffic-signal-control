"""Dynamic, congestion-aware traffic signal controller (the default mode).

A heuristic controller inspired by the state used in reinforcement-learning signal control
(queue, waiting time, density, flow and downstream congestion), without any learning. Every
simulated second it measures each incoming road (sim/controllers/traffic_monitor.py) and:

1. scores each road (priority score, higher = more urgent):

       S = (w_q·Q + w_rho·10·rho/rho_jam + w_f·F) · f_down  +  w_w·W/10

   Q = waiting vehicles, rho = density (veh/km/lane), rho_jam = 133 veh/km/lane, F = arrivals per
   minute, W = mean waiting time (s), f_down = 1 − w_down·(downstream occupancy), or
   blocked_factor when the downstream road is blocked. A closed road scores 0.

2. gives green to the phase (North–South or East–West) with the higher total score, for a
   duration that fits its demand:

       G = clamp(startup_lost + headway · max over its roads of (Q_road / lanes), min_green, max_green)

   capped by the space on the downstream road (only vehicles that fit are released), and the
   minimum green when the downstream road is blocked.

3. keeps watching during the green: it extends the green (up to max_green) while the queue is
   still discharging and this phase is still the busier one; it ends it early, after min_green,
   when the road has emptied (no stopped car and none within gap_dist_m of the stop line), when
   its downstream road becomes blocked, or when a car on red has waited fairness_wait_s.

4. when the green time is up, switches only if the other phase has vehicles; otherwise the
   current green continues (no green for an empty road).

Every switch goes through yellow (yellow_time) and all-red (all_red_time) in BaseController, so
conflicting movements are never green together. Each decision is logged with a short reason,
shown on the dashboard's Signals page.
"""

from __future__ import annotations

import math
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Tuple

from sim.controllers.base import BaseController
from sim.controllers.traffic_monitor import JAM_DENSITY, ApproachStats, TrafficMonitor
from sim.sensing import StateProvider

GROUPS = {"NS": ("N", "S"), "EW": ("E", "W")}
GROUP_NAME = {"NS": "North–South", "EW": "East–West"}
DIR_NAME = {"N": "North", "S": "South", "E": "East", "W": "West"}
LOG_SIZE = 8


def other_group(g: str) -> str:
    return "EW" if g == "NS" else "NS"


class DynamicController(BaseController):
    """Congestion-aware signal control: phase choice and green length from live road measures."""

    def __init__(self, tls_id: str, params: Optional[Dict[str, Any]] = None,
                 provider: Optional[StateProvider] = None, sumo_api: Any = None) -> None:
        super().__init__(tls_id, params=params, provider=provider, sumo_api=sumo_api)
        p = self.params
        self.min_green = float(p.get("min_green", 5.0))
        self.max_green = float(p.get("max_green", 45.0))
        self.decision_interval = max(1.0, float(p.get("decision_interval", 1.0)))
        self.startup_lost_s = float(p.get("startup_lost_s", 2.0))
        self.headway_s = float(p.get("headway_s", 2.0))
        self.fairness_wait_s = float(p.get("fairness_wait_s", 40.0))
        self.w_queue = float(p.get("w_queue", 1.0))
        self.w_wait = float(p.get("w_wait", 0.5))
        self.w_density = float(p.get("w_density", 0.5))
        self.w_flow = float(p.get("w_flow", 0.2))
        self.w_down = float(p.get("w_down", 0.6))
        self.blocked_factor = float(p.get("blocked_factor", 0.2))
        self.light_max_s = float(p.get("light_max_s", 15.0))
        self.moderate_max_s = float(p.get("moderate_max_s", 30.0))

        link_dirs = list(getattr(self, "_link_directions", []) or [])
        self.monitor = TrafficMonitor(tls_id, self.sumo_api, link_dirs, provider=self.provider, params=p)

        self.stats: Dict[str, ApproachStats] = {}
        self.scores: Dict[str, float] = {}
        self.group_scores: Dict[str, float] = {"NS": 0.0, "EW": 0.0}
        self.elapsed = 0.0  # seconds of the current green
        self.planned: Optional[float] = None  # planned length of the current green (s)
        self.tier = ""
        self.extended = False
        self._tick = 0.0
        self.t = 0.0
        self.reason = ""
        self.log: Deque[Dict[str, Any]] = deque(maxlen=LOG_SIZE)
        self._next_reason: Optional[str] = None  # why the phase being switched to was chosen

        self.current_phase = self.PHASE_NS_GREEN
        if self.sumo_api:
            try:
                self._apply_phase(self.PHASE_NS_GREEN)
            except Exception:
                pass

    # ------------------------------------------------------------------ measures and scores
    def _measure(self, t: float) -> None:
        closed = set()
        incidents: Dict[str, str] = {}
        if self.provider is not None:
            try:
                closed = set(self.provider.get_blocked_approaches(self.tls_id))
                incidents = dict(self.provider.get_incidents())
            except Exception:
                pass
        self.stats = self.monitor.observe(t, closed_approaches=closed, incidents=incidents)
        self.scores = {d: self.score(s) for d, s in self.stats.items()}
        self.group_scores = {g: round(sum(self.scores.get(d, 0.0) for d in dirs), 2) for g, dirs in GROUPS.items()}

    def score(self, s: ApproachStats) -> float:
        """Priority score of one road (see the module docstring)."""
        if s.closed:
            return 0.0
        rho = min(1.0, s.density / JAM_DENSITY)
        demand = self.w_queue * s.waiting + self.w_density * 10.0 * rho + self.w_flow * s.flow_vpm
        f_down = self.blocked_factor if s.down_status == "blocked" else max(self.blocked_factor, 1.0 - self.w_down * s.down_occ)
        return round(demand * f_down + self.w_wait * s.mean_wait_s / 10.0, 2)

    def _roads(self, g: str) -> List[ApproachStats]:
        return [self.stats[d] for d in GROUPS[g] if d in self.stats and not self.stats[d].closed]

    def _has_demand(self, g: str) -> bool:
        """Vehicles on any road of the phase (waiting or approaching)."""
        return any(s.vehicles > 0 for s in self._roads(g))

    def _waiting(self, g: str) -> float:
        return sum(s.waiting for s in self._roads(g))

    def _max_wait(self, g: str) -> float:
        return max((s.max_wait_s for s in self._roads(g)), default=0.0)

    def _label(self, g: str) -> str:
        roads = [r for s in self._roads(g) for r in s.roads]
        return f"{GROUP_NAME[g]} ({', '.join(roads)})" if roads else GROUP_NAME[g]

    def green_need(self, g: str) -> Tuple[float, Optional[str]]:
        """Green time the phase's queues need (s), and a note when downstream space limits it."""
        need = 0.0
        note: Optional[str] = None
        roads = self._roads(g)
        with_queue = [s for s in roads if s.waiting > 0]
        if with_queue and all(s.down_status == "blocked" for s in with_queue):
            s = with_queue[0]
            return 0.0, f"{DIR_NAME[s.direction]} road {', '.join(s.roads)} green duration reduced: downstream road {s.down_worst} blocked"
        uncapped = 0.0
        limited: Optional[ApproachStats] = None
        for s in roads:
            if s.waiting <= 0:
                continue
            full = self.startup_lost_s + self.headway_s * math.ceil(s.waiting / max(1, s.lanes))
            release = 0.0 if s.down_status == "blocked" else min(s.waiting, s.down_space)
            this = self.startup_lost_s + self.headway_s * math.ceil(release / max(1, s.lanes)) if release > 0 else 0.0
            if this < full:
                limited = s
            uncapped = max(uncapped, full)
            need = max(need, this)
        # Say so only when the downstream space really shortens the green
        if limited is not None and min(need, self.max_green) < min(uncapped, self.max_green) - 0.5:
            s = limited
            what = "blocked" if s.down_status == "blocked" else f"congested (room for {int(s.down_space)} vehicles)"
            note = (f"{DIR_NAME[s.direction]} road {', '.join(s.roads)} green duration reduced: "
                    f"downstream road {s.down_worst} {what}")
        return need, note

    def _plan(self, g: str) -> None:
        need, note = self.green_need(g)
        self.planned = min(self.max_green, max(self.min_green, need))
        self.tier = ("short" if self.planned <= self.light_max_s
                     else "medium" if self.planned <= self.moderate_max_s else "long")
        self.extended = False
        why = self._next_reason or f"{self._label(g)} green"
        self._record(f"{why}. Green {self.planned:.0f} s ({self.tier}: {self._waiting(g):.0f} waiting).")
        if note:
            self._record(note + ".")
        self._next_reason = None

    def _record(self, text: str) -> None:
        self.reason = text
        self.log.append({"t": int(self.t), "text": text})

    def _switch(self, end_reason: str, choose_reason: str) -> None:
        self._record(end_reason)
        self._next_reason = choose_reason
        target = self.PHASE_EW_GREEN if self.current_phase == self.PHASE_NS_GREEN else self.PHASE_NS_GREEN
        self.set_phase_safe(target)
        self.elapsed = 0.0
        self.planned = None
        self._tick = 0.0

    def _choose_reason(self, nxt: str) -> str:
        """Why the other phase gets the next green."""
        cur = other_group(nxt)
        if self._max_wait(nxt) >= self.fairness_wait_s:
            return f"{self._label(nxt)} prioritised: longest waiting time ({self._max_wait(nxt):.0f} s)"
        if self.group_scores[nxt] >= self.group_scores[cur]:
            return (f"{self._label(nxt)} selected: highest congestion score "
                    f"({self.group_scores[nxt]:.1f} vs {self.group_scores[cur]:.1f})")
        return (f"{self._label(nxt)} selected: vehicles waiting "
                f"(score {self.group_scores[nxt]:.1f}), {GROUP_NAME[cur]} served")

    # ------------------------------------------------------------------ control loop
    def serving(self) -> Optional[str]:
        if self.is_in_transition():
            return None
        return {self.PHASE_NS_GREEN: "NS", self.PHASE_EW_GREEN: "EW"}.get(self.current_phase)

    def step(self, sim_time: float) -> None:
        self.t = float(sim_time)
        self.update_transitions(1.0)
        self._measure(sim_time)
        g = self.serving()
        if g is None:
            self.elapsed = 0.0
            return
        if self.planned is None:
            self._plan(g)
        self.elapsed += 1.0
        self._tick += 1.0
        if self._tick < self.decision_interval:
            return
        self._tick = 0.0
        self._decide(g)

    def _decide(self, g: str) -> None:
        nxt = other_group(g)
        if self.elapsed < self.min_green:
            return
        other_demand = self._has_demand(nxt)
        roads = self._roads(g)

        if other_demand and self._max_wait(nxt) >= self.fairness_wait_s:
            self._switch(f"{GROUP_NAME[g]} ended after {self.elapsed:.0f} s: a car on red has waited "
                         f"{self._max_wait(nxt):.0f} s", self._choose_reason(nxt))
            return
        if other_demand and self._waiting(nxt) > 0:
            empty = all(s.waiting <= 0 and s.near_stopline <= 0 for s in roads)
            if empty:
                self._switch(f"{GROUP_NAME[g]} ended early after {self.elapsed:.0f} s: no more vehicles waiting",
                             self._choose_reason(nxt))
                return
            queued = [s for s in roads if s.waiting > 0]
            other_free = any(s.down_status != "blocked" and s.waiting > 0 for s in self._roads(nxt))
            if queued and all(s.down_status == "blocked" for s in queued) and other_free:
                s = queued[0]
                self._switch(f"{GROUP_NAME[g]} ended after {self.elapsed:.0f} s: downstream road "
                             f"{s.down_worst} congested", self._choose_reason(nxt))
                return

        # Extend while the queue still needs time and this phase is still the busier one
        if self.planned is not None and self.planned < self.max_green and self.elapsed >= self.planned - 1.0:
            need, _ = self.green_need(g)
            if self.group_scores[g] < self.group_scores[nxt]:
                need = 0.0  # the queue alone no longer earns more green: the other phase is busier
            if any(s.near_stopline > 0 and s.down_status != "blocked" for s in roads):
                # Vehicles close to the stop line: let them through rather than stop them (gap
                # extension); the fairness limit and max_green still bound the green
                need = max(need, self.headway_s)
            if need > 0:
                new_plan = min(self.max_green, self.elapsed + need)
                if new_plan > self.planned + 0.5:
                    self.planned = new_plan
                    if not self.extended:
                        self._record(f"{GROUP_NAME[g]} extended to {self.planned:.0f} s: queue still discharging.")
                        self.extended = True

        limit = self.max_green if self.planned is None else self.planned
        if self.elapsed >= limit:
            if other_demand:
                end = (f"{GROUP_NAME[g]} ended: maximum green {self.max_green:.0f} s reached"
                       if self.elapsed >= self.max_green else f"{GROUP_NAME[g]} green time used ({self.elapsed:.0f} s)")
                self._switch(end, self._choose_reason(nxt))
            elif not self.reason.startswith(f"{GROUP_NAME[nxt]} skipped"):
                self._record(f"{GROUP_NAME[nxt]} skipped: no waiting vehicles; {GROUP_NAME[g]} stays green.")

    # ------------------------------------------------------------------ SPaT and live view
    def _green_remaining_bounds(self) -> Tuple[Optional[float], Optional[float]]:
        """The green can end at the first decision after min_green, and ends by max_green unless
        the other phase is empty (then within one decision after a vehicle arrives there)."""
        return max(0.0, self.min_green - self.elapsed), max(0.0, self.max_green - self.elapsed) + self.decision_interval

    def _green_length_bounds(self, group: str) -> Tuple[Optional[float], Optional[float]]:
        return self.min_green, self.max_green + self.decision_interval

    def _target_group(self) -> Optional[str]:
        target = self.pending_target_phase if self.pending_target_phase is not None else self.target_phase
        return {self.PHASE_NS_GREEN: "NS", self.PHASE_EW_GREEN: "EW"}.get(target)

    def get_live_info(self) -> Dict[str, Any]:
        """Why the controller does what it does, for the dashboard (one junction)."""
        g = self.serving()
        signals = self.get_approach_signals()
        roads = {}
        for d, s in self.stats.items():
            roads[d] = {
                "roads": s.roads, "waiting": round(s.waiting, 1), "queue_m": round(s.queue_m, 1),
                "density": round(s.density, 1), "mean_wait_s": round(s.mean_wait_s, 1),
                "max_wait_s": round(s.max_wait_s, 1), "flow_vpm": round(s.flow_vpm, 1),
                "down_roads": s.down_roads or [], "down_worst": s.down_worst, "down_occ": round(s.down_occ, 2),
                "down_status": "closed" if s.closed else s.down_status,
                "score": self.scores.get(d, 0.0), "signal": signals.get(d, "r"),
            }
        planned = self.planned if g is not None else None
        return {
            "serving": g,
            "transition": self.transition_state if self.is_in_transition() else None,
            "next": self._target_group() if g is None else None,
            "elapsed_s": round(self.elapsed, 0) if g is not None else None,
            "planned_s": None if planned is None else round(planned, 0),
            "remaining_s": None if planned is None else round(max(0.0, planned - self.elapsed), 0),
            "tier": self.tier if g is not None else None,
            "group_scores": self.group_scores,
            "roads": roads,
            "reason": self.reason,
            "log": list(self.log),
            "params": {"min_green": self.min_green, "max_green": self.max_green,
                       "yellow": self.yellow_time, "all_red": self.all_red_time},
        }

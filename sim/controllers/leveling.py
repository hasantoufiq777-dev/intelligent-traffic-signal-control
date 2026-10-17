"""Queue-levelling signal control: bring the longest queue down to the next one, then repeat.

At a four-way junction:

1. The road with the most waiting cars gets the green, alone.
2. It keeps the green only until its queue is down to the second-longest queue.
3. The roads now level come down to the third-longest queue, one after the other, then to the fourth,
   and finally to zero. Queues are measured again at every green, so the order follows the traffic.
   Two level roads that face each other (north and south, or east and west) go together.
4. When no other road has a queue, the green stays until the cars that are left have been freed.
5. Free road: when nobody is waiting, the green goes ahead of the first car that will arrive, so it
   passes without slowing down. A green is not held on an empty road while a car waits elsewhere.

Limits: a green lasts min_green to max_green; a car that has waited fairness_wait_s on red gets the
next green; a road whose exit is blocked does not hold the green while another road can move. With
partial (VANET) sensing a road can look empty while unconnected cars wait, so max_green is a hard limit
and no road stays red longer than max_red_s.

Clearance (dynamic_clearance), so every change stays safe without fixed times:

    yellow  = ceil(max(yellow_min, t + v / (2a)))     kinematic yellow for the road's speed limit
    all-red = from all_red_min until the junction is empty, at most all_red_max

The "junction empty" check uses an exact junction detector (SUMO), never VANET beacons: a car without a
radio inside the junction must still hold the next green.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Set, Tuple

from sim.controllers.dynamic import DynamicController
from sim.controllers.traffic_monitor import ApproachStats
from sim.sensing import StateProvider

SINGLE_GREEN = {"N": 10, "S": 11, "E": 12, "W": 13}
SINGLE_YELLOW = {d: code + 10 for d, code in SINGLE_GREEN.items()}
OPPOSITE = {"N": "S", "S": "N", "E": "W", "W": "E"}
PAIR = {"N": "NS", "S": "NS", "E": "EW", "W": "EW"}
NAME = {"N": "North", "S": "South", "E": "East", "W": "West", "NS": "North + South", "EW": "East + West"}


# Roads inside a serving code: 'NS' -> ('N', 'S'), 'W' -> ('W',).
def members(serving: str) -> Tuple[str, ...]:
    return tuple(serving)


# Queue levelling (`level`, `level_vanet`, the default): reuses DynamicController's sensing and step
# loop, but replaces how the next green is chosen and allows one road to be green alone.
class LevelingController(DynamicController):
    """Longest queue first, down to the next queue; level roads repeat; the rest is cleared."""

    # Add the levelling settings: the fairness limit and how close facing roads must be to go together.
    def __init__(self, tls_id: str, params: Optional[Dict[str, Any]] = None,
                 provider: Optional[StateProvider] = None, sumo_api: Any = None) -> None:
        super().__init__(tls_id, params=params, provider=provider, sumo_api=sumo_api)
        self.fairness_wait_s = float(self.params.get("fairness_wait_s", 60.0))
        self.level_tolerance = float(self.params.get("level_tolerance", 1.0))
        self.target_queue = 0.0
        self.tier_tolerance = float(self.params.get("tier_tolerance", 1.0))  # queues this close are one level
        self.free_flow = bool(self.params.get("free_flow", False))
        self._stage_target: Optional[float] = None  # the queue this green brings its road down to
        self._stage_peers: Set[str] = set()  # roads level with it when the green started
        # Partial sensing: a road that looks empty may hold unconnected cars (not with a stop-line detector)
        self.partial_sensing = (getattr(self.provider, "name", "") == "vanet"
                                and not bool(self.params.get("stopline_detector", False)))
        self.max_red_s = float(self.params.get("max_red_s", 60.0))
        self.last_green: Dict[str, float] = {d: 0.0 for d in SINGLE_GREEN}
        # Clearance times computed from the road and the junction
        self.dynamic_clearance = bool(self.params.get("dynamic_clearance", False))
        self.reaction_s = float(self.params.get("yellow_reaction_s", 1.0))
        self.comfort_decel = float(self.params.get("yellow_decel", 3.05))
        self.yellow_min_s = float(self.params.get("yellow_min_s", 3.0))
        self.all_red_min_s = float(self.params.get("all_red_min_s", 1.0))
        self.all_red_max_s = max(float(self.params.get("all_red_max_s", 4.0)), self.all_red_time)
        self._all_red_elapsed = 0.0
        self.last_all_red_s: Optional[float] = None
        self.last_yellow_s = self.yellow_time
        self._cleared_roads: Tuple[str, ...] = ()
        self._junction_lanes: Optional[List[str]] = None
        self._yellow_by_road = self._road_yellow_times() if self.dynamic_clearance else {}

    # ------------------------------------------------------------------ clearance times
    # Kinematic yellow per road: reaction time + braking time from the speed limit, rounded up to the
    # 1 s simulation step so it is never shorter than the formula.
    def _road_yellow_times(self) -> Dict[str, float]:
        out: Dict[str, float] = {}
        if not self.sumo_api:
            return out
        for d, lanes in self.monitor.in_lanes.items():
            try:
                v = max((float(self.sumo_api.lane.getMaxSpeed(lane)) for lane in lanes), default=0.0)
            except Exception:
                continue
            if v > 0:
                needed = self.reaction_s + v / (2.0 * self.comfort_decel)
                out[d] = float(math.ceil(max(self.yellow_min_s, needed) - 1e-9))
        return out

    # Yellow for the road(s) now ending: the longest one, so every served road is covered.
    def _yellow_for(self, road: Optional[str]) -> float:
        if not self.dynamic_clearance or not road:
            return self.yellow_time
        return max((self._yellow_by_road.get(d, self.yellow_time) for d in members(road)), default=self.yellow_time)

    # Junction detector (exact SUMO data, deliberately not VANET): no vehicle inside the junction, no
    # tail still over it, and no car on the road just stopped that is too close to brake. If the check
    # cannot be made, the junction counts as occupied.
    def _junction_clear(self) -> bool:
        api = self.sumo_api
        if api is None:
            return True
        try:
            if self._junction_lanes is None:
                prefixes = {str(link[2]).rsplit("_", 2)[0] + "_" for links in api.trafficlight.getControlledLinks(self.tls_id)
                            for link in links if len(link) > 2 and str(link[2]).startswith(":")}
                self._junction_lanes = [lane for lane in api.lane.getIDList() if any(lane.startswith(x) for x in prefixes)]
            if any(api.lane.getLastStepVehicleNumber(lane) > 0 for lane in self._junction_lanes):
                return False
            for lane in self.monitor.out_lane_set:
                for vid in api.lane.getLastStepVehicleIDs(lane):
                    if float(api.vehicle.getLanePosition(vid)) <= float(api.vehicle.getLength(vid)) + 0.5:
                        return False
            for d in self._cleared_roads:
                for lane in self.monitor.in_lanes.get(d, []):
                    length = self.monitor.lane_len.get(lane, 100.0)
                    for vid in api.lane.getLastStepVehicleIDs(lane):
                        v = float(api.vehicle.getSpeed(vid))
                        if v > 1.0 and length - float(api.vehicle.getLanePosition(vid)) < v * v / (2.0 * float(api.vehicle.getDecel(vid))):
                            return False
        except Exception:
            return False
        return True

    # With dynamic clearance, all-red ends as soon as the junction is empty (after the minimum), and at
    # the maximum at the latest. Without it, the base class's fixed times apply.
    def update_transitions(self, dt: float = 1.0) -> None:
        if not self.dynamic_clearance or self.transition_state == "STABLE":
            super().update_transitions(dt)
            return
        if self.transition_state == "ALL_RED":
            self._all_red_elapsed += dt
            done = self._all_red_elapsed >= self.all_red_max_s or (
                self._all_red_elapsed >= self.all_red_min_s and self._junction_clear())
            if done:
                self.last_all_red_s = self._all_red_elapsed
                self.transition_timer = dt
            else:
                self.transition_timer = dt + self.all_red_max_s - self._all_red_elapsed
            super().update_transitions(dt)
            return
        super().update_transitions(dt)
        if self.transition_state == "ALL_RED":
            self._all_red_elapsed = 0.0
            self.transition_timer = self.all_red_max_s

    # ------------------------------------------------------------------ phases
    # Serving name -> signal phase code ('NS', 'EW', or a single road).
    def _code(self, serving: str) -> int:
        if serving == "NS":
            return self.PHASE_NS_GREEN
        if serving == "EW":
            return self.PHASE_EW_GREEN
        return SINGLE_GREEN[serving]

    # Signal phase code -> serving name; None for yellow codes.
    def _serving_of(self, code: int) -> Optional[str]:
        if code == self.PHASE_NS_GREEN:
            return "NS"
        if code == self.PHASE_EW_GREEN:
            return "EW"
        return next((d for d, c in SINGLE_GREEN.items() if c == code), None)

    # Road or facing pair that has the green now; None during yellow and all-red.
    def serving(self) -> Optional[str]:
        return None if self.is_in_transition() else self._serving_of(self.current_phase)

    # Road or pair that the current yellow/all-red is changing to.
    def _target_group(self) -> Optional[str]:
        target = self.pending_target_phase if self.pending_target_phase is not None else self.target_phase
        return self._serving_of(target)

    # Use the controller's own code: single-road phases do not exist in SUMO's stored program.
    def get_current_phase(self) -> int:
        return self.current_phase

    # Phase name for the dashboard, including N_GREEN, E_YELLOW and the other single-road phases.
    def get_phase_name(self) -> str:
        if self.transition_state == "ALL_RED":
            return "ALL_RED"
        for d, code in SINGLE_GREEN.items():
            if self.current_phase == code:
                return f"{d}_GREEN"
            if self.current_phase == SINGLE_YELLOW[d]:
                return f"{d}_YELLOW"
        return super().get_phase_name()

    # Build SUMO's signal string: one road gets `char`, every other link stays red.
    def _single_state(self, d: str, char: str) -> str:
        return "".join(char if x == d else "r" for x in getattr(self, "_link_directions", []) or [])

    # Send a phase to SUMO. Single-road phases are set directly; NS and EW use the network's program.
    def _apply_phase(self, phase_index: int) -> None:
        road = next((d for d, c in SINGLE_GREEN.items() if c == phase_index), None)
        char = "G"
        if road is None:
            road = next((d for d, c in SINGLE_YELLOW.items() if c == phase_index), None)
            char = "y"
        if road is None:
            super()._apply_phase(phase_index)
            return
        self.current_phase = phase_index
        if self.sumo_api:
            state = self._single_state(road, char)
            if state:
                self.sumo_api.trafficlight.setRedYellowGreenState(self.tls_id, state)

    # Start a safe change: the road(s) now green go yellow, then the base class applies all-red and
    # the target green. A green is never switched straight to another green.
    def set_phase_safe(self, target_phase: int) -> bool:
        if target_phase == self.current_phase and self.transition_state == "STABLE":
            return True
        if self.transition_state != "STABLE":
            self.pending_target_phase = target_phase
            return False
        self.target_phase = target_phase
        self.pending_target_phase = None
        road = self._serving_of(self.current_phase)
        if road == "NS":
            yellow = self.PHASE_NS_YELLOW
        elif road == "EW":
            yellow = self.PHASE_EW_YELLOW
        else:
            yellow = SINGLE_YELLOW[road]
        self._cleared_roads = members(road or "")
        self.last_yellow_s = self._yellow_for(road)
        self.transition_state = "YELLOW"
        self.transition_timer = self.last_yellow_s
        self._apply_phase(yellow)
        return False

    # ------------------------------------------------------------------ measures
    # In this mode a road's rank is simply its number of waiting cars (0 if closed).
    def score(self, s: ApproachStats) -> float:
        """The rank on the dashboard is simply the number of waiting cars."""
        return 0.0 if s.closed else round(s.waiting, 1)

    # Open roads inside the current road or pair.
    def _roads(self, g: str) -> List[ApproachStats]:
        return [self.stats[d] for d in members(g) if d in self.stats and not self.stats[d].closed]

    # All roads not closed by an incident.
    def _open(self) -> List[ApproachStats]:
        return [s for s in self.stats.values() if not s.closed]

    # Cars left to serve. Started cars no longer count as waiting, so cars still at the line are
    # included; otherwise the green would end while its queue is still moving away.
    @staticmethod
    def _still_to_clear(s: ApproachStats) -> float:
        """A served queue: stopped cars, or cars that have started but are still at the line."""
        return max(s.waiting, s.near_stopline)

    # Choose the next green: longest queue, then longest wait, preferring a free road ahead.
    # If the facing road is level with it, both go together because they do not conflict.
    def _next_serving(self, current: Set[str], forced: Optional[ApproachStats] = None) -> Optional[str]:
        """The road (or facing pair) that goes next: longest queue, then longest wait."""
        others = [s for s in self._open() if s.direction not in current and (s.waiting > 0 or s.vehicles > 0)]
        if not others and forced is None:
            return None
        free = [s for s in others if s.down_status != "blocked" and s.waiting > 0]
        pool = free or [s for s in others if s.waiting > 0] or others
        lead = forced or max(pool, key=lambda s: (s.waiting, s.max_wait_s, s.vehicles))
        facing = self.stats.get(OPPOSITE[lead.direction])
        if (facing is not None and not facing.closed and facing.waiting > 0
                and facing.waiting >= lead.waiting - self.level_tolerance):
            return PAIR[lead.direction]
        return lead.direction

    # Log why this green ended, remember why the next was chosen, and start the safe change.
    def _switch_to(self, nxt: str, end: str, why: str) -> None:
        self._record(end)
        self._next_reason = why
        self.set_phase_safe(self._code(nxt))
        self.elapsed = 0.0
        self.planned = None
        self._commit = None
        self._stage_target = None
        self._stage_peers = set()
        self._tick = 0.0

    # ------------------------------------------------------------------ announced changes (eco runs)
    # The road or pair that a locked green hands over to.
    def _commit_target(self, g: str) -> Optional[str]:
        current = set(members(g))
        if not any(s.waiting > 0 or s.vehicles > 0 for s in self._open() if s.direction not in current):
            return None
        return self._next_serving(current)

    # The announced moment has arrived. If the chosen road was closed meanwhile, choose again.
    def _commit_execute(self, g: str, target: str) -> None:
        current = set(members(g))
        if any(d not in self.stats or self.stats[d].closed for d in members(target)):
            target = self._next_serving(current)
            if target is None:
                self._commit = None
                return
        self._switch_to(target, f"{NAME[g]} ended as announced after {self.elapsed:.0f} s",
                        f"{NAME[target]} next: announced in advance")

    # ------------------------------------------------------------------ decisions
    # The levelling numbers: this green's cars to clear, the longest other queue, and its lane count.
    def _targets(self, g: str) -> Tuple[float, float, int]:
        served = self._roads(g)
        others = [s for s in self._open() if s.direction not in members(g)]
        mine = max((self._still_to_clear(s) for s in served), default=0.0)
        target = max((s.waiting for s in others), default=0.0) if self._stage_target is None else self._stage_target
        lanes = max((s.lanes for s in served), default=1)
        return mine, target, lanes

    # The level this green must reach: the next lower queue. Roads already level with it are peers and
    # come down together; if another road is clearly longer, this green only goes down to that road.
    def _set_stage(self, g: str) -> None:
        mine = max((s.waiting for s in self._roads(g)), default=0.0)
        others = [s for s in self._open() if s.direction not in members(g)]
        longest = max((s.waiting for s in others), default=0.0)
        if longest > mine + self.tier_tolerance:
            self._stage_target, self._stage_peers = longest, set()
            return
        self._stage_peers = {s.direction for s in others if s.waiting >= mine - self.tier_tolerance and s.waiting > 0}
        self._stage_target = max((s.waiting for s in others if s.direction not in self._stage_peers), default=0.0)

    # Nobody is waiting: serve whichever car reaches its stop line first, before it has to brake.
    def _free_road(self, g: str) -> None:
        current = set(members(g))
        served = [s.approach_eta_s for s in self._roads(g) if s.approach_eta_s is not None]
        coming = [s for s in self._open() if s.direction not in current and s.approach_eta_s is not None]
        if not coming:
            if not served and not self.reason.startswith("All clear"):
                self._record(f"All clear: {NAME[g]} stays green until a car approaches.")
            return
        first = min(coming, key=lambda s: s.approach_eta_s)
        if (served and min(served) <= first.approach_eta_s + 1.0) or any(s.near_stopline > 0 for s in self._roads(g)):
            return  # the green road's own car goes first
        facing = self.stats.get(OPPOSITE[first.direction])
        together = facing is not None and not facing.closed and facing.approach_eta_s is not None
        nxt = PAIR[first.direction] if together else first.direction
        self._switch_to(nxt, f"{NAME[g]} is free", f"{NAME[nxt]} free road: green before its car arrives "
                                                  f"({first.approach_eta_s:.0f} s away)")

    # Run when a green starts: estimate how long it takes to come down to the next queue, and log the goal.
    def _plan(self, g: str) -> None:
        self._set_stage(g)
        mine, target, lanes = self._targets(g)
        self.target_queue = target
        need = self.startup_lost_s + self.headway_s * math.ceil(max(0.0, mine - target) / max(1, lanes))
        self.planned = min(self.max_green, max(self.min_green, need))
        self.tier = "short" if self.planned <= self.light_max_s else "medium" if self.planned <= self.moderate_max_s else "long"
        why = self._next_reason or f"{NAME[g]} green"
        goal = f"until its queue is down to {target:.0f}" if target > 0 else "until the cars left are freed"
        self._record(f"{why}. Green {goal} ({mine:.0f} to clear).")
        self._next_reason = None

    # The levelling rule, checked each second after the minimum green, in priority order:
    # 1) a 60 s wait, 2) maximum green, 3) blocked road ahead, 4) queue is down to the next queue,
    # 5) everything is cleared. If none applies, this road keeps the green.
    def _decide(self, g: str) -> None:
        for d in members(g):
            self.last_green[d] = self.t
        mine, target, lanes = self._targets(g)
        self.target_queue = target
        self.planned = min(self.max_green, max(self.min_green, self.elapsed + self.headway_s
                                               * math.ceil(max(0.0, mine - target) / max(1, lanes))))
        current = set(members(g))
        others = [s for s in self._open() if s.direction not in current]
        if self.free_flow:
            if not any(s.waiting > 0 for s in self._open()):
                self._free_road(g)
                return
            if not any(s.vehicles > 0 for s in self._roads(g)) and any(s.waiting > 0 for s in others):
                nxt = self._next_serving(current)  # never hold a green on an empty road while a car waits
                self._switch_to(nxt, f"{NAME[g]} is empty", f"{NAME[nxt]} next: most cars waiting")
                return
        if self.elapsed < self.min_green:
            return

        late = [s for s in others if s.waiting > 0 and s.max_wait_s >= self.fairness_wait_s]
        if late:
            s = max(late, key=lambda x: x.max_wait_s)
            nxt = self._next_serving(current, forced=s) or s.direction
            self._switch_to(nxt, f"{NAME[g]} ended after {self.elapsed:.0f} s: a car on {NAME[s.direction]} waited "
                                 f"{s.max_wait_s:.0f} s", f"{NAME[nxt]} next: longest waiting time")
            return

        # Partial sensing: a road unseen for too long gets a turn, in case unconnected cars wait there
        if self.partial_sensing:
            unseen = [s for s in others if self.t - self.last_green.get(s.direction, 0.0) >= self.max_red_s]
            if unseen:
                s = min(unseen, key=lambda x: self.last_green.get(x.direction, 0.0))
                nxt = self._next_serving(current, forced=s) or s.direction
                self._switch_to(nxt, f"{NAME[g]} ended after {self.elapsed:.0f} s: {NAME[s.direction]} has been red "
                                     f"{self.t - self.last_green.get(s.direction, 0.0):.0f} s",
                                f"{NAME[nxt]} next: its turn (cars without a radio may be waiting)")
                return

        waiting_others = [s for s in others if s.waiting > 0]
        if self.elapsed >= self.max_green:
            # Hard limit: with partial sensing every other road counts, even if it looks empty
            due = waiting_others or [s for s in others if s.vehicles > 0] or (others if self.partial_sensing else [])
            if due:
                s = None if waiting_others else min(due, key=lambda x: self.last_green.get(x.direction, 0.0))
                nxt = self._next_serving(current, forced=s) or (s.direction if s else due[0].direction)
                self._switch_to(nxt, f"{NAME[g]} ended: maximum green {self.max_green:.0f} s",
                                f"{NAME[nxt]} next: " + (f"most cars waiting ({target:.0f})" if waiting_others else "longest on red"))
                return

        queued = [s for s in self._roads(g) if self._still_to_clear(s) > 0]
        if queued and all(s.down_status == "blocked" for s in queued) and any(s.down_status != "blocked" for s in waiting_others):
            nxt = self._next_serving(current)
            self._switch_to(nxt, f"{NAME[g]} ended: road ahead {queued[0].down_worst} is blocked",
                            f"{NAME[nxt]} next: its road ahead is free")
            return

        rising = [s for s in waiting_others if s.direction not in self._stage_peers and s.waiting > mine + self.tier_tolerance]
        if waiting_others and (mine <= target or rising):
            nxt = self._next_serving(current)
            self._switch_to(nxt, f"{NAME[g]} is down to the next queue ({mine:.0f} vs {target:.0f})",
                            f"{NAME[nxt]} next: most cars waiting ({target:.0f})")
            return

        if not waiting_others and mine <= 0:
            coming = [s for s in others if s.vehicles > 0]
            if coming:
                nxt = self._next_serving(current)
                self._switch_to(nxt, f"{NAME[g]} is clear", f"{NAME[nxt]} next: cars arriving")
            elif not self.reason.startswith("All clear"):
                self._record(f"All clear: {NAME[g]} stays green until a car arrives elsewhere.")

    # ------------------------------------------------------------------ timing broadcast and live view
    # Signal timing broadcast to cars: exact during a change, otherwise only the green road's limits,
    # because the next road is not decided until the queues level.
    def get_spat(self) -> Dict[str, Any]:
        """Exact timing only while a change is under way; otherwise only the served road's limits."""
        signals = self.get_approach_signals()
        g = self.serving()
        out: Dict[str, Any] = {}
        low = high = None
        target: Tuple[str, ...] = ()
        if g is None:
            target = members(self._target_group() or "")
            rem = max(0.0, float(self.transition_timer))
            if not self.dynamic_clearance:
                low = high = rem + self.all_red_time if self.transition_state == "YELLOW" else rem
            elif self.transition_state == "YELLOW":
                low, high = rem + self.all_red_min_s, rem + self.all_red_max_s
            else:
                low = max(0.0, self.all_red_min_s - self._all_red_elapsed)
                high = max(0.0, self.all_red_max_s - self._all_red_elapsed)
        locked = self._commit if g is not None else None
        if locked is not None:  # a locked green: its end and the next road's start are known
            remaining = max(0.0, locked[0] - self.elapsed)
            yellow = self._yellow_for(g)
            target = members(locked[1])
            if self.dynamic_clearance:
                low, high = remaining + yellow + self.all_red_min_s, remaining + yellow + self.all_red_max_s
            else:
                low = high = remaining + yellow + self.all_red_time
        for d in ("N", "S", "E", "W"):
            row: Dict[str, Any] = {"signal": signals.get(d, "r"), "min_s": None, "max_s": None,
                                   "next_green_min_s": None, "next_green_max_s": None}
            if g is not None and d in members(g):
                if locked is not None:
                    row["min_s"] = row["max_s"] = round(remaining, 1)
                else:
                    row["min_s"] = round(max(0.0, self.min_green - self.elapsed), 1)
                    row["max_s"] = round(max(0.0, self.max_green - self.elapsed) + self.decision_interval, 1)
            elif d in target:
                row.update(min_s=round(low, 1), max_s=round(high, 1), next_green_min_s=round(low, 1),
                           next_green_max_s=round(high, 1))
            out[d] = row
        return out

    # Dynamic's dashboard data, plus the mode and the queue level this green is aiming for.
    def get_live_info(self) -> Dict[str, Any]:
        info = super().get_live_info()
        info.update(mode="level", target_queue=round(self.target_queue, 1))
        info["params"].update(yellow=self.last_yellow_s, dynamic_clearance=self.dynamic_clearance,
                              all_red_min=self.all_red_min_s, all_red_max=self.all_red_max_s,
                              last_all_red=self.last_all_red_s)
        return info

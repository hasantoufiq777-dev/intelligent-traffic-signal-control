"""Eco-driving with GLOSA (Green Light Optimal Speed Advisory) over the VANET.

Flow (I2V):
- Every spat_interval each RSU broadcasts its junction's Signal Phase and Timing (SPaT) through
  the wireless channel (range, loss, delay) to the eco cars in range.
- Every update_interval each eco car looks at the next signalised junction on its route and the
  latest SPaT it received for that junction (aged by the time since it was sent), then:
    go   - it reaches the stop line before the green surely ends, or after the next green starts,
           at its normal speed: no change;
    slow - it would arrive on red: lower the speed so it arrives just as the next green starts;
    stop - the required speed is below v_min: creep at v_min and stop briefly at the line;
    none - no fresh SPaT, junction too far, timing unknown (e.g. EV preemption): no advice.
  Advice is applied with traci.vehicle.setSpeed (SUMO still keeps safe gaps and comfortable
  acceleration/deceleration) and released with setSpeed(-1).

Which cars are eco cars: equipped vehicles (OBU) selected by a seeded hash with probability
eco_fraction. Emergency vehicles and incident blockers are never eco cars.

Fuel and CO2 for results come from SUMO's emission device in tripinfo (HBEFA4 model). For the
live view, per-step emissions of all vehicles are accumulated here (live mode only).
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from sim.vanet.nodes import build_edge_info, distance_along_route, is_vehicle_equipped

logger = logging.getLogger(__name__)

PETROL_DENSITY_G_PER_L = 745.0


def is_eco_vehicle(vid: str, vtype: str, eco_fraction: float, penetration: float, seed: int) -> bool:
    """Deterministic eco-car selection among equipped, non-emergency vehicles. A vehicle of the city's
    eco-car category is always an eco car."""
    if vtype == "eco_car":
        return True
    if (eco_fraction <= 0.0 or vtype in ("emergency", "incident_blocker") or str(vtype).startswith("driven")
            or vid.startswith("incblk_")):
        return False
    if not is_vehicle_equipped(vid, vtype=vtype, penetration=penetration, seed=seed):
        return False
    if eco_fraction >= 1.0:
        return True
    digest = hashlib.sha256(f"{seed}:eco:{vid}".encode("utf-8")).hexdigest()[:8]
    return int(digest, 16) / 0xFFFFFFFF < eco_fraction


def glosa_advice(
    dist_m: float,
    approach_spat: Optional[Dict[str, Any]],
    age_s: float,
    v_max: float,
    v_min: float = 5.0,
    margin_s: float = 1.0,
    max_uncertainty_s: float = 5.0,
) -> Dict[str, Any]:
    """GLOSA decision for one car approaching one signal.

    Returns {"advice": go|slow|stop|none, "speed": target m/s or None, "green_in": s or None,
    "green_ends_in": s or None, "certain": bool}. Times in the SPaT are relative to when it was
    sent, so age_s is subtracted.

    - go: the car reaches the line while the green surely lasts, or after the next green starts.
    - slow: the next green start is known within max_uncertainty_s; drive at the speed that
      arrives just after it (never below v_min). This is the only advice that changes speed.
    - stop: the red is known to last too long to bridge at v_min; the car drives normally and
      stops at the line (no speed change - creeping for a long distance costs more time than
      it saves fuel).
    - none: no usable timing (unknown, too uncertain, or preemption): drive normally.
    """
    out: Dict[str, Any] = {"advice": "none", "speed": None, "green_in": None, "green_ends_in": None,
                           "certain": False}
    if not approach_spat or approach_spat.get("preempted") or dist_m <= 0.0 or v_max <= 0.1:
        return out

    def aged(key: str) -> Optional[float]:
        val = approach_spat.get(key)
        return None if val is None else float(val) - age_s

    signal = approach_spat.get("signal", "r")
    t_free = dist_m / v_max
    if signal == "G":
        ends = aged("min_s")
        out["green_ends_in"] = None if ends is None else max(0.0, round(ends, 1))
        if ends is not None and t_free <= ends - margin_s:
            out.update(advice="go", speed=round(v_max, 2), certain=True)  # green surely lasts
            return out
    g_min, g_max = aged("next_green_min_s"), aged("next_green_max_s")
    if g_min is None:
        return out
    g_min = max(0.0, g_min)
    out["green_in"] = round(g_min, 1)
    if t_free >= g_min + margin_s:
        # At normal speed the car arrives after the earliest possible green start
        out.update(advice="go", speed=round(v_max, 2))
        return out
    certain = g_max is not None and (g_max - g_min) <= max_uncertainty_s
    out["certain"] = bool(certain)
    if not certain:
        return out  # timing too uncertain (e.g. adaptive control): no speed advice
    v_adv = dist_m / (g_min + margin_s)
    if v_adv >= v_min:
        out.update(advice="slow", speed=round(min(v_adv, v_max), 2))
    else:
        out.update(advice="stop")
    return out


class EcoDrivingManager:
    """SPaT broadcast + GLOSA advice for eco cars, and live emission tracking."""

    def __init__(
        self,
        sumo_api: Any,
        vanet_net: Any,
        tls_ids: List[str],
        spat_source: Callable[[str], Dict[str, Any]],
        config: Optional[Dict[str, Any]] = None,
        eco_fraction: float = 0.0,
        penetration: float = 1.0,
        seed: int = 1,
        track_live_emissions: bool = False,
        eco_select: str = "random",
    ) -> None:
        cfg = dict(config or {})
        # How eco cars are chosen: "random" (seeded share of connected cars) or "data" (the cars that
        # burn the most fuel above the SUMO fuel-curve model, sim/eco_selection.py)
        self.eco_select = eco_select if eco_select in ("random", "data") else "random"
        self.selector = None
        if self.eco_select == "data":
            from sim.eco_selection import EcoSelector

            self.selector = EcoSelector(cfg)
            track_live_emissions = True  # the selection needs every car's fuel, distance and stops
        self.sumo_api = sumo_api
        self.vanet_net = vanet_net
        self.tls_ids = list(tls_ids)
        self.tls_set: Set[str] = set(tls_ids)
        self.spat_source = spat_source
        self.eco_fraction = float(eco_fraction)
        self.penetration = float(penetration)
        self.seed = int(seed)
        self.spat_interval = float(cfg.get("spat_interval", 1.0))
        self.spat_timeout = float(cfg.get("spat_timeout", 2.5))
        self.advisory_range = float(cfg.get("advisory_range_m", 300.0))
        self.update_interval = float(cfg.get("update_interval", 1.0))
        self.v_min = float(cfg.get("v_min_advice", 5.0))
        self.margin_s = float(cfg.get("margin_s", 1.0))
        self.max_uncertainty_s = float(cfg.get("max_uncertainty_s", 5.0))
        self.queue_lookahead_m = float(cfg.get("queue_lookahead_m", 60.0))
        self.queue_speed = float(cfg.get("queue_speed", 3.0))
        self.track_live = bool(track_live_emissions)

        self.edge_info = build_edge_info(sumo_api)
        self.vtype: Dict[str, str] = {}
        self.eco: Set[str] = set()
        self.ever_eco: Set[str] = set()  # every car that was an eco car during its trip
        self.chosen_at: Dict[str, Dict[str, float]] = {}  # data mode: the car's totals when it was chosen
        self.select_effect: List[Tuple[Optional[float], Optional[float]]] = []  # finished: (before %, since %)
        self.controlled: Set[str] = set()  # cars whose speed we currently set
        self.advice: Dict[str, Dict[str, Any]] = {}  # latest advice per eco car (for the UI)
        self.advised_vehicles: Set[str] = set()  # eco cars that got slow/stop advice at least once
        self.advice_counts: Dict[str, int] = {"go": 0, "slow": 0, "stop": 0, "none": 0}
        self._last_spat = -1e9
        self._last_advice = -1e9
        self._active: Set[str] = set()

        # Live emissions (mg, m) per finished trip group
        self._live_acc: Dict[str, Dict[str, float]] = {}
        self.live_groups: Dict[str, Dict[str, float]] = {
            g: {"co2_mg": 0.0, "fuel_mg": 0.0, "dist_m": 0.0, "stops": 0.0, "n": 0.0} for g in ("eco", "non")
        }

    # ------------------------------------------------------------------ helpers
    def _type(self, vid: str) -> str:
        if vid not in self.vtype:
            try:
                self.vtype[vid] = str(self.sumo_api.vehicle.getTypeID(vid))
            except Exception:
                self.vtype[vid] = "car"
        return self.vtype[vid]

    def _release(self, vid: str) -> None:
        if vid in self.controlled:
            try:
                self.sumo_api.vehicle.setSpeed(vid, -1)
            except Exception:
                pass
            self.controlled.discard(vid)

    def is_eco(self, vid: str) -> bool:
        return vid in self.eco

    # ------------------------------------------------------------------ main loop
    def step(self, sim_time: float) -> None:
        try:
            ids = [v for v in self.sumo_api.vehicle.getIDList() if not v.startswith("incblk_")]
        except Exception:
            return
        current = set(ids)
        for vid in self._active - current:  # departed
            self.eco.discard(vid)
            self.controlled.discard(vid)
            self.advice.pop(vid, None)
            if self.vanet_net is not None:
                self.vanet_net.forget_vehicle(vid)
            self._finish_live(vid)
        for vid in current - self._active:  # new
            vtype = self._type(vid)
            if self.eco_select == "data":
                if vtype == "eco_car":  # the city's eco-car category is always an eco car
                    self.eco.add(vid)
                    self.ever_eco.add(vid)
            elif is_eco_vehicle(vid, vtype, self.eco_fraction, self.penetration, self.seed):
                self.eco.add(vid)
                self.ever_eco.add(vid)
        self._active = current

        if self.track_live:
            self._track_live(ids)
        if self.selector is not None and self.selector.due(sim_time):
            self._select_by_data(sim_time, ids)
        if not self.eco or self.vanet_net is None:
            return

        if sim_time - self._last_spat >= self.spat_interval - 1e-9:
            self._last_spat = sim_time
            receivers: List[Tuple[str, Tuple[float, float]]] = []
            for vid in self.eco:
                try:
                    receivers.append((vid, tuple(self.sumo_api.vehicle.getPosition(vid))))
                except Exception:
                    continue
            for tid in self.tls_ids:
                self.vanet_net.broadcast_spat(tid, self.spat_source(tid), receivers, sim_time)

        if sim_time - self._last_advice >= self.update_interval - 1e-9:
            self._last_advice = sim_time
            for vid in sorted(self.eco):
                self._advise(vid, sim_time)

    def _advise(self, vid: str, sim_time: float) -> None:
        api = self.sumo_api
        try:
            route = list(api.vehicle.getRoute(vid))
            idx = int(api.vehicle.getRouteIndex(vid))
            road = str(api.vehicle.getRoadID(vid))
            lane_pos = float(api.vehicle.getLanePosition(vid))
        except Exception:
            return
        nxt = None
        for tid in self.tls_ids:
            along = distance_along_route(tid, route, idx, road, lane_pos, self.edge_info, self.tls_set)
            if along is not None and along[2] == 0:
                nxt = (tid, along[0], along[1])
                break
        result: Dict[str, Any] = {"advice": "none", "speed": None, "green_in": None, "green_ends_in": None}
        tid = None
        dist = None
        if nxt is not None and 0.0 < nxt[1] <= self.advisory_range:
            tid, dist, incoming = nxt
            msg = self.vanet_net.spat_inbox.get(vid, {}).get(tid)
            age = sim_time - msg.timestamp if msg is not None else None
            rsu = self.vanet_net.rsus.get(tid)
            approach = rsu.edge_directions.get(incoming) if rsu is not None else None
            if msg is not None and age is not None and age <= self.spat_timeout and approach:
                if self._needs_lane_change(vid):
                    # Not yet in a lane that continues on its route: slowing it down makes it miss the
                    # gap it needs, and two cars stuck in each other's lanes at the stop line deadlock.
                    result["lane_change"] = True
                elif self._queue_ahead(vid):
                    # In or behind a queue the arrival time is set by the queue, not the signal:
                    # slowing down here only wastes green time for everyone behind (no advice).
                    result["queue_ahead"] = True
                else:
                    try:
                        v_max = float(api.vehicle.getAllowedSpeed(vid))
                    except Exception:
                        v_max = 13.89
                    result = glosa_advice(dist, msg.approaches.get(approach), age, v_max, self.v_min,
                                          self.margin_s, self.max_uncertainty_s)
                result["signal"] = msg.approaches.get(approach, {}).get("signal")
                result["approach"] = approach

        advice = result["advice"]
        self.advice_counts[advice] = self.advice_counts.get(advice, 0) + 1
        self.advice[vid] = dict(result, tls=tid, dist=None if dist is None else round(dist, 1), t=sim_time)
        if advice == "slow" and result["speed"] is not None:
            try:
                api.vehicle.setSpeed(vid, float(result["speed"]))
                self.controlled.add(vid)
                self.advised_vehicles.add(vid)
            except Exception as e:
                logger.debug("setSpeed failed for %s: %s", vid, e)
        else:
            self._release(vid)

    def _needs_lane_change(self, vid: str) -> bool:
        """True when the car's current lane does not continue on its route (best-lane offset != 0)."""
        try:
            lane = self.sumo_api.vehicle.getLaneID(vid)
            for best in self.sumo_api.vehicle.getBestLanes(vid):
                if best[0] == lane:
                    return int(best[3]) != 0 or not bool(best[4])
        except Exception:
            return False
        return False

    def _queue_ahead(self, vid: str) -> bool:
        """True when a slow or stopped vehicle is close ahead (the car's own sensors / V2V)."""
        try:
            leader = self.sumo_api.vehicle.getLeader(vid, self.queue_lookahead_m)
        except Exception:
            return False
        if not leader or not leader[0]:
            return False
        try:
            return float(self.sumo_api.vehicle.getSpeed(leader[0])) < self.queue_speed
        except Exception:
            return False

    # ------------------------------------------------------------------ live emissions
    def _track_live(self, ids: List[str]) -> None:
        api = self.sumo_api
        for vid in ids:
            if self._type(vid) == "emergency":
                continue
            try:
                co2 = float(api.vehicle.getCO2Emission(vid))
                fuel = float(api.vehicle.getFuelConsumption(vid))
                speed = float(api.vehicle.getSpeed(vid))
                dist = float(api.vehicle.getDistance(vid))
            except Exception:
                continue
            acc = self._live_acc.setdefault(vid, {"co2_mg": 0.0, "fuel_mg": 0.0, "dist_m": 0.0, "stops": 0.0,
                                                  "moving": 1.0, "moving_s": 0.0, "idle_s": 0.0})
            acc["co2_mg"] += co2  # mg/s x 1 s step
            acc["fuel_mg"] += fuel
            acc["dist_m"] = dist
            if speed > 1.0:
                acc["moving_s"] += 1.0
            elif speed < 0.1:
                acc["idle_s"] += 1.0
            if speed < 0.1 and acc["moving"]:
                acc["stops"] += 1
                acc["moving"] = 0.0
            elif speed > 1.0:
                acc["moving"] = 1.0

    def _finish_live(self, vid: str) -> None:
        acc = self._live_acc.get(vid)
        snap = self.chosen_at.pop(vid, None)
        if acc and snap:
            from sim.eco_selection import category_of, compare_car

            cat = category_of(self._type(vid))
            delta = {k: acc.get(k, 0.0) - snap.get(k, 0.0) for k in ("fuel_mg", "co2_mg", "dist_m", "moving_s", "stops", "idle_s")}
            b, a = compare_car(cat, snap), compare_car(cat, delta) if delta["dist_m"] >= 50 else None
            if b and a:
                self.select_effect.append((b["excess_pct"], a["excess_pct"]))
        acc = self._live_acc.pop(vid, None)
        if not acc or self._type(vid) == "emergency":
            return
        g = self.live_groups["eco" if self._was_eco(vid) else "non"]
        for k in ("co2_mg", "fuel_mg", "dist_m", "stops"):
            g[k] += acc[k]
        g["n"] += 1

    def _was_eco(self, vid: str) -> bool:
        return vid in self.ever_eco

    # ------------------------------------------------------------------ data-driven selection
    # Cars that may become eco cars: not ambulances, driven cars or incident blockers.
    def _eligible(self, vid: str) -> bool:
        vtype = self._type(vid)
        return not (vtype in ("emergency", "incident_blocker") or str(vtype).startswith("driven")
                    or vid.startswith("incblk_"))

    # True when the car has a VANET radio, so it can receive signal timing.
    def _equipped(self, vid: str) -> bool:
        return is_vehicle_equipped(vid, vtype=self._type(vid), penetration=self.penetration, seed=self.seed)

    # Compare every eligible car on the road with its HBEFA4 reference.
    def compare_all(self, ids: List[str]) -> Dict[str, Dict[str, Any]]:
        """Each eligible car's measured fuel, CO2 and stops against the model (sim/eco_selection.py)."""
        from sim.eco_selection import category_of, compare_car

        out: Dict[str, Dict[str, Any]] = {}
        for vid in ids:
            acc = self._live_acc.get(vid)
            if acc is None or not self._eligible(vid):
                continue
            c = compare_car(category_of(self._type(vid)), acc)
            if c is not None:
                c["category"] = category_of(self._type(vid))
                out[vid] = c
        return out

    # Data-driven choice: fill the eco-car budget with the connected cars wasting the most fuel.
    def _select_by_data(self, sim_time: float, ids: List[str]) -> None:
        connected = [v for v in ids if self._eligible(v) and self._equipped(v)]
        budget = int(round(self.eco_fraction * len(connected)))
        comps = self.compare_all(connected)
        for vid in self.selector.choose(sim_time, comps, self.eco & set(connected), budget):
            self.eco.add(vid)
            self.ever_eco.add(vid)
            self.chosen_at[vid] = dict(self._live_acc.get(vid, {}))

    # Build the Driving page's table of cars against the model, most wasteful first.
    def comparison_block(self, limit: int = 15) -> Dict[str, Any]:
        """Cars on the road against the model, for the dashboard: the most wasteful first, eco cars kept."""
        ids = list(self._active)
        comps = self.compare_all(ids)
        sel = self.selector
        rows = []
        for vid, c in comps.items():
            eco = vid in self.eco
            if eco:
                status = "eco"
            elif not self._equipped(vid):
                status = "no_radio"
            elif sel is not None and c["dist_m"] < sel.min_dist_m:
                status = "too_little_data"
            elif sel is not None and (c["excess_pct"] or 0) < sel.min_excess_pct:
                status = "efficient"
            else:
                status = "candidate"
            reason = (sel.reasons.get(vid) if sel is not None else None) if eco else None
            since = None
            snap = self.chosen_at.get(vid)
            acc = self._live_acc.get(vid)
            if eco and snap and acc:
                from sim.eco_selection import compare_car as _cmp

                delta = {k: acc.get(k, 0.0) - snap.get(k, 0.0) for k in ("fuel_mg", "co2_mg", "dist_m", "moving_s", "stops", "idle_s")}
                s2 = _cmp(c["category"], delta) if delta["dist_m"] >= 50 else None
                since = s2["excess_pct"] if s2 else None
                c = dict(c, before_pct=_cmp(c["category"], snap)["excess_pct"] if _cmp(c["category"], snap) else None)
            if eco and reason is None:
                reason = "eco-car type" if self._type(vid) == "eco_car" else "chosen at random (eco fraction)"
            rows.append({"id": vid, **{k: v for k, v in c.items() if k != "excess_mg_per_m"},
                         "waste_mg_m": round(c["excess_mg_per_m"], 1), "status": status, "reason": reason,
                         "since_pct": since})
        # Judged cars first (most fuel above the model first); cars that only just started come last
        rows.sort(key=lambda r: (r["status"] == "too_little_data", -r["waste_mg_m"]))
        shown = rows[:limit]
        shown += [r for r in rows[limit:] if r["status"] == "eco"][: max(0, limit // 2)]

        def mean(key: str, group: List[Dict[str, Any]]) -> Optional[float]:
            vals = [r[key] for r in group if r.get(key) is not None]
            return round(sum(vals) / len(vals), 1) if vals else None

        eco_rows = [r for r in rows if r["status"] == "eco"]
        other = [r for r in rows if r["status"] != "eco"]
        # Eco cars chosen from data, finished or on the road: fuel above the model before and since
        before = [b for b in self.select_effect if b[0] is not None and b[1] is not None]
        before += [(r.get("before_pct"), r.get("since_pct")) for r in eco_rows
                   if r.get("before_pct") is not None and r.get("since_pct") is not None]
        return {
            "before_pct": round(sum(b[0] for b in before) / len(before), 1) if before else None,
            "since_pct": round(sum(b[1] for b in before) / len(before), 1) if before else None,
            "n_before_since": len(before),
            "rows": shown,
            "n_cars": len(rows),
            "excess_pct_eco": mean("excess_pct", eco_rows),
            "excess_pct_other": mean("excess_pct", other),
            "min_dist_m": sel.min_dist_m if sel is not None else None,
            "min_excess_pct": sel.min_excess_pct if sel is not None else None,
        }

    # ------------------------------------------------------------------ outputs
    @staticmethod
    def _per_km(g: Dict[str, float]) -> Tuple[Optional[float], Optional[float], Optional[float]]:
        if g["dist_m"] <= 0 or g["n"] <= 0:
            return None, None, None
        km = g["dist_m"] / 1000.0
        co2 = g["co2_mg"] / 1000.0 / km
        fuel = g["fuel_mg"] / 1000.0 / PETROL_DENSITY_G_PER_L / km * 100.0
        return round(co2, 1), round(fuel, 2), round(g["stops"] / g["n"], 2)

    def get_live_block(self) -> Dict[str, Any]:
        """The "eco" block of the live state message."""
        eco_co2, eco_fuel, eco_stops = self._per_km(self.live_groups["eco"])
        non_co2, non_fuel, non_stops = self._per_km(self.live_groups["non"])
        sent = getattr(self.vanet_net, "spat_sent", 0) if self.vanet_net is not None else 0
        delivered = getattr(self.vanet_net, "spat_delivered", 0) if self.vanet_net is not None else 0
        return {
            "eco_fraction": self.eco_fraction,
            "eco_select": self.eco_select,
            "eco_chosen_total": len(self.ever_eco),
            "comparison": self.comparison_block() if self.track_live else None,
            "eco_active": len(self.eco),
            "advised_now": len(self.controlled),
            "co2_g_per_km_eco": eco_co2,
            "co2_g_per_km_non": non_co2,
            "fuel_l_100km_eco": eco_fuel,
            "fuel_l_100km_non": non_fuel,
            "stops_eco": eco_stops,
            "stops_non": non_stops,
            "spat_pdr": round(delivered / sent, 3) if sent else None,
        }

    def get_metrics(self) -> Dict[str, Any]:
        sent = getattr(self.vanet_net, "spat_sent", 0) if self.vanet_net is not None else 0
        delivered = getattr(self.vanet_net, "spat_delivered", 0) if self.vanet_net is not None else 0
        total = sum(self.advice_counts.values())
        return {
            "eco_advised_vehicles": len(self.advised_vehicles),
            # Data-chosen eco cars: fuel above the SUMO model before they were chosen, and since
            "eco_select_before_pct": (round(sum(b for b, _ in self.select_effect) / len(self.select_effect), 1)
                                      if self.select_effect else None),
            "eco_select_since_pct": (round(sum(a for _, a in self.select_effect) / len(self.select_effect), 1)
                                     if self.select_effect else None),
            "eco_advice_share_slow": round(self.advice_counts.get("slow", 0) / total, 3) if total else None,
            "eco_advice_share_stop": round(self.advice_counts.get("stop", 0) / total, 3) if total else None,
            "spat_pdr": round(delivered / sent, 3) if sent else None,
        }

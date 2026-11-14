"""One-driver mode: one dashboard viewer drives one car in the live simulation.

Commands arrive as JSON lines on stdin (sent by the Node gateway, which allows one driver at a
time). They only change the car's target speed:

  {"cmd": "drive_start", "from": "<edge>", "to": "<edge>", "step_delay": 0.5}
  {"cmd": "drive", "mode": "go"}                      # drive normally (SUMO's own speed choice)
  {"cmd": "drive", "mode": "ease", "speed": 8.3}      # hold at most this speed (m/s)
  {"cmd": "drive", "mode": "stop"}                    # brake to a stop
  {"cmd": "drive_end", "reason": "released"}          # hand the car back to SUMO

SUMO still keeps safe gaps, comfortable acceleration and braking limits, and stops at red
lights (default speed mode), so a driver cannot cause a crash or run a red light. A stop
requested inside a junction is applied after the car leaves the junction, so it never blocks
the crossing traffic.

The driven car has an OBU. Every second it gets:
- SPaT from the RSU of its next junction over the emulated channel (range, loss, delay) when
  the run has a VANET, otherwise directly from the controller, and GLOSA speed advice from it;
- the ambulances' EV_ALERT broadcasts (position, route, progress) by V2V when within radio
  range (loss probability of the channel), which become a warning when the ambulance will pass
  the driver's position or cross the driver's next junctions.

When the car arrives (or is handed back) a trip report compares it with the other cars that
finished during the run (same origin and destination if any, otherwise all, per km).
Live mode only. The car's vType "driven" is excluded from navigation, eco-driving and the
run's result metrics.
"""

from __future__ import annotations

import logging
import math
import random
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from sim.commands import CommandReader  # noqa: F401  (re-exported for the runner)
from sim.vanet.glosa import glosa_advice
from sim.vanet.nodes import build_edge_info, distance_along_route

logger = logging.getLogger(__name__)

DRIVER_VTYPE = "driven"
DRIVER_PREFIX = "drv"
GUI_COLOR = (170, 60, 220, 255)  # purple, as on the dashboard map

MODES = ("go", "ease", "stop")
MIN_EASE_MS = 2.0  # lowest "ease off" speed (about 7 km/h)
FOLLOW_MARGIN_MS = 1.4  # within 5 km/h of the advised speed counts as following it
ALERT_HOLD_S = 3.0  # keep a received ambulance alert this long (alerts repeat every second)
JUNCTION_LOOKAHEAD = 2  # warn when the ambulance crosses one of the driver's next two signalised junctions
CROSS_EV_M = 200.0  # ... and the ambulance is at most this far from that junction
CROSS_DRIVER_M = 250.0  # ... and so is the driver


def _kmh(v: Optional[float]) -> Optional[float]:
    return None if v is None else round(float(v) * 3.6, 1)


class DriverManager:
    """The single driven car: commands, advice, ambulance warning, trip report."""

    def __init__(
        self,
        sumo_api: Any,
        tls_ids: List[str],
        spat_source: Callable[[str], Dict[str, Any]],
        tracker: Any,
        vanet_net: Optional[Any] = None,
        range_m: float = 300.0,
        eco_config: Optional[Dict[str, Any]] = None,
        seed: int = 1,
        gui: bool = False,
    ) -> None:
        cfg = dict(eco_config or {})
        self.api = sumo_api
        self.tls_ids = list(tls_ids)
        self.tls_set: Set[str] = set(tls_ids)
        self.spat_source = spat_source
        self.tracker = tracker
        self.vanet_net = vanet_net
        self.range_m = float(vanet_net.channel.range_m if vanet_net is not None else range_m)
        self.spat_timeout = float(cfg.get("spat_timeout", 2.5))
        self.advisory_range = float(cfg.get("advisory_range_m", 300.0))
        self.v_min = float(cfg.get("v_min_advice", 5.0))
        self.margin_s = float(cfg.get("margin_s", 1.0))
        self.max_uncertainty_s = float(cfg.get("max_uncertainty_s", 5.0))
        self.queue_lookahead_m = float(cfg.get("queue_lookahead_m", 60.0))
        self.queue_speed = float(cfg.get("queue_speed", 3.0))
        self.rng = random.Random(f"{seed}:driver")
        self.gui = gui
        self.edge_info = build_edge_info(sumo_api)
        self._vtype_ready = False
        self._n = 0
        self.notices: List[Dict[str, Any]] = []  # driver messages to send right away
        self.events: List[Dict[str, Any]] = []
        self.vtype_cache: Dict[str, str] = {}
        self._reset()

    # ------------------------------------------------------------------ state
    def _reset(self) -> None:
        self.vid: Optional[str] = None
        self.status: Optional[str] = None  # waiting | driving
        self.origin = self.destination = None
        self.mode = "go"
        self.target: Optional[float] = None  # m/s for "ease"
        self.step_delay: Optional[float] = None
        self.t_added = self.t_depart = None
        self.seen = False
        self.holding = False  # stop requested inside a junction
        self.applied: Optional[float] = None  # last value given to setSpeed (-1 = normal)
        self.last: Dict[str, Any] = {}
        self.advice: Dict[str, Any] = {"advice": "none"}
        self.adv_seconds = 0
        self.adv_followed = 0
        self.amb: Optional[Dict[str, Any]] = None
        self._amb_rx: Dict[str, float] = {}  # ambulance id -> time its alert was last received
        self.amb_warned: Set[str] = set()
        self.route: List[str] = []

    @property
    def active(self) -> bool:
        return self.vid is not None

    def active_step_delay(self) -> Optional[float]:
        """Live pacing while someone drives (None = the run's own pacing)."""
        return self.step_delay if self.active else None

    def _type(self, vid: str) -> str:
        if vid not in self.vtype_cache:
            try:
                self.vtype_cache[vid] = str(self.api.vehicle.getTypeID(vid))
            except Exception:
                self.vtype_cache[vid] = "car"
        return self.vtype_cache[vid]

    def _notice(self, run_t: float, status: str, message: str, report: Optional[Dict[str, Any]] = None,
                vid: Optional[str] = None) -> None:
        msg: Dict[str, Any] = {"type": "driver", "t": int(run_t), "vid": self.vid if vid is None else vid, "status": status,
                               "message": message}
        if report is not None:
            msg["report"] = report
        self.notices.append(msg)

    def pop_notices(self, run_id: str) -> List[Dict[str, Any]]:
        out = [dict(n, run_id=run_id) for n in self.notices]
        self.notices.clear()
        return out

    def pop_events(self) -> List[Dict[str, Any]]:
        out = self.events[:]
        self.events.clear()
        return out

    # ------------------------------------------------------------------ commands
    def handle(self, cmd: Dict[str, Any], t: float) -> None:
        kind = cmd.get("cmd")
        try:
            if kind == "drive_start":
                self._start(cmd, t)
            elif kind == "drive":
                self._drive(cmd, t)
            elif kind == "drive_end":
                self._end(t, str(cmd.get("reason") or "released"))
            else:
                logger.warning("Unknown driver command %r", kind)
        except Exception as e:  # never let a bad command stop the run
            logger.warning("Driver command %r failed: %s", kind, e)
            if kind == "drive_start":
                if self.vid is not None:
                    try:
                        self.api.vehicle.remove(self.vid)
                    except Exception:
                        pass
                self._reset()
                self._notice(t, "error", f"Could not start the drive: {e}", vid="")

    def _ensure_vtype(self) -> None:
        if self._vtype_ready:
            return
        types = set(self.api.vehicletype.getIDList())
        if DRIVER_VTYPE not in types:
            base = "car" if "car" in types else "DEFAULT_VEHTYPE"
            self.api.vehicletype.copy(base, DRIVER_VTYPE)
            try:
                self.api.vehicletype.setColor(DRIVER_VTYPE, GUI_COLOR)
            except Exception:
                pass
        self._vtype_ready = True

    def _start(self, cmd: Dict[str, Any], t: float) -> None:
        if self.active:
            self._notice(t, "error", "A car is already being driven")
            return
        origin, dest = str(cmd.get("from") or ""), str(cmd.get("to") or "")
        edges = {e for e in self.api.edge.getIDList() if not e.startswith(":")}
        if origin not in edges or dest not in edges:
            self._notice(t, "error", f"Unknown road: {origin if origin not in edges else dest}", vid="")
            return
        self._ensure_vtype()
        route = list(self.api.simulation.findRoute(origin, dest, vType=DRIVER_VTYPE).edges)
        if not route:
            self._notice(t, "error", f"No route from {origin} to {dest}", vid="")
            return
        self._n += 1
        vid = f"{DRIVER_PREFIX}{self._n}"
        rid = f"{vid}_route"
        self.api.route.add(rid, route)
        self.api.vehicle.add(vid, rid, typeID=DRIVER_VTYPE, depart="now", departLane="best", departSpeed="max")
        self._reset()
        self.vid, self.status = vid, "waiting"
        self.origin, self.destination, self.route = origin, dest, route
        self.t_added = t
        delay = cmd.get("step_delay")
        self.step_delay = max(0.0, float(delay)) if isinstance(delay, (int, float)) else None
        self.events.append({"kind": "driver_start", "tls": "",
                            "detail": f"A viewer is driving {vid} from {origin} to {dest} ({len(route)} roads)"})
        self._notice(t, "waiting", "Your car is entering the road")

    def _drive(self, cmd: Dict[str, Any], t: float) -> None:
        if not self.active:
            return
        mode = cmd.get("mode")
        if mode not in MODES:
            return
        self.mode = str(mode)
        if self.mode == "ease":
            try:
                v = float(cmd.get("speed"))
            except (TypeError, ValueError):
                v = 8.3
            self.target = max(MIN_EASE_MS, v) if math.isfinite(v) else 8.3
        else:
            self.target = None
        if self.status == "driving":
            self._apply_speed()

    def _end(self, t: float, reason: str) -> None:
        if not self.active:
            return
        vid = self.vid
        if self.status == "driving":
            self._set_speed(-1.0)  # back to normal driving; the car finishes its trip as normal traffic
        else:
            try:
                self.api.vehicle.remove(vid)  # still waiting to enter: no trip to finish
            except Exception:
                pass
        report = self._report(t, finished=False) if self.seen else None
        texts = {"idle": "No input for a while: the car was handed back to the simulation",
                 "left": "The driver left: the car was handed back to the simulation",
                 "admin": "The admin ended the drive", "run_ended": "The run ended"}
        self.events.append({"kind": "driver_end", "tls": "", "detail": f"{vid}: drive ended ({reason})"})
        self._notice(t, "released", texts.get(reason, "You handed the car back"), report=report, vid=vid)
        self._reset()

    # ------------------------------------------------------------------ speed
    def _set_speed(self, v: float) -> None:
        if self.applied is not None and abs(self.applied - v) < 0.05:
            return
        try:
            self.api.vehicle.setSpeed(self.vid, v)
            self.applied = v
        except Exception as e:
            logger.debug("setSpeed failed for %s: %s", self.vid, e)

    def _apply_speed(self) -> None:
        road = self.last.get("road", "")
        self.holding = False
        if self.mode == "stop":
            if road.startswith(":"):
                self.holding = True  # never stop inside the junction; stop once past it
                self._set_speed(-1.0)
            else:
                self._set_speed(0.0)
        elif self.mode == "ease" and self.target is not None:
            limit = self.last.get("allowed") or self.target
            self._set_speed(min(self.target, limit))
        else:
            self._set_speed(-1.0)

    # ------------------------------------------------------------------ per step
    def step(self, t: float, active_vehs: List[str]) -> None:
        if not self.active:
            return
        alive = set(active_vehs)
        vid = self.vid
        if vid not in alive:
            if self.seen:  # arrived at its destination
                report = self._report(t, finished=True)
                self.events.append({"kind": "driver_end", "tls": "",
                                    "detail": f"{vid} reached {self.destination} in {report['travel_s']:.0f} s"})
                self._notice(t, "finished", "You reached your destination", report=report)
                self._reset()
            return
        if not self.seen:
            self.seen, self.status, self.t_depart = True, "driving", t
            if self.gui:
                try:
                    self.api.vehicle.highlight(vid, GUI_COLOR)
                except Exception:
                    pass
        api = self.api
        try:
            route = list(api.vehicle.getRoute(vid))
            idx = int(api.vehicle.getRouteIndex(vid))
            road = str(api.vehicle.getRoadID(vid))
            lane_pos = float(api.vehicle.getLanePosition(vid))
            pos = tuple(api.vehicle.getPosition(vid))
            speed = float(api.vehicle.getSpeed(vid))
            allowed = float(api.vehicle.getAllowedSpeed(vid))
        except Exception:
            return
        self.route = route
        self.last = {"route": route, "idx": idx, "road": road, "lane_pos": lane_pos, "pos": pos,
                     "speed": speed, "allowed": allowed}
        self._apply_speed()

        # Next signal on the route and the advice for it
        nxt = None
        for tid in self.tls_ids:
            along = distance_along_route(tid, route, idx, road, lane_pos, self.edge_info, self.tls_set)
            if along is not None and along[2] == 0:
                nxt = (tid, along[0], along[1])
                break
        self.last["next"] = nxt
        self.advice = self._advise(t, vid, nxt, allowed)
        if self.advice.get("advice") == "slow" and self.advice.get("speed") is not None:
            self.adv_seconds += 1
            if speed <= float(self.advice["speed"]) + FOLLOW_MARGIN_MS:
                self.adv_followed += 1

        self._ambulance(t, alive, pos, route, idx, road, lane_pos)

    def _advise(self, t: float, vid: str, nxt: Optional[Tuple[str, float, str]], allowed: float) -> Dict[str, Any]:
        out: Dict[str, Any] = {"advice": "none", "speed": None, "green_in": None, "signal": None}
        if nxt is None:
            return out
        tid, dist, incoming = nxt
        approach = None
        msg_spat: Optional[Dict[str, Any]] = None
        age = 0.0
        if self.vanet_net is not None:
            # I2V over the emulated channel: the RSU sends SPaT to the car; read what arrived
            rsu = self.vanet_net.rsus.get(tid)
            pos = self.last.get("pos")
            if rsu is not None and pos is not None:
                self.vanet_net.broadcast_spat(tid, self.spat_source(tid), [(vid, pos)], t)
                approach = rsu.edge_directions.get(incoming)
            msg = self.vanet_net.spat_inbox.get(vid, {}).get(tid)
            if msg is not None and t - msg.timestamp <= self.spat_timeout and approach:
                msg_spat, age = msg.approaches.get(approach), t - msg.timestamp
        else:
            from sim.vanet.network import resolve_lane_direction

            approach = resolve_lane_direction(f"{incoming}_0", tid, self.api)
            msg_spat = (self.spat_source(tid) or {}).get(approach)
        out["signal"] = (msg_spat or {}).get("signal")
        if msg_spat is None or dist > self.advisory_range:
            return out
        if self._queue_ahead(vid):
            out["queue_ahead"] = True
            return out
        res = glosa_advice(dist, msg_spat, age, allowed, self.v_min, self.margin_s, self.max_uncertainty_s)
        res["signal"] = out["signal"]
        return res

    def _queue_ahead(self, vid: str) -> bool:
        try:
            leader = self.api.vehicle.getLeader(vid, self.queue_lookahead_m)
            return bool(leader and leader[0]) and float(self.api.vehicle.getSpeed(leader[0])) < self.queue_speed
        except Exception:
            return False

    def _signals_ahead(self, route: List[str], idx: int, road: str, lane_pos: float,
                       n: int) -> List[Tuple[str, float]]:
        """The next n signalised junctions on the route with the distance to each (m)."""
        out: List[Tuple[str, float]] = []
        for tid in self.tls_ids:
            along = distance_along_route(tid, route, idx, road, lane_pos, self.edge_info, self.tls_set)
            if along is not None:
                out.append((tid, float(along[0]), int(along[2])))
        out.sort(key=lambda r: r[2])  # order along the route (number of signals in between)
        return [(j, d) for j, d, _ in out[:n]]

    def _ambulance(self, t: float, alive: Set[str], pos: Tuple[float, float], route: List[str], idx: int,
                   road: str, lane_pos: float) -> None:
        """V2V EV_ALERT reception and the warning shown to the driver.

        An alert carries the ambulance's position, route and progress (EvAlertMessage). It warns
        when the ambulance is behind the driver on the driver's road (or will enter it), or when it
        crosses one of the driver's next signalised junctions and both are close to it.
        """
        best: Optional[Dict[str, Any]] = None
        mine = dict(self._signals_ahead(route, idx, road, lane_pos, JUNCTION_LOOKAHEAD))
        for ev in alive:
            if self._type(ev) != "emergency":
                continue
            try:
                ev_pos = tuple(self.api.vehicle.getPosition(ev))
            except Exception:
                continue
            d = math.hypot(ev_pos[0] - pos[0], ev_pos[1] - pos[1])
            if d <= self.range_m:
                p_loss = (self.vanet_net.channel.calculate_loss_probability(d)
                          if self.vanet_net is not None else 0.0)
                if self.rng.random() >= p_loss:
                    self._amb_rx[ev] = t
            if t - self._amb_rx.get(ev, -1e9) > ALERT_HOLD_S:
                continue
            try:
                ev_route = list(self.api.vehicle.getRoute(ev))
                ev_idx = int(self.api.vehicle.getRouteIndex(ev))
                ev_road = str(self.api.vehicle.getRoadID(ev))
                ev_lane_pos = float(self.api.vehicle.getLanePosition(ev))
            except Exception:
                continue
            rec: Optional[Dict[str, Any]] = None
            if not road.startswith(":") and (
                (ev_road == road and ev_lane_pos < lane_pos)
                or (ev_road != road and road in ev_route[ev_idx + 1:])
            ):
                # Behind the driver on this road, or the road is still ahead on its route
                # (it enters the road at its start, so behind the driver)
                rec = {"id": ev, "dist_m": round(d), "where": "behind", "junction": None}
            else:
                for j, ev_d in self._signals_ahead(ev_route, ev_idx, ev_road, ev_lane_pos, 3):
                    if j in mine and ev_d <= CROSS_EV_M and mine[j] <= CROSS_DRIVER_M:
                        rec = {"id": ev, "dist_m": round(ev_d), "where": "crossing", "junction": j}
                        break
            if rec is not None and (best is None or rec["dist_m"] < best["dist_m"]):
                best = rec
        for ev in [e for e in self._amb_rx if e not in alive]:
            self._amb_rx.pop(ev, None)
        if best is not None:
            if best["where"] == "behind":
                best["text"] = f"Ambulance approaching from behind ({best['dist_m']} m). Keep to the side and let it pass."
            else:
                best["text"] = (f"Ambulance {best['dist_m']} m from {best['junction']}, crossing your route. "
                                f"Its light turns green for it: wait for it to pass.")
            self.amb_warned.add(best["id"])
        self.amb = best

    # ------------------------------------------------------------------ output
    def _trip_of(self, vid: str) -> Dict[str, Any]:
        """Distance, fuel, CO2 and stops of a car so far (or at arrival) from the live tracker."""
        rec = self.tracker.trip(vid) if self.tracker is not None else None
        return rec or {"dist_m": 0.0, "co2_g": 0.0, "fuel_ml": 0.0, "stops": 0}

    def _report(self, t: float, finished: bool) -> Dict[str, Any]:
        trip = self._trip_of(self.vid)
        travel = max(0.0, t - (self.t_depart if self.t_depart is not None else t))
        dist_km = trip["dist_m"] / 1000.0
        per_km = (lambda x: round(x / dist_km, 1)) if dist_km > 0.05 else (lambda x: None)
        report: Dict[str, Any] = {
            "finished": finished,
            "from": self.origin, "to": self.destination,
            "travel_s": round(travel, 1),
            "distance_m": round(trip["dist_m"]),
            "avg_speed_kmh": round(trip["dist_m"] / travel * 3.6, 1) if travel > 0 else None,
            "stops": int(trip["stops"]),
            "fuel_ml": round(trip["fuel_ml"], 1),
            "co2_g": round(trip["co2_g"], 1),
            "co2_g_per_km": per_km(trip["co2_g"]),
            "fuel_l_per_100km": per_km(trip["fuel_ml"] / 10.0),
            "stops_per_km": round(trip["stops"] / dist_km, 2) if dist_km > 0.05 else None,
            "advice_seconds": self.adv_seconds,
            "advice_followed_pct": round(100.0 * self.adv_followed / self.adv_seconds) if self.adv_seconds else None,
            "amb_warnings": len(self.amb_warned),
            "compare": self._compare(),
        }
        return report

    def _compare(self) -> Optional[Dict[str, Any]]:
        """Other cars that finished during this run: same origin and destination if any, else all."""
        done = [r for r in (self.tracker.finished.values() if self.tracker is not None else [])
                if r.get("vtype") not in ("emergency", DRIVER_VTYPE) and r.get("dist_m", 0) > 50]
        same = [r for r in done if r.get("origin") == self.origin and r.get("destination") == self.destination]
        group, rows = ("same_route", same) if same else ("all", done)
        if not rows:
            return None
        dist = sum(r["dist_m"] for r in rows) / 1000.0
        travel = sum(r["travel_s"] for r in rows)
        return {
            "group": group, "n": len(rows),
            "travel_s": round(travel / len(rows), 1),
            "avg_speed_kmh": round(sum(r["dist_m"] for r in rows) / travel * 3.6, 1) if travel > 0 else None,
            "co2_g_per_km": round(sum(r["co2_g"] for r in rows) / dist, 1) if dist > 0 else None,
            "fuel_l_per_100km": round(sum(r["fuel_ml"] for r in rows) / 10.0 / dist, 1) if dist > 0 else None,
            "stops_per_km": round(sum(r["stops"] for r in rows) / dist, 2) if dist > 0 else None,
        }

    def build_message(self, run_id: str, t: float) -> Optional[Dict[str, Any]]:
        """The driver block for this second (None when nobody drives)."""
        if not self.active:
            return None
        msg: Dict[str, Any] = {"type": "driver", "run_id": run_id, "t": int(t), "vid": self.vid,
                               "status": self.status, "from": self.origin, "to": self.destination,
                               "mode": self.mode, "target_kmh": _kmh(self.target)}
        if self.status == "waiting":
            msg["waiting_s"] = int(t - (self.t_added or t))
            return msg
        last = self.last
        trip = self._trip_of(self.vid)
        nxt = last.get("next")
        idx = int(last.get("idx", 0))
        left = 0.0
        route = last.get("route") or []
        for e in route[idx:]:
            left += self.edge_info.get(e, (None, 0.0))[1]
        left = max(0.0, left - float(last.get("lane_pos", 0.0))) if not str(last.get("road", "")).startswith(":") else left
        a = self.advice
        msg.update({
            "speed_kmh": _kmh(last.get("speed")),
            "limit_kmh": _kmh(last.get("allowed")),
            "holding": self.holding,
            "road": last.get("road"),
            "dist_left_m": round(left),
            "elapsed_s": int(t - (self.t_depart or t)),
            "tls": nxt[0] if nxt else None,
            "dist": round(nxt[1], 1) if nxt else None,
            "advice": {"advice": a.get("advice", "none"), "speed_kmh": _kmh(a.get("speed")),
                       "green_in": a.get("green_in"), "green_ends_in": a.get("green_ends_in"),
                       "signal": a.get("signal"), "queue_ahead": bool(a.get("queue_ahead"))},
            "advice_followed_pct": round(100.0 * self.adv_followed / self.adv_seconds) if self.adv_seconds else None,
            "stops": int(trip["stops"]),
            "fuel_ml": round(trip["fuel_ml"], 1),
            "co2_g": round(trip["co2_g"], 1),
            "amb": self.amb,
        })
        return msg

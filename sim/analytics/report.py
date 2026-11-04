"""Write docs/results_discussion.md from the experiment outputs (numbers come from the files).

Reads results/summary.json (sim/analytics/evaluate.py) and results/ga/ga_<target>_evaluation.json
(sim/optimizer/ga_tuner.py). Every number in the document is taken from those files; the text
around them is a starting point marked with [Interpretation] where your own discussion goes.

    python sim/analytics/report.py
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import sys
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent.parent


def _load(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _e(cfgs: Dict[str, Any], label: str, metric: str) -> Optional[Dict[str, Any]]:
    return (cfgs.get(label) or {}).get(metric)


def _v(entry: Optional[Dict[str, Any]], digits: int = 1) -> str:
    if not entry or entry.get("mean") is None:
        return "n/a"
    return f"{entry['mean']:.{digits}f} ± {entry['std']:.{digits}f}"


def _imp(entry: Optional[Dict[str, Any]]) -> str:
    if not entry or entry.get("improvement_pct") is None:
        return ""
    p = entry.get("p_value")
    ptxt = "" if p is None else (", p < 0.001" if p < 0.001 else f", p = {p:.3f}")
    return f" ({entry['improvement_pct']:+.1f}%{ptxt})"


def _param(label: str, name: str) -> Optional[float]:
    m = re.search(rf"{name}([0-9.]+)", label)
    return float(m.group(1).rstrip(".")) if m else None


def section_signals(s: Dict[str, Any]) -> List[str]:
    out = ["## 1. Signal control and VANET penetration", ""]
    for scen, cfgs in s.items():
        out.append(f"**Scenario `{scen}`** (average waiting time, s; improvement vs fixed-time):")
        out.append("")
        out.append("| Configuration | Waiting time | Travel time | Queue estimate error |")
        out.append("|---|---|---|---|")
        labels = ["fixed", "adaptive"] + sorted([l for l in cfgs if l.startswith("adaptive_vanet@")],
                                                  key=lambda l: _param(l, "pen") or 0)
        for lab in labels:
            if lab not in cfgs:
                continue
            w = _e(cfgs, lab, "avg_waiting_time")
            out.append(f"| {lab} | {_v(w)}{_imp(w)} | {_v(_e(cfgs, lab, 'avg_travel_time'))}"
                       f"{_imp(_e(cfgs, lab, 'avg_travel_time'))} | {_v(_e(cfgs, lab, 'queue_estimate_mae'), 2)} |")
        out.append("")
    out += ["[Interpretation] How much of the oracle adaptive gain survives with VANET sensing, and from which "
            "penetration the estimate is good enough; why the queue-estimate error grows as penetration drops "
            "(fewer beacons, scaled by 1/penetration).", ""]
    return out


def section_ev(s: Dict[str, Any]) -> List[str]:
    out = ["## 2. Emergency vehicles: preemption, packet loss and V2V relay", ""]
    for scen, cfgs in s.items():
        out.append(f"**Scenario `{scen}`** (ambulance travel time, s; improvement vs adaptive without preemption):")
        out.append("")
        for lab in ("fixed", "adaptive", "adaptive_ev"):
            if lab in cfgs:
                out.append(f"- `{lab}`: {_v(_e(cfgs, lab, 'ev_travel_time'))}{_imp(_e(cfgs, lab, 'ev_travel_time'))}")
        out.append("")
        out.append("| Packet loss | Relay on | Relay off | Missed preemptions (on / off) |")
        out.append("|---|---|---|---|")
        losses = sorted({_param(l, "loss") for l in cfgs if l.startswith("adaptive_ev_vanet@")} - {None})
        for loss in losses:
            key = f"{loss:g}"
            on = f"adaptive_ev_vanet@loss{key}_relay_on"
            off = f"adaptive_ev_vanet@loss{key}_relay_off"
            out.append(f"| {loss:g} | {_v(_e(cfgs, on, 'ev_travel_time'))}{_imp(_e(cfgs, on, 'ev_travel_time'))} | "
                       f"{_v(_e(cfgs, off, 'ev_travel_time'))}{_imp(_e(cfgs, off, 'ev_travel_time'))} | "
                       f"{_v(_e(cfgs, on, 'missed_preemptions'), 2)} / {_v(_e(cfgs, off, 'missed_preemptions'), 2)} |")
        tuned = [l for l in cfgs if l.startswith("adaptive_ev_vanet_tuned@")]
        for lab in tuned:
            out.append("")
            out.append(f"- GA-tuned `{lab}`: {_v(_e(cfgs, lab, 'ev_travel_time'))}"
                       f"{_imp(_e(cfgs, lab, 'ev_travel_time'))}; waiting time of all vehicles "
                       f"{_v(_e(cfgs, lab, 'avg_waiting_time'))}{_imp(_e(cfgs, lab, 'avg_waiting_time'))}")
        out.append("")
    out += ["[Interpretation] VANET preemption vs the oracle; how fast the gain degrades with loss; whether the V2V "
            "relay matters at a 300 m range on a 250 m grid (RSUs are usually in direct range, so the relay mainly "
            "adds redundancy); the cost for other traffic (waiting time).", ""]
    return out


def section_evroute(s: Dict[str, Any]) -> List[str]:
    out = ["## 2.1 Ambulance route choice on the TMC map", ""]
    names = {"emergency": "normal demand", "ev_incident": "road closure ahead on its route",
             "ev_congested": "heavy demand on its default corridor"}
    for scen, cfgs in s.items():
        out.append(f"**Scenario `{scen}`** ({names.get(scen, scen)}; improvement vs the same controller "
                   "with the scenario route):")
        out.append("")
        out.append("| Configuration | Ambulance travel time (s) | Waiting time, all vehicles (s) | Reroutes | Missed preemptions |")
        out.append("|---|---|---|---|---|")
        for lab in sorted(cfgs):
            ev = _e(cfgs, lab, "ev_travel_time")
            w = _e(cfgs, lab, "avg_waiting_time")
            missed = _v(_e(cfgs, lab, "missed_preemptions"), 2) if lab.startswith("adaptive_ev") else "– (no preemption)"
            out.append(f"| {lab} | {_v(ev)}{_imp(ev)} | {_v(w)}{_imp(w)} | {_v(_e(cfgs, lab, 'ev_reroutes'), 1)} | "
                       f"{missed} |")
        out.append("")
    out += ["[Interpretation] When route choice helps the ambulance (closures ahead, congested corridor) and "
            "when it changes nothing; how it combines with preemption.", ""]
    return out


def section_nav(s: Dict[str, Any]) -> List[str]:
    out = ["## 3. Navigation: A* vs ACO", ""]
    for scen, cfgs in s.items():
        pens = sorted({_param(l, "pen") for l in cfgs if l.startswith("none@")} - {None}, reverse=True)
        for pen in pens:
            base = f"none@pen{pen:g}"
            out.append(f"**Scenario `{scen}`, penetration {pen:g}**. No navigation: travel time "
                       f"{_v(_e(cfgs, base, 'avg_travel_time'))} s, edge-load CV {_v(_e(cfgs, base, 'edge_load_cv'), 3)}.")
            out.append("")
            out.append("| Mode @ fraction | All vehicles' travel time | Navigating | Not navigating | Edge-load CV | Search time (ms) |")
            out.append("|---|---|---|---|---|---|")
            for lab in sorted([l for l in cfgs if not l.startswith("none@") and _param(l, "_pen") == pen]):
                at = _e(cfgs, lab, "avg_travel_time")
                out.append(f"| {lab.split('_pen')[0]} | {_v(at)}{_imp(at)} | {_v(_e(cfgs, lab, 'nav_avg_travel_time'))} | "
                           f"{_v(_e(cfgs, lab, 'nonnav_avg_travel_time'))} | {_v(_e(cfgs, lab, 'edge_load_cv'), 3)} | "
                           f"{_v(_e(cfgs, lab, 'route_search_ms'), 0)} |")
            out.append("")
    out += ["[Interpretation] A* returns the same optimal path as Dijkstra (fewer expansions); with few alternatives "
            "on a 2x2 grid the gains are small and herding can make them negative. Whether ACO's probabilistic "
            "spreading lowers the edge-load CV and helps when many vehicles navigate.", ""]
    return out


def section_eco(s: Dict[str, Any]) -> List[str]:
    out = ["## 4. Eco-driving (GLOSA)", ""]
    for scen, cfgs in s.items():
        out.append(f"**Scenario `{scen}`** (improvement vs the same controller without eco cars):")
        out.append("")
        out.append("| Configuration | CO₂ (g/km) | Fuel (L/100 km) | Stops per vehicle | Travel time (s) |")
        out.append("|---|---|---|---|---|")
        for lab in sorted(cfgs, key=lambda l: (l.split("@")[0], _param(l, "eco") or 0)):
            c = [_e(cfgs, lab, m) for m in ("co2_g_per_km", "fuel_l_per_100km", "stops_per_vehicle", "avg_travel_time")]
            out.append(f"| {lab} | {_v(c[0])}{_imp(c[0])} | {_v(c[1], 2)}{_imp(c[1])} | {_v(c[2], 2)}{_imp(c[2])} | "
                       f"{_v(c[3])}{_imp(c[3])} |")
        out.append("")
    out += ["[Interpretation] GLOSA needs predictable signal timing: with fixed-time signals it saves stops and fuel; "
            "with adaptive control the next green is rarely known within 5 s, so little advice is given. Cars that "
            "must still change lanes or are behind a queue get no advice (this avoided lane deadlocks).", ""]
    return out


def section_incident() -> List[str]:
    """Table from results/incident_experiments.md (sim/analytics/vanet_experiments.py --incident)."""
    path = ROOT / "results" / "incident_experiments.md"
    out = ["## 5. Incident detection through VANET", ""]
    try:
        text = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out + ["No incident results found (run `python sim/analytics/vanet_experiments.py --incident`).", ""]
    body = [l for l in text if not l.startswith("# ")]
    out += [l for l in body if l.strip()] + [""]
    out += ["[Interpretation] Detection rate and delay against penetration for closures and stopped vehicles; "
            "whether navigation and the TMC incident map keep vehicles off the incident edge.", ""]
    return out


def section_ga() -> List[str]:
    out = ["## 6. GA tuning and whether the gain holds", ""]
    for target in ("signals", "aco"):
        rep = _load(ROOT / "results" / "ga" / f"ga_{target}_evaluation.json")
        hist = _load(ROOT / "results" / "ga" / f"ga_{target}_history.json")
        if not rep:
            out += [f"- `{target}`: no GA evaluation found (run `python sim/optimizer/ga_tuner.py --target {target}`).", ""]
            continue
        if hist:
            out.append(f"**Target `{target}`**: best tuning fitness {hist['best_fitness']:.2f} vs defaults "
                       f"{hist['default_fitness']:.2f} on tuning seeds {hist['settings']['seeds']}; tuned values "
                       f"`{json.dumps(hist['best_chromosome'])}`.")
            out.append("")
        out.append("| Condition | Default fitness | Tuned fitness | Improvement | Seeds better | p-value |")
        out.append("|---|---|---|---|---|---|")
        for c in rep["conditions"]:
            cond = c["condition"]
            p = c.get("p_value_wilcoxon")
            out.append(f"| pen {cond['penetration']}, loss {cond['loss']}{' (tuning)' if c['is_tuning_condition'] else ''} | "
                       f"{c['default_fitness_mean']} ± {c['default_fitness_std']} | {c['tuned_fitness_mean']} ± "
                       f"{c['tuned_fitness_std']} | {c['improvement_pct']}% | {c['seeds_tuned_better']}/{len(rep['seeds'])} | "
                       f"{'n/a' if p is None else f'{p:.3f}'} |")
        out.append("")
    out += ["[Interpretation] Tuning used seeds 101-103 and 600 s runs; the table uses held-out seeds 1-10. Say whether "
            "the gain holds under the other communication condition, and that 12 x 12 evaluations is a small search.", ""]
    return out


def _keep_discussions(lines: List[str], path: Path) -> List[str]:
    """Reuse the hand-written "**Discussion" block of each section from the existing document.

    A block runs from a line starting with "**Discussion" to the next "## " heading. A section without
    one keeps its [Interpretation] placeholder.
    """
    try:
        old = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return lines
    blocks: Dict[str, List[str]] = {}
    heading: Optional[str] = None
    block: Optional[List[str]] = None
    for line in old + ["## end"]:
        if line.startswith("## "):
            if heading and block:
                while block and not block[-1].strip():
                    block.pop()
                blocks[heading] = block
            heading, block = line.strip(), None
        elif line.startswith("**Discussion"):
            block = [line]
        elif block is not None:
            block.append(line)
    out: List[str] = []
    heading = None
    for line in lines:
        if line.startswith("## "):
            heading = line.strip()
        if line.startswith("[Interpretation]") and heading in blocks:
            out.extend(blocks[heading])
        else:
            out.append(line)
    return out


def main() -> int:
    summary = _load(ROOT / "results" / "summary.json")
    if not summary:
        print("results/summary.json not found: run python sim/analytics/evaluate.py --suite all", file=sys.stderr)
        return 1
    suites = summary.get("suites", {})
    lines = [
        "# Results and discussion",
        "",
        f"Numbers generated from `results/summary.json` ({summary.get('generated_at')}, seeds "
        f"{summary.get('meta', {}).get('seeds')}{', QUICK run' if summary.get('meta', {}).get('quick') else ''}) "
        "and `results/ga/`. Values are mean ± standard deviation over seeds. Improvements are versus the suite "
        "baseline (positive = better), with a paired p-value: Wilcoxon signed-rank for 6 or more seeds, otherwise a t-test. "
        "The tables are generated by `python sim/analytics/report.py`. The **Discussion** paragraphs are written "
        "by hand; regenerating keeps them, so check them whenever the numbers change.",
        "",
    ]
    if "signals" in suites:
        lines += section_signals(suites["signals"])
    if "ev" in suites:
        lines += section_ev(suites["ev"])
    if "evroute" in suites:
        lines += section_evroute(suites["evroute"])
    if "nav" in suites:
        lines += section_nav(suites["nav"])
    if "eco" in suites:
        lines += section_eco(suites["eco"])
    lines += section_incident()
    lines += section_ga()
    lines += [
        "## 7. Limitations",
        "",
        "- **Emulated channel.** The VANET is emulated in Python: distance-based loss, delay and jitter, but no MAC contention, interference or obstacles.",
        "- **Small network.** The 2x2 grid offers few alternative routes, so navigation gains are small; results may not transfer to large networks.",
        "- **A\\* finds the same path as Dijkstra** for the same edge costs. Its benefit is fewer node expansions.",
        "- **Model estimates.** Fuel and CO₂ come from SUMO's HBEFA4 emission model (petrol Euro 4 passenger car).",
        "- **Simplified ambulances.** They obey red lights and other cars form no rescue lane, so preemption is what clears their way.",
        "- **Tuning horizon.** GA fitness uses 600 s runs on three seeds; the evaluation uses 1000 s runs on ten held-out seeds.",
        "",
        "## 8. Future work",
        "",
        "- **Real maps.** Use OpenStreetMap networks with SUMO's netconvert, with more junctions and real route choice.",
        "- **Full network simulator.** Run the communication part in OMNeT++/Veins instead of the emulated channel.",
        "- **Reinforcement learning.** RL signal control that uses the same VANET StateProvider.",
        "- **Commitment-based GLOSA.** Adaptive controllers that commit to their next switch a few seconds ahead, so eco-driving also works with adaptive signals.",
        "- **Rescue lane.** Model how other vehicles make way for ambulances (SUMO bluelight device with the sublane model).",
        "",
    ]
    out = ROOT / "docs" / "results_discussion.md"
    lines = _keep_discussions(lines, out)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

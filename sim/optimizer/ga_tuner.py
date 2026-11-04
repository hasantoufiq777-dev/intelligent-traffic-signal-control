"""Genetic algorithm parameter tuning (Module 11).

Two targets:

- signals: [min_green, max_green, queue_threshold, decision_interval, eta_threshold,
  alert_interval, beacon_interval] for adaptive_ev_vanet.
  Fitness (minimise) = avg_waiting_time + w1 * ev_travel_time + w2 * missed_preemptions,
  averaged over the tuning scenarios and seeds, + penalty for constraint violations.
  Output: best_params_signals.yaml, read by the *_tuned controllers.

- aco: [alpha, beta, rho, n_ants, n_iterations, Q, elite_k] for --nav aco.
  Fitness (minimise) = nav_avg_travel_time + w * edge_load_cv + penalty for route-search
  time above a limit. Output: best_params_aco.yaml, read by --nav aco_tuned.

GA: tournament selection, BLX-alpha (blend) crossover, bounded Gaussian mutation, elitism,
a cache of already evaluated chromosomes (kept on disk, so a rerun with the same settings
resumes), and a process pool where each worker runs its own SUMO instance.

Tuning uses seeds 101-103; the tuned parameters are then compared with the defaults on
held-out seeds 1-10, at the tuning communication condition and at other conditions.

Usage:
    python sim/optimizer/ga_tuner.py --target signals [--quick] [--workers 4]
    python sim/optimizer/ga_tuner.py --target aco [--quick]
Logs go to stderr.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import os
from pathlib import Path
import random
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import yaml

logger = logging.getLogger("ga_tuner")

TARGETS = ("signals", "aco")
VANET_GENES = {"alert_interval", "beacon_interval"}
RESULT_KEYS = (
    "avg_waiting_time", "avg_travel_time", "ev_travel_time", "missed_preemptions",
    "throughput_veh_per_hr", "pdr", "nav_avg_travel_time", "nonnav_avg_travel_time",
    "edge_load_cv", "route_search_ms", "route_search_evals",
)


# --------------------------------------------------------------------------- genes
@dataclass(frozen=True)
class Gene:
    name: str
    low: float
    high: float
    kind: str  # "float" | "int"

    def clip(self, value: float) -> float:
        v = min(max(float(value), self.low), self.high)
        return float(int(round(v))) if self.kind == "int" else round(v, 4)


def load_config() -> Dict[str, Any]:
    with open(PROJECT_ROOT / "sim" / "config.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def gene_specs(target: str, ga_cfg: Dict[str, Any]) -> List[Gene]:
    bounds = ga_cfg[target]["bounds"]
    return [Gene(name, float(b[0]), float(b[1]), str(b[2]) if len(b) > 2 else "float") for name, b in bounds.items()]


def default_genes(target: str, cfg: Dict[str, Any], genes: List[Gene]) -> Dict[str, float]:
    """The hand-set defaults from config.yaml, as a chromosome (clipped to the bounds)."""
    if target == "signals":
        src: Dict[str, Any] = {}
        src.update(cfg.get("controllers", {}).get("adaptive", {}))
        src.update(cfg.get("controllers", {}).get("preemption", {}))
        src.update({k: v for k, v in cfg.get("vanet", {}).items() if k in VANET_GENES})
    else:
        src = dict(cfg.get("aco", {}))
    return {g.name: g.clip(src.get(g.name, (g.low + g.high) / 2.0)) for g in genes}


def constraint_violation(target: str, c: Dict[str, float]) -> float:
    """0 when feasible, otherwise how far the chromosome is from feasible."""
    if target == "signals":
        # max_green must exceed min_green by at least one decision step (1 s)
        return max(0.0, c["min_green"] + 1.0 - c["max_green"])
    return max(0.0, c["elite_k"] - c["n_ants"])


def chromosome_to_params(target: str, c: Dict[str, float], network: str) -> Dict[str, Any]:
    """Parameter overrides for run_simulation(params=...)."""
    nested = tuned_file_content(target, c)
    params: Dict[str, Any] = {"network": network}
    if target == "signals":
        params.update(nested)
    else:
        params["aco"] = nested
    return params


def tuned_file_content(target: str, c: Dict[str, float]) -> Dict[str, Any]:
    """The YAML layout the runner reads for *_tuned controllers / --nav aco_tuned."""
    def typed(k: str) -> Any:
        v = c[k]
        return int(v) if float(v).is_integer() and k in ("queue_threshold", "n_ants", "n_iterations", "elite_k") else float(v)

    if target == "signals":
        out = {k: typed(k) for k in c if k not in VANET_GENES}
        out["vanet"] = {k: typed(k) for k in c if k in VANET_GENES}
        return out
    return {k: typed(k) for k in c}


def chromosome_key(c: Dict[str, float]) -> str:
    return json.dumps({k: c[k] for k in sorted(c)}, sort_keys=True)


# --------------------------------------------------------------------------- runs
def run_task(task: Dict[str, Any]) -> Dict[str, Any]:
    """Run one simulation (top level so a process pool can pickle it)."""
    from sim.runner import run_simulation

    tag = hashlib.sha1(json.dumps(task, sort_keys=True, default=str).encode()).hexdigest()[:10]
    with tempfile.TemporaryDirectory(prefix="ga_") as tmp:
        try:
            res = run_simulation(
                scenario=task["scenario"],
                controller=task["controller"],
                seed=int(task["seed"]),
                params=task.get("params"),
                run_id=f"ga_{tag}_{os.getpid()}",
                max_steps=task.get("max_steps"),
                metrics_csv_path=Path(tmp) / "metrics.csv",
                output_dir=Path(tmp),
                nav=task.get("nav", "none"),
                nav_fraction=float(task.get("nav_fraction", 0.3)),
                penetration=float(task["penetration"]),
                loss=float(task["loss"]),
                range_m=float(task["range"]),
                aco_params=task.get("aco_params"),
            )
        except Exception as e:  # a crashed run gets a penalty, it does not stop the GA
            logger.warning("Run failed (%s, seed %s): %r", task["scenario"], task["seed"], e)
            return {"error": repr(e)}
    return {k: res.get(k) for k in RESULT_KEYS}


def _scenario_has_ev(scenario: str) -> bool:
    path = PROJECT_ROOT / "sim" / "scenarios" / f"{scenario}.yaml"
    try:
        with open(path, "r", encoding="utf-8") as f:
            return bool((yaml.safe_load(f) or {}).get("emergency", {}).get("enabled", False))
    except OSError:
        return False


def run_cost(target: str, res: Dict[str, Any], ga_cfg: Dict[str, Any], task: Dict[str, Any]) -> float:
    """Fitness contribution of one simulation run (lower is better)."""
    penalty = float(ga_cfg.get("penalty", 1000.0))
    if "error" in res:
        return 10.0 * penalty
    if target == "signals":
        t_cfg = ga_cfg["signals"]
        cost = float(res.get("avg_waiting_time") or 0.0)
        ev_tt = float(res.get("ev_travel_time") or 0.0)
        if task.get("has_ev"):
            if ev_tt <= 0.0:
                # The EV did not arrive: never reward that with a zero travel time
                ev_tt = float(task.get("max_steps") or 1000.0)
                cost += penalty
            cost += float(t_cfg["w_ev_travel_time"]) * ev_tt
            cost += float(t_cfg["w_missed_preemptions"]) * float(res.get("missed_preemptions") or 0.0)
        return cost
    t_cfg = ga_cfg["aco"]
    nav_tt = float(res.get("nav_avg_travel_time") or 0.0)
    if nav_tt <= 0.0:
        return penalty  # no navigating vehicle finished
    cost = nav_tt + float(t_cfg["w_edge_load_cv"]) * float(res.get("edge_load_cv") or 0.0)
    # Search cost in edge-cost evaluations, not milliseconds: wall-clock time depends on how busy
    # the machine is, which made the fitness of the same chromosome vary between runs.
    over = float(res.get("route_search_evals") or 0.0) / 1000.0 - float(t_cfg["max_search_kevals"])
    return cost + float(t_cfg["w_search_kevals"]) * max(0.0, over)


class Runner:
    """Runs tasks inline or in a process pool (each worker starts its own SUMO)."""

    def __init__(self, workers: int) -> None:
        self.workers = max(1, int(workers))
        self.pool = None
        if self.workers > 1:
            import multiprocessing as mp

            self.pool = mp.get_context("spawn").Pool(self.workers)

    def map(self, tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not tasks:
            return []
        if self.pool is None:
            return [run_task(t) for t in tasks]
        return self.pool.map(run_task, tasks, chunksize=1)

    def close(self) -> None:
        if self.pool is not None:
            self.pool.close()
            self.pool.join()


# --------------------------------------------------------------------------- GA
class GeneticTuner:
    """Real-coded GA over one target's chromosome."""

    def __init__(
        self,
        target: str,
        cfg: Dict[str, Any],
        runner: Runner,
        out_dir: Path,
        population: int,
        generations: int,
        seeds: List[int],
        max_steps: Optional[int],
    ) -> None:
        if target not in TARGETS:
            raise ValueError(f"Unknown target '{target}' (use {TARGETS})")
        self.target = target
        self.cfg = cfg
        self.ga = cfg["ga"]
        self.runner = runner
        self.out_dir = out_dir
        self.population = max(2, int(population))
        self.generations = max(1, int(generations))
        self.seeds = list(seeds)
        self.max_steps = max_steps
        self.genes = gene_specs(target, self.ga)
        self.defaults = default_genes(target, cfg, self.genes)
        self.rng = random.Random(int(self.ga.get("ga_seed", 42)))
        self.condition = dict(self.ga.get("condition", {"penetration": 0.5, "loss": 0.1, "range": 300.0}))
        self.network = str(self.ga.get("network", "grid2x2"))

        if target == "signals":
            self.scenarios = list(self.ga["signals"]["scenarios"])
            self.controller = str(self.ga["signals"]["controller"])
        else:
            self.scenarios = [str(self.ga["aco"]["scenario"])]
            self.controller = str(self.ga["aco"]["controller"])
        self.has_ev = {s: _scenario_has_ev(s) for s in self.scenarios}

        self.cache_path = out_dir / f"ga_{target}_cache.json"
        self.cache: Dict[str, Dict[str, Any]] = self._load_cache()
        self.n_simulations = 0

    # ---- cache -----------------------------------------------------------
    def _context(self) -> Dict[str, Any]:
        """Everything besides the chromosome that changes a fitness value."""
        return {
            "target": self.target, "seeds": self.seeds, "max_steps": self.max_steps,
            "scenarios": self.scenarios, "controller": self.controller, "network": self.network,
            "condition": self.condition, "weights": {k: v for k, v in self.ga[self.target].items() if k != "bounds"},
            "penalty": self.ga.get("penalty"),
        }

    def _load_cache(self) -> Dict[str, Dict[str, Any]]:
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if data.get("context") != json.loads(json.dumps(self._context())):
            logger.info("Ignoring cache %s (settings changed)", self.cache_path)
            return {}
        logger.info("Loaded %d cached chromosomes from %s", len(data.get("entries", {})), self.cache_path)
        return dict(data.get("entries", {}))

    def _save_cache(self) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(
            json.dumps({"context": self._context(), "entries": self.cache}, indent=1), encoding="utf-8"
        )

    # ---- tasks / fitness -------------------------------------------------
    def make_task(self, scenario: str, seed: int, condition: Dict[str, Any], **overrides: Any) -> Dict[str, Any]:
        task = {
            "scenario": scenario, "seed": int(seed), "controller": self.controller,
            "max_steps": self.max_steps, "penetration": condition["penetration"],
            "loss": condition["loss"], "range": condition.get("range", 300.0),
            "has_ev": self.has_ev.get(scenario, False), "params": {"network": self.network},
        }
        if self.target == "aco":
            task["nav"] = "aco"
            task["nav_fraction"] = float(self.ga["aco"]["nav_fraction"])
        task.update(overrides)
        return task

    def evaluate(self, pop: List[Dict[str, float]]) -> List[float]:
        """Fitness of each chromosome; cached ones and infeasible ones are not simulated."""
        penalty = float(self.ga.get("penalty", 1000.0))
        pending: Dict[str, Dict[str, float]] = {}
        for c in pop:
            key = chromosome_key(c)
            if key in self.cache or key in pending:
                continue
            viol = constraint_violation(self.target, c)
            if viol > 0:
                self.cache[key] = {"fitness": penalty * (1.0 + viol), "feasible": False, "runs": []}
            else:
                pending[key] = c

        tasks: List[Tuple[str, Dict[str, Any]]] = []
        for key, c in pending.items():
            params = chromosome_to_params(self.target, c, self.network)
            for scen in self.scenarios:
                for seed in self.seeds:
                    tasks.append((key, self.make_task(scen, seed, self.condition, params=params)))
        if tasks:
            t0 = time.time()
            results = self.runner.map([t for _, t in tasks])
            self.n_simulations += len(tasks)
            logger.info("Evaluated %d chromosome(s) with %d simulations in %.0f s",
                        len(pending), len(tasks), time.time() - t0)
            per_key: Dict[str, List[Tuple[Dict[str, Any], Dict[str, Any]]]] = {}
            for (key, task), res in zip(tasks, results):
                per_key.setdefault(key, []).append((task, res))
            for key, pairs in per_key.items():
                costs = [run_cost(self.target, res, self.ga, task) for task, res in pairs]
                self.cache[key] = {
                    "fitness": round(float(np.mean(costs)), 4),
                    "feasible": not any("error" in r for _, r in pairs),
                    "runs": [dict(r, scenario=t["scenario"], seed=t["seed"]) for t, r in pairs],
                }
            self._save_cache()
        return [float(self.cache[chromosome_key(c)]["fitness"]) for c in pop]

    # ---- operators -------------------------------------------------------
    def random_chromosome(self) -> Dict[str, float]:
        return {g.name: g.clip(self.rng.uniform(g.low, g.high)) for g in self.genes}

    def tournament(self, pop: List[Dict[str, float]], fit: List[float]) -> Dict[str, float]:
        k = min(int(self.ga.get("tournament_size", 3)), len(pop))
        idx = self.rng.sample(range(len(pop)), k)
        return pop[min(idx, key=lambda i: fit[i])]

    def crossover(self, a: Dict[str, float], b: Dict[str, float]) -> Tuple[Dict[str, float], Dict[str, float]]:
        """BLX-alpha: each child gene is uniform on the parents' interval widened by alpha."""
        alpha = float(self.ga.get("blend_alpha", 0.5))
        c1, c2 = {}, {}
        for g in self.genes:
            lo, hi = min(a[g.name], b[g.name]), max(a[g.name], b[g.name])
            d = hi - lo
            c1[g.name] = g.clip(self.rng.uniform(lo - alpha * d, hi + alpha * d))
            c2[g.name] = g.clip(self.rng.uniform(lo - alpha * d, hi + alpha * d))
        return c1, c2

    def mutate(self, c: Dict[str, float]) -> Dict[str, float]:
        """Bounded Gaussian mutation, sigma = mutation_sigma * gene range."""
        rate = float(self.ga.get("mutation_rate", 0.2))
        sigma = float(self.ga.get("mutation_sigma", 0.15))
        out = dict(c)
        for g in self.genes:
            if self.rng.random() < rate:
                out[g.name] = g.clip(out[g.name] + self.rng.gauss(0.0, sigma * (g.high - g.low)))
        return out

    # ---- main loop -------------------------------------------------------
    def run(self) -> Dict[str, Any]:
        n_elite = min(int(self.ga.get("elitism", 2)), self.population)
        cx_rate = float(self.ga.get("crossover_rate", 0.8))
        # The hand-set defaults are always in the first population, so the result is never worse
        # than the defaults on the tuning seeds.
        pop = [dict(self.defaults)] + [self.random_chromosome() for _ in range(self.population - 1)]

        history: List[Dict[str, Any]] = []
        best_c, best_f = None, float("inf")
        default_f = None
        for gen in range(self.generations):
            fit = self.evaluate(pop)
            if gen == 0:
                default_f = fit[0]
            i_best = int(np.argmin(fit))
            if fit[i_best] < best_f:
                best_f, best_c = fit[i_best], dict(pop[i_best])
            feasible = [f for c, f in zip(pop, fit) if self.cache[chromosome_key(c)]["feasible"]
                        and constraint_violation(self.target, c) == 0]
            history.append({
                "generation": gen,
                "best_fitness": round(best_f, 4),
                "generation_best_fitness": round(fit[i_best], 4),
                "mean_fitness": round(float(np.mean(feasible)), 4) if feasible else None,
                "n_feasible": len(feasible),
                "best_chromosome": dict(best_c),
            })
            logger.info("[%s] generation %d/%d: best %.2f, mean %s", self.target, gen + 1,
                        self.generations, best_f, history[-1]["mean_fitness"])
            if gen == self.generations - 1:
                break

            order = sorted(range(len(pop)), key=lambda i: fit[i])
            nxt = [dict(pop[i]) for i in order[:n_elite]]
            while len(nxt) < self.population:
                p1, p2 = self.tournament(pop, fit), self.tournament(pop, fit)
                if self.rng.random() < cx_rate:
                    c1, c2 = self.crossover(p1, p2)
                else:
                    c1, c2 = dict(p1), dict(p2)
                nxt.extend([self.mutate(c1), self.mutate(c2)])
            pop = nxt[: self.population]

        return {
            "target": self.target,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "settings": {**self._context(), "population": self.population, "generations": self.generations},
            "default_chromosome": self.defaults,
            "default_fitness": default_f,
            "best_chromosome": best_c,
            "best_fitness": best_f,
            "n_simulations": self.n_simulations,
            "generations": history,
        }


# --------------------------------------------------------------------------- outputs
def write_outputs(result: Dict[str, Any], out_dir: Path, params_file: Path) -> None:
    from sim.analytics.plots import plot_ga_convergence

    target = result["target"]
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"ga_{target}_history.json").write_text(json.dumps(result, indent=2), encoding="utf-8")

    gene_names = list(result["best_chromosome"].keys())
    with open(out_dir / f"ga_{target}_history.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["generation", "best_fitness", "generation_best_fitness", "mean_fitness", "n_feasible"] + gene_names)
        for g in result["generations"]:
            w.writerow([g["generation"], g["best_fitness"], g["generation_best_fitness"], g["mean_fitness"],
                        g["n_feasible"]] + [g["best_chromosome"][n] for n in gene_names])

    content = tuned_file_content(target, result["best_chromosome"])
    content["meta"] = {
        "target": target,
        "generated_at": result["generated_at"],
        "fitness": result["best_fitness"],
        "default_fitness": result["default_fitness"],
        "tuning_seeds": result["settings"]["seeds"],
        "condition": result["settings"]["condition"],
        "max_steps": result["settings"]["max_steps"],
        "population": result["settings"]["population"],
        "generations": result["settings"]["generations"],
    }
    params_file.parent.mkdir(parents=True, exist_ok=True)
    with open(params_file, "w", encoding="utf-8") as f:
        f.write(f"# GA-tuned parameters (Module 11, target '{target}'). Generated by sim/optimizer/ga_tuner.py\n")
        yaml.safe_dump(content, f, sort_keys=False)

    plot_ga_convergence(result, out_dir / f"ga_{target}_convergence.png")
    logger.info("Wrote %s and history/plot in %s", params_file, out_dir)


def evaluate_heldout(
    tuner: GeneticTuner,
    params_file: Path,
    seeds: List[int],
    conditions: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Defaults vs tuned on held-out seeds, at the tuning condition and the other conditions."""
    from scipy import stats

    target = tuner.target
    jobs: List[Tuple[str, int, str, int, Dict[str, Any]]] = []  # (variant, cond index, scenario, seed, task)
    for ci, cond in enumerate(conditions):
        for scen in tuner.scenarios:
            for seed in seeds:
                jobs.append(("default", ci, scen, seed, tuner.make_task(scen, seed, cond)))
                if target == "signals":
                    tuned_task = tuner.make_task(
                        scen, seed, cond, controller=f"{tuner.controller}_tuned",
                        params={"network": tuner.network, "tuned_params_file": str(params_file)},
                    )
                else:
                    tuned_task = tuner.make_task(scen, seed, cond, nav="aco_tuned", aco_params=str(params_file))
                jobs.append(("tuned", ci, scen, seed, tuned_task))

    logger.info("Held-out evaluation: %d simulations", len(jobs))
    results = tuner.runner.map([j[4] for j in jobs])

    report: Dict[str, Any] = {"target": target, "seeds": seeds, "conditions": []}
    for ci, cond in enumerate(conditions):
        per_seed: Dict[str, Dict[int, List[float]]] = {"default": {}, "tuned": {}}
        metrics: Dict[str, Dict[str, List[float]]] = {"default": {}, "tuned": {}}
        for (variant, cj, scen, seed, task), res in zip(jobs, results):
            if cj != ci:
                continue
            per_seed[variant].setdefault(seed, []).append(run_cost(target, res, tuner.ga, task))
            for k in RESULT_KEYS:
                if res.get(k) is not None:
                    metrics[variant].setdefault(f"{scen}.{k}", []).append(float(res[k]))
        d = [float(np.mean(per_seed["default"][s])) for s in seeds]
        t = [float(np.mean(per_seed["tuned"][s])) for s in seeds]
        diffs = [a - b for a, b in zip(d, t)]
        p_value = None
        if len(seeds) >= 5 and any(abs(x) > 1e-9 for x in diffs):
            try:
                p_value = float(stats.wilcoxon(d, t).pvalue)
            except ValueError:
                p_value = None
        d_mean, t_mean = float(np.mean(d)), float(np.mean(t))
        report["conditions"].append({
            "condition": cond,
            "is_tuning_condition": ci == 0,
            "default_fitness_mean": round(d_mean, 3),
            "tuned_fitness_mean": round(t_mean, 3),
            "default_fitness_std": round(float(np.std(d, ddof=1)) if len(d) > 1 else 0.0, 3),
            "tuned_fitness_std": round(float(np.std(t, ddof=1)) if len(t) > 1 else 0.0, 3),
            "improvement_pct": round(100.0 * (d_mean - t_mean) / d_mean, 2) if d_mean else None,
            "seeds_tuned_better": sum(1 for x in diffs if x > 0),
            "p_value_wilcoxon": p_value,
            "gain_holds": t_mean < d_mean,
            "metrics_mean": {
                v: {k: round(float(np.mean(vals)), 3) for k, vals in sorted(metrics[v].items())}
                for v in ("default", "tuned")
            },
        })
    return report


def write_evaluation(report: Dict[str, Any], out_dir: Path) -> None:
    target = report["target"]
    (out_dir / f"ga_{target}_evaluation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    lines = [
        f"# GA tuning, target `{target}`: defaults vs tuned on held-out seeds",
        "",
        f"Seeds: {report['seeds']}. Fitness is lower-is-better (same formula as during tuning).",
        "p-value: paired Wilcoxon signed-rank test over seeds (needs at least 5 seeds).",
        "",
        "| Condition | Default fitness | Tuned fitness | Improvement | Seeds tuned better | p-value | Gain holds |",
        "|---|---|---|---|---|---|---|",
    ]
    for c in report["conditions"]:
        cond = c["condition"]
        label = f"pen {cond['penetration']}, loss {cond['loss']}, range {cond.get('range', 300)}"
        if c["is_tuning_condition"]:
            label += " (tuning)"
        p = f"{c['p_value_wilcoxon']:.3f}" if c["p_value_wilcoxon"] is not None else "n/a"
        lines.append(
            f"| {label} | {c['default_fitness_mean']} ± {c['default_fitness_std']} | "
            f"{c['tuned_fitness_mean']} ± {c['tuned_fitness_std']} | {c['improvement_pct']}% | "
            f"{c['seeds_tuned_better']}/{len(report['seeds'])} | {p} | {'yes' if c['gain_holds'] else 'no'} |"
        )
    (out_dir / f"ga_{target}_evaluation.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("Wrote held-out evaluation to %s", out_dir / f"ga_{target}_evaluation.md")


# --------------------------------------------------------------------------- CLI
def main(argv: Optional[List[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s [%(levelname)s] ga_tuner: %(message)s")
    ap = argparse.ArgumentParser(description="GA tuning of signal/VANET or ACO parameters (Module 11)")
    ap.add_argument("--target", choices=TARGETS, required=True)
    ap.add_argument("--quick", action="store_true", help="Tiny run for testing (population 4, 2 generations)")
    ap.add_argument("--workers", type=int, default=None, help="Parallel SUMO runs (default: ga.workers)")
    ap.add_argument("--population", type=int, default=None)
    ap.add_argument("--generations", type=int, default=None)
    ap.add_argument("--max-steps", type=int, default=None, help="Simulated seconds per run (default: ga.eval_max_steps)")
    ap.add_argument("--out-dir", type=str, default="results/ga", help="History, cache, plots and evaluation")
    ap.add_argument("--params-file", type=str, default=None, help="Where to write the tuned YAML (default from config)")
    ap.add_argument("--skip-eval", action="store_true", help="Do not run the held-out comparison")
    args = ap.parse_args(argv)

    cfg = load_config()
    ga = cfg["ga"]
    if args.quick:
        population, generations = 4, 2
        tune_seeds, eval_seeds = list(ga["tuning_seeds"])[:1], [1, 2]
        max_steps = 300
        conditions = [ga["condition"]] + list(ga.get("cross_conditions", []))[:1]
    else:
        population, generations = int(ga["population_size"]), int(ga["generations"])
        tune_seeds, eval_seeds = list(ga["tuning_seeds"]), list(ga["eval_seeds"])
        max_steps = ga.get("eval_max_steps")
        conditions = [ga["condition"]] + list(ga.get("cross_conditions", []))
    population = args.population or population
    generations = args.generations or generations
    if args.max_steps is not None:
        max_steps = args.max_steps
    workers = args.workers if args.workers is not None else int(ga.get("workers", 1))

    out_dir = Path(args.out_dir)
    out_dir = out_dir if out_dir.is_absolute() else PROJECT_ROOT / out_dir
    params_file = Path(args.params_file or ga[f"{args.target}_params_file"])
    params_file = params_file if params_file.is_absolute() else PROJECT_ROOT / params_file

    n_scen = len(ga["signals"]["scenarios"]) if args.target == "signals" else 1
    tune_runs = population * generations * n_scen * len(tune_seeds)
    eval_runs = 0 if args.skip_eval else 2 * len(conditions) * n_scen * len(eval_seeds)
    logger.info("Plan: up to %d tuning + %d evaluation simulations (%s s each), %d worker(s)",
                tune_runs, eval_runs, max_steps or "full", workers)

    runner = Runner(workers)
    try:
        tuner = GeneticTuner(args.target, cfg, runner, out_dir, population, generations, tune_seeds, max_steps)
        result = tuner.run()
        write_outputs(result, out_dir, params_file)
        logger.info("Best fitness %.3f (defaults %.3f): %s", result["best_fitness"],
                    result["default_fitness"], result["best_chromosome"])
        if not args.skip_eval:
            report = evaluate_heldout(tuner, params_file, eval_seeds, conditions)
            write_evaluation(report, out_dir)
            for c in report["conditions"]:
                logger.info("Held-out %s: default %.2f -> tuned %.2f (%s%%), gain holds: %s",
                            c["condition"], c["default_fitness_mean"], c["tuned_fitness_mean"],
                            c["improvement_pct"], c["gain_holds"])
    finally:
        runner.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

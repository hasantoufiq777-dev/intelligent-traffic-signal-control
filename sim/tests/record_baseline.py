"""Record regression baseline before Module 4b refactoring."""

from __future__ import annotations

import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sim.runner import run_simulation


def main() -> None:
    scenarios = ["normal", "congested"]
    controllers = ["fixed", "adaptive"]
    seeds = [1, 2, 3]
    max_steps = 200

    baseline: dict[str, dict] = {}

    for sc in scenarios:
        for ctrl in controllers:
            for s in seeds:
                key = f"{sc}_{ctrl}_s{s}"
                print(f"Recording baseline for {key}...", file=sys.stderr)
                res = run_simulation(
                    scenario=sc,
                    controller=ctrl,
                    seed=s,
                    max_steps=max_steps,
                    gui=False,
                    live=False,
                )
                baseline[key] = res

    out_file1 = PROJECT_ROOT / "sim" / "tests" / "regression_baseline.json"
    out_file2 = PROJECT_ROOT / "tests" / "regression_baseline.json"

    out_file1.parent.mkdir(parents=True, exist_ok=True)
    out_file2.parent.mkdir(parents=True, exist_ok=True)

    with open(out_file1, "w", encoding="utf-8") as f:
        json.dump(baseline, f, indent=2)

    with open(out_file2, "w", encoding="utf-8") as f:
        json.dump(baseline, f, indent=2)

    print("Baseline recorded successfully!")


if __name__ == "__main__":
    main()

"""Read committed counts without mixing the primary and baseline execution modes."""
import argparse
import json
from decimal import Decimal
from pathlib import Path

from m1234_sim_metrics import collect, empty_counts


def split_modes(metrics):
    modes = {}
    for mode in ("main", "baseline"):
        totals = {s: {**empty_counts(), "signals": counts["signals"]}
                  for s, counts in metrics["strategies"].items()}
        profiles = {}
        for key, counts in metrics["by_profile_scenario"].items():
            strategy, profile, scenario = key.split("/", 2)
            selected = (not scenario.startswith("main/") if mode == "baseline" else
                        strategy == "M1" or scenario.startswith("main/"))
            if not selected:
                continue
            profiles[key] = counts
            for field, value in counts.items():
                if field == "filled_shares":
                    totals[strategy][field] = str(Decimal(totals[strategy][field]) + Decimal(value))
                else:
                    totals[strategy][field] += value
        modes[mode] = {"strategies": totals, "by_profile_scenario": profiles}
    return {k: v for k, v in metrics.items() if k not in {"strategies", "by_profile_scenario"}} | {
        "primary_mode": "main", "modes": modes,
        "m1_scope": "same two unchanged M1 execution profiles shown in each view; not additive",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(split_modes(collect(args.run, args.cache)), ensure_ascii=False, indent=2))

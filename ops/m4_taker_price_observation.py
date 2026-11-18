"""Update the authorized 0.90–0.95 observation using this review's PnL files."""
import argparse
import json
from pathlib import Path

from polymaker.m4_price_observation import run_observation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--pnl-dir", type=Path, required=True)
    args = parser.parse_args()
    result = run_observation(args.project_dir, args.pnl_dir)
    print(json.dumps({k: result[k] for k in ("status", "summary", "milestones_due") if k in result}, default=str))


if __name__ == "__main__":
    main()

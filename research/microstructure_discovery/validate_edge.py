"""Conservative block test for a single preregistered candidate; no edge declaration."""

import collections
import itertools
import json
import random
import statistics
import sys
from pathlib import Path


def validate(rows, selected_tests=8):
    grouped = collections.defaultdict(list)
    for r in rows:
        grouped[r["delay"]].append(r)
    result = {}
    for delay, rs in grouped.items():
        blocks = collections.defaultdict(list)
        for r in rs:
            blocks[int(r["signal"]["time"] // 300)].append(r["net_per_share"])
        vals = [statistics.mean(v) for v in blocks.values()]
        n = len(vals)
        observation = statistics.mean(vals)
        # Block sign randomization assumes symmetric null block returns. Report assumption.
        if n <= 16:
            null = [
                sum(s * v for s, v in zip(signs, vals, strict=True)) / n
                for signs in itertools.product((-1, 1), repeat=n)
            ]
        else:
            rng = random.Random(917)
            null = [sum(rng.choice((-1, 1)) * v for v in vals) / n for _ in range(65536)]
        p = (1 + sum(v >= observation for v in null)) / (1 + len(null))
        # Percentile block bootstrap lower bound, Bonferroni-adjusted for registered candidates.
        rng = random.Random(624)
        means = sorted(statistics.mean(rng.choices(vals, k=n)) for _ in range(20000))
        lower = means[int(len(means) * 0.05 / selected_tests)]
        result[str(delay)] = dict(
            entries=len(rs),
            blocks=n,
            mean_block_net=observation,
            lower_block_bootstrap=lower,
            adjusted_sign_p=min(1, p * selected_tests),
            passes_minimum=n >= 6 and len(rs) >= 20 and lower > 0 and p * selected_tests < 0.05,
        )
    return dict(
        delays=result,
        passes_statistical_screen=all(
            str(d) in result and result[str(d)]["passes_minimum"] for d in (0.5, 1.0, 2.0)
        ),
        qualifications=[
            "Requires frozen rules/market before collection, independent confirmation window, verified costs and execution.",
            "Block bootstrap and sign test do not establish causal trader information; symmetry/stationarity assumptions apply.",
            "No live fills asserted; this output alone never marks the research goal complete.",
        ],
    )


if __name__ == "__main__":
    root = Path(sys.argv[1])
    data = json.loads((root / "holdout_execution.json").read_text())
    result = validate(data["rows"])
    (root / "edge_validation.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))

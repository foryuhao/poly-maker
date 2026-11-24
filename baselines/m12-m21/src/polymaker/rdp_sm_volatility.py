"""One causal probability-volatility estimate for NORMAL, TP and SL."""

import math
from dataclasses import dataclass

WINDOW_SECONDS = 15
MIN_PAIRS = 5
MIN_DURATION_SECONDS = 10
MAX_PAIR_GAP_SECONDS = 5


def sigma_specification():
    return {"window_seconds": WINDOW_SECONDS, "min_pairs": MIN_PAIRS,
            "min_effective_seconds": MIN_DURATION_SECONDS,
            "max_pair_gap_seconds": MAX_PAIR_GAP_SECONDS,
            "completed_seconds_only": True, "clear_on_reconnect": True,
            "scope": ["NORMAL", "TP", "SL"]}


@dataclass(frozen=True)
class SigmaEstimate:
    sigma: float | None
    pairs: int
    duration: float
    generation: int
    as_of_monotonic: float
    window_seconds: int = WINDOW_SECONDS


def estimate_sigma(feed, *, monotonic, wall, generation):
    """Completed real seconds only; gaps, reconnects and future points add no data."""
    with feed.lock:
        points = [p for p in feed.probability_points
                  if p[3] == generation and p[0] < math.floor(wall)
                  and monotonic-WINDOW_SECONDS <= p[1] <= monotonic]
    pairs = [(a, b) for a, b in zip(points, points[1:], strict=False)
             if 0 < b[1]-a[1] <= MAX_PAIR_GAP_SECONDS]
    duration = sum(b[1]-a[1] for a, b in pairs)
    sigma = (math.sqrt(sum((b[2]-a[2])**2 for a, b in pairs)/duration)
             if len(pairs) >= MIN_PAIRS and duration >= MIN_DURATION_SECONDS else None)
    return SigmaEstimate(sigma, len(pairs), duration, generation, monotonic)

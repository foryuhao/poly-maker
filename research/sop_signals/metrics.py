"""Small pure cohort calculations, kept separate from signal generation."""


def coverage(markets, rows):
    if len(markets) != 24 or len(set(markets)) != 24:
        raise ValueError("PREDECLARED_24_DISTINCT_MARKETS_REQUIRED")
    observations = {}
    for r in rows:
        if r["market"] not in markets or r["market"] in observations:
            raise ValueError("UNDECLARED_OR_DUPLICATED_MARKET_RESULT")
        observations[r["market"]] = r
    raw = [m for m in markets if observations.get(m, {}).get("signal_ids")]
    ready = [m for m in markets if observations.get(m, {}).get(
        "admission_signal_ids", observations.get(m, {}).get("signal_ids"))]
    primary = [m for m in ready if observations[m].get("primary_eligible") is True]
    result = {
        "denominator": 24,
        "all_recorded_signal_markets": len(raw),
        "all_recorded_signal_count": sum(len(set(r.get("signal_ids", []))) for r in rows),
        "primary_signal_markets": len(primary),
        "primary_coverage": len(primary) / 24,
        "frequency_passed": len(primary) >= 10,
        "complete_windows": sum(r.get("complete") is True for r in rows),
        "unknown_markets": [m for m in markets if observations.get(m, {}).get("unknown") is True],
        "isolated_markets": [
            m
            for m in markets
            if m in observations and observations[m].get("primary_eligible") is not True
        ],
        "missing_markets": [m for m in markets if m not in observations],
    }
    if any("admission_signal_ids" in r for r in rows):
        result.update(
            signal_market_basis="EVER_REFERENCE_READY_BEFORE_ORIGINAL_DEADLINE",
            all_recorded_reference_ready_signal_markets=len(ready),
            primary_confirmed_signal_markets=sum(observations[m].get("primary_eligible") is True for m in raw),
        )
    return result

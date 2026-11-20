"""The frequency denominator is the declared cohort, never selected outcomes."""

from sop_signals.metrics import coverage


def test_market_coverage_deduplicates_tokens_and_retains_unknown_denominator():
    markets = [f"market-{i}" for i in range(24)]
    rows = [
        {
            "market": m,
            "signal_ids": ["a", "b", "b"],
            "primary_eligible": True,
            "complete": True,
            "unknown": False,
        }
        for m in markets[:9]
    ]
    rows += [
        {
            "market": markets[9],
            "signal_ids": ["c"],
            "primary_eligible": False,
            "complete": True,
            "unknown": False,
        }
    ]
    result = coverage(markets, rows)
    assert result["all_recorded_signal_markets"] == 10
    assert result["primary_signal_markets"] == 9
    assert result["denominator"] == 24 and not result["frequency_passed"]
    assert len(result["missing_markets"]) == 14


def test_unknown_is_not_a_determined_zero_and_cannot_prove_pass():
    markets = list(range(24))
    rows = [
        {
            "market": m,
            "signal_ids": [str(m)] if m < 10 else [],
            "primary_eligible": m < 9,
            "complete": True,
            "unknown": m >= 9,
        }
        for m in markets
    ]
    result = coverage(markets, rows)
    assert result["primary_signal_markets"] == 9
    assert result["unknown_markets"] == list(range(9, 24))
    assert not result["frequency_passed"]

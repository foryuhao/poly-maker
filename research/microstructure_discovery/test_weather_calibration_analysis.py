import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from weather_calibration_analysis import account, clustered_interval, no_payout


def test_scalar_and_vector_resolutions_agree_and_pending_is_rejected():
    scalar = dict(data=[dict(condition_id="c", status="resolved", price="0")])
    vector = dict(data=[dict(condition_id="c", status="resolved", payouts=[0, 1000000])])
    assert no_payout(scalar, "c") == no_payout(vector, "c") == 1
    with pytest.raises(ValueError):
        no_payout(dict(data=[dict(condition_id="c", status="proposed", price="0")]), "c")


def test_buy_fees_in_shares_and_all_losses_pending_or_missing_retained():
    row = dict(
        city="nyc",
        date="2026-08-01",
        status="SELECTED",
        trade=dict(condition_id="c", price=0.1),
        market=dict(conditionId="c", feeSchedule=dict(rate=0, exponent=1)),
        resolution=dict(data=[dict(condition_id="c", status="resolved", price="0")]),
    )
    assert abs(account(row)["pnl"] - 85.48) < 1e-10
    row["resolution"]["data"][0]["price"] = "1"
    assert account(row)["pnl"] == -10.02
    row["resolution"]["data"][0]["status"] = "proposed"
    assert account(row)["uncertain"] and account(row)["pnl"] == -10.02
    assert account(dict(city="x", date="d", status="DATA_QUALITY_ERROR"))["pnl"] == -10.02
    assert account(dict(city="x", date="d", status="NO_QUALIFYING_FILL"))["notional"] == 0


def test_uma_scaled_price_is_not_a_probability_and_chain_crosscheck_is_required():
    chain = dict(
        chain_id=137,
        condition="c",
        contract="0x4D97DCd97eC945f40cF65F87097ACe5EA0476045",
        denominator=1,
        numerators=[1, 0],
    )
    resolution = dict(data=[dict(condition_id="c", status="resolved", price="1000000000000000000")])
    with pytest.raises(ValueError, match="scalar units"):
        no_payout(resolution, "c")
    assert no_payout(resolution, "c", chain) == 0
    resolution["data"][0]["payouts"] = [0, 1000000]
    with pytest.raises(ValueError, match="disagree"):
        no_payout(resolution, "c", chain)


def test_one_exposed_day_never_produces_a_degenerate_confidence_interval():
    result = clustered_interval(
        [dict(date="2026-08-01", pnl=-10.02, notional=10)], ["2026-08-01", "2026-08-02"]
    )
    assert result["lower"] is None and result["effective_calendar_clusters"] == 1

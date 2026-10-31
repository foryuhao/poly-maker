import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from weather_counterparty_analysis import account


def test_counterparty_direction_capacity_and_loss_not_yes_buy_return():
    row = dict(
        city="x",
        date="d",
        status="SELECTED",
        trade=dict(condition_id="c", token_id="y", price=0.9, size=100),
        market=dict(
            conditionId="c",
            outcomes=["Yes", "No"],
            clobTokenIds=["y", "n"],
            feeSchedule=dict(takerOnly=True, rate=0.05),
        ),
        resolution=dict(data=[dict(condition_id="c", status="resolved", payouts=[0, 1000000])]),
    )
    assert abs(account(row)["pnl"] - 17.98) < 1e-9
    row["resolution"]["data"][0]["payouts"] = [1000000, 0]
    assert account(row)["pnl"] == -2.02
    row["trade"]["size"] = 1
    assert account(row)["uncertain"]

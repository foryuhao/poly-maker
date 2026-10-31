import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from weather_counterparty import choose


def test_real_counterparty_capacity_and_clock_not_synthetic_no_ask():
    event = dict(markets=[dict(conditionId="c", outcomes=["Yes", "No"], clobTokenIds=["y", "n"])])

    def trade(t, p, size=100, token="y"):
        return dict(
            condition_id="c",
            side="BUY",
            timestamp=t,
            price=p,
            size=size,
            token_id=token,
            transaction_hash=str(t),
        )

    rows = [trade(10, 0.9, 10), trade(11, 0.85), trade(12, 0.95), trade(10, 0.9, 100, "n")]
    assert choose(rows, event, 10, 20)["timestamp"] == 11
    assert choose(rows, event, 20, 30) is None

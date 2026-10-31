import datetime as dt
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from weather_calibration import first_candidate, interval


def test_city_clock_uses_local_timezone_and_dst():
    assert interval("nyc", dt.date(2026, 8, 1))[0] == int(
        dt.datetime(2026, 8, 1, 13, tzinfo=dt.UTC).timestamp()
    )
    assert interval("london", dt.date(2026, 10, 30))[0] == int(
        dt.datetime(2026, 10, 30, 9, tzinfo=dt.UTC).timestamp()
    )
    assert interval("tokyo", dt.date(2026, 8, 1))[0] == int(
        dt.datetime(2026, 8, 1, 0, tzinfo=dt.UTC).timestamp()
    )


def test_first_eligible_fill_not_best_outcome_or_later_cheaper_price():
    mapping = {"c": dict(token="n")}

    def fill(t, price, token="n"):
        return dict(
            condition_id="c",
            side="BUY",
            price=price,
            size=1000,
            token_id=token,
            timestamp=t,
            transaction_hash=str(t),
        )

    assert (
        first_candidate([fill(19, 0.05), fill(11, 0.24), fill(10, 0.20, "y")], mapping, 10, 20)[
            "timestamp"
        ]
        == 11
    )
    assert first_candidate([fill(20, 0.1), fill(9, 0.1)], mapping, 10, 20) is None


def test_ignored_filters_are_failures_not_zero_observations():
    row = dict(
        condition_id="c",
        side="BUY",
        price=0.1,
        size=1,
        token_id="n",
        timestamp=10,
        transaction_hash="x",
    )
    with pytest.raises(ValueError, match="CASH"):
        first_candidate([row], {"c": dict(token="n")}, 10, 20)
    with pytest.raises(ValueError, match="foreign"):
        first_candidate([dict(row, condition_id="foreign")], {"c": dict(token="n")}, 10, 20)

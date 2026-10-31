import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from crossvenue_confirm import execution
from crossvenue_scan import calculate, walk
from crossvenue_sequential import evaluate, hedge_cap


def test_public_binary_bid_conversion_and_real_fees():
    trigger = {
        "calculations": {
            "poly_no_kalshi_yes": dict(
                poly_levels=[(0.45, 10)], kalshi_levels=[(0.45, 10)], poly_gross_shares=10
            )
        }
    }
    books = dict(
        poly=dict(data=dict(asks=[dict(price=".45", size="100")])),
        kalshi=dict(data=dict(orderbook_fp=dict(no_dollars=[[".55", "100"]]))),
    )
    result = execution(books, trigger, "poly_no_kalshi_yes", {"r": 0})
    assert abs(result["paired_floor_net"] - 0.82) < 1e-9
    books["poly"]["data"]["asks"][0]["price"] = ".46"
    result = execution(books, trigger, "poly_no_kalshi_yes", {"r": 0})
    assert not result["poly_fok_available"] and result["kalshi_fok_available"]


def test_share_fee_uses_real_paired_delivery_when_execution_price_changes():
    trigger = {
        "calculations": {
            "poly_no_kalshi_yes": dict(
                poly_levels=[(0.57, 10.22)], kalshi_levels=[(0.36, 10)], poly_gross_shares=10.22
            )
        }
    }
    books = dict(
        poly=dict(data=dict(asks=[dict(price=".55", size="100")])),
        kalshi=dict(data=dict(orderbook_fp=dict(no_dollars=[[".64", "100"]]))),
    )
    result = execution(books, trigger, "poly_no_kalshi_yes", {"r": 0.05})
    assert result["poly_delivered"] < 10
    assert (
        abs(result["paired_floor_net"] - (result["poly_delivered"] - 10.22 * 0.55 - 3.6 - 0.17))
        < 1e-9
    )


def test_sizing_and_insufficient_depth_not_optimistic_midpoint_marks():
    result = calculate({"asks": [dict(price=".50", size="100")]}, [(0.45, 100)], 0.05, 1)
    assert result["poly_gross_shares"] == 10.26
    assert 0 < result["net_per_contract"] < 0.05
    assert calculate({"asks": [dict(price=".50", size="5")]}, [(0.45, 100)], 0.05, 1) is None
    assert walk([(-0.40, 3), (-0.42, 4)], 5)[0] == -(4 * 0.42 + 0.40)


def test_sequential_cap_is_decided_before_future_book_and_failed_hedge_loses():
    trigger = {
        "calculations": {
            "poly_no_kalshi_yes": dict(
                poly_levels=[(0.57, 10.22)], kalshi_levels=[(0.36, 10)], poly_gross_shares=10.22
            )
        }
    }
    snaps = {
        0.5: dict(
            books=dict(
                kalshi=dict(received=0.7, data=dict(orderbook_fp=dict(no_dollars=[[".64", "100"]])))
            )
        ),
        2.5: dict(
            books=dict(poly=dict(requested=2.5, data=dict(asks=[dict(price=".60", size="100")])))
        ),
        5.0: dict(
            books=dict(kalshi=dict(data=dict(orderbook_fp=dict(yes_dollars=[[".35", "100"]]))))
        ),
    }
    assert abs(hedge_cap(10.22, 3.77, 0.05, 0.01) - 0.60) < 1e-9
    row = evaluate(trigger, "poly_no_kalshi_yes", snaps, {"r": 0.05}, 0.01, 1)
    assert row["status"] == "PAIRED_COUNTERFACTUAL" and abs(row["net"] - 0.078) < 1e-8
    snaps[2.5]["books"]["poly"]["data"]["asks"][0]["price"] = ".61"
    row = evaluate(trigger, "poly_no_kalshi_yes", snaps, {"r": 0.05}, 0.01, 1)
    assert row["status"] == "HEDGE_REJECTED_UNWIND" and abs(row["net"] + 0.45) < 1e-8
    snaps[2.5]["books"]["poly"]["requested"] = 1.0
    assert (
        evaluate(trigger, "poly_no_kalshi_yes", snaps, {"r": 0.05}, 0.01, 1)["status"]
        == "LATENCY_ABORT_UNWIND"
    )
    snaps[0.5]["books"]["kalshi"]["data"]["orderbook_fp"]["no_dollars"][0][0] = ".63"
    row = evaluate(trigger, "poly_no_kalshi_yes", snaps, {"r": 0.05}, 0.01, 1)
    assert row["status"] == "NO_FIRST_FILL" and row["net"] == 0

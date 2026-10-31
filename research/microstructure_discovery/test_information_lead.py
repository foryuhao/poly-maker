import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from information_lead import cash_fill, lead_gate, plan


def test_usd_fok_spends_budget_and_buys_more_when_price_improves():
    book = dict(asks=[dict(price=".50", size="100")])
    got = cash_fill(book, 7, 0.69, 0.05)
    assert got["cash"] == 7
    assert got["levels"][0][1] == 14
    assert got["token_floor"] > 10
    assert cash_fill(dict(asks=[dict(price=".50", size="10")]), 7, 0.69, 0.05) is None


def test_training_case_has_positive_strict_cost_floor_and_valid_minimum():
    poly = dict(
        bids=[dict(price=".68", size="1000")],
        asks=[dict(price=".69", size="1000")],
        min_order_size="5",
    )
    kalshi = dict(yes_dollars=[[".20", "1000"]], no_dollars=[[".79", "1000"]])
    spec = plan(poly, kalshi, "yes", 0.05)
    assert spec["poly_budget"] == 7.13
    assert spec["kalshi_fee_bound"] == 0.22
    assert abs(spec["margin_floor"] - 0.53) < 1e-8
    tiny = dict(
        poly, asks=[dict(price=".003", size="1000")], bids=[dict(price=".002", size="1000")]
    )
    assert plan(tiny, kalshi, "yes", 0.05) is None


def test_lead_signal_requires_reference_move_and_unchanged_local_ask():
    old = dict(kalshi={"k": {"yes": [0.31, 0.32]}}, poly_ask={"a": 0.69})
    cur = dict(kalshi={"k": {"yes": [0.20, 0.21]}}, poly_ask={"a": 0.69})
    assert lead_gate(old, cur, "k", "a", "yes")
    bad = json.loads(json.dumps(cur))
    bad["poly_ask"]["a"] = 0.81
    assert not lead_gate(old, bad, "k", "a", "yes")
    bad = json.loads(json.dumps(cur))
    bad["kalshi"]["k"]["yes"] = [0.30, 0.31]
    assert not lead_gate(old, bad, "k", "a", "yes")

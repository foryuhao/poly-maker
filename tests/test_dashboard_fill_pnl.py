"""Fill-level PnL is shown only after position history and settlement reconcile."""

import copy
from decimal import Decimal

import pytest

from polymaker.dashboard.fill_pnl import fill_pnls
from polymaker.dashboard.m1_trades import trade_records
from polymaker.dashboard.pm_account import apply_account, fetch_account
from test_dashboard_pm_account import (
    CONDITION,
    WALLET,
    activity,
    position,
    resolution,
    session,
    transport_for,
)


def example(*, trades=None, positions=None):
    s = session()
    result = fetch_account(WALLET, [s], {CONDITION: resolution()},
        transport=transport_for(rows=positions, trades=trades))[CONDITION]
    apply_account(s, result)
    return s


def two_buys():
    return [activity(size=4, usdc_size=.4), activity(size=6, usdc_size=.6)]


def test_complete_zero_fee_buy_fills_get_individual_settlement_pnl():
    s = example(trades=two_buys())
    result = fill_pnls(s)
    values = [Decimal(result[t["id"]]["pnl"]) for t in s["pm_account"]["trades"]]
    assert values == [Decimal("3.6"), Decimal("5.4")]
    assert sum(values) == Decimal(s["trading_pnl"])
    rows = trade_records([s])
    actual = [r for r in rows if r.get("record_type") == "ACCOUNT_TRADE"]
    assert all(r["pnl"] is not None for r in actual)
    order = next(r for r in rows if r.get("record_type") != "ACCOUNT_TRADE")
    assert order["pnl"] is None and order["pnl_pending"] == "未归单"
    assert s["orders"][0]["confirmed_quantity"] is None


def test_losing_fills_use_actual_spend_not_the_one_dollar_target():
    s = example(trades=two_buys())
    account = s["pm_account"]
    account["resolution"]["winner"] = "NO"
    account["settled_pnl"] = "-1"
    values = list(fill_pnls(s).values())
    assert [Decimal(r["pnl"]) for r in values] == [Decimal("-.4"), Decimal("-.6")]


@pytest.mark.parametrize("issue", ["partial_history", "sell", "unknown_fee", "nonzero_fee", "inconsistent_pnl"])
def test_ambiguous_histories_or_fees_remain_unattributed(issue):
    s = example(trades=two_buys())
    account = s["pm_account"]
    if issue == "partial_history":
        account["trades"] = account["trades"][:1]
    elif issue == "sell":
        account["trades"][1]["side"] = "SELL"
    elif issue == "unknown_fee":
        account["positions"][0].pop("entry_fees_usdc")
    elif issue == "nonzero_fee":
        account["positions"][0]["entry_fees_usdc"] = ".02"
    else:
        account["positions"][0]["total_pnl"] = "6.7"
    assert all(row["pnl"] is None for row in fill_pnls(s).values())


def test_single_fill_can_take_exact_official_profit_including_known_fees():
    s = example(positions=[position(entry_fees_usdc=.02, total_cost_usdc=1.02,
                                    unrealized_pnl=6.58, total_pnl=6.58)])
    assert next(iter(fill_pnls(s).values()))["pnl"] == "8.98"


def test_single_fill_does_not_inherit_unexplained_position_profit():
    s = example()
    s["pm_account"]["positions"][0]["total_pnl"] = "1000"
    assert next(iter(fill_pnls(s).values()))["pnl"] is None


def test_closed_single_fill_does_not_need_a_guessed_winner():
    s = example()
    account = s["pm_account"]
    account["resolution"] = None
    account["positions"][0].update(current_size="0", current_value="0", status="CLOSED", total_pnl="9")
    assert next(iter(fill_pnls(s).values()))["pnl"] == "9"


def test_unresolved_or_conflicting_market_identity_is_not_given_settlement_profit():
    s = example(trades=two_buys())
    unknown = copy.deepcopy(s)
    unknown["pm_account"]["settled_pnl"] = None
    assert all(r == {"pnl": None, "pending": "待结算"} for r in fill_pnls(unknown).values())
    s["pm_account"]["resolution"]["condition_id"] = "foreign-market"
    assert all(r["pnl"] is None for r in fill_pnls(s).values())


def test_small_aggregate_rounding_gap_does_not_change_actual_fill_calculation():
    s = example(trades=[activity(size=4.00005, usdc_size=.400005), activity(size=6, usdc_size=.6)])
    values = [Decimal(r["pnl"]) for r in fill_pnls(s).values()]
    assert values == [Decimal("3.600045"), Decimal("5.4")]
    assert sum(values) == Decimal("9.000045") and s["trading_pnl"] == "9.0"

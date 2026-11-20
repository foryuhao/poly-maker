from m1223_report import summary


def order(market, pnl, *, price=".50", unresolved=False):
    return {
        "market": market,
        "assumed_gross_pnl": str(pnl),
        "known_simulated_gross_pnl": str(pnl),
        "has_positive_modelled_fill": True,
        "price": price,
        "unresolved": unresolved,
        "official_winner": "YES",
    }


def test_trim_only_profitable_markets_and_keep_every_losing_market():
    rows = [order("a", 10), order("a", 1), order("b", 7), order("c", -20), order("d", -3)]
    result = summary(rows, 5, "simulated")
    assert result["settled_gross_pnl"] == "-5"
    assert result["trading_markets"] == 4 and result["orders"] == 5
    assert result["drop_best_profitable_markets"]["1"]["settled_gross_pnl"] == "-16"
    assert result["drop_best_profitable_markets"]["3"]["settled_gross_pnl"] == "-23"
    assert result["drop_best_profitable_markets"]["3"]["removed_markets"] == ["a", "b"]


def test_unknown_execution_does_not_make_assumed_ledger_unknown():
    rows = [order("a", 1, unresolved=True)]
    assert not summary(rows, 1, "simulated")["full_execution_pnl_known"]
    assert summary(rows, 1, "assumed")["full_execution_pnl_known"]


def test_no_sample_pnl_is_missing_and_pending_settlement_is_not_imputed():
    assert summary([], 0, "simulated")["settled_gross_pnl"] is None
    r = order("a", 0)
    r.update(official_winner=None, assumed_gross_pnl=None, known_simulated_gross_pnl=None)
    result = summary([r], 1, "simulated")
    assert result["settled_gross_pnl"] is None
    assert result["pending_settlement_orders"] == 1
    assert not result["full_execution_pnl_known"]


def test_tail_contribution_is_explicitly_one_through_two_cent_inclusive():
    rows = [order("a", 20, price=".01"), order("b", 5, price=".02"), order("c", 9, price=".025")]
    result = summary(rows, 3, "assumed")
    assert result["one_to_two_cent_contribution"] == {"orders": 2, "settled_gross_pnl": "25"}

from copy import deepcopy

import pytest
from m1223_settlement_closeout import supplement_orders


def order(**changes):
    return {"market": "market-one", "token": "up", "price": "0.3", "quantity": "10",
            "known_filled_quantity": "2", "has_positive_modelled_fill": True,
            "unresolved": True, "primary_eligible": False, "official_winner": None,
            "assumed_gross_pnl": None, "known_simulated_gross_pnl": None, **changes}


def resolution(**changes):
    return {"slug": "market-one", "token_ids": {"YES": "up", "NO": "down"},
            "winner": "YES", **changes}


def test_partial_fill_settlement_does_not_resolve_execution_or_modify_source():
    original = [order()]
    before = deepcopy(original)
    updated = supplement_orders(original, {"market-one": resolution()})
    assert original == before
    assert updated[0]["assumed_gross_pnl"] == "7.0"
    assert updated[0]["known_simulated_gross_pnl"] == "1.4"
    assert updated[0]["unresolved"] is True
    assert updated[0]["primary_eligible"] is False
    assert updated[0]["known_filled_quantity"] == "2"


def test_pending_remains_missing_and_no_positive_fill_is_not_created():
    pending = order()
    assert supplement_orders([pending], {}) == [pending]
    empty = order(known_filled_quantity="0", has_positive_modelled_fill=False)
    updated = supplement_orders([empty], {"market-one": resolution(winner="NO")})[0]
    assert updated["assumed_gross_pnl"] == "-3.0"
    assert updated["known_simulated_gross_pnl"] == "-0.0"
    assert updated["has_positive_modelled_fill"] is False
    assert updated["unresolved"] is True


@pytest.mark.parametrize("row,official", [
    (order(), resolution(slug="another-market")),
    (order(token="other-token"), resolution()),
    (order(), resolution(token_ids={"YES": "up", "NO": "up"})),
    (order(official_winner="NO"), resolution()),
    (order(assumed_gross_pnl="999"), resolution()),
    (order(known_filled_quantity="11"), resolution()),
    (order(quantity="20"), resolution()),
    (order(price="NaN"), resolution()),
    (order(has_positive_modelled_fill=False), resolution()),
])
def test_conflicting_identity_quantity_or_payout_is_rejected(row, official):
    with pytest.raises(ValueError):
        supplement_orders([row], {"market-one": official})

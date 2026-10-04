"""Deterministic tests for the pure MakerInventory controller."""

from dataclasses import replace
from decimal import Decimal

import pytest

from polymaker.domain import Side
from polymaker.strategy.maker_inventory import (
    Action,
    MakerInventoryBook,
    MakerInventoryConfig,
    MakerInventoryOrder,
    MakerInventorySnapshot,
    MakerInventoryState,
    OrderLifecycle,
    Outcome,
    evaluate,
)


def d(value):
    return Decimal(str(value))


def snapshot(
    p="0.5",
    *,
    yes_qty="0",
    no_qty="0",
    cash="100",
    elapsed="100",
    valid=True,
    step="0.01",
    venue_min="5",
):
    probability = d(p)
    return MakerInventorySnapshot(
        yes=MakerInventoryBook(probability - d("0.01"), probability + d("0.01")),
        no=MakerInventoryBook(d("1") - probability - d("0.01"), d("1") - probability + d("0.01")),
        confirmed_yes_qty=yes_qty,
        confirmed_no_qty=no_qty,
        cash_available_for_new_order=cash,
        elapsed_s=elapsed,
        data_valid=valid,
        tick_size="0.01",
        venue_min_order_size=venue_min,
        quantity_step=step,
    )


def order(
    outcome=Outcome.YES,
    *,
    price="0.59",
    remaining="10",
    reserved="10",
    lifecycle=OrderLifecycle.WORKING,
):
    return MakerInventoryState(
        MakerInventoryOrder(
            outcome=outcome,
            price=price,
            remaining_qty=remaining,
            reserved_cash=reserved,
            lifecycle=lifecycle,
        )
    )


@pytest.mark.parametrize(
    "p,target",
    [
        ("0.10", "-20"),
        ("0.20", "-15"),
        ("0.30", "-10"),
        ("0.40", "-5"),
        ("0.50", "0"),
        ("0.60", "5"),
        ("0.65", "7.5"),
        ("0.70", "10"),
        ("0.80", "15"),
        ("0.90", "20"),
    ],
)
def test_probability_and_continuous_target(p, target):
    result = evaluate(snapshot(p))
    assert result.p == d(p)
    assert result.n_target == d(target)


@pytest.mark.parametrize(
    "p,action,qty",
    [
        ("0.5998", Action.WAIT, None),  # target +4.99
        ("0.6000", Action.PLACE_BUY_YES, d("5")),
        ("0.4002", Action.WAIT, None),  # target -4.99
        ("0.4000", Action.PLACE_BUY_NO, d("5")),
        ("0.6500", Action.PLACE_BUY_YES, d("7.5")),
    ],
)
def test_threshold_and_continuous_order_size(p, action, qty):
    # A 0.0001 tick is needed to express the 4.99 boundary in both books.
    s = replace(snapshot(p), tick_size="0.0001")
    result = evaluate(s)
    assert (result.action, result.qty) == (action, qty)


def test_uses_net_confirmed_inventory_and_reports_pairs():
    result = evaluate(snapshot("0.60", yes_qty="10", no_qty="5"))
    assert (result.n_actual, result.q_pair, result.q_residual) == (d(5), d(5), d(5))
    assert (result.delta, result.tracking_error) == (d(0), d(0))
    assert result.action is Action.WAIT


def test_full_direction_flip_when_cash_allows():
    result = evaluate(snapshot("0.10", yes_qty="20", cash="100"))
    assert result.action is Action.PLACE_BUY_NO
    assert result.qty == d(40)
    assert result.reducing_old_exposure is False
    assert (result.potential_inventory_min, result.potential_inventory_max) == (d(-20), d(20))


def test_cash_constrained_flip_reduces_old_exposure_before_crossing_zero():
    result = evaluate(snapshot("0.10", yes_qty="20", cash="22.75"))  # over 25 NO @ .89
    assert result.action is Action.PLACE_BUY_NO
    assert result.qty == d(20)
    assert result.reducing_old_exposure is True
    assert (result.potential_inventory_min, result.potential_inventory_max) == (d(0), d(20))


def test_small_old_exposure_is_not_rounded_up_during_constrained_flip():
    result = evaluate(snapshot("0.30", yes_qty="3", cash="5"))
    assert result.action is Action.WAIT
    assert result.reason == "FLIP_REDUCTION_BELOW_MIN"


def test_flip_is_symmetric_for_old_no_exposure():
    result = evaluate(snapshot("0.90", no_qty="20", cash="22.75"))
    assert result.action is Action.PLACE_BUY_YES
    assert result.qty == d(20)
    assert result.reducing_old_exposure
    assert result.potential_inventory_max == d(0)


def test_quantity_step_rounds_down_without_enlarging_order():
    result = evaluate(snapshot("0.65", step="0.1"))
    assert result.qty == d("7.5")
    result = evaluate(snapshot("0.65", step="0.2"))
    assert result.qty == d("7.4")
    assert result.qty < result.delta


def test_venue_minimum_can_prevent_a_subminimum_adjustment():
    result = evaluate(snapshot("0.65", venue_min="8"))
    assert result.action is Action.WAIT
    assert result.reason == "INSUFFICIENT_SIZE_OR_CASH"
    assert result.qty is None


def test_quantized_full_flip_keeps_near_full_order():
    result = evaluate(snapshot("0.10", yes_qty="20", step="0.3"))
    assert result.action is Action.PLACE_BUY_NO
    assert result.qty == d("39.9")
    assert not result.reducing_old_exposure
    assert result.potential_inventory_min == d("-19.9")


def test_quantized_full_flip_is_symmetric_for_no_to_yes():
    result = evaluate(snapshot("0.90", no_qty="20", step="0.3"))
    assert result.action is Action.PLACE_BUY_YES
    assert result.qty == d("39.9")
    assert not result.reducing_old_exposure
    assert result.potential_inventory_max == d("19.9")


@pytest.mark.parametrize(
    "p,yes_qty,no_qty,action,qty",
    [
        ("0.50", "20", "0", Action.PLACE_BUY_NO, "20"),
        ("0.50", "0", "20", Action.PLACE_BUY_YES, "20"),
        ("0.60", "20", "0", Action.PLACE_BUY_NO, "15"),
        ("0.40", "0", "20", Action.PLACE_BUY_YES, "15"),
    ],
)
def test_pure_reduction_flag_covers_zero_and_same_side_targets(p, yes_qty, no_qty, action, qty):
    result = evaluate(snapshot(p, yes_qty=yes_qty, no_qty=no_qty))
    assert result.action is action
    assert result.qty == d(qty)
    assert result.reducing_old_exposure
    assert result.potential_inventory_min * result.potential_inventory_max >= d(0)


def test_cash_constrained_quantized_flip_stays_pure_reduction():
    result = evaluate(snapshot("0.10", yes_qty="20", cash="22.25", step="0.3"))
    assert result.action is Action.PLACE_BUY_NO
    assert result.qty == d("19.8")
    assert result.reducing_old_exposure
    assert result.potential_inventory_min == d("0.2")


def test_cash_cap_and_inventory_cap_never_expand_desired_quantity():
    cash = evaluate(snapshot("0.90", cash="6"))
    assert cash.action is Action.PLACE_BUY_YES
    assert cash.qty == d("6.74")  # floor(6 / .89) to .01 shares
    bound = evaluate(snapshot("0.90", yes_qty="23", no_qty="20"))
    assert bound.action is Action.PLACE_BUY_YES
    assert bound.qty == d(17)  # target 20, net 3
    hard = evaluate(snapshot("0.90", yes_qty="23"))
    assert hard.action is Action.WAIT  # desired already points away from +23


def test_hard_inventory_bound_rejects_a_five_share_buy_from_plus_23():
    # Even a target clamped to +25 leaves only two shares of desired adjustment.
    result = evaluate(
        snapshot("0.60", yes_qty="23"), config=MakerInventoryConfig(target_scale=d(250))
    )
    assert result.n_target == d(25)
    assert result.action is Action.WAIT
    assert result.potential_inventory_max == d(23)


@pytest.mark.parametrize("lifecycle", list(OrderLifecycle))
def test_every_order_lifecycle_blocks_second_place(lifecycle):
    result = evaluate(snapshot("0.60"), order(lifecycle=lifecycle))
    assert result.action not in (Action.PLACE_BUY_YES, Action.PLACE_BUY_NO)


def test_existing_order_potential_inventory_uses_remaining_not_confirmed():
    result = evaluate(snapshot("0.90", yes_qty="20"), order(remaining="4"))
    assert result.n_actual == d(20)
    assert result.potential_inventory_min == d(20)
    assert result.potential_inventory_max == d(24)


def test_partial_fill_remainder_uses_updated_confirmed_inventory():
    s = snapshot("0.20", yes_qty="12", no_qty="7")
    result = evaluate(
        s,
        order(
            Outcome.NO,
            price="0.79",
            remaining="5",
            reserved="4",
            lifecycle=OrderLifecycle.PARTIAL_REMAINDER_WORKING,
        ),
    )
    assert result.n_actual == d(5)
    assert result.potential_inventory_min == d(0)
    assert result.action is Action.KEEP_EXISTING_ORDER


def test_partial_fill_remainder_below_new_order_minimum_is_kept():
    s = snapshot("0.20", yes_qty="12", no_qty="7")
    result = evaluate(
        s,
        order(
            Outcome.NO,
            price="0.79",
            remaining="4",
            reserved="4",
            lifecycle=OrderLifecycle.PARTIAL_REMAINDER_WORKING,
        ),
    )
    assert result.action is Action.KEEP_EXISTING_ORDER
    assert result.potential_inventory_min == d("1")


def test_best_bid_change_cancels_without_replacing():
    result = evaluate(snapshot("0.60"), order(price="0.58"))
    assert (result.action, result.reason) == (Action.CANCEL_EXISTING_ORDER, "BEST_BID_CHANGED")
    assert result.qty is None


def test_direction_change_cancels_without_opposite_place():
    result = evaluate(snapshot("0.40"), order(price="0.39"))
    assert (result.action, result.reason) == (Action.CANCEL_EXISTING_ORDER, "DIRECTION_CHANGED")
    assert result.side is None


def test_order_no_longer_needed_cancels():
    result = evaluate(snapshot("0.50"), order(price="0.49"))
    assert (result.action, result.reason) == (
        Action.CANCEL_EXISTING_ORDER,
        "ORDER_NO_LONGER_NEEDED",
    )


def test_small_target_drift_keeps_order_without_resizing():
    result = evaluate(snapshot("0.68"), order(remaining="10"))
    assert result.delta == d(9)
    assert result.action is Action.CANCEL_EXISTING_ORDER  # old price differs by 9 ticks
    same_price = evaluate(snapshot("0.68"), order(price="0.67", remaining="10"))
    assert same_price.action is Action.KEEP_EXISTING_ORDER


@pytest.mark.parametrize(
    "valid,elapsed,reason",
    [
        (False, "100", "DATA_INVALID"),
        (True, "285", "FINAL_NO_BUY_WINDOW"),
        (True, "300", "FINAL_NO_BUY_WINDOW"),
    ],
)
def test_data_and_time_gate(valid, elapsed, reason):
    s = snapshot("0.60", valid=valid, elapsed=elapsed)
    empty, active = evaluate(s), evaluate(s, order())
    assert (empty.action, empty.reason) == (Action.WAIT, reason)
    assert (active.action, active.reason) == (Action.CANCEL_EXISTING_ORDER, reason)


def test_working_order_cash_or_potential_violation_cancels():
    s = snapshot("0.90", yes_qty="23")
    bound = evaluate(s, order(price="0.89", remaining="5"))
    assert (bound.action, bound.reason) == (
        Action.CANCEL_EXISTING_ORDER,
        "POTENTIAL_INVENTORY_BOUND",
    )
    cash = evaluate(snapshot("0.60", cash="0"), order(reserved="1"))
    assert (cash.action, cash.reason) == (Action.CANCEL_EXISTING_ORDER, "WORKING_ORDER_CASH")


def test_pending_submit_can_request_cancel_but_never_place_again():
    pending = order(lifecycle=OrderLifecycle.SUBMIT_PENDING)
    assert evaluate(snapshot("0.60"), pending).action is Action.WAIT_FOR_ORDER_LIFECYCLE
    assert evaluate(snapshot("0.60", valid=False), pending).action is Action.CANCEL_EXISTING_ORDER
    assert evaluate(snapshot("0.60", elapsed="285"), pending).action is Action.CANCEL_EXISTING_ORDER


def test_cancel_pending_waits_for_lifecycle_confirmation():
    pending = order(lifecycle=OrderLifecycle.CANCEL_PENDING)
    result = evaluate(snapshot("0.40"), pending)
    assert result.action is Action.WAIT_FOR_ORDER_LIFECYCLE
    assert result.potential_inventory_max == d(10)


def test_missing_book_and_out_of_bound_confirmed_inventory_fail_closed():
    missing = replace(snapshot("0.60"), yes=MakerInventoryBook(None, None))
    assert evaluate(missing).reason == "DATA_INVALID"
    assert evaluate(missing, order()).action is Action.CANCEL_EXISTING_ORDER
    assert evaluate(snapshot("0.10", yes_qty="26")).reason == ("CONFIRMED_INVENTORY_OUT_OF_BOUNDS")
    bad_tick = replace(snapshot("0.60"), yes=MakerInventoryBook("0.595", "0.61"))
    assert evaluate(bad_tick).reason == "DATA_INVALID"


@pytest.mark.parametrize(
    "p,yes_qty,no_qty,cash",
    [
        ("0.10", "20", "0", "100"),
        ("0.10", "20", "0", "22.75"),
        ("0.90", "0", "20", "22.75"),
        ("0.65", "0", "0", "100"),
        ("0.90", "24", "0", "100"),
        ("0.50", "5", "0", "100"),
    ],
)
def test_place_invariants(p, yes_qty, no_qty, cash):
    result = evaluate(snapshot(p, yes_qty=yes_qty, no_qty=no_qty, cash=cash))
    if result.action not in (Action.PLACE_BUY_YES, Action.PLACE_BUY_NO):
        return
    assert result.side is Side.BUY
    assert result.qty is not None and result.qty >= d(5)
    assert -d(25) <= result.potential_inventory_min <= result.potential_inventory_max <= d(25)
    assert result.qty <= abs(result.delta)
    assert result.price * result.qty <= d(cash)
    if result.reducing_old_exposure:
        assert result.qty <= abs(result.n_actual)
        assert result.potential_inventory_min * result.potential_inventory_max >= d(0)


def test_config_rejects_invalid_limits():
    with pytest.raises(ValueError):
        MakerInventoryConfig(max_abs_inventory=d(0))

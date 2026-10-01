"""Tests for user-WS normalization (maker fill extraction + order tracking).

Frames modeled on v1's observed shape; reconfirm field names in the wallet spike.
"""

from __future__ import annotations

import pytest

from polymaker.domain import Side, TradeState
from polymaker.userstream.parse import normalize_order, normalize_trade, parse_trade

OUR = "0xMyWallet"


def _other(token: str) -> str | None:
    return {"yes-tok": "no-tok", "no-tok": "yes-tok"}.get(token)


def test_documented_user_ws_maker_buy_fixture_uses_entry_fields():
    msg = {
        "event_type": "trade",
        "market": "0xcond",
        "asset_id": "yes-tok",
        "side": "BUY",
        "outcome": "Yes",
        "status": "MATCHED",
        "id": "trade1",
        "timestamp": "1700000000000",
        "type": "TRADE", "taker_order_id": "OTHER_ORDER", "size": "99", "price": "0.90",
        "maker_orders": [
            {"order_id": "OUR_ORDER_ID", "asset_id": "yes-tok", "matched_amount": "2.5",
             "price": "0.18", "side": "BUY", "owner": "owner", "maker_address": OUR},
        ],
    }
    evs = normalize_trade(msg, OUR, _other)
    assert len(evs) == 1
    ev = evs[0]
    assert ev.token_id == "yes-tok"
    assert ev.our_side is Side.BUY
    assert ev.maker_order_id == "OUR_ORDER_ID"
    assert ev.size == 2.5 and ev.price == 0.18
    assert ev.status is TradeState.MATCHED


def test_maker_entry_side_and_asset_are_authoritative():
    # taker BUYs YES; we are the maker on NO -> a mint: we BUY NO
    msg = {
        "event_type": "trade",
        "market": "0xcond",
        "asset_id": "yes-tok",
        "side": "BUY",
        "outcome": "Yes",
        "status": "MATCHED",
        "id": "trade2",
        "timestamp": "1700000000000",
        "maker_orders": [
            {"maker_address": OUR, "order_id": "m", "asset_id": "no-tok",
             "side": "BUY", "matched_amount": "30", "price": "0.51"}
        ],
    }
    evs = normalize_trade(msg, OUR, _other)
    assert len(evs) == 1
    assert evs[0].token_id == "no-tok"
    assert evs[0].our_side is Side.BUY
    assert evs[0].size == 30


def test_ignores_maker_orders_that_are_not_ours():
    msg = {
        "event_type": "trade", "market": "0xcond", "asset_id": "yes-tok", "side": "BUY",
        "outcome": "Yes", "status": "MATCHED", "id": "t", "timestamp": "1700000000000",
        "maker_orders": [
            {"maker_address": "0xSomeoneElse", "matched_amount": "50", "price": "0.49", "outcome": "Yes"}
        ],
    }
    assert normalize_trade(msg, OUR, _other) == []


def test_trade_normalization_preserves_exact_maker_order_id():
    msg = {
        "status": "MATCHED", "asset_id": "yes-tok", "side": "SELL",
        "outcome": "Yes", "id": "trade-1", "timestamp": 100,
        "maker_orders": [
            {"maker_address": OUR, "order_id": "our-order", "asset_id": "yes-tok",
             "side": "BUY", "matched_amount": "1.25", "price": "0.18"},
            {"maker_address": "0xother", "order_id": "other-order", "asset_id": "yes-tok",
             "side": "BUY", "matched_amount": "4", "price": "0.18"},
        ],
    }
    events = normalize_trade(msg, OUR, _other)
    assert len(events) == 2
    assert {(e.maker_order_id, e.size) for e in events} == {("our-order", 1.25), ("other-order", 4.0)}


def test_conflicting_maker_order_id_aliases_are_malformed():
    from polymaker.userstream.parse import MalformedMakerLeg
    with pytest.raises(MalformedMakerLeg, match="order ID aliases contradict"):
        parse_trade({"id": "t", "status": "MATCHED", "maker_orders": [{
            "order_id": "foreign", "orderId": "ours", "asset_id": "token",
            "side": "BUY", "matched_amount": "1", "price": ".2"}]})


@pytest.mark.parametrize("key", ["order_id", "orderID", "orderId"])
def test_each_supported_maker_order_id_alias_normalizes(key):
    parsed = parse_trade({"id": "t", "status": "MATCHED", "maker_orders": [{
        key: "ABC", "asset_id": "token", "side": "BUY",
        "matched_amount": "1", "price": ".2"}]})
    assert parsed.maker_legs[0].order_id == "ABC"


def test_equal_maker_order_id_aliases_normalize_once():
    parsed = parse_trade({"id": "t", "status": "MATCHED", "maker_orders": [{
        "order_id": "ABC", "orderID": "ABC", "orderId": "ABC",
        "asset_id": "token", "side": "BUY", "matched_amount": "1", "price": ".2"}]})
    assert parsed.maker_legs[0].order_id == "ABC"


@pytest.mark.parametrize("aliases", [
    {"order_id": "A", "orderID": "B"},
    {"order_id": "A", "orderId": "B"},
    {"orderID": "A", "orderId": "B"},
    {"order_id": "A", "orderID": "A", "orderId": "B"},
])
def test_every_conflicting_maker_order_id_alias_pair_is_malformed(aliases):
    from polymaker.userstream.parse import MalformedMakerLeg
    with pytest.raises(MalformedMakerLeg, match="order ID aliases contradict"):
        parse_trade({"id": "t", "status": "MATCHED", "maker_orders": [{
            **aliases, "asset_id": "token", "side": "BUY",
            "matched_amount": "1", "price": ".2"}]})


@pytest.mark.parametrize("bad", ["", None, 12345])
def test_malformed_present_maker_alias_does_not_normalize(bad):
    from polymaker.userstream.parse import MalformedMakerLeg
    with pytest.raises(MalformedMakerLeg):
        parse_trade({"id": "t", "status": "MATCHED", "maker_orders": [{
            "order_id": "OUR", "orderId": bad, "asset_id": "token", "side": "BUY",
            "matched_amount": "1", "price": ".2"}]})


@pytest.mark.parametrize("fields,valid,market", [
    ({}, True, ""),
    ({"market": "A"}, True, "A"),
    ({"condition_id": "A"}, True, "A"),
    ({"market": "A", "condition_id": "A"}, True, "A"),
    ({"market": "A", "condition_id": "B"}, False, None),
])
def test_trade_market_aliases_must_all_be_present_and_consistent(fields, valid, market):
    msg = {"id": "t", "status": "MATCHED", "maker_orders": [], **fields}
    if valid:
        assert parse_trade(msg).market == market
    else:
        with pytest.raises(ValueError, match="market identities contradict"):
            parse_trade(msg)


def test_normalize_order_remaining_and_cancel():
    ev = normalize_order({
        "event_type": "order", "asset_id": "yes-tok", "side": "BUY", "price": "0.49",
        "original_size": "100", "size_matched": "40", "status": "LIVE", "id": "o1",
    })
    assert ev is not None
    assert ev.remaining_size == 60
    assert ev.is_cancel is False

    cancel = normalize_order({
        "event_type": "order", "asset_id": "yes-tok", "side": "BUY", "price": "0.49",
        "original_size": "100", "size_matched": "0", "status": "CANCELED", "id": "o1",
    })
    assert cancel is not None and cancel.is_cancel is True


@pytest.mark.parametrize("matched,valid", [
    (0, True), (0.0, True), ("0", True), (False, False), (True, False),
    (None, False), ("", False), ("oops", False), (-1, False),
])
def test_normalize_order_requires_explicit_valid_cumulative_quantity(matched, valid):
    row = {"id": "o", "asset_id": "token", "side": "BUY", "price": ".2",
           "original_size": "5", "size_matched": matched}
    event = normalize_order(row)
    assert (event is not None) is valid
    if valid:
        assert event.size_matched == 0


def test_normalize_order_missing_size_matched_is_unknown():
    row = {"id": "o", "asset_id": "token", "side": "BUY", "price": ".2",
           "original_size": "5"}
    assert normalize_order(row) is None


@pytest.mark.parametrize("field", ["original_size", "size_matched", "price"])
@pytest.mark.parametrize("value", [False, True])
def test_order_numeric_fields_reject_booleans(field, value):
    row = {"id": "o", "asset_id": "token", "side": "BUY", "price": ".2",
           "original_size": "5", "size_matched": "0"}
    row[field] = value
    assert normalize_order(row) is None


@pytest.mark.parametrize("fields,valid,market", [
    ({"market": "A"}, True, "A"),
    ({"condition_id": "A"}, True, "A"),
    ({"market": "A", "condition_id": "A"}, True, "A"),
    ({"market": "A", "condition_id": "B"}, False, None),
])
def test_order_market_aliases_must_agree(fields, valid, market):
    row = {"id": "o", "asset_id": "token", "side": "BUY", "price": ".2",
           "original_size": "5", "size_matched": "0", **fields}
    event = normalize_order(row)
    assert (event is not None) is valid
    if valid:
        assert event.market_id == market


@pytest.mark.parametrize("field", ["matched_amount", "price"])
@pytest.mark.parametrize("value", [False, True])
def test_maker_trade_numeric_fields_reject_booleans(field, value):
    leg = {"order_id": "o", "asset_id": "token", "side": "BUY",
           "matched_amount": "1", "price": ".2"}
    leg[field] = value
    with pytest.raises(ValueError, match="maker leg has invalid economics"):
        parse_trade({"id": "t", "status": "MATCHED", "maker_orders": [leg]})


@pytest.mark.parametrize("field,valid,expected", [
    ({}, True, ()),
    ({"associate_trades": []}, True, ()),
    ({"associate_trades": ["TRADE1"]}, True, ("TRADE1",)),
    ({"associate_trades": "TRADE1"}, False, None),
    ({"associate_trades": None}, False, None),
    ({"associate_trades": [None]}, False, None),
    ({"associate_trades": [""]}, False, None),
])
def test_associate_trades_optional_only_when_absent_and_strict_when_present(field, valid, expected):
    row = {"id": "o", "asset_id": "token", "side": "BUY", "price": ".2",
           "original_size": "5", "size_matched": "0", **field}
    event = normalize_order(row)
    assert (event is not None) is valid
    if valid:
        assert event.associate_trades == expected

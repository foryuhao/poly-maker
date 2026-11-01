"""M1 causal examples and the real shared maker/close-proof execution path."""

import asyncio
import json
import time
from copy import deepcopy
from decimal import Decimal as D

import pytest
from pydantic import ValidationError

from polymaker.config import RDPM1Config, RDPM1SignalConfig, RDPSMConfig
from polymaker.rdp_sm import session_capital_limit
from polymaker.rdp_sm_m1 import M1Controller, budget_quantity
from polymaker.rdp_sm_m1_signal import M1Signals
from polymaker.rdp_sm_marketdata import SessionFeed
from test_rdp_sm_ba import depth, execution_setup, trade
from test_rdp_sm_ba import env as env
from test_rdp_sm_m1 import CID
from test_rdp_sm_obi import feed_fixture
from test_rdp_sm_obi_execution import cleanup
from test_rdp_sm_repair import reflect_positions


def m1_feed():
    old, clock, rows = feed_fixture()
    f = SessionFeed(old.meta, old.cfg, monotonic=old.monotonic, wall=old.wall,
                    normal_entry_mode="m1_v1")
    f.verify_ticks({t: b.tick for t, b in old.books.items()})
    f.connect()
    f.apply(deepcopy(rows))
    return f, clock


def refresh(f):
    f.apply([{"event_type": "book", "market": CID, "asset_id": t,
        "timestamp": str(f.wall()*1000),
        "bids": [{"price": str(p), "size": str(q)} for p, q in b.bids],
        "asks": [{"price": str(p), "size": str(q)} for p, q in b.asks]}
        for t, b in f.books.items()])


def advance(f, clock, target):
    while clock.now < target-1e-8:
        clock.now = round(min(target, clock.now+.025), 6)
        refresh(f)


def hit(f, clock, at, token="1", qty=10):
    advance(f, clock, at)
    size = f.books[token].best_bid_size
    f.apply(trade(f, token, size=str(qty)))
    f.apply(depth(f, size-qty, token))
    f.apply(depth(f, size, token))


def formation(f, clock, token="1"):
    # Three independent 750ms refill rounds. AP's second reference starts
    # during round two, so RF and EX overlap for a genuinely executable lease.
    for at in (.02, .28, .53, .80, 1.30, 1.60, 1.85):
        hit(f, clock, at, token)
    advance(f, clock, 2.825)
    return f.m1.evaluate(f.snapshot())


@pytest.mark.parametrize("new_depth,restore_at,stable", [(60, .69, False), (60, .30, True), (71, .69, False)])
def test_recovery_failure_requires_a_fresh_full_hold(new_depth, restore_at, stable):
    f, clock = m1_feed()
    hit(f, clock, .02)
    advance(f, clock, .15)
    assert f.m1.states["1"]["round"].held
    advance(f, clock, .20)
    f.apply(depth(f, new_depth))
    assert not f.m1.states["1"]["round"].held
    advance(f, clock, restore_at)
    f.apply(depth(f, 80))
    advance(f, clock, .775)
    closed = [e for e in f.m1.events if e["event"] == "ROUND_CLOSED"]
    assert len(closed) == 1 and closed[0]["stable"] is stable


def mirror_feed():
    f, clock = m1_feed()
    f.m1 = M1Signals(f, signal_config=RDPM1SignalConfig(mirror_complement_flow=True))
    f.apply({"event_type": "book", "market": CID, "asset_id": "2",
             "timestamp": str(f.wall()*1000), "bids": [{"price": ".48", "size": "20"}],
             "asks": [{"price": ".50", "size": "80"}]})
    return f, clock


def mirror_buy(f, at, *, tx=None):
    f.apply(trade(f, token="2", side="BUY", price=".50", size="10",
                  transaction_hash=tx or f"0x{int(at*1000000):064x}"))


def test_complementary_buy_flow_forms_real_same_queue_rf_and_sell_exhaustion():
    f, clock = mirror_feed()
    for at in (.02, .28, .53, .80, 1.30, 1.60, 1.85):
        advance(f, clock, at)
        mirror_buy(f, at)
        for quantity in (70, 80):
            f.apply({"event_type": "price_change", "market": CID, "timestamp": str(f.wall()*1000),
                     "price_changes": [
                         {"asset_id": "1", "side": "BUY", "price": ".50", "size": str(quantity)},
                         {"asset_id": "2", "side": "SELL", "price": ".50", "size": str(quantity)}]})
    advance(f, clock, 2.825)
    assert f.m1.evaluate(f.snapshot()).direction == "YES"
    assert f.m1.counts["COMPLEMENT_TRADE_VIEW"] == 7
    assert f.m1.counts["RF_CONFIRMED"] == 1
    assert f.m1.counts["EX_CONFIRMED"] >= 1
    assert f.m1.counts["ROUND_CLOSED"] == 3
    assert f.m1.selected.anchor_bid_price == D(".50")
    assert all(e["origin_asset_id"] == "2" and e["token_id"] == "1"
               for e in f.m1.events if e["event"] == "COMPLEMENT_TRADE_VIEW")


def test_complement_view_requires_matching_book_quantities():
    f, clock = mirror_feed()
    f.apply({"event_type": "book", "market": CID, "asset_id": "2",
             "timestamp": str(f.wall()*1000), "bids": [{"price": ".48", "size": "20"}],
             "asks": [{"price": ".50", "size": "79"}]})
    clock.now = .02
    mirror_buy(f, .02)
    assert not f.m1.counts["COMPLEMENT_TRADE_VIEW"]
    assert any(e.get("reason") == "M1_MIRROR_BOOK_UNALIGNED" for e in f.m1.events)
    assert not f.m1.counts["ROUND_STARTED"]


def test_duplicate_complement_representation_is_unavailable_not_double_volume():
    f, clock = mirror_feed()
    clock.now = .02
    tx = "0x"+"a"*64
    mirror_buy(f, .02, tx=tx)
    f.apply(trade(f, token="1", size="10", transaction_hash=tx))
    assert any(e.get("reason") == "M1_DUPLICATE_AMBIGUITY" for e in f.m1.events)
    assert not f.m1.counts["AP_CONFIRMED"]
    assert not f.m1.evaluate(f.snapshot()).direction


@pytest.mark.parametrize("token,side", [("1", "YES"), ("2", "NO")])
def test_real_ordered_rounds_pressure_exhaustion_and_fixed_overlap(token, side):
    f, clock = m1_feed()
    signal = formation(f, clock, token)
    assert signal.direction == side, f.m1.diagnostics()
    ep = f.m1.selected
    assert ep.order_deadline-clock.now > .45
    assert f.m1.counts["ROUND_CLOSED"] == 3
    assert f.m1.counts["RF_CONFIRMED"] == 1
    assert f.m1.counts["EX_CONFIRMED"] >= 1
    json.dumps(f.m1.diagnostics(), allow_nan=False)
    claim = f.m1.claim(signal)
    f.m1.consume(claim)
    assert not f.m1.evaluate(f.snapshot()).direction
    f.m1.release(claim)
    assert not f.m1.evaluate(f.snapshot()).direction
    assert ep.rf_id in f.m1.used_rf and ep.ex_id in f.m1.used_ex


def test_single_large_sell_and_quiet_stream_do_not_create_m1():
    f, clock = m1_feed()
    hit(f, clock, .02, qty=50)
    advance(f, clock, 1.6)
    assert not f.m1.counts["AP_CONFIRMED"]
    assert not f.m1.counts["EX_CONFIRMED"]
    assert not f.m1.evaluate(f.snapshot()).direction


def test_calibration_requires_two_independent_refill_rounds_and_records_parameters():
    f, clock = m1_feed()
    cfg = RDPM1SignalConfig(profile="two-round-test", rf_rounds=2, rf_ttl_sec=2)
    f.m1 = M1Signals(f, signal_config=cfg)
    for at in (.02, .28, .53):
        hit(f, clock, at)
    advance(f, clock, .79)
    assert not f.m1.counts["RF_CONFIRMED"]
    for at in (.80, 1.30):
        hit(f, clock, at)
    advance(f, clock, 1.575)
    assert f.m1.counts["RF_CONFIRMED"] == 1
    confirmed = next(e for e in f.m1.events if e["event"] == "RF_CONFIRMED")
    assert len(confirmed["rounds"]) == 2
    assert confirmed["deadline"]-confirmed["available_monotonic"] == pytest.approx(2)
    assert confirmed["parameter_hash"] == f.m1.diagnostics()["parameter_hash"]
    assert f.m1.diagnostics()["parameters"]["profile"] == "two-round-test"


@pytest.mark.parametrize("values", [{"rf_rounds": 1}, {"ap_min_records": 1},
                                    {"ap_max_concentration": "1"},
                                    {"min_round_depth_fraction": "0"}])
def test_calibration_cannot_turn_a_single_print_or_round_into_m1(values):
    with pytest.raises(ValidationError):
        RDPM1SignalConfig(**values)


@pytest.mark.parametrize("span,expected", [(5., None), (10., "YES")])
def test_combination_span_is_separate_from_signal_expiry(span, expected):
    f, clock = m1_feed()
    f.m1 = M1Signals(f, signal_config=RDPM1SignalConfig(rf_ttl_sec=8, combination_span_sec=span))
    clock.now = 7.
    refresh(f)
    state = f.m1.state("1")
    state["anchor"] = f.books["1"].best_bid
    state["rf"] = {"id": "rf", "confirmed": 2., "deadline": 10., "rounds": [(0., "r1", D(1))]}
    state["ex"] = {"id": "ex", "ap_id": "ap", "confirmed": 7., "deadline": 7.75,
                   "volume": D(100), "evidence_start": 6.}
    assert f.m1.evaluate(f.snapshot()).direction == expected
    if expected:
        assert f.m1.selected.order_deadline == 7.75  # Longer RF never extends the EX lease.


@pytest.mark.parametrize("span,expected", [(5., 0), (15., 1)])
def test_sparse_independent_refills_use_configured_sequence_span(span, expected):
    f, clock = m1_feed()
    f.m1 = M1Signals(f, signal_config=RDPM1SignalConfig(rf_rounds=2, rf_sequence_span_sec=span))
    hit(f, clock, .02)
    hit(f, clock, 8.)
    advance(f, clock, 8.80)
    assert f.m1.counts["RF_CONFIRMED"] == expected
    if expected:
        event = next(e for e in f.m1.events if e["event"] == "RF_CONFIRMED")
        assert len(event["rounds"]) == 2 and event["rounds"][1][0]-event["rounds"][0][0] > 5


def test_longer_ap_window_normalizes_ex_reference_rate_before_confirmation():
    f, clock = m1_feed()
    f.m1 = M1Signals(f, signal_config=RDPM1SignalConfig(ap_window_sec=2.25, ap_min_records=2))
    for at in (.02, 1.60):
        hit(f, clock, at)
    advance(f, clock, 2.30)
    watch = f.m1.state("1")["ex_watch"]
    assert f.m1.counts["AP_CONFIRMED_SELL"] == 1
    assert watch["volume"] == 20
    assert watch["comparison_volume"] == D(20)/3
    start = watch["start"]
    advance(f, clock, start+.10)
    # 1.5 would pass an unscaled 20/6 per-bin threshold, but it exceeds
    # the true reference rate's 20/3/6 threshold of 1.111... shares.
    f.apply(trade(f, size="1.5"))
    advance(f, clock, start+.78)
    assert f.m1.counts["EX_NOT_CONFIRMED"] == 1
    assert not f.m1.counts["EX_CONFIRMED"]


def test_short_ap_window_uses_its_actual_reference_rate():
    f, clock = m1_feed()
    f.m1 = M1Signals(f, signal_config=RDPM1SignalConfig(ap_window_sec=.4, ap_min_records=2))
    for at in (.02, .30):
        hit(f, clock, at)
    advance(f, clock, .45)
    watch = f.m1.state("1")["ex_watch"]
    assert watch["comparison_volume"] == D("37.5")  # 20 shares / .4s * .75s.
    start = watch["start"]
    advance(f, clock, start+.10)
    f.apply(trade(f, size="5"))
    advance(f, clock, start+.78)
    assert f.m1.counts["EX_CONFIRMED"] == 1
    event = next(e for e in f.m1.events if e["event"] == "EX_CONFIRMED")
    assert event["reference_volume"] == "20"
    assert D(event["reference_comparison_volume"]) == D("37.5")


@pytest.mark.parametrize("hits,expected", [((.02, .28, .54), 1), ((.02, .08, .16), 0)])
def test_short_rounds_still_require_three_independent_stable_shocks(hits, expected):
    f, clock = m1_feed()
    f.m1 = M1Signals(f, signal_config=RDPM1SignalConfig(round_window_sec=.25, rf_rounds=3))
    for at in hits:
        hit(f, clock, at)
    advance(f, clock, .82)
    assert f.m1.counts["RF_CONFIRMED"] == expected
    if expected:
        event = next(e for e in f.m1.events if e["event"] == "RF_CONFIRMED")
        assert len({r[1] for r in event["rounds"]}) == 3
        closed = [e for e in f.m1.events if e["event"] == "ROUND_CLOSED"]
        assert all(e["held"] and e["stable"] for e in closed)
        assert all(e["evidence_end"]-e["evidence_start"] == pytest.approx(.25) for e in closed)


def test_stream_gap_invalidates_instead_of_manufacturing_exhaustion():
    f, clock = m1_feed()
    formation(f, clock)
    assert f.m1.selected is not None
    clock.now += .25
    f.m1.tick()
    assert not f.m1.evaluate(f.snapshot()).direction
    assert f.m1.block_reason == "M1_BOOK_UNAVAILABLE"


def test_pending_received_frame_prevents_premature_window_confirmation():
    f, clock = m1_feed()
    for at in (.02, .28, .53):
        hit(f, clock, at)
    advance(f, clock, .74)
    # A public frame received before the AP window closes is still waiting
    # in the consumer queue. A timer must not declare that window complete.
    f.m1_pending_received.append(.75)
    clock.now = .78
    f.m1.tick()
    assert not f.m1.counts["AP_CONFIRMED"]
    f.m1_pending_received.clear()
    f.m1.tick()
    assert f.m1.counts["AP_CONFIRMED"] == 1


def test_backlog_beyond_processing_deadline_is_unavailable_not_late_confirmation():
    f, clock = m1_feed()
    for at in (.02, .28, .53):
        hit(f, clock, at)
    advance(f, clock, .74)
    f.m1_pending_received.append(.75)
    clock.now = .83
    f.m1.tick()
    assert not f.m1.counts["AP_CONFIRMED"]
    assert f.m1.block_reason == "M1_INPUT_BACKLOG"


def test_no_hit_flashing_depth_cannot_create_refill():
    f, clock = m1_feed()
    for i in range(30):
        advance(f, clock, .02+i*.05)
        f.apply(depth(f, 60))
        f.apply(depth(f, 80))
    assert not f.m1.counts["ROUND_CLOSED"]
    assert not f.m1.counts["RF_CONFIRMED"]


def test_failed_rf_group_does_not_lend_rounds_to_next_group():
    f, clock = m1_feed()
    for i, recovery in enumerate((6, 6, 6, 10, 10)):
        advance(f, clock, .02+i*.80)
        initial = f.books["1"].best_bid_size
        f.apply(trade(f, size="10"))
        f.apply(depth(f, initial-10))
        f.apply(depth(f, initial-10+recovery))
    advance(f, clock, 4.02)
    assert f.m1.counts["RF_NOT_CONFIRMED"] == 1
    assert not f.m1.counts["RF_CONFIRMED"]


def test_anchor_change_and_expiry_cannot_revive_old_opportunity():
    f, clock = m1_feed()
    formation(f, clock)
    ep = f.m1.selected
    advance(f, clock, ep.order_deadline+.01)
    assert not f.m1.evaluate(f.snapshot()).direction
    refresh(f)
    assert not f.m1.evaluate(f.snapshot()).direction


def test_trade_beyond_ca_tolerance_is_unknown_not_silent_zero_volume():
    f, clock = m1_feed()
    formation(f, clock)
    f.apply(trade(f, timestamp=str((f.wall()-.03)*1000)))
    assert not f.m1.evaluate(f.snapshot()).direction
    assert f.m1.counts["UNAVAILABLE"]


def test_ap_sell_survives_real_bid_queue_changes_but_refill_does_not():
    f, clock = m1_feed()
    for at, price in ((.02, ".50"), (.28, ".49"), (.53, ".48")):
        advance(f, clock, at)
        f.apply({"event_type": "book", "market": CID, "asset_id": "1",
                 "timestamp": str(f.wall()*1000),
                 "bids": [{"price": price, "size": "80"}],
                 "asks": [{"price": ".52", "size": "20"}]})
        f.apply(trade(f, size="10"))
    advance(f, clock, .80)
    assert f.m1.counts["AP_CONFIRMED_SELL"] == 1
    assert f.m1.state("1")["ex_watch"] is not None
    assert f.m1.counts["RF_INVALIDATED"] == 2
    assert not f.m1.counts["RF_CONFIRMED"]


def test_visible_anchor_keeps_same_queue_history_through_a_better_bid():
    f, clock = m1_feed()
    f.m1 = M1Signals(f, signal_config=RDPM1SignalConfig(track_visible_anchor=True, rf_rounds=2))
    hit(f, clock, .02)
    advance(f, clock, .80)
    assert len(f.m1.state("1")["rounds"]) == 1
    row = {"event_type": "book", "market": CID, "asset_id": "1",
           "timestamp": str(f.wall()*1000),
           "bids": [{"price": ".51", "size": "80"}, {"price": ".50", "size": "80"}],
           "asks": [{"price": ".52", "size": "20"}]}
    f.apply(row)
    f.apply(trade(f))  # Hit at .51 cannot count towards the fixed .50 queue.
    assert f.m1.state("1")["anchor"] == D(".50")
    assert f.m1.state("1")["round"] is None
    assert len(f.m1.state("1")["rounds"]) == 1
    row["bids"] = row["bids"][1:]
    f.apply(row)
    hit(f, clock, 1.0)
    advance(f, clock, 1.8)
    assert f.m1.counts["RF_CONFIRMED"] == 1
    # A better bid does not relabel the RF; deleting its fixed level ends it.
    row["timestamp"] = str(f.wall()*1000)
    row["bids"] = [{"price": ".51", "size": "80"}]
    f.apply(row)
    assert f.m1.state("1")["rf"] is None


def test_atomic_ask_replacement_does_not_destroy_bid_refill_or_flow_evidence():
    f, clock = m1_feed()
    formation(f, clock)
    rf = f.m1.state("1")["rf"]
    f.apply({"event_type": "price_change", "market": CID, "timestamp": str(f.wall()*1000),
             "price_changes": [
                 {"asset_id": "1", "side": "SELL", "price": ".52", "size": "0",
                  "best_bid": ".50", "best_ask": ".53"},
                 {"asset_id": "1", "side": "SELL", "price": ".53", "size": "20",
                  "best_bid": ".50", "best_ask": ".53"}]})
    assert f.books["1"].best_ask == D(".53")
    assert f.m1.state("1")["rf"] is rf
    assert f.m1.evaluate(f.snapshot()).direction == "YES"
    assert not f.m1.counts["UNAVAILABLE"]


def test_one_delta_message_keeps_both_fixed_queue_depletion_and_refill():
    f, clock = m1_feed()
    advance(f, clock, .02)
    f.apply(trade(f, size="10"))
    row = depth(f, 70)
    row["price_changes"].append({**row["price_changes"][0], "size": "80"})
    f.apply(row)
    assert f.m1.state("1")["round"].low == 70
    advance(f, clock, .80)
    closed = [e for e in f.m1.events if e["event"] == "ROUND_CLOSED"]
    assert len(closed) == 1 and closed[0]["stable"]


@pytest.mark.parametrize("rollback", ["1", "20", "20.001"])
def test_ca_tolerance_ignores_old_depth_without_overwriting_or_renewing(rollback):
    f, clock = m1_feed()
    formation(f, clock)
    ep = f.m1.selected
    book = f.books["1"]
    row = depth(f, 1)
    row["timestamp"] = str(f.m1.source["1"]*1000-D(rollback))
    f.apply(row)
    assert f.books["1"] == book
    if D(rollback) <= 20:
        assert f.m1.evaluate(f.snapshot()).direction == "YES"
        assert f.m1.selected.order_deadline == ep.order_deadline
        assert f.m1.counts["EVENT_IGNORED"] == 1
    else:
        assert not f.m1.evaluate(f.snapshot()).direction
        assert f.m1.counts["UNAVAILABLE"]


def test_ignored_sell_volume_cannot_manufacture_exhaustion():
    f, clock = m1_feed()
    for at in (.02, .28, .53):
        hit(f, clock, at)
    advance(f, clock, .90)
    assert f.m1.counts["AP_CONFIRMED_SELL"] == 1
    f.apply(trade(f, size="20", timestamp=str((f.wall()-.001)*1000)))
    advance(f, clock, 1.60)
    assert f.m1.counts["EVENT_IGNORED"] == 1
    assert f.m1.counts["EX_NOT_CONFIRMED"] == 1
    assert not f.m1.counts["EX_CONFIRMED"]


@pytest.mark.parametrize("side,quantity,confirmed", [("SELL", "10", 1), ("BUY", "10", 0),
                                                    ("SELL", "300", 0)])
def test_ignored_volume_share_and_concentration_bounds_respect_known_direction(side, quantity, confirmed):
    f, clock = m1_feed()
    for at in (.02, .28, .53):
        hit(f, clock, at)
    advance(f, clock, .60)
    f.apply(trade(f, side=side, size=quantity,
                  price=str(f.books["1"].best_bid if side == "SELL" else f.books["1"].best_ask),
                  timestamp=str((f.wall()-.001)*1000)))
    advance(f, clock, .80)
    assert f.m1.counts["EVENT_IGNORED"] == 1
    assert f.m1.counts["AP_CONFIRMED_SELL"] == confirmed
    if confirmed:
        event = next(e for e in f.m1.events if e["event"] == "AP_CONFIRMED")
        assert event["volume"] == "30"  # Excluded volume never pads primary evidence.
        assert event["records"] == 3
        assert event["share"] == "1"


def test_fresh_off_top_executed_flow_counts_for_ap_but_not_unknown_rf_queue():
    f, clock = m1_feed()
    f.m1 = M1Signals(f, signal_config=RDPM1SignalConfig(flow_requires_top_match=False))
    for at in (.02, .28, .53):
        advance(f, clock, at)
        f.apply(trade(f, price=".49", size="10"))  # Queue .49 is not visible.
    advance(f, clock, .80)
    assert f.m1.counts["AP_CONFIRMED_SELL"] == 1
    assert f.m1.counts["FLOW_TRADE_OFF_TOP"] == 3
    assert not f.m1.counts["ROUND_STARTED"]
    advance(f, clock, 1.0)
    f.apply(trade(f, price=".49", size="100"))
    advance(f, clock, 1.60)
    assert f.m1.counts["EX_NOT_CONFIRMED"] == 1
    assert not f.m1.counts["EX_CONFIRMED"]  # Off-top SELL cannot be hidden to create exhaustion.


def test_visible_off_top_queue_receives_only_its_own_sell_hit_and_refill():
    f, clock = m1_feed()
    f.m1 = M1Signals(f, signal_config=RDPM1SignalConfig(
        flow_requires_top_match=False, track_visible_anchor=True))
    f.apply({"event_type": "book", "market": CID, "asset_id": "1",
             "timestamp": str(f.wall()*1000),
             "bids": [{"price": ".50", "size": "80"}, {"price": ".49", "size": "100"}],
             "asks": [{"price": ".52", "size": "20"}]})
    advance(f, clock, .02)
    f.apply(trade(f, price=".49", size="10"))
    for size in (90, 100):
        f.apply(depth(f, size, price=".49"))
    advance(f, clock, .80)
    event = next(e for e in f.m1.events if e["event"] == "ROUND_CLOSED")
    assert event["initial"] == "100" and event["trough"] == "90"
    assert event["held"] and event["stable"]
    assert f.books["1"].best_bid == D(".50")
    assert f.m1.state("1")["anchor"] == D(".49")


@pytest.mark.parametrize("price,quantity", [(".50", "10"), (".25", "20"),
                                           (".03", "166.66"), (".99", "5.05")])
def test_fixed_cash_budget_rounds_down_without_legacy_fifteen_share_cap(price, quantity):
    q = budget_quantity(price, 5)
    assert q == D(quantity) and q*D(price) <= 5
    assert session_capital_limit(type("C", (), {"rdp_sm": RDPSMConfig(normal_entry_mode="m1_v1")})()) == 20


@pytest.mark.parametrize("values", [{"order_budget_usdc": 6}, {"session_budget_usdc": 30},
                                   {"start_sec": 99}, {"cutoff_sec": 241},
                                   {"post_budget_floor_ms": 750}])
def test_contract_configuration_rejects_changed_budget_or_impossible_lease(values):
    with pytest.raises(ValidationError):
        RDPM1Config(**values)


def seed_opportunity(f):
    # Execution tests isolate the write boundary; detector causality is tested
    # above with actual public frames, never with a mocked trade outcome.
    now = f.monotonic()
    s = f.m1.state("1")
    s["anchor"] = f.books["1"].best_bid
    s["rf"] = {"id": f.m1.new_id("RF"), "confirmed": now, "deadline": now+.7}
    s["ex"] = {"id": f.m1.new_id("EX"), "ap_id": "reference", "confirmed": now,
               "deadline": now+.7, "volume": D(60)}


async def m1_execution(env, monkeypatch):
    setup = await execution_setup(env, monkeypatch)
    v, e, f, life = setup[:4]
    e.cfg.rdp_sm.normal_entry_mode = "m1_v1"
    e.cfg.rdp_sm.m1 = RDPM1Config(observation_only=False, post_budget_floor_ms=10, min_live_budget_ms=20)
    e.official_time += 50  # Fixture starts at T60; M1 starts at T100.
    f.ba = f.opposite_ba = None
    f.m1 = M1Signals(f)
    controller = M1Controller(e, life)
    setup[4] = controller
    await asyncio.sleep(.02)
    seed_opportunity(f)
    return setup


async def test_observation_marks_once_without_signing_reserving_or_native_post(env, monkeypatch):
    setup = await m1_execution(env, monkeypatch)
    v, e, f, _, controller = setup[:5]
    e.cfg.rdp_sm.m1.observation_only = True

    async def forbidden_signing(*args, **kwargs):
        raise AssertionError("observation attempted signing")

    monkeypatch.setattr(e.gateway, "prepare", forbidden_signing)
    try:
        assert await controller.enter(D(0), D(0), "observe-1") is None
        assert await controller.enter(D(0), D(0), "observe-2") is None
        assert f.m1.counts["WOULD_POST"] == 1
        assert not f.m1.own_active
        assert not e.attempts and not v.post_requests and e.committed() == 0
        assert not controller.guard(None, None, None)
        with pytest.raises(Exception, match="M1_OBSERVATION_ONLY"):
            e.gateway._client._post("https://clob.polymarket.com/orders", data="[]")
        assert not v.post_requests
    finally:
        await cleanup(setup)


async def test_visible_anchor_observation_quotes_original_queue_below_better_bid(env, monkeypatch):
    setup = await m1_execution(env, monkeypatch)
    v, e, f, _, controller = setup[:5]
    cfg = RDPM1SignalConfig(track_visible_anchor=True)
    e.cfg.rdp_sm.m1.signal = cfg
    e.cfg.rdp_sm.m1.observation_only = True
    f.m1 = M1Signals(f, signal_config=cfg)
    seed_opportunity(f)
    try:
        f.apply({"event_type": "book", "market": CID, "asset_id": "1",
                 "timestamp": str(f.wall()*1000),
                 "bids": [{"price": ".51", "size": "80"}, {"price": ".50", "size": "80"}],
                 "asks": [{"price": ".52", "size": "20"}]})
        assert await controller.enter(D(0), D(0), "observe-fixed-queue") is None
        event = next(e for e in f.m1.events if e["event"] == "WOULD_POST")
        assert event["quotes"][0]["price"] == .5
        assert not v.post_requests and not e.attempts
    finally:
        await cleanup(setup)


@pytest.mark.parametrize("elapsed,markers", [(14.9, 0), (60., 1), (240., 0)])
async def test_observation_window_uses_configured_start_and_keeps_cutoff(env, monkeypatch, elapsed, markers):
    setup = await m1_execution(env, monkeypatch)
    v, e, f, _, controller = setup[:5]
    e.cfg.rdp_sm.m1.observation_start_sec = 15
    e.cfg.rdp_sm.m1.observation_only = True
    e.official_time = int(e.slug.rsplit("-", 1)[1])+elapsed
    e.time_anchor = e.monotonic()
    try:
        assert await controller.enter(D(0), D(0), "observe-t15") is None
        assert f.m1.counts["WOULD_POST"] == markers
        if markers:
            event = next(e for e in f.m1.events if e["event"] == "WOULD_POST")
            assert event["trading_window"] == {"start_sec": 15, "cutoff_sec": 240}
        assert not v.post_requests
    finally:
        await cleanup(setup)


def test_expanded_observation_window_does_not_change_live_start():
    cfg = RDPM1Config(observation_start_sec=15)
    assert cfg.entry_start_sec == 15
    cfg.observation_only = False
    assert cfg.entry_start_sec == 100


async def test_heartbeat_runs_without_market_updates_and_reports_stale_data(env, monkeypatch):
    from types import SimpleNamespace

    from polymaker.rdp_sm_loop import SMTradingLoop
    from polymaker.rdp_sm_m1 import M1Engine

    setup = await m1_execution(env, monkeypatch)
    v, e, f, life = setup[:4]
    displayed = []
    e.display_events = []
    loop = SMTradingLoop(e.cfg, e.slug, e.session_id,
                         observer=SimpleNamespace(emit=lambda kind, p: displayed.extend(p)))
    loop.executor = e
    engine = M1Engine(e, life)
    engine.advance(110)
    clock = {}
    counts, opportunities = dict(f.m1.counts), dict(f.m1.opportunities)
    try:
        now = e.monotonic()
        e.display_events.append(("sm_window_result", {"not_yet_closed": True}))
        with monkeypatch.context() as patch:
            patch.setattr(e, "monotonic", lambda: now)
            loop._m1_heartbeat(engine, life, clock)
            first = [p for k, p in displayed if k == "sm_m1_heartbeat"]
            assert len(first) == 1 and first[0]["state"] == "TRADING"
            assert not any(k == "sm_window_result" for k, p in displayed)
            assert ("sm_window_result", {"not_yet_closed": True}) in e.display_events
            assert first[0]["submitted_orders"] == first[0]["confirmed_fills"] == 0
            now += 9.99
            loop._m1_heartbeat(engine, life, clock)
            assert len([p for k, p in displayed if k == "sm_m1_heartbeat"]) == 1
            now += .02
            loop._m1_heartbeat(engine, life, clock)
            latest = [p for k, p in displayed if k == "sm_m1_heartbeat"][-1]
            assert latest["status"] == "DATA_UNAVAILABLE"
            assert latest["ws_updates"] == 0
            assert not any(latest["usable_books"].values())
            # Phase changes print immediately, even within the 10-second interval.
            engine.transition("HOLD_SETTLEMENT")
            loop._m1_heartbeat(engine, life, clock)
            assert len([p for k, p in displayed if k == "sm_m1_heartbeat"]) == 3
            loop.monotonic = e.monotonic
            loop.market_deadline = now+150
            patch.setattr(e, "official_time", 0)  # In-flight account refresh.
            engine.transition("DRAINING")
            loop._m1_heartbeat(engine, life, clock)
            latest = [p for k, p in displayed if k == "sm_m1_heartbeat"][-1]
            assert latest["elapsed_s"] == 150
        assert dict(f.m1.counts) == counts and f.m1.opportunities == opportunities
        assert not v.post_requests and not e.attempts
    finally:
        await cleanup(setup)


@pytest.mark.parametrize("cached", [False, True])
async def test_real_sdk_maker_submission_money_sizing_and_no_repair(env, monkeypatch, tmp_path, cached):
    setup = await m1_execution(env, monkeypatch)
    v, e, f, _, controller = setup[:5]
    companion = None
    try:
        if cached:
            from test_rdp_sm_cached_reads import attach

            companion, _ = await attach(setup, tmp_path)
            e.official_time += 50
            seed_opportunity(f)
        outcome = await controller.enter(D(0), D(0), "m1-real-sdk")
        assert outcome is not None, f.m1.diagnostics()
        result, sizing = outcome
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert len(v.post_requests) == 1
        row = v.post_requests[0][0]
        assert row["postOnly"] and row["order"]["side"] == "BUY"
        assert result.requested_quotes[0].size == 10
        assert result.requested_quotes[0].price == .5
        assert e.repair_episode is None and not e.cooldown.active
        assert sizing["order_budget_usdc"] == "5"
        assert len(f.m1.used_rf) == len(f.m1.used_ex) == 1
        assert not await controller.enter(D(0), D(0), "same-opportunity")
    finally:
        if companion is not None:
            from test_rdp_sm_cached_reads import finish

            await finish(setup, companion)
        else:
            await cleanup(setup)


@pytest.mark.parametrize("elapsed", [99., 240.])
async def test_time_boundaries_never_submit(env, monkeypatch, elapsed):
    setup = await m1_execution(env, monkeypatch)
    v, e, _, _, controller = setup[:5]
    try:
        start = int(e.slug.rsplit("-", 1)[1])
        e.official_time = start+elapsed
        e.time_anchor = time.monotonic()
        assert await controller.enter(D(0), D(0), "m1-outside") is None
        assert not v.post_requests
    finally:
        await cleanup(setup)


async def test_cutoff_is_part_of_submission_budget_not_only_a_later_cancel(env, monkeypatch):
    setup = await m1_execution(env, monkeypatch)
    v, e, f, _, controller = setup[:5]
    try:
        start = int(e.slug.rsplit("-", 1)[1])
        e.official_time, e.time_anchor = start+239.99, time.monotonic()
        snapshot = controller.lifecycle.snapshot(controller, D(0), D(0))
        assert f.m1.selected is None or f.m1.selected.order_deadline > time.monotonic()+.1
        assert controller.deadline(snapshot)-time.monotonic() < .02
        assert await controller.enter(D(0), D(0), "m1-cutoff-budget") is None
        assert not v.post_requests
    finally:
        await cleanup(setup)


async def test_known_unprocessed_market_frame_blocks_a_new_order(env, monkeypatch):
    setup = await m1_execution(env, monkeypatch)
    v, _, f, _, controller = setup[:5]
    try:
        f.m1_pending_received.append(time.monotonic())
        assert await controller.enter(D(0), D(0), "m1-input-pending") is None
        assert not v.post_requests and not f.m1.used_rf
    finally:
        f.m1_pending_received.clear()
        await cleanup(setup)


async def test_unrelated_quiet_counterpart_book_does_not_veto_token_local_m1(env, monkeypatch):
    setup = await m1_execution(env, monkeypatch)
    v, _, f, _, controller = setup[:5]
    setup[7]["running"] = False
    await setup[-1]
    setup[7]["running"] = True

    async def yes_updates_only():
        while setup[7]["running"]:  # noqa: ASYNC110 - emulate the single-token socket stream
            book = f.books["1"]
            f.apply({"event_type": "book", "market": CID, "asset_id": "1",
                     "timestamp": str(f.wall()*1000),
                     "bids": [{"price": str(book.best_bid), "size": str(book.best_bid_size)}],
                     "asks": [{"price": str(book.best_ask), "size": str(book.best_ask_size)}]})
            await asyncio.sleep(.01)

    setup[-1] = asyncio.create_task(yes_updates_only())
    try:
        await asyncio.sleep(.25)
        assert not f.snapshot().valid
        seed_opportunity(f)
        result, _ = await controller.enter(D(0), D(0), "m1-token-local")
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert v.post_requests[0][0]["order"]["tokenId"] == "1"
    finally:
        await cleanup(setup)


async def test_native_post_rechecks_original_deadline_after_preparation(env, monkeypatch):
    setup = await m1_execution(env, monkeypatch)
    v, e, f, life, controller = setup[:5]
    try:
        snapshot = life.snapshot(controller, D(0), D(0))
        controller.select(snapshot)
        slot = await life.prepare(controller, D(0), D(0), "m1-expiring")
        assert slot is not None
        await asyncio.sleep(.71)
        assert await life.activate(controller, slot) is None
        assert not v.post_requests and not f.m1.used_rf
    finally:
        await cleanup(setup)


async def test_existing_residual_does_not_request_repair(env, monkeypatch):
    setup = await m1_execution(env, monkeypatch)
    v, _, _, _, controller = setup[:5]
    try:
        # An existing reconciled inventory must not turn signal-side sizing
        # into base+Delta or force a NO recovery order.
        result, _ = await controller.enter(D(20), D(0), "m1-residual")
        assert result.close_barrier == "SAFE_TO_CONTINUE"
        assert v.post_requests[0][0]["order"]["tokenId"] == "1"
        assert result.requested_quotes[0].size == 10
    finally:
        await cleanup(setup)


@pytest.mark.parametrize("fill", [0, 4, 10])
async def test_single_engine_coordinator_and_natural_residual(env, monkeypatch, fill):
    from polymaker.rdp_sm_loop import RunResult, SMTradingLoop

    setup = await m1_execution(env, monkeypatch)
    v, e, f = setup[:3]
    native_time = e.reads.server_time

    async def official_time():
        return await native_time()+50

    monkeypatch.setattr(e.reads, "server_time", official_time)
    loop = SMTradingLoop(e.cfg, e.slug, e.session_id, monotonic=e.monotonic)
    loop.executor = e
    result = RunResult(e.meta.condition_id, e.session_id)

    def fill_after_cancel(venue):
        for oid, row in venue.rows.items():
            if fill and row["status"] == "CANCELED" and not row["associate_trades"]:
                venue.fill(oid, "m1-late-fill", fill)
        reflect_positions(venue)

    async def end_after_window():
        async with asyncio.timeout(5):
            while not result.m1_windows:  # noqa: ASYNC110 - observe the actual coordinator output
                await asyncio.sleep(.005)
        setup[7]["running"] = False
        await setup[-1]
        v.time = int(e.slug.rsplit("-", 1)[1])+300

    task = None
    try:
        v.on_cancel = fill_after_cancel
        task = asyncio.create_task(end_after_window())
        await asyncio.wait_for(loop._run_obi_session(result), 7)
        await task
        assert result.execution_status == "COMPLETED", result.stop_reason
        assert result.m1_windows == 1 and result.repair_windows == result.normal_windows == 0
        assert result.confirmed_yes == fill and result.confirmed_no == 0
        assert result.sizing_report["residual_policy"] == "NATURAL_M1_ACCUMULATION"
        assert e.repair_episode is None and loop.rebalance_controller is None
        assert len(v.post_requests) == 1
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await cleanup(setup)


async def test_twenty_usdc_budget_allows_four_fills_but_no_fifth(env, monkeypatch):
    setup = await m1_execution(env, monkeypatch)
    v, e, f, _, controller = setup[:5]

    def filled(venue):
        for oid, row in venue.rows.items():
            if row["status"] == "CANCELED" and not row["associate_trades"]:
                venue.fill(oid, "m1-"+str(len(venue.trades)), 10)
        reflect_positions(venue)

    try:
        v.on_cancel = filled
        for i in range(4):
            if i:
                await e._snapshot(include_books=False)
                e.official_time += 50
                seed_opportunity(f)
            result, _ = await controller.enter(*e.economic_inventory(), "m1-budget-"+str(i))
            assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
            assert e.committed() == (i+1)*5
        await e._snapshot(include_books=False)
        e.official_time += 50
        seed_opportunity(f)
        assert await controller.enter(*e.economic_inventory(), "m1-fifth") is None
        assert len(v.post_requests) == 4 and e.committed() == 20
    finally:
        await cleanup(setup)


@pytest.mark.parametrize("lost_ack", [False, True])
@pytest.mark.parametrize("cached", [False, True])
async def test_rejected_or_unknown_post_consumes_once_and_retains_close_proof(env, monkeypatch, tmp_path, lost_ack, cached):
    setup = await m1_execution(env, monkeypatch)
    v, e, f, _, controller, wire = setup[:6]
    companion = None
    try:
        if cached:
            from test_rdp_sm_cached_reads import attach

            companion, _ = await attach(setup, tmp_path)
            e.official_time += 50
            seed_opportunity(f)
        wire.fail_ack = lost_ack
        wire.reject_index = None if lost_ack else 0
        result, _ = await controller.enter(D(0), D(0), "m1-unknown-or-rejected")
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert result.placement_results[0]["post_started"]
        assert len(f.m1.used_rf) == 1
        assert await controller.enter(D(0), D(0), "no-repost") is None
        assert len(v.post_requests) == 1
    finally:
        if companion is not None:
            from test_rdp_sm_cached_reads import finish

            await finish(setup, companion)
        else:
            await cleanup(setup)


@pytest.mark.parametrize("cached", [False, True])
async def test_actual_discovery_preflight_open_and_teardown(env, monkeypatch, cached):
    from test_rdp_sm_obi_entry import (
        test_new_mode_entire_entry_has_no_rest_book_and_owns_ws_teardown,
    )

    await test_new_mode_entire_entry_has_no_rest_book_and_owns_ws_teardown(
        env, monkeypatch, False, True, False, cached, mode="m1_v1")


def test_reverse_buy_attack_ends_old_exhaustion_reference():
    f, clock = m1_feed()
    for at in (.02, .28, .53):
        hit(f, clock, at)
    advance(f, clock, .80)
    assert f.m1.state("1")["ex_watch"] is not None
    for at in (.81, 1.08, 1.34):
        advance(f, clock, at)
        f.apply(trade(f, side="BUY", price=str(f.books["1"].best_ask), size="10"))
    advance(f, clock, 1.60)
    assert f.m1.state("1")["ex_watch"] is None
    assert f.m1.state("1")["ex"] is None


def test_small_source_regression_never_overwrites_shared_depth():
    f, clock = m1_feed()
    advance(f, clock, .05)
    original = f.books["1"].best_bid_size
    row = depth(f, 1)
    row["timestamp"] = str((f.wall()-.001)*1000)
    f.apply(row)
    assert f.books["1"].best_bid_size == original
    assert f.m1.counts["EVENT_IGNORED"] == 1
    assert not f.m1.counts["UNAVAILABLE"]


@pytest.mark.parametrize("price", [".25", ".03"])
async def test_real_sdk_five_dollar_order_can_exceed_old_share_cap(env, monkeypatch, price):
    setup = await m1_execution(env, monkeypatch)
    v, e, f, _, controller = setup[:5]
    e.cfg.rdp_sm.max_order_size = D(15)
    try:
        f.apply({"event_type": "book", "market": CID, "asset_id": "1",
                 "timestamp": str(f.wall()*1000),
                 "bids": [{"price": price, "size": "100"}],
                 "asks": [{"price": str(D(price)+D(".02")), "size": "100"}]})
        seed_opportunity(f)
        result, _ = await controller.enter(D(0), D(0), "m1-cash-sizing")
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        signed = v.post_requests[0][0]["order"]
        assert D(signed["takerAmount"])/1_000_000 > e.cfg.rdp_sm.max_order_size
        assert D(signed["makerAmount"])/1_000_000 <= 5
    finally:
        await cleanup(setup)

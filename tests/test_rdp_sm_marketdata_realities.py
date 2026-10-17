"""Venue frame boundaries, explicit BBO reconciliation and strict dust boundaries."""

from decimal import Decimal

import pytest

from polymaker.config import RDPSMConfig
from polymaker.rdp_sm import SessionStatus, run_preflight
from polymaker.rdp_sm_quotes import make_quotes
from test_rdp_sm_m1 import CID, SLUG, START
from test_rdp_sm_m1 import cfg as cfg
from test_rdp_sm_m1 import venue as venue
from test_rdp_sm_obi import feed_fixture


def delta(*changes, stamp=None):
    return {"event_type": "price_change", "market": CID,
            "timestamp": str((START + 20) * 1000) if stamp is None else stamp,
            "price_changes": [{"asset_id": "1", **ch} for ch in changes]}


def test_whole_frame_removes_intermediate_cross_before_publication():
    feed, _, _ = feed_fixture()
    feed.apply(delta(
        {"side": "BUY", "price": ".53", "size": "10"},
        {"side": "SELL", "price": ".52", "size": "0"},
        {"side": "SELL", "price": ".54", "size": "10",
         "best_bid": ".53", "best_ask": ".54"}))
    assert feed.snapshot().valid and feed.connected and feed.failure_count == 0
    assert feed.snapshot().yes.best_bid == Decimal('.53')
    assert feed.snapshot().yes.best_ask == Decimal('.54')


def test_initial_delta_waits_for_full_snapshot_instead_of_reconnect_loop():
    feed, _, rows = feed_fixture()
    feed.connect()
    generation = feed.generation
    feed.apply(delta({'side': 'BUY', 'price': '.50', 'size': '91'}))
    assert feed.connected and feed.snapshot().reason == 'WS_REBUILD_REQUIRED'
    assert not feed.snapshot().yes.bids
    feed.apply(rows)
    assert feed.snapshot().valid and feed.generation == generation and feed.failure_count == 0


def test_venue_top_removes_contradictory_cached_levels_without_inventing_sizes():
    feed, _, _ = feed_fixture()
    feed.apply(delta({"side": "BUY", "price": ".49", "size": "60",
                      "best_bid": ".49", "best_ask": ".52"}))
    b = feed.snapshot().yes
    assert feed.snapshot().valid and feed.connected and feed.failure_count == 0
    assert b.best_bid == Decimal('.49') and b.best_bid_size == 60
    assert Decimal('.50') not in dict(b.bids)
    assert feed.diagnostics()['notices']['WS_TOP_RECONCILED'] == 1


def test_missing_top_quantity_waits_for_data_without_disconnect_or_fabrication():
    feed, _, _ = feed_fixture()
    feed.apply(delta({"side": "BUY", "price": ".49", "size": "60",
                      "best_bid": ".51", "best_ask": ".52"}))
    assert feed.snapshot().reason == 'WS_TOP_PENDING' and feed.connected
    assert Decimal('.51') not in dict(feed.snapshot().yes.bids)
    feed.apply(delta({"side": "BUY", "price": ".51", "size": "17",
                      "best_bid": ".51", "best_ask": ".52"}))
    assert feed.snapshot().valid and feed.snapshot().yes.best_bid_size == 17


def test_real_cross_is_published_unquotable_and_next_update_recovers_same_connection():
    feed, _, _ = feed_fixture()
    generation = feed.generation
    feed.apply(delta({"side": "BUY", "price": ".53", "size": "10"}))
    assert feed.snapshot().reason == 'WS_CROSSED_BOOK' and feed.connected
    assert feed.snapshot().yes.best_bid == Decimal('.53')
    feed.apply(delta({"side": "BUY", "price": ".53", "size": "0"}))
    assert feed.snapshot().valid and feed.generation == generation


def test_source_clock_accepts_millisecond_regression_within_same_second():
    feed, clock, rows = feed_fixture()
    clock.now = .5
    for r in rows:
        r['timestamp'] = str((START + 20) * 1000 + 417)
    feed.apply(rows)
    feed.apply(delta({"side": "BUY", "price": ".50", "size": "91"},
                     stamp=str((START + 20) * 1000 + 416)))
    assert feed.snapshot().valid and feed.snapshot().yes.best_bid_size == 91
    assert feed.snapshot().yes.source_time == START + 20.417


def test_old_second_does_not_overwrite_book_or_reconnect():
    feed, _, _ = feed_fixture()
    before = feed.snapshot()
    feed.apply(delta({"side": "BUY", "price": ".50", "size": "91"},
                     stamp=str((START + 18) * 1000 + 999)))
    assert feed.snapshot() == before and feed.connected
    assert feed.diagnostics()['notices']['WS_OLD_SOURCE_IGNORED'] == 1


def test_reconnect_resets_source_clock_before_fresh_snapshot():
    feed, clock, rows = feed_fixture()
    clock.now = .5
    for r in rows:
        r['timestamp'] = str((START + 20) * 1000 + 417)
    feed.apply(rows)
    feed.connect()
    assert all(b.source_time == 0 for b in feed.books.values())
    for r in rows:
        r['timestamp'] = str((START + 18) * 1000 + 999)
    feed.apply(rows)
    assert feed.snapshot().valid and feed.failure_count == 0


def test_source_pair_comparison_is_second_precision_receipt_skew_stays_precise():
    feed, _, rows = feed_fixture()
    rows[0]['timestamp'] = str((START + 20) * 1000 + 100)
    rows[1]['timestamp'] = str((START + 20) * 1000 + 400)
    feed.apply(rows)
    assert feed.snapshot().valid


def test_subsecond_rollback_and_pair_skew_across_second_boundary_do_not_false_alarm():
    feed, _, rows = feed_fixture()
    feed.apply(delta({'side': 'BUY', 'price': '.50', 'size': '91'},
                     stamp=str((START + 19) * 1000 + 999)))
    assert feed.snapshot().valid and feed.snapshot().yes.best_bid_size == 91
    assert feed.failure_count == 0
    feed.connect()
    rows[0]['timestamp'] = str((START + 20) * 1000 + 1)
    rows[1]['timestamp'] = str((START + 19) * 1000 + 999)
    feed.apply(rows)
    assert feed.snapshot().valid and feed.failure_count == 0


def test_repeated_tick_notification_is_idempotent():
    feed, _, _ = feed_fixture()
    tick = {"event_type": "tick_size_change", "market": CID, "asset_id": "1",
            "old_tick_size": ".01", "new_tick_size": ".001"}
    feed.apply(tick)
    feed.apply(tick)
    assert feed.connected and feed.snapshot().yes.tick == Decimal('.001')
    assert feed.snapshot().reason == 'WS_TICK_UNVERIFIED'


def test_bad_json_retains_diagnostic_without_unbound_local_error():
    feed, _, _ = feed_fixture()
    with pytest.raises(ValueError, match='WS_FRAME_REBUILD'):
        feed.apply('{')
    assert feed.last_failure['context']['event_type'] is None


@pytest.mark.parametrize('dust,size', [('0.01', 5), ('0.099999', 5), ('0.1', 5.1)])
@pytest.mark.parametrize('sign', [1, -1])
@pytest.mark.parametrize('direction', ['YES', 'NO'])
def test_dust_does_not_skew_next_orders_but_exact_threshold_does(dust, size, sign, direction):
    feed, _, _ = feed_fixture()
    yes, no = (Decimal(dust), Decimal(0)) if sign == 1 else (Decimal(0), Decimal(dust))
    evidence = {}
    state, quotes = make_quotes(feed.meta, feed.snapshot().books(), yes, no, 'dust',
                               RDPSMConfig(), evidence=evidence, signal_side=direction)
    assert state == 'NORMAL'
    reducing_side = 'NO' if sign == 1 else 'YES'
    assert [(q.outcome, q.size) for q in quotes] == [(direction, size if direction == reducing_side else 5)]
    assert evidence['actual_delta_before'] == str(yes - no)


@pytest.mark.parametrize('direction', ['YES', 'NO'])
def test_dust_gap_between_large_positions_does_not_skew_next_orders(direction):
    feed, _, _ = feed_fixture()
    _, quotes = make_quotes(feed.meta, feed.snapshot().books(), Decimal('5.09'), Decimal(5),
                            'dust-gap', RDPSMConfig(), signal_side=direction)
    assert [(q.outcome, q.size) for q in quotes] == [(direction, 5)]


@pytest.mark.parametrize('quantity,ready', [(.099999, True), (.1, False)])
async def test_startup_ignores_only_strictly_sub_threshold_inventory(venue, cfg, quantity, ready):
    venue.positions = [{"token_id": "1", "current_size": quantity, "avg_price": .49}]
    result = await run_preflight(cfg, SLUG)
    assert (result.status is SessionStatus.READY) is ready
    assert result.account['yes_inventory'] == quantity

"""Shared raw events, independent strategies and causal queue examples."""

from copy import deepcopy
from decimal import Decimal as D

import pytest

from polymaker.config import RDPM1Config
from polymaker.m2_config import M2Config
from polymaker.m2_signal import M2Signals
from polymaker.m234_signal import M234Feed
from polymaker.rdp_sm_marketdata import SessionFeed
from test_rdp_sm_ba import depth, trade
from test_rdp_sm_m1_strategy import advance, hit
from test_rdp_sm_obi import feed_fixture


def shared(*, frequency=False):
    old, clock, rows = feed_fixture()
    feed = M234Feed(old.meta, old.cfg, monotonic=old.monotonic, wall=old.wall,
                   normal_entry_mode='m1_v1', m1_config=RDPM1Config(),
                   research_settings=M2Config(include_m3_m4=True, m4_frequency_study=frequency))
    feed.m1.cfg.mirror_complement_flow = False
    feed.verify_ticks({t: b.tick for t, b in old.books.items()})
    feed.connect()
    feed.apply(deepcopy(rows))
    return feed, clock, rows


def queue_hit(feed, clock, at, side='ASK', recovery=D(1), qty=D(10)):
    advance(feed, clock, at)
    book = feed.books['1']
    price = book.best_ask if side == 'ASK' else book.best_bid
    initial = book.best_ask_size if side == 'ASK' else book.best_bid_size
    feed.apply(trade(feed, side='BUY' if side == 'ASK' else 'SELL',
                     price=str(price), size=str(qty)))
    def update(quantity):
        feed.apply({'event_type': 'price_change', 'market': feed.meta.condition_id,
            'timestamp': str(feed.wall()*1000), 'price_changes': [{'asset_id': '1',
            'side': 'SELL' if side == 'ASK' else 'BUY', 'price': str(price), 'size': str(quantity)}]})
    update(initial-qty)
    update(initial-qty+qty*recovery)
    advance(feed, clock, at+.525)


def test_m3_needs_new_sell_after_ca_and_preserves_original_deadline():
    feed, clock, _ = shared()
    advance(feed, clock, .02)
    feed.apply(trade(feed, size='5'))
    advance(feed, clock, .05)
    feed.apply(trade(feed, side='BUY', price='.52', size='10'))
    assert feed.hub.m3.campaigns
    assert not feed.hub.m3.candidates
    advance(feed, clock, .10)
    feed.apply(trade(feed, size='5'))
    feed.apply(depth(feed, 75))
    feed.apply(depth(feed, 80))
    feed.hub.tick()
    c = feed.hub.m3.candidates['1']
    assert c['price'] == D('.50')
    assert c['deadline'] == pytest.approx(3.02)
    public = next(c for c in feed.hub.candidates() if c['strategy'] == 'M3')
    assert feed.hub.active(public)
    assert not feed.hub.active({**public, 'deadline': clock.now-.001})
    assert not feed.hub.active({**public, 'generation': feed.generation-1})
    assert feed.ba is None and not feed.ba_detectors()
    advance(feed, clock, 3.025)
    assert not feed.hub.m3.candidates
    assert not feed.hub.failures


def test_pre_ca_absorption_is_not_reused():
    feed, clock, _ = shared()
    advance(feed, clock, .02)
    feed.apply(trade(feed, size='5'))
    feed.apply(depth(feed, 75))
    feed.apply(depth(feed, 80))
    advance(feed, clock, .05)
    feed.apply(trade(feed, side='BUY', price='.52', size='10'))
    feed.hub.tick()
    assert feed.hub.m3.campaigns and not feed.hub.m3.candidates


def test_ask_success_then_two_comparable_weak_rounds_and_live_bid_rf():
    feed, clock, _ = shared()
    queue_hit(feed, clock, .02)
    queue_hit(feed, clock, .60)
    assert feed.hub.m4.pair('1')['ASK'].reference
    queue_hit(feed, clock, 1.20, side='BID')
    queue_hit(feed, clock, 1.80, side='BID')
    queue_hit(feed, clock, 2.40, recovery=D('.2'))
    assert not feed.hub.m4.pair('1')['ASK'].df
    queue_hit(feed, clock, 3.00, recovery=D('.1'))
    df = feed.hub.m4.pair('1')['ASK'].df
    assert df and len(df['weak_rounds']) == 2
    candidates = [c for c in feed.hub.candidates() if c['strategy'] == 'M4']
    assert len(candidates) == 1
    assert candidates[0]['deadline'] == df['deadline']
    assert candidates[0]['price'] == D('.50')
    advance(feed, clock, df['deadline']+.025)
    assert not feed.hub.m4.candidates
    assert not feed.hub.failures


@pytest.mark.parametrize('fault', ['stronger_shock', 'normal_recovery', 'disappeared'])
def test_no_df_from_incomparable_or_censored_rounds(fault):
    feed, clock, _ = shared()
    baseline = D(2) if fault == 'stronger_shock' else D(10)
    queue_hit(feed, clock, .02, qty=baseline)
    queue_hit(feed, clock, .60, qty=baseline)
    if fault == 'stronger_shock':
        queue_hit(feed, clock, 1.20, qty=D(10), recovery=D('.1'))
    elif fault == 'normal_recovery':
        queue_hit(feed, clock, 1.20, recovery=D('.8'))
    else:
        advance(feed, clock, 1.20)
        feed.apply({'event_type': 'price_change', 'market': feed.meta.condition_id,
            'timestamp': str(feed.wall()*1000), 'price_changes': [{'asset_id': '1',
            'side': 'SELL', 'price': '.52', 'size': '0'}]})
    assert not feed.hub.m4.pair('1')['ASK'].df


@pytest.mark.parametrize('frequency', [False, True])
def test_m2_same_input_has_same_book_and_candidate_with_observers(frequency):
    feed, clock, rows = shared(frequency=frequency)
    old = SessionFeed(feed.meta, feed.cfg, monotonic=feed.monotonic, wall=feed.wall,
                      normal_entry_mode='m1_v1', m1_config=RDPM1Config())
    cfg = M2Config()
    cfg.signal.mirror_complement_flow = False
    old_rows = []
    old.m1 = M2Signals(old, settings=cfg, record=lambda r: old_rows.append(r) or True)
    old.verify_ticks({t: b.tick for t, b in feed.books.items()})
    old.connect()
    old.apply(deepcopy(rows))
    # Deterministic common clock and source stream. Drain evidence separately.
    original = feed.apply
    def both(raw, **kwargs):
        result = original(raw, **kwargs)
        old.apply(deepcopy(raw), **kwargs)
        return result
    feed.apply = both
    for at in (.02, .28, .53):
        advance(feed, clock, at)
        feed.apply(trade(feed, side='BUY', price='.52', size='10'))
    advance(feed, clock, .80)
    hit(feed, clock, .9)
    hit(feed, clock, 1.5)
    advance(feed, clock, 2.05)
    assert feed.books == old.books
    assert len(feed.m1.candidates) == len(old.m1.candidates) == 1
    new = next(iter(feed.m1.candidates.values()))
    previous = next(iter(old.m1.candidates.values()))
    for key in ('reference_volume', 'confirmed', 'deadline', 'anchor_bid_price', 'rf_rounds'):
        if key != 'rf_rounds':
            assert new[key] == previous[key]
    assert any(r['event'] == 'M2_OBSERVATION' for r in old_rows)
    assert not feed.hub.failures


def test_observer_failure_does_not_mutate_m2_input():
    feed, clock, _ = shared()
    feed.hub.m4.before = lambda *a: 1/0
    hit(feed, clock, .02)
    assert feed.hub.failures['M4'] == 'ZeroDivisionError'
    assert feed.books['1'].best_bid_size == 80
    assert feed.m1.failure is None


def test_ca_internal_failure_is_exposed_to_shared_study():
    feed, _, _ = shared()
    feed.hub.m3.ca.tick = lambda: 1/0
    feed.hub.tick()
    assert feed.hub.failures['M3'] == 'CA_OBSERVER_ERROR:ZeroDivisionError'
    assert feed.m1.failure is None


def test_empty_m4_queues_do_not_flood_evidence_on_invalid_input():
    feed, _, _ = shared()
    before = feed.hub.counts['M4:QUEUE_ENDED']
    feed.hub.m4.pair('1')
    feed.hub.m4.invalidate('BOOK_UNAVAILABLE', '1')
    assert feed.hub.counts['M4:QUEUE_ENDED'] == before


def test_m4_rebuilt_queues_cannot_reuse_old_round_or_signal_identity():
    feed, _, _ = shared()
    first = feed.hub.m4.pair('1')['BID']
    first.hit(D('.50'), D(10), feed.books['1'], feed.monotonic())
    old_round, old_signal = first.round.id, first.identity('RF')
    generation = feed.generation
    feed.hub.m4.invalidate('INPUT_GAP', '1')
    second = feed.hub.m4.pair('1')['BID']
    second.hit(D('.50'), D(10), feed.books['1'], feed.monotonic())
    assert feed.generation == generation
    assert second.round.id != old_round
    assert second.identity('RF') != old_signal


def test_m2_ap_does_not_clear_m4_bid_rounds():
    feed, clock, _ = shared()
    queue_hit(feed, clock, .02, side='BID')
    queue_hit(feed, clock, .60, side='BID')
    rf = feed.hub.m4.pair('1')['BID'].rf
    for at in (1.2, 1.46, 1.71):
        advance(feed, clock, at)
        feed.apply(trade(feed, side='BUY', price='.52', size='10'))
    advance(feed, clock, 2.0)
    assert feed.hub.m4.pair('1')['BID'].rf['id'] == rf['id']

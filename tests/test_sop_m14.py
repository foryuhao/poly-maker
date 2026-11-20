"""M14 observable examples: first new weak-Ask attempt and causal guard handoff."""

import gc
import weakref
from decimal import Decimal as D

import pytest
from sop_signals.m14 import M14


class Tape:
    def __init__(self, parameters=None):
        self.events, self.seq, self.quantities = [], 0, {}
        self.s = M14(lambda kind, payload: self.events.append((kind, payload)), parameters)

    def at(self, now):
        self.seq += 1
        self.s.clock(now, self.seq)

    def price(self, side):
        return D('.40' if side == 'BID' else '.42')

    def hit(self, now, direction, *, quantity='10', token='yes', received=None, q0=None, price=None):
        self.at(now)
        side = 'BID' if direction == 'SELL' else 'ASK'
        p = D(price) if price is not None else self.price(side)
        initial = D(q0) if q0 is not None else self.quantities.get((token, side, p), D(100))
        self.s.trade(token=token, side=direction, price=p, quantity=D(quantity),
                     received=now if received is None else received, q0=initial, generation=1)

    def depth(self, now, side, quantity, *, token='yes', received=None, price=None):
        self.at(now)
        p = D(price) if price is not None else self.price(side)
        self.quantities[token, side, p] = D(quantity)
        self.s.depth(token, side, p, D(quantity), now if received is None else received)

    def close(self, now, *, covered=True, publish=True):
        self.at(now)
        self.s.close(now, lambda *_: covered)
        if publish:
            self.s.publish()

    def first(self, *, with_bid=True, loss='20', recovery='.10'):
        self.hit(1, 'BUY')
        if with_bid:
            self.hit(1, 'SELL')
            self.depth(1.01, 'BID', '80')
        low = D(100) - D(loss)
        self.depth(1.01, 'ASK', low)
        self.depth(1.2, 'ASK', low + D(recovery) * D(loss))
        if with_bid:
            self.depth(1.2, 'BID', '95')
        self.close(1.501)
        return self.s.roots.get(('yes', D('.42')))

    def second(self, *, start=1.6, loss='12', recovery='.10', hit='10',
               close_time=None, publish=True, covered=True):
        q0 = self.quantities.get(('yes', 'ASK', D('.42')), D(100))
        low = q0 - D(loss)
        self.hit(start, 'BUY', quantity=hit)
        self.depth(start + .01, 'ASK', low)
        self.depth(start + .2, 'ASK', low + D(recovery) * D(loss))
        self.close(start + .501 if close_time is None else close_time,
                   publish=publish, covered=covered)

    def bid(self, start=2.3, *, token='yes', price='.40', q0='100', loss='20', hit='10'):
        self.hit(start, 'SELL', token=token, price=price, q0=q0, quantity=hit)
        self.depth(start + .01, 'BID', D(q0) - D(loss), token=token, price=price)
        self.depth(start + .2, 'BID', D(q0) - D(loss) * D('.25'), token=token, price=price)
        self.close(start + .501)

    def reasons(self):
        return [payload.get('reason') for _, payload in self.events]


def test_two_new_weak_asks_and_prior_strict_bid_confirm_once_at_original_anchor():
    t = Tape()
    first = t.first()
    assert first.phase == 'WAIT_A2' and not t.s.signals
    t.second()
    assert len(t.s.signals) == 1
    s = next(iter(t.s.signals.values()))
    assert s['root_id'] == first.first.id
    assert s['anchor_price'] == D('.40') and s['deadline'] == pytest.approx(4.501)
    assert s['ask2']['guard'] == D(76) and s['bid']['guard'] == D(88)
    assert s['ask1']['r'] == s['ask2']['r'] == D('.1')
    t.s.publish()
    assert t.s.counts['SIGNAL_CONFIRMED'] == 1
    assert all(p['strategy'] == 'M14' for _, p in t.events)


def test_second_ask_handoff_releases_first_formation_timer_while_waiting_for_bid():
    t = Tape()
    t.first(with_bid=False)
    t.second()
    assert next(iter(t.s.roots.values())).phase == 'WAIT_BID'
    t.bid(3.05)  # B confirms after cA1+2s, within 1.5s of cA2.
    signal = next(iter(t.s.signals.values()))
    assert signal['confirmed'] == pytest.approx(3.551)
    assert signal['deadline'] == pytest.approx(5.101)


def test_bid_strength_is_not_compared_against_ask_strength():
    t = Tape()
    t.first(with_bid=False)
    t.second()
    t.bid(q0='10000', loss='1', hit='1')
    assert len(t.s.signals) == 1  # Cross-side u/d ratios are far outside M12's range.


@pytest.mark.parametrize('hit,passed', [('4.0999', False), ('4.1', True), ('16.4', True), ('16.4001', False)])
def test_ask_normalized_hit_ratio_inclusive_boundaries(hit, passed):
    t = Tape()
    t.first()
    t.second(hit=hit)
    assert bool(t.s.signals) is passed


@pytest.mark.parametrize('loss,passed', [('8.1999', False), ('8.2', True), ('32.8', True), ('32.8001', False)])
def test_ask_normalized_loss_ratio_inclusive_boundaries(loss, passed):
    t = Tape()
    t.first()
    t.second(loss=loss)
    assert bool(t.s.signals) is passed


@pytest.mark.parametrize('recovery,passed', [('.20', True), ('.20001', False)])
def test_second_recovery_absolute_increase_boundary(recovery, passed):
    t = Tape()
    t.first()
    t.second(recovery=recovery)
    assert bool(t.s.signals) is passed


def test_zero_first_recovery_uses_absolute_increment_not_division_by_zero():
    t = Tape()
    t.first(recovery='0')
    t.second(recovery='.10')
    assert len(t.s.signals) == 1


@pytest.mark.parametrize('loss,recovery,formed', [
    ('5', '.30', True), ('4.999', '.10', False), ('20', '.30001', False), ('20', '.50', False),
])
def test_first_weak_ask_materiality_and_recovery_boundaries(loss, recovery, formed):
    t = Tape()
    root = t.first(with_bid=False, loss=loss, recovery=recovery)
    assert (root is not None) is formed
    assert not t.s.signals


def test_first_failed_second_round_cannot_be_skipped_for_a_third_round():
    t = Tape()
    original = t.first()
    t.second(loss='0', hit='.1')
    assert not t.s.roots and 'FIRST_A2_FAILED_OR_EXPIRED' in t.reasons()
    t.second(start=2.2)
    assert not t.s.signals
    assert next(iter(t.s.roots.values())).first.id != original.first.id
    assert t.s.counts['ASK2_STARTED'] == 1  # Third can only be a new A1, requiring its own new A2.


def test_second_round_stays_consumed_if_first_guard_dies_mid_formation():
    t = Tape()
    t.first()
    t.hit(1.6, 'BUY')
    t.depth(1.61, 'ASK', '70')
    t.depth(1.7, 'ASK', '90')
    t.depth(1.8, 'ASK', '71.2')
    t.close(2.101)
    assert not t.s.signals and not t.s.roots and not t.s.second_rounds


def test_new_second_ceiling_applies_immediately_while_waiting_for_bid():
    t = Tape()
    t.first(with_bid=False)
    t.second()
    t.depth(2.2, 'ASK', '80')  # Below old90 ceiling, above new76 ceiling.
    t.depth(2.21, 'ASK', '71.2')
    t.bid()
    assert not t.s.signals and not t.s.roots


@pytest.mark.parametrize('side,quantity', [('ASK', '80'), ('BID', '87.99')])
def test_later_parent_rows_can_invalidate_pending_second_before_signal_publication(side, quantity):
    t = Tape()
    t.first()
    t.second(publish=False)
    assert not t.s.signals
    t.depth(2.11, side, quantity)
    t.depth(2.12, side, '71.2' if side == 'ASK' else '95')
    t.s.publish()
    assert not t.s.signals


@pytest.mark.parametrize('side,quantity,alive', [
    ('BID', '88', True), ('BID', '87.999', False),
    ('ASK', '75.999', True), ('ASK', '76', False), ('BID', '0', False), ('ASK', '0', False),
])
def test_frozen_signal_guard_boundaries_and_level_loss(side, quantity, alive):
    t = Tape()
    t.first()
    t.second()
    t.depth(2.2, side, quantity)
    assert bool(t.s.signals) is alive
    if not alive:
        t.depth(2.21, side, '71.2' if side == 'ASK' else '95')
        t.s.publish()
        assert not t.s.signals and t.s.counts['SIGNAL_CONFIRMED'] == 1


@pytest.mark.parametrize('gap,formed', [(1.9999, True), (2.0, False), (2.0001, False)])
def test_second_publication_must_strictly_precede_first_formation_deadline(gap, formed):
    t = Tape()
    t.first(with_bid=False)
    close = 1.501 + gap
    t.second(start=close - .501, close_time=close)
    assert (t.s.counts['ASK2_CONFIRMED'] == 1) is formed


@pytest.mark.parametrize('gap,paired', [(1.5, True), (1.50001, False)])
def test_bid_pair_distance_is_inclusive_but_not_an_extension(gap, paired):
    t = Tape()
    t.first(with_bid=False)
    t.second()
    t.bid(start=2.101 + gap - .501)
    assert bool(t.s.signals) is paired


@pytest.mark.parametrize('received', [1.49, 1.501])
def test_old_or_equal_receipt_cannot_start_second_ask(received):
    t = Tape()
    t.first()
    t.hit(1.6, 'BUY', received=received)
    assert t.s.counts['ASK2_STARTED'] == 0 and not t.s.signals
    if received < 1.5:
        assert 'LATE_TRADE_TO_CLOSED_ROUND' in t.reasons()
        assert not t.s.roots


def test_post_window_path_cannot_be_repaired_at_first_publication():
    t = Tape()
    t.hit(1, 'BUY')
    t.depth(1.01, 'ASK', '80')
    t.depth(1.2, 'ASK', '82')
    t.depth(1.51, 'ASK', '90')
    t.depth(1.52, 'ASK', '82')
    t.close(1.53)
    assert not t.s.roots and 'POST_WINDOW_GUARD' in t.reasons()


def test_post_second_window_guard_checked_in_addition_to_first_guard():
    t = Tape()
    t.first()
    t.hit(1.6, 'BUY')
    t.depth(1.61, 'ASK', '70')
    t.depth(1.8, 'ASK', '71.2')
    t.depth(2.11, 'ASK', '80')
    t.depth(2.12, 'ASK', '71.2')
    t.close(2.13)
    assert not t.s.signals and 'A2_POST_WINDOW_GUARD' in t.reasons()


def test_later_qualified_failed_bid_ends_signal_without_breaking_old_floor():
    t = Tape()
    t.first()
    t.second()
    t.hit(2.3, 'SELL', q0='95')
    t.depth(2.31, 'BID', '89')  # Still above original88.
    t.depth(2.5, 'BID', '89.1')
    t.close(2.801)
    assert not t.s.signals and 'SUBSEQUENT_ROUND_COUNTEREVIDENCE' in t.reasons()


def test_later_stable_ask_ends_signal_even_below_original_supply_ceiling():
    t = Tape()
    t.first()
    t.second()
    t.hit(2.3, 'BUY')
    t.depth(2.31, 'ASK', '60')
    t.depth(2.5, 'ASK', '70')  # Below original76, but strongly recovered in this new round.
    t.close(2.801)
    assert not t.s.signals and 'SUBSEQUENT_STABLE_ASK' in t.reasons()


def test_new_good_bid_does_not_extend_existing_signal_deadline():
    t = Tape()
    t.first()
    t.second()
    identity, signal = next(iter(t.s.signals.items()))
    t.bid(3.0, q0='100', loss='10')
    assert t.s.signals[identity]['deadline'] == signal['deadline'] == pytest.approx(4.501)
    t.at(4.501)
    assert not t.s.signals and t.s.counts['SIGNAL_CONFIRMED'] == 1


@pytest.mark.parametrize('reason', ['DATA_GAP', 'TICK_CHANGED', 'UNRECONSTRUCTABLE_SOURCE_ORDER'])
def test_quality_failure_ends_roots_rounds_and_signals(reason):
    t = Tape()
    t.first()
    t.hit(1.6, 'BUY')
    t.s.invalidate('yes', reason)
    assert not t.s.roots and not t.s.rounds and not t.s.components and not t.s.second_rounds
    assert t.s.quality['yes'] == reason


def test_rebuilt_generation_cannot_inherit_first_ask_without_bid_components():
    t = Tape()
    t.first(with_bid=False)
    t.s.new_generation('yes', 2)
    assert not t.s.roots


@pytest.mark.parametrize('covered,delay', [(False, .001), (True, .051)])
def test_second_unknown_is_not_skipped_or_counted_as_failed_zero(covered, delay):
    t = Tape()
    t.first()
    t.second(covered=covered, close_time=2.1 + delay)
    assert not t.s.signals and not t.s.roots and t.s.counts['ROUND_UNKNOWN'] > 0


def test_other_token_bid_does_not_complete_this_ask_sequence():
    t = Tape()
    t.first(with_bid=False)
    t.second()
    t.bid(token='no')
    assert not t.s.signals


def test_latest_strict_bid_at_one_fixed_price_is_selected_before_first_pairing():
    t = Tape()
    t.first()
    old = next(iter(t.s.components))
    t.hit(1.6, 'SELL', q0='95')
    t.hit(1.6, 'BUY')
    t.depth(1.61, 'BID', '90')
    t.depth(1.61, 'ASK', '70')
    t.depth(1.8, 'BID', '94')
    t.depth(1.8, 'ASK', '71.2')
    t.close(2.101)
    signal = next(iter(t.s.signals.values()))
    assert signal['bid']['id'] != old and signal['bid']['guard'] == D(92)
    assert signal['deadline'] == pytest.approx(5.101)


def test_same_key_different_bid_anchors_cannot_be_arbitrarily_selected():
    t = Tape()
    t.first(with_bid=False)
    t.second()
    for price in ('.40', '.39'):
        t.hit(2.3, 'SELL', price=price, q0='100')
    for price in ('.40', '.39'):
        t.depth(2.31, 'BID', '80', price=price)
    for price in ('.40', '.39'):
        t.depth(2.5, 'BID', '95', price=price)
    t.close(2.801)
    assert not t.s.signals and not t.s.roots
    assert 'NON_UNIQUE_EARLIEST_BID_SELECTION' in t.reasons()
    assert t.s.counts['ROUND_UNKNOWN'] == 1


def test_second_received_window_end_is_exclusive_and_needs_later_watermark():
    t = Tape()
    t.first()
    t.hit(1.6, 'BUY')
    t.depth(1.61, 'ASK', '70')
    t.depth(1.8, 'ASK', '71.2')
    t.close(2.1)
    assert not t.s.signals and t.s.counts['ASK2_CONFIRMED'] == 0
    t.close(2.101)
    assert len(t.s.signals) == 1


def test_release_drops_live_root_and_all_retained_runtime_collections():
    t = Tape()
    root = t.first()
    reference = weakref.ref(root)
    del root
    t.hit(1.6, 'BUY')
    t.s.release()
    gc.collect()
    assert reference() is None
    assert all(not getattr(t.s, name) for name in ['rounds', 'components', 'signals', 'roots',
                                                  'pending_bids', 'second_rounds', 'last_close'])

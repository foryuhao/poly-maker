"""Private observer reuse preserves quality gates and resource ownership."""
from types import SimpleNamespace

import pytest

from maker_signals.model import D
from maker_signals.strategies import M910_DENSITY, Strategies
from test_m511_strategies import H


def private_manager(h):
    h.s = Strategies(h.c, signal_variant=M910_DENSITY,
        parent_filters=False, enabled_strategies=['M9'],
        frequency_parameters={'M9': {'private_seed': True}})
    return h.s


@pytest.mark.parametrize('failure', ['not_good', 'disconnected', 'crossed'])
def test_private_observer_creation_still_requires_current_legal_book(failure):
    h = H()
    s = private_manager(h)
    if failure == 'not_good':
        h.c.good['up'] = False
    elif failure == 'disconnected':
        h.c.connected = False
    else:
        h.c.books['up'].asks = {D('.39'): D(100)}
    s.ensure_private_m9_seed('up')
    assert 'up' not in s.m9_seed_observers
    assert not h.c.observers


@pytest.mark.parametrize('book_legal', [True, False])
@pytest.mark.parametrize('state', ['active_round', 'unanchored', 'supported'])
def test_existing_observer_no_op_never_recreates_or_ends_evidence(state, book_legal):
    h = H()
    s = private_manager(h)
    s.ensure_private_m9_seed('up')
    observer = s.m9_seed_observers['up']
    observer.anchor = D('.4')
    if state == 'active_round':
        observer.round = SimpleNamespace(marker='unchanged')
    elif state == 'unanchored':
        observer.anchor = None
    else:
        s.m9_support['up'] = h.fact('ROUND', ttl=1)
    before = (observer.round, observer.anchor, list(h.events), list(h.c.observers))
    h.c.connected = book_legal
    for _ in range(3):
        s.ensure_private_m9_seed('up')
    assert s.m9_seed_observers['up'] is observer
    assert (observer.round, observer.anchor, h.events, h.c.observers) == before


@pytest.mark.parametrize('support', ['absent', 'expired', 'invalidated'])
def test_idle_anchor_reset_waits_for_good_legal_input(support):
    h = H()
    s = private_manager(h)
    s.ensure_private_m9_seed('up')
    observer = s.m9_seed_observers['up']
    observer.anchor = D('.4')
    if support != 'absent':
        fact = h.fact('ROUND', ttl=1)
        s.m9_support['up'] = fact
        if support == 'expired':
            h.at(1)
        else:
            h.c.registry.end(fact, h.c.now, 'ANCHOR_LOST')
    h.c.connected = False
    s.ensure_private_m9_seed('up')
    assert observer.anchor == D('.4')
    h.c.connected = True
    s.ensure_private_m9_seed('up')
    assert observer.anchor is None
    assert s.m9_seed_observers['up'] is observer


def test_closed_observer_is_replaced_only_after_quality_recovers():
    h = H()
    s = private_manager(h)
    s.ensure_private_m9_seed('up')
    old = s.m9_seed_observers['up']
    s.end_private_m9_support('up', 'TEST')
    h.c.good['up'] = False
    s.ensure_private_m9_seed('up')
    assert s.m9_seed_observers['up'] is old
    h.c.good['up'] = True
    s.ensure_private_m9_seed('up')
    assert s.m9_seed_observers['up'] is not old
    assert old.closed

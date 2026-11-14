"""Offline regressions for excess diagnostics and ENOSPC bootstrap remnants."""
import asyncio
import json
import sqlite3
from dataclasses import asdict
from decimal import Decimal
from types import SimpleNamespace

import pytest
from m4_helpers import feed_fixture, identity, send

from polymaker import m4_baseline
from polymaker.m4_config import M4Config
from polymaker.m4_signals import M4LiveSignals
from polymaker.m4_state import M4Ledger


def test_depth_burst_bounds_diagnostics_without_losing_counts_or_signal_facts():
    clock = SimpleNamespace(now=1.0)
    feed = SimpleNamespace(monotonic=lambda: clock.now, generation=1)
    records = []
    signal = M4LiveSignals(feed, records.append)
    decision = dict(token_id='one', kind='price_change', accepted=True, reason=None)
    for frame in range(10000):
        signal.emit('EVENT_DECISION', **decision, frame=frame)
    assert signal.counts['EVENT_DECISION'] == 10000
    assert len(records) == 1
    for event in ('ROUND_CLOSED', 'DF_ASK_CONFIRMED', 'BID_SUPPORT_CONFIRMED',
                  'CANDIDATE', 'CANDIDATE_ENDED', 'OPPORTUNITY_IDENTIFIED'):
        for _ in range(3):
            signal.emit(event, token='one')
        assert sum(r['event'] == event for r in records) == 3
    # Fresh rejection reasons, event types, tokens and generations are distinct.
    for overrides in ({'accepted': False, 'reason': 'M4_SOURCE_AGE'},
                      {'accepted': False, 'reason': 'M4_SOURCE_REGRESSION'},
                      {'kind': 'book'}, {'token_id': 'two'}):
        signal.emit('EVENT_DECISION', **{**decision, **overrides})
    feed.generation = 2
    signal.emit('EVENT_DECISION', **decision)
    clock.now = 2.0
    signal.emit('EVENT_DECISION', **decision)
    assert sum(r['event'] == 'EVENT_DECISION' for r in records) == 7
    assert signal.counts['EVENT_DECISION'] == 10006


def test_empty_bootstrap_file_does_not_block_later_risk_recovery(tmp_path):
    feed, _, _ = feed_fixture()
    epoch = int(feed.meta.slug.rsplit('-', 1)[1])
    root = tmp_path.resolve()
    empty = root/'ledgers'/f'btc-updown-5m-{epoch-300}.sqlite3'
    empty.parent.mkdir()
    empty.touch()
    path = root/'ledgers'/f'{feed.meta.slug}.sqlite3'
    ident = {**identity(), 'market_id': feed.meta.condition_id}
    ledger = M4Ledger(path, ident)
    ledger.initialize_session('synthetic', M4Config().model_dump(mode='json'), asdict(feed.meta))
    send(ledger, 1, state='UNKNOWN')
    ledger.close()
    result = m4_baseline.pending(root, epoch+301)
    assert [row[0] for row in result] == [path]
    assert empty.exists() and empty.stat().st_size == 0
    ledger = M4Ledger(path, ident)
    try:
        assert ledger.get('complete')
        assert ledger.committed() == Decimal('4.97')
        assert ledger.row('df1')['state'] == 'UNKNOWN'
        assert ledger.row('df1')['posts'] == 1
    finally:
        ledger.close()


def test_foreign_database_is_not_skipped_as_empty(tmp_path):
    path = tmp_path/'ledgers/btc-updown-5m-1800000000.sqlite3'
    path.parent.mkdir()
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE foreign_risk (id TEXT)')
        db.execute("INSERT INTO foreign_risk VALUES ('must preserve')")
    before = path.read_bytes()
    with pytest.raises(ValueError, match='M4_FOREIGN_DATABASE'):
        m4_baseline.pending(tmp_path.resolve(), 1800000301)
    assert path.read_bytes() == before


async def test_successful_resolution_pass_replaces_stale_failure(tmp_path, monkeypatch):
    status = tmp_path/'resolution-status.json'
    status.write_text(json.dumps({'state':'PUBLIC_RESOLUTION_UNAVAILABLE', 'error_type':'ValueError'}))
    stop = asyncio.Event()
    write = m4_baseline.atomic_json

    def save(path, value):
        write(path, value)
        if value.get('state') == 'OK':
            stop.set()

    class Public:
        async def clock(self):
            return 1800000301, 0, 0

    monkeypatch.setattr(m4_baseline, 'atomic_json', save)
    await asyncio.wait_for(m4_baseline.resolutions(tmp_path.resolve(), Public(), stop), 1)
    result = json.loads(status.read_text())
    assert result['state'] == 'OK' and result['unresolved_markets'] == 0
    assert result['observed_at'] > 0 and result['actual_fills_queried'] is False
    assert 'error_type' not in result

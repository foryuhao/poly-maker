"""Diagnostic aggregation retains exact report meaning and first-seen order."""

import json
import sqlite3
from collections import Counter

from m1234_fixtures import fixture_events
from polymaker.m1234_sim.report import summary
from polymaker.m1234_sim.store import read_audit
from test_m1234_sim_engine import drive


async def test_aggregated_diagnostics_match_streaming_reference(tmp_path):
    await drive(fixture_events(), tmp_path/'sample')
    path = tmp_path/'sample/simulation.sqlite3'
    with sqlite3.connect(path) as db:
        for reason in (True, None, False, 'True', 1, {'a': 1}, ['x'], 'x', 'x'):
            payload = {'strategy': 'M1', 'event': 'UNAVAILABLE', 'payload': {'reason': reason}}
            db.execute("INSERT INTO audit(input_seq,kind,payload) VALUES(0,'SIGNAL',?)",
                       (json.dumps(payload),))
        db.execute("INSERT INTO audit(input_seq,kind,payload) VALUES(0,'SIGNAL',?)",
                   (json.dumps({'strategy': 'M4', 'event': 'EVENT_IGNORED', 'payload': {}}),))
    quality, kinds = Counter(), Counter()
    for row in read_audit(path):
        kinds[row['kind']] += 1
        payload = row['payload']
        if row['kind'] == 'SIGNAL' and payload.get('event') in {
            'UNAVAILABLE', 'INPUT_UNAVAILABLE', 'EVENT_IGNORED'
        }:
            quality[(payload.get('strategy'), str(payload.get('payload', {}).get('reason', 'UNSPECIFIED')))] += 1
    result = summary(path)
    assert result['event_counts'] == dict(kinds)
    assert result['input_quality_diagnostics'] == [
        {'strategy': s, 'reason': r, 'count': count,
         'denominator': 'recorded diagnostics, not distinct public frames'}
        for (s, r), count in quality.items()
    ]

import importlib.util
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location('m6_hourly_watch', Path(__file__).resolve().parents[2]/'ops/m6_hourly_watch.py')
watch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(watch)


def test_queue_preserves_review_cursor_and_deduplicates_same_hour(tmp_path):
    (tmp_path/'agent-state.json').write_text(json.dumps({'reviewed_report_directory': 'old'}))
    calls = []

    def runner(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout='', stderr='')

    report = {'bucket': 7, 'directory': '/tmp/m6-report'}
    control = {'thread': 'm6-test-thread'}
    assert watch.queue(tmp_path, control, report, runner)['status'] == 'QUEUED_AND_RESUME_REQUESTED'
    assert watch.queue(tmp_path, control, report, runner)['status'] == 'ALREADY_QUEUED'
    assert len(calls) == 1 and calls[0][3] == 'm6-test-thread'
    assert json.loads((tmp_path/'agent-state.json').read_text())['reviewed_report_directory'] == 'old'


def test_failed_delivery_can_retry(tmp_path):
    report = {'bucket': 7, 'directory': '/tmp/m6-report'}
    result = watch.queue(tmp_path, {'thread': 'm6-test-thread'}, report,
        lambda *a, **kw: SimpleNamespace(returncode=1, stdout='', stderr=''))
    assert result['status'] == 'WAKE_FAILED'
    assert not (tmp_path/'agent-state.json').exists()


@pytest.mark.parametrize('mode', ['VIRTUAL', 'LIVE'])
def test_market_is_read_only_and_requires_m6_virtual_identity(tmp_path, mode):
    path = tmp_path/'btc-updown-5m-1800000000.sqlite3'
    with sqlite3.connect(path) as db:
        db.executescript('CREATE TABLE metadata(key TEXT,value TEXT); CREATE TABLE opportunities(id TEXT); CREATE TABLE facts(kind TEXT,payload TEXT);')
        for key, value in {'identity': {'strategy': 'm6_withdrawal_supported_v1_1', 'mode': mode}, 'session_id': 'test'}.items():
            db.execute('INSERT INTO metadata VALUES(?,?)', (key, json.dumps(value)))
    before = path.read_bytes()
    if mode == 'LIVE':
        with pytest.raises(ValueError, match='FOREIGN_LEDGER'):
            watch.market(path, tmp_path)
    else:
        result = watch.market(path, tmp_path)
        assert result['opportunities'] == [] and result['facts']['virtual_post'] == []
    assert path.read_bytes() == before

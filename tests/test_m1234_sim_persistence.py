"""Independent persistence-fault regression: a durable failure cannot publish PASS."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from polymaker.m1234_sim.runner import failed_output
from test_m1234_sim_validity import filled_engine, late_constraint

PROJECT = Path('/home/ubuntu/polymaker')

@pytest.fixture
async def failed_database(tmp_path, monkeypatch):
    engine, _ = await filled_engine(tmp_path)
    engine.persist()
    def fail_snapshot(*args):
        raise OSError('INDEPENDENT_SNAPSHOT_WRITE_FAILURE')
    monkeypatch.setattr(engine.store, 'snapshot', fail_snapshot)
    with pytest.raises(OSError) as caught:
        await late_constraint(engine, at=.16)
    result = await failed_output(engine, tmp_path, 'OFFLINE_REPLAY', caught.value,
                                 tmp_path / 'recorded-input-placeholder.jsonl.gz')
    assert result['status'] == 'FAIL' and result['validity']['status'] == 'INVALID'
    # Preserve failed persistence, rather than repairing the DB before checking consumers.
    engine.store.db.rollback()
    engine.store.db.close()
    engine.closed = True
    return tmp_path

def run_cli(directory, operation):
    command = [sys.executable, '-m', 'polymaker.m1234_sim', operation,
               '--database', str(directory / 'simulation.sqlite3')]
    if operation == 'report':
        command += ['--output', str(directory / 'reopened-report')]
    result = subprocess.run(command, cwd=PROJECT, capture_output=True, text=True,
        env={**os.environ, 'PYTHONPATH': 'src:tests', 'PYTHONHASHSEED': '0', 'PYTHONDONTWRITEBYTECODE': '1'})
    (directory / (operation + '-command.json')).write_text(json.dumps({
        'command': command, 'exit_code': result.returncode, 'stdout': result.stdout,
        'stderr': result.stderr}, indent=2) + '\n')
    return result

async def test_failed_output_must_not_publish_valid_performance(failed_database):
    report = json.loads((failed_database / 'latest.json').read_text())
    assert report['status'] == 'FAIL', report['status']
    assert report['markets'][0]['validity']['status'] != 'VALID'
    assert all(not a['sample_eligible'] and not a['performance_eligible']
               for a in report['markets'][0]['accounts'])

@pytest.mark.parametrize('operation', ['audit', 'report'])
async def test_reopened_consumers_respect_durable_failure(failed_database, operation):
    result = run_cli(failed_database, operation)
    assert result.returncode != 0, result.stdout
    assert json.loads(result.stdout)['status'] == 'FAIL'

async def test_healthy_committed_database_remains_usable(tmp_path):
    engine, _ = await filled_engine(tmp_path)
    await engine.close()
    for operation in ('audit', 'report'):
        result = run_cli(tmp_path, operation)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)['status'] == 'PASS'

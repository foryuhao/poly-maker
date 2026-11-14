"""RR3: reuse one actual run for report-only failure regeneration and export controls."""
import hashlib
import shutil
from pathlib import Path
from uuid import uuid4

from polymaker.m1234_sim.events import write_json
from polymaker.m1234_sim.runstate import inspect_run
from test_m1234_sim_live_cleanup import cli_directory as research_output_fixture
from test_m1234_sim_live_cleanup import (
    command,
    driver,
    read,
    verify,
)

cli_directory = research_output_fixture


def databases(directory):
    return {str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(directory.rglob('*.sqlite3'))}


async def test_report_only_failure_survives_regeneration_export_and_foreign_control(cli_directory):
    output, observation = await driver(cli_directory, mode='normal', fault='terminal-files')
    run = output/'run'
    assert observation['terminal_saved'] is False
    assert 'SIM_RUN_TERMINAL_EVIDENCE_UNAVAILABLE' in observation['exception']['message']
    assert not (run/'completion.json').exists() and not list(run.glob('*.run-failure.json'))
    before = read(run/'latest.json')
    write_json(cli_directory/'report-before.json', before)
    assert before['status'] == 'FAIL' and before['run_failure']['cleanup_errors']
    failure = before['run_failure']
    assert failure['run_identity'] == observation['run_identity']
    assert failure['run_schema'] == 1 and failure['status'] == 'FAIL'
    fields = ['run_schema', 'run_identity', 'status', 'reason', 'primary_error', 'cleanup_errors']
    original = databases(run)
    assert original
    formal = await verify(cli_directory, run, 1, 'formal-before')
    assert formal['status'] == 'FAIL' and formal['run_evidence']['records'][0]['file'] == 'latest.json'
    database = Path(observation['members'][0]['directory'])/'simulation.sqlite3'
    reports = []
    for repeat in (1, 2):
        await command(cli_directory, 'regenerated-'+str(repeat), ['-m', 'polymaker.m1234_sim',
            'report', '--database', database, '--output', run], 1)
        after = read(run/'latest.json')
        write_json(cli_directory/('report-after-'+str(repeat)+'.json'), after)
        assert after['status'] == 'FAIL'
        assert {key: after['run_failure'][key] for key in fields} == {key: failure[key] for key in fields}
        assert after['markets'] == before['markets']
        assert not (run/'completion.json').exists() and not list(run.glob('*.run-failure.json'))
        checked = await verify(cli_directory, run, 1, 'formal-after-'+str(repeat))
        assert checked['status'] == 'FAIL'
        assert checked['run_evidence']['run_identity'] == failure['run_identity']
        assert databases(run) == original
        reports.append({'repeat':repeat, 'report_exit':1, 'formal_exit':1,
                        'failure':after['run_failure'], 'database_hashes':databases(run)})
    exported = cli_directory/'exported-run'
    shutil.copytree(run, exported)
    reopened = await verify(cli_directory, exported, 1, 'exported-formal')
    assert reopened['status'] == 'FAIL' and reopened['run_evidence']['run_identity'] == failure['run_identity']
    assert {key: reopened['run_evidence']['terminal'][key] for key in fields} == {key: failure[key] for key in fields}
    assert databases(exported) == original
    foreign = cli_directory/'foreign-report-only'
    shutil.copytree(run, foreign)
    foreign_report = read(foreign/'latest.json')
    foreign_report['run_failure']['run_identity']['run_id'] = str(uuid4())
    write_json(foreign/'latest.json', foreign_report)
    write_json(cli_directory/'foreign-report-before.json', foreign_report)
    unverified = inspect_run(foreign)
    assert unverified['status'] == 'INCOMPLETE' and unverified['terminal'] is None
    assert any('REPORT_FAILURE_UNVERIFIED' in error for error in unverified['errors'])
    foreign_database = foreign/database.relative_to(run)
    await command(cli_directory, 'foreign-regenerated-report', ['-m', 'polymaker.m1234_sim',
        'report', '--database', foreign_database, '--output', foreign], 0)
    after_foreign = read(foreign/'latest.json')
    assert after_foreign['status'] == 'PASS' and 'run_failure' not in after_foreign
    missing = await verify(cli_directory, foreign, 2, 'foreign-formal')
    assert missing['status'] == 'INCOMPLETE' and missing['run_evidence']['terminal'] is None
    assert databases(foreign) == original and databases(run) == original
    write_json(cli_directory/'rr3-proof.json', {'status':'PASS', 'actual_run_count':1,
        'run_exit':1, 'run_identity':observation['run_identity'], 'failure_before':failure,
        'regenerations':reports, 'exported_formal_exit':1, 'exported_run_identity':reopened['run_evidence']['run_identity'],
        'foreign_report_exit':0, 'foreign_formal_exit':2, 'foreign_record_verified':False,
        'database_hashes_before':original, 'database_hashes_after':databases(run),
        'exported_database_hashes':databases(exported), 'foreign_database_hashes':databases(foreign),
        'economic_reports_unchanged':True, 'terminal_files_created_by_report':False})

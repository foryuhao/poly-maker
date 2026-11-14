"""Health classification, incident deduplication and same-thread repair requests."""
import json
from types import SimpleNamespace

from ops.m1234_sim_watch import assess, atomic_json, wake


def fixture(tmp_path):
    run = tmp_path/'research_outputs/m1234_sim/forward/24h-test'
    run.mkdir(parents=True)
    unit = 'polymaker-m1234-sim-24h-test.service'
    control = {'run_directory':str(run),'unit':unit,'thread':'explicit-current-thread',
               'started_at':1000,'enabled':True}
    plan = {'mode':'SIMULATION_ONLY','real_order_transport':'ABSENT','started_utc_epoch':1000,
            'target_complete_windows':288,'run_identity':{'run_id':'test'},
            'settings':{'input_queue_frames':2048,'max_rss_mib':512,'min_free_disk_mib':1}}
    atomic_json(run/'plan.json', plan)
    return run, control


def service(state):
    def respond(args, **kwargs):
        return SimpleNamespace(stdout=f'ActiveState={state}\nMainPID=1\n',stderr='',returncode=0)
    return respond


def test_alignment_grace_heartbeat_staleness_and_backlog(tmp_path):
    run, control = fixture(tmp_path)
    assert assess(control, now=1300, project=tmp_path, runner=service('active'))['stage']=='STARTING'
    assert assess(control, now=1500, project=tmp_path, runner=service('active'))['problems']==['HEARTBEAT_STALE']
    beat={'event':'HEARTBEAT','at':1480,'frames':1000,'backlog':0,'rss_peak_mib':100}
    (run/'runtime.jsonl').write_text(json.dumps(beat)+'\n')
    assert assess(control, now=1500, project=tmp_path, runner=service('active'))['stage']=='RUNNING'
    beat['backlog']=1900
    (run/'runtime.jsonl').write_text(json.dumps(beat)+'\n')
    assert assess(control, now=1500, project=tmp_path, runner=service('active'))['problems']==['INPUT_BACKLOG_HIGH']


def test_failure_is_not_normal_completion_or_manual_stop(tmp_path):
    run, control = fixture(tmp_path)
    terminal={'run_identity':{'run_id':'test'},'status':'FAIL','reason':'SIM_CAPTURE_QUEUE_OVERFLOW',
              'complete_windows':3,'cleanup_errors':[],'recording_errors':[],'invalid_markets':['fourth']}
    atomic_json(run/'completion.json',terminal)
    failed=assess(control,now=1500,project=tmp_path,runner=service('failed'))
    assert failed['stage']=='REPAIR_REQUIRED' and failed['incident']
    terminal.update(status='PASS',reason='PLANNED_COMPLETE',complete_windows=288,invalid_markets=[])
    atomic_json(run/'completion.json',terminal)
    assert assess(control,now=1500,project=tmp_path,runner=service('inactive'))['stage']=='COMPLETED'
    terminal.update(status='FAIL',reason='MANUAL_STOP',complete_windows=2)
    atomic_json(run/'completion.json',terminal)
    assert assess(control,now=1500,project=tmp_path,runner=service('inactive'))['stage']=='STOPPED_MANUALLY'


def test_repair_wake_uses_authorized_thread_and_coalesces(tmp_path):
    _, control=fixture(tmp_path)
    report={'incident':'failure-1','stage':'REPAIR_REQUIRED','problems':['SERVICE_EXITED']}
    calls=[]

    def respond(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(stdout='',stderr='',returncode=0)

    result=wake(control,report,tmp_path,runner=respond,now=2000)
    assert result['status']=='QUEUED_AND_RESUME_REQUESTED'
    assert calls[0][1:4]==['queue','--thread',control['thread']]
    assert '--resume' in calls[0] and '新目录' in calls[0][5]
    assert wake(control,report,tmp_path,runner=respond,now=2300)['status']=='REPAIR_ALREADY_QUEUED'
    assert len(calls)==1
    state=json.loads((tmp_path/'agent-state.json').read_text())
    assert state['thread']==control['thread']


def test_wake_failure_is_retried_instead_of_marked_queued(tmp_path):
    _, control=fixture(tmp_path)
    report={'incident':'failure','problems':['SERVICE_EXITED']}
    result=wake(control,report,tmp_path,now=2000,
                runner=lambda *a,**k:SimpleNamespace(returncode=1,stdout='',stderr='offline'))
    assert result['status']=='WAKE_FAILED' and not (tmp_path/'agent-state.json').exists()


def test_every_check_reports_healthy_and_does_not_suppress_same_bucket_failure(tmp_path):
    _, control = fixture(tmp_path)
    control['notify_every_check'] = True
    report = {'bucket': 10, 'stage': 'RUNNING', 'incident': None, 'problems': [],
              'metrics': {'status': 'OK', 'strategies': {}, 'sources': ['watermark']}}
    calls = []

    def respond(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout='', stderr='')

    assert wake(control, report, tmp_path, now=3000, runner=respond)['status'] == 'QUEUED_AND_RESUME_REQUESTED'
    assert wake(control, report, tmp_path, now=3010, runner=respond)['status'] == 'CHECK_ALREADY_QUEUED'
    report.update(stage='REPAIR_REQUIRED', incident='new-failure', problems=['SERVICE_EXITED'])
    assert wake(control, report, tmp_path, now=3020, runner=respond)['status'] == 'QUEUED_AND_RESUME_REQUESTED'
    report['bucket'] = 11
    assert wake(control, report, tmp_path, now=3300, runner=respond)['status'] == 'QUEUED_AND_RESUME_REQUESTED'
    assert len(calls) == 3
    assert '即使计数没有变化也要汇报' in calls[0][5]
    assert 'watermark' not in calls[0][5]
    report.update(stage='RUNNING', incident=None, problems=[], bucket=12)
    assert wake(control, report, tmp_path, now=3600, runner=respond)['status'] == 'QUEUED_AND_RESUME_REQUESTED'

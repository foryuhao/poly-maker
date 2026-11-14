"""Start an isolated simulation attempt and bind its five-minute health monitor."""
import argparse
import fcntl
import hashlib
import json
import shutil
import subprocess
import tarfile
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from polymaker.m1234_sim.events import digest
from polymaker.m1234_sim.runner import preflight, settings

PROJECT = Path(__file__).resolve().parents[1]


def save(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)


def next_control(previous, *, unit, run, thread, started_at):
    """Change run ownership while retaining the user's monitoring preference."""
    control = dict(previous)
    control.pop('signal_validation', None)  # A sidecar must bind the new source separately.
    control.update(unit=unit, run_directory=str(run), thread=thread, started_at=started_at)
    control.setdefault('enabled', True)
    control.setdefault('interval_seconds', 300)
    control.setdefault('notify_every_check', control['enabled'])
    return control


def launch(config, contract, thread):
    monitor = PROJECT/'research_outputs/m1234_sim/monitor'
    monitor.mkdir(parents=True, exist_ok=True)
    with (monitor/'watch.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for process in Path('/proc').iterdir():
            if not process.name.isdecimal():
                continue
            try:
                words = (process/'cmdline').read_bytes().split(b'\0')
                if (b'polymaker.m1234_sim' in words
                        and words[words.index(b'polymaker.m1234_sim')+1] == b'run'):
                    raise RuntimeError('SIMULATION_ALREADY_RUNNING:' + process.name)
            except (OSError, IndexError):
                continue
        check = preflight(config, contract)
        if check['status'] != 'PASS':
            raise RuntimeError('SIM_PREFLIGHT_FAILED:' + json.dumps(check))
        now = datetime.now(ZoneInfo('Asia/Shanghai'))
        label = now.strftime('%Y%m%dT%H%M%S') + '-' + uuid4().hex[:6]
        run = PROJECT/'research_outputs/m1234_sim/forward'/('24h-' + label)
        launch_dir = run/'launch'
        launch_dir.mkdir(parents=True, exist_ok=False)
        shutil.copyfile(config, launch_dir/'24h.toml')
        shutil.copyfile(contract, launch_dir/'strategy-contract.json')
        cfg = settings(launch_dir/'24h.toml', launch_dir/'strategy-contract.json')
        if digest(cfg['implementation']) != check['implementation_hash']:
            raise RuntimeError('SIM_SOURCE_CHANGED_DURING_LAUNCH')
        with tarfile.open(launch_dir/'source.tar.gz', 'w:gz') as archive:
            for name, expected in cfg['implementation'].items():
                if hashlib.sha256((PROJECT/name).read_bytes()).hexdigest() != expected:
                    raise RuntimeError('SIM_SOURCE_CHANGED_DURING_ARCHIVE:' + name)
                archive.add(PROJECT/name, arcname=name, recursive=False)
            archive.add(PROJECT/'pyproject.toml', arcname='pyproject.toml', recursive=False)
        save(launch_dir/'implementation.json', cfg['implementation'])
        save(launch_dir/'preflight.json', check)
        unit = 'polymaker-m1234-sim-24h-' + label.lower() + '.service'
        command = [str(PROJECT/'.venv/bin/python'), '-u', '-m', 'polymaker.m1234_sim', 'run',
                   '--config', str(launch_dir/'24h.toml'),
                   '--contract', str(launch_dir/'strategy-contract.json'), '--output', str(run)]
        record = {'launched_at_beijing':now.isoformat(), 'started_at':time.time(), 'unit':unit,
                  'directory':str(run), 'command':command, 'status':'START_REQUESTED',
                  'target_markets':cfg['max_markets'], 'real_order_transport':'ABSENT',
                  'implementation_hash':check['implementation_hash']}
        save(launch_dir/'launch.json', record)
        reply = subprocess.run(['systemd-run', '--user', '--unit='+unit,
            '--description=M1234 24-hour public market simulation', '--service-type=exec',
            '--working-directory='+str(PROJECT), '--setenv=PYTHONPATH='+str(PROJECT/'src'),
            '--property=OnFailure=polymaker-m1234-sim-watch.service',
            '--property=Restart=no', '--property=KillSignal=SIGINT', '--property=TimeoutStopSec=120',
            '--property=StandardOutput=append:'+str(run/'console.log'),
            '--property=StandardError=inherit', *command], capture_output=True, text=True, timeout=30)
        record.update(status='STARTED' if reply.returncode == 0 else 'START_FAILED',
                      systemd_exit_code=reply.returncode, systemd_output=reply.stdout+reply.stderr)
        save(launch_dir/'launch.json', record)
        if reply.returncode:
            raise RuntimeError('SIM_SERVICE_START_FAILED:' + reply.stderr)
        previous = monitor/'control.json'
        old_control = json.loads(previous.read_text()) if previous.exists() else {}
        if previous.exists():
            save(launch_dir/'previous-monitor-control.json', old_control)
        save(previous, next_control(old_control, unit=unit, run=run, thread=thread,
                                    started_at=record['started_at']))
        return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=PROJECT/'config/m1234-sim-24h.toml')
    parser.add_argument('--contract', type=Path, default=PROJECT/'config/m1234-sim-contract.json')
    parser.add_argument('--thread', required=True)
    args = parser.parse_args()
    print(json.dumps(launch(args.config, args.contract, args.thread), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()

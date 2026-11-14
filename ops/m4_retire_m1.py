"""Archive the retired M1 runtime outside all active roots; default is plan only.

No account or exchange API exists here. SQLite files are opaque bytes and travel
with their WAL/SHM siblings. The active balance latch is copied before any move.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from polymaker.m4_control import atomic_json

OLD_UNITS = ('polymaker-m1-live.service', 'polymaker-m1.service',
             'polymaker-m1-watch.service', 'polymaker-m1-watch.timer')


def scan_error(error):
    raise error  # Never silently omit a root-owned subtree.


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024*1024), b''):
            h.update(block)
    return h.hexdigest()


def inventory(project):
    manifest = project/'research_outputs/analysis/m4_cutover_review_20261108/cleanup_manifest.json'
    # The previous review has positively identified these legacy session names.
    paths = {project/i['path'] for i in json.loads(manifest.read_text())}
    paths.update((project/'journal').glob('m1_live_*.sqlite3*'))
    paths.update((project/'journal').glob('*_m1_live_*.jsonl'))
    roots = [project/'research_outputs'/name for name in
             ('m1_live','m1_virtual','m1_live_watch','m1_calibration','m1_audit')]
    roots += list((project/'research_outputs/analysis').glob('m1_*'))
    roots += [project/'config/m1-live']
    for root in roots:
        if root.is_dir():
            for directory, dirs, files in os.walk(root, onerror=scan_error):
                paths.update(Path(directory)/name for name in files)
                paths.update(Path(directory)/name for name in dirs if (Path(directory)/name).is_symlink())
        elif root.is_file():
            paths.add(root)
    result = []
    for path in sorted(paths):
        if not path.exists() and not path.is_symlink():
            continue
        relative = str(path.relative_to(project))
        row = {'path':relative,'bytes':path.lstat().st_size}
        if path.is_symlink():
            row['symlink_target'] = os.readlink(path)
        else:
            # Revalidate session headers only; never parse fill/position records.
            if relative.startswith('journal/rdp_sm_'):
                with path.open() as source:
                    first = json.loads(source.readline())
                payload = first.get('data',{}).get('payload',{})
                if (first.get('kind') != 'sm_session' or
                        payload.get('normal_entry_mode') != 'm1_v1'
                        and not payload.get('strategy','').startswith('m1_')):
                    raise ValueError('M1_SESSION_HEADER_MISMATCH:'+relative)
            row['sha256'] = digest(path)
        result.append(row)
    return result, roots


def retire(project, archive, *, apply=False):
    project, archive = project.resolve(), archive.absolute()
    if archive == project or project in archive.parents or archive.resolve() != archive:
        raise ValueError('ARCHIVE_MUST_BE_OUTSIDE_PROJECT_AND_NOT_SYMLINKED')
    for unit in OLD_UNITS:
        state = subprocess.run(['systemctl','--user','show',unit,'-p','ActiveState','--value'],
                               check=True,capture_output=True,text=True).stdout.strip()
        if state not in {'inactive','failed'}:
            raise ValueError('OLD_RUNTIME_STILL_ACTIVE:'+unit+':'+state)
    rows, roots = inventory(project)
    plan = {'created_at':datetime.now(UTC).isoformat(),'project':str(project),
        'archive':str(archive),'files':rows,'bytes':sum(r['bytes'] for r in rows),
        'file_count':len(rows),'applied':False,'account_queries':0}
    evidence = project/'research_outputs/analysis/m4_implementation_20261108'
    atomic_json(evidence/'retirement-plan.json',plan)
    if not apply:
        return {k:v for k,v in plan.items() if k!='files'}
    archive.mkdir(parents=True,exist_ok=False,mode=0o700)
    atomic_json(archive/'manifest.json',plan)
    controls = project/'runtime/trading-control'
    old_stop = project/'research_outputs/m1_live_watch/balance-stop.json'
    stop = json.loads(old_stop.read_text())
    if stop.get('requires_confirmation') is not True:
        raise ValueError('EXPECTED_EXISTING_CONFIRMATION_LATCH')
    if (controls/'balance-stop.json').exists():
        if json.loads((controls/'balance-stop.json').read_text()) != stop:
            raise ValueError('BALANCE_LATCH_CONFLICT')
    else:
        atomic_json(controls/'balance-stop.json',stop)
    for mode in ('live','baseline'):
        atomic_json(controls/f'm4-{mode}-stop.json',{'reason':'USER_STOP_PRESERVED_DURING_M4_CUTOVER',
            'requires_confirmation':True,'created_at':plan['created_at']})
    atomic_json(controls/'m1-retired.json',{'reason':'M1_REPLACED_BY_M4','archive':str(archive)})
    # Retain dereferenced transport configuration alongside the old symlinks.
    for path in (project/'config/m1-live').glob('*.toml'):
        if path.is_symlink():
            target = archive/'referenced-config'/path.name
            target.parent.mkdir(exist_ok=True)
            shutil.copy2(path,target)
    unit_root = Path.home()/'.config/systemd/user'
    for unit in OLD_UNITS:
        for source in (unit_root/unit, unit_root/(unit+'.d')):
            if source.exists() or source.is_symlink():
                dest = archive/'systemd'/source.name
                dest.parent.mkdir(exist_ok=True)
                shutil.move(source,dest)
        (unit_root/unit).symlink_to('/dev/null')
    for row in rows:
        source, target = project/row['path'], archive/'runtime'/row['path']
        target.parent.mkdir(parents=True,exist_ok=True)
        if 'sha256' in row and digest(source) != row['sha256']:
            raise ValueError('SOURCE_CHANGED_BEFORE_MOVE:'+row['path'])
        # Same-volume rename preserves DB/WAL bytes and avoids a delete phase.
        os.rename(source,target)
        if 'sha256' in row and digest(target) != row['sha256']:
            raise ValueError('ARCHIVE_HASH_MISMATCH:'+row['path'])
    for root in roots:
        if root.is_dir():
            for directory, _, files in os.walk(root, topdown=False, onerror=scan_error):
                if files:
                    raise ValueError('UNPLANNED_FILES_REMAIN:'+directory)
                Path(directory).rmdir()
    for unit in ('polymaker-m4-live.service','polymaker-m4-baseline.service'):
        shutil.copy2(project/'ops'/unit,unit_root/unit)
    subprocess.run(['systemctl','--user','daemon-reload'],check=True)
    plan['applied'],plan['verified_files'] = True,len(rows)
    plan['balance_stop_id'] = stop['id']
    plan['new_services_started'] = False
    plan['retained_compatibility'] = 'M1 source imported by separate M1234/M234 research; M1 service entrypoints retired. Dashboard and shared system journal untouched.'
    atomic_json(archive/'manifest.json',plan)
    atomic_json(evidence/'retirement-result.json',{k:v for k,v in plan.items() if k!='files'})
    return {k:v for k,v in plan.items() if k!='files'}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-dir',type=Path,default=Path.cwd())
    parser.add_argument('--archive',type=Path,required=True)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    print(json.dumps(retire(args.project_dir,args.archive,apply=args.apply),indent=2))

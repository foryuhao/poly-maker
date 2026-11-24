"""Explicit new SOPs on retained public inputs: frequency first, PnL only after gate.

Source capture identity remains M19. This creates a new independent simulation
ledger, not a relabelled historical result or independent realtime validation.
"""
import argparse
import asyncio
import hashlib
import json
import resource
import shutil
import sqlite3
import time
from collections import Counter
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from m1223_replay import file_hash, replay_market, validate_dataset
from polymaker.m1234_sim.events import write_json


def frequency_market(database, owner):
    """Inspect committed identities/counts without reading any PnL or winner."""
    from polymaker.m1234_sim.validity import database_validity

    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        state = json.loads(db.execute("SELECT payload FROM snapshots WHERE name='state'").fetchone()[0])
        bound = json.loads(db.execute("SELECT payload FROM metadata WHERE name='run_owner'").fetchone()[0])
        gate = json.loads(db.execute("SELECT payload FROM metadata WHERE name='research_sample_eligibility'").fetchone()[0])
        definition = json.loads(db.execute("SELECT payload FROM audit WHERE kind='DEFINITION'").fetchone()[0])['definition']
        if bound != owner or definition.get('artificial_fixture'):
            raise ValueError("FOREIGN_OR_ARTIFICIAL_REPLAY_AUDIT")
        signals = {json.loads(p)['payload']['id'] for (p,) in db.execute(
            "SELECT payload FROM audit WHERE kind='SOP_EVIDENCE' "
            "AND json_extract(payload,'$.event')='SIGNAL_CONFIRMED'")}
        counts = dict(db.execute("SELECT kind,COUNT(*) FROM audit GROUP BY kind"))
        evidence = dict(db.execute("SELECT json_extract(payload,'$.event'),COUNT(*) FROM audit "
                                   "WHERE kind='SOP_EVIDENCE' GROUP BY 1"))
        reasons = dict(db.execute("SELECT json_extract(payload,'$.payload.reason'),COUNT(*) FROM audit "
            "WHERE kind='SOP_EVIDENCE' AND json_extract(payload,'$.event') IN "
            "('QUALITY_UNKNOWN','ROUND_UNKNOWN','ROOT_UNKNOWN','SEED_UNKNOWN','PUBLICATION_REJECTED','ROOT_ENDED') GROUP BY 1"))
        filled = {json.loads(p)['order'] for (p,) in db.execute(
            "SELECT payload FROM audit WHERE kind='MODELLED_FILL'") if Decimal(json.loads(p)['quantity']) > 0}
        uncertain = {json.loads(p)['order'] for (p,) in db.execute(
            "SELECT payload FROM audit WHERE kind IN ('ORDER_UNCERTAIN','UNKNOWN_TAIL')")}
        watermarks = db.execute("SELECT MAX(seq),MAX(input_seq) FROM audit").fetchone()
    validity = database_validity(database, state)['status']
    if validity != 'VALID' or counts.get('OBSERVATION_END') != 1:
        raise ValueError("REPLAY_AUDIT_NOT_VALID_AND_SEALED")
    sop = definition['settings']['sop']
    return {
        'market': state['market']['slug'], 'variant_id': sop['variant_id'],
        'signal_ids': sorted(signals), 'signal_count': len(signals),
        'submitted_orders': counts.get('SUBMIT', 0), 'assumed_orders': counts.get('ASSUMED_FILL', 0),
        'positive_modelled_fill_orders': len(filled), 'orders_with_uncertainty_events': len(uncertain),
        'primary_eligible': gate['status'] == 'ELIGIBLE', 'eligibility': gate,
        'complete_window': state['coverage'].get('complete_window') is True,
        'has_unknown': any(evidence.get(k) for k in ('QUALITY_UNKNOWN','ROUND_UNKNOWN','ROOT_UNKNOWN','SEED_UNKNOWN')),
        'evidence_counts': evidence, 'unknown_and_rejection_reasons': reasons,
        'audit_seq': watermarks[0], 'audit_input_seq': watermarks[1],
        'snapshot_input_seq': state['input_seq'], 'validity': validity,
        'database_sha256': file_hash(database),
    }


def frequency_result(markets, owner, revision):
    if len(markets) != 24 or len({m['market'] for m in markets}) != 24:
        raise ValueError('ALL_FIXED24_REQUIRED_BEFORE_FREQUENCY_DECISION')
    raw = [m['market'] for m in markets if m['signal_count']]
    primary = [m['market'] for m in markets if m['signal_count'] and m['primary_eligible']]
    return {
        'status': 'PASS', 'scope': 'FIXED24_DEVELOPMENT_REPLAY_NOT_INDEPENDENT',
        'metrics_status': 'PARTIAL' if any(m['has_unknown'] or not m['complete_window']
                                         for m in markets) else 'OK',
        'owner': owner, 'revision': revision, 'denominator': 24,
        'all_recorded_signal_markets': raw, 'primary_signal_markets': primary,
        'all_recorded_signal_count': sum(m['signal_count'] for m in markets),
        'primary_signal_count': sum(m['signal_count'] for m in markets if m['primary_eligible']),
        'primary_distinct_signal_markets': len(primary), 'primary_coverage': len(primary) / 24,
        'frequency_target_met': len(primary) >= 10, 'minimum_signal_markets': 10,
        'complete_windows': sum(m['complete_window'] for m in markets),
        'unknown_markets': [m['market'] for m in markets if m['has_unknown']],
        'isolated_markets': [m['market'] for m in markets if not m['primary_eligible']],
        'markets': markets, 'pnl_used_for_selection': False,
        'pnl_evaluated': False, 'independent_or_24h_admission_granted': False,
    }


def evaluate_pnl(output, frequency):
    """A separate post-gate call; never use this artifact to select parameters."""
    import csv
    from m1223_report import market_result, summary

    if not frequency['frequency_target_met'] or frequency['primary_distinct_signal_markets'] < 10:
        raise ValueError('PNL_REQUIRES_COMPLETED_FREQUENCY_TARGET')
    markets, orders = [], []
    for fact in frequency['markets']:
        database = output / fact['market'] / 'simulation.sqlite3'
        if file_hash(database) != fact['database_sha256']:
            raise ValueError('POST_FREQUENCY_NATIVE_AUDIT_CHANGED')
        m, o = market_result(database, Path('/home/ubuntu/polymaker'), eligibility=fact['eligibility'])
        if m['signal_count'] != fact['signal_count']:
            raise ValueError('PNL_SIGNAL_POPULATION_DIFFERS_FROM_FREQUENCY')
        markets.append(m); orders.extend(o)
    primary = [o for o in orders if o['primary_eligible']]
    report = {'status':'PASS','frequency_gate_checked_before_pnl':True,
        'frequency_report_sha256':file_hash(output/'frequency-only.json'),
        'owner':frequency['owner'],'revision':frequency['revision'],'markets':markets,
        'all_recorded':{mode:summary(orders,frequency['all_recorded_signal_count'],mode)
                        for mode in ('assumed','simulated')},
        'primary':{mode:summary(primary,frequency['primary_signal_count'],mode)
                   for mode in ('assumed','simulated')},
        'limitations':['Development samples only,not independent profitability proof',
                      'Pending and unknown execution is not zero; known PnL may be incomplete']}
    write_json(output/'pnl-report.json',report)
    with (output/'orders.csv').open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(orders[0]) if orders else ['market','order_id'])
        writer.writeheader();writer.writerows(orders)
    return report


async def run(dataset, document, output, provenance, *, frequency_only=False):
    from m1223_sim import definition
    from sop_signals.parameters import load_revision

    records = validate_dataset(dataset)
    parameters, revision = load_revision(document)
    selected = definition(parameters)
    if selected.get('variant_id') not in {'M12_VALIDITY_OVERLAP','M23_FIRST_SELL_WHILE_VALID','M21_ENDPOINT_COFLOW','M7_PAUSE_RELEASE','M9_EX_VALIDITY_OVERLAP'}:
        raise ValueError('EXPLICIT_SELECTED_NEW_SOP_REQUIRED')
    if dataset.get('strategy') != 'M19' or dataset['capture_sop_definition']['strategy'] != 'M19':
        raise ValueError('ONLY_THE_USER_RETAINED_M19_PUBLIC_COHORT_IS_APPROVED')
    if output.exists():
        raise ValueError('NEW_OUTPUT_AND_RUN_IDENTITY_REQUIRED')
    if shutil.disk_usage(output.parent).free < 6 * 1024**3:
        raise ValueError('REPLAY_REQUIRES_2GIB_PLUS_4GIB_RESERVE')
    provenance = {**provenance, 'public_input_reuse': {
        'mode':'PUBLIC_INPUT_ONLY_NEW_STRATEGY_LEDGER', 'source_strategy':'M19',
        'source_sop_sha256':hashlib.sha256(json.dumps(dataset['capture_sop_definition'],sort_keys=True).encode()).hexdigest(),
        'target_strategy':selected['strategy'],'target_variant':selected['variant_id']}}
    owner = {'run_id':str(uuid4()),'mode':'EXPLICIT_VARIANT_DEVELOPMENT_REPLAY',**provenance}
    output.mkdir(parents=True)
    write_json(output/'replay-plan.json',{'owner':owner,'revision':revision,'dataset':dataset,
        'selected_sop':selected,'frequency_before_pnl':True,'frequency_only':frequency_only,'fixed_denominator':24,
        'limits':{'rss_mib':512,'output_mib':2048,'reserve_mib':4096}})
    markets, samples = [], []
    try:
        for record in records:
            await replay_market(record,output/record['market'],parameters,owner,provenance,revision)
            markets.append(frequency_market(output/record['market']/'simulation.sqlite3',owner))
            size=sum(f.stat().st_size for f in output.rglob('*') if f.is_file())
            rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024
            samples.append({'completed_markets':len(markets),'rss_peak_mib':rss,
                            'output_bytes':size,'wall_epoch':time.time()})
            write_json(output/'progress.json',{'status':'RUNNING','variant_id':selected['variant_id'],
                'completed_markets':len(markets),'signals_so_far':sum(m['signal_count'] for m in markets),
                'signal_markets_so_far':sum(bool(m['signal_count']) and m['primary_eligible'] for m in markets),
                'pnl_evaluated':False,'resources':samples[-1]})
            if rss>512 or size>=2*1024**3 or shutil.disk_usage(output).free<4*1024**3:
                raise RuntimeError('REPLAY_RESOURCE_CAPACITY_EXHAUSTED')
        result=frequency_result(markets,owner,revision)
        result['resources']=samples
        write_json(output/'frequency-only.json',result)
        # Frequency decision is durable before opening economic report fields.
        write_json(output/'frequency-decision.json',{
            'frequency_sha256':file_hash(output/'frequency-only.json'),
            'target_met':result['frequency_target_met'],
            'primary_signal_markets':result['primary_distinct_signal_markets'],
            'denominator':24,'decision_before_pnl':True,'pnl_used_for_selection':False})
        if result['frequency_target_met'] and not frequency_only:
            evaluate_pnl(output,result)
        write_json(output/'progress.json',{'status':'PASS','completed_markets':24,
            'variant_id':selected['variant_id'],
            'primary_signal_markets':result['primary_distinct_signal_markets'],
            'signals':result['primary_signal_count'],'frequency_target_met':result['frequency_target_met'],
            'pnl_evaluated':result['frequency_target_met'] and not frequency_only,'resources':samples[-1]})
        return result
    except BaseException as error:
        write_json(output/'failure.json',{'status':'FAIL','completed_markets':len(markets),
            'error_type':type(error).__name__,'error':str(error),'owner':owner,'resources':samples})
        raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('runtime','dataset','parameters','output'):
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--frequency-only',action='store_true',help='Do not read economic results, even after frequency passes')
    args=parser.parse_args()
    for rel, expected in json.loads((args.runtime/'frozen-manifest.json').read_text()).items():
        if file_hash(args.runtime/rel)!=expected:raise ValueError('FROZEN_SOURCE_CHANGED:'+rel)
    if not args.output.resolve().is_relative_to(Path('/home/ubuntu/polymaker/research_outputs/m1223_sim')):
        raise ValueError('OWN_OUTPUT_REQUIRED')
    dataset=json.loads(args.dataset.read_text());document=json.loads(args.parameters.read_text())
    result=asyncio.run(run(dataset,document,args.output,{
        'dataset_sha256':file_hash(args.dataset),'parameters_sha256':file_hash(args.parameters),
        'runtime':str(args.runtime),'wrapper_sha256':file_hash(Path(__file__))},frequency_only=args.frequency_only))
    print(json.dumps({'status':result['status'],'primary_signal_markets':result['primary_distinct_signal_markets'],
                      'denominator':24,'signals':result['primary_signal_count'],
                      'frequency_target_met':result['frequency_target_met']}))


if __name__=='__main__':
    main()

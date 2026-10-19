"""Offline production-policy replay. No venue imports, orders or assumed fills."""
import csv
import hashlib
import json
from collections import Counter
from decimal import Decimal
from pathlib import Path

from polymaker.config import RDPRepairPathConfig
from polymaker.repair_policy import PathPoint, RepairPolicy

OUT = Path(__file__).resolve().parent
PRIOR = OUT.parent/'20261019_repair_parameter_research'
ROOT = OUT.parents[3]


def read(path):
    return list(csv.DictReader(path.open()))


def main():
    cfg = RDPRepairPathConfig()
    expected = {(r['sid'], int(float(r['t0'])), r['side']): r
                for r in read(PRIOR/'path_probe_results.csv')
                if r['basis']=='fair' and r['sl_spec']=='fixed06' and r['r_tp']=='0.002'}
    assert len(expected) == 704
    outputs = []
    for path in sorted((PRIOR/'cache').glob('*.csv')):
        rows = read(path)
        sid = rows[0]['session_id']
        for t0 in range(40,120,10):
            for side in ['YES','NO']:
                if (sid,t0,side) not in expected:
                    continue
                ref = expected[sid,t0,side]
                direction = 1 if side=='YES' else -1
                # Production builds p from Decimal BBO. The old raw observer
                # used float (bid+ask)/2, which can serialize a half-tick with
                # ~1e-16 noise. Restore the actual BBO midpoint, not that noise.
                entry_p = (Decimal(rows[t0]['yes_bid'])+Decimal(rows[t0]['yes_ask']))/2
                policy = RepairPolicy(cfg)
                tp, sl, reason, censored = None, None, None, None
                for t in range(t0,181):
                    r = rows[t]
                    if not r['p'] or r['generation'] != rows[t0]['generation']:
                        censored = t
                        break
                    p = (Decimal(r['yes_bid'])+Decimal(r['yes_ask']))/2
                    point = PathPoint(t,float(p),float(r['sigma']) if r['sigma'] else None,
                        int(float(r['generation'])),direction*5,float(direction*(p-entry_p)),
                        episode_delta=direction*5,tick=float(r['tick']),path_segment=1)
                    d = policy.decide(point, original_side=side, original_qty=5, first_fill_elapsed=t0)
                    if d['mode']=='TP' and tp is None:
                        tp = t
                    if d['mode']=='SL':
                        sl, reason = t, {'ADVERSE_SHOCK':'SHOCK','CUMULATIVE_DRAWDOWN':'DRAWDOWN',
                                         'TIME_FORCE_SL':'TIME'}[d['reason']]
                        break
                result = {'sid':sid,'t0':t0,'side':side,'tp_t':tp,'sl_t':sl,
                          'sl_reason':reason,'censored_t':censored}
                differences = {key: {'implementation':result[key],'research':ref[key]}
                               for key in ['tp_t','sl_t','sl_reason','censored_t']
                               if str(result[key] if result[key] is not None else '') != ref[key]}
                result['differences'] = differences
                outputs.append(result)
    mismatches = [r for r in outputs if r['differences']]
    (OUT/'grid_replay.json').write_text(json.dumps(outputs,indent=2)+'\n')

    # Known-case input snapshots are sparse; this is explanation, not a fill replay.
    policy, case = RepairPolicy(cfg), []
    for i,r in enumerate(read(ROOT/'docs/development/reviews/20261018_repair_live_1810/decisions.csv'),1):
        t,p,sigma = [float(r[k]) for k in ['model_input_T','p_yes','sigma_per_sqrt_second']]
        point = PathPoint(t,p,sigma,1,5,float(Decimal(str(p))-Decimal('.53')),episode_delta=5)
        d = policy.decide(point,original_side='YES',original_qty=5,first_fill_elapsed=35.494)
        case.append({'decision':i,'input_T':t,'p':p,'mode':d['mode'],'reason':d['reason'],
                     'short_z':d['observations']['short_z'],
                     'drawdown':d['observations']['drawdown_per_share']})
    assert case[0]['mode']=='TP'
    assert next(r['decision'] for r in case if r['mode']=='SL') == 11
    assert case[10]['reason']=='ADVERSE_SHOCK'
    (OUT/'known_case_replay.json').write_text(json.dumps(case,indent=2)+'\n')
    summary = {'scope':'PRICE_POLICY_ONLY; NO_ASSUMED_MAKER_FILLS_OR_SESSION_PAYOFF',
               'parameters':cfg.model_dump(mode='json'), 'markets':47,'cases':len(outputs),
               'research_comparison_differences':len(mismatches),
               'known_case':{'first_tp_decision':1,'first_sl_decision':11,'z':case[10]['short_z']},
               'counts':dict(Counter('TP_FIRST' if r['tp_t'] is not None else 'SL_FIRST'
                                    if r['sl_t'] is not None else 'CENSORED_FIRST' for r in outputs)),
               'source_sha256':{name:hashlib.sha256((PRIOR/name).read_bytes()).hexdigest()
                                for name in ['path_probe_results.csv','raw_manifest.json']},
               'ready_for_live':False}
    (OUT/'replay_summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary,indent=2))
    assert not mismatches, f'Implementation vs research path differences: {mismatches[:3]}'


if __name__=='__main__':
    main()

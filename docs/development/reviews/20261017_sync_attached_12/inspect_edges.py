"""As-of eligibility and source-change checks over the same immutable cohort."""
import collections
import csv
import json
import pathlib
from analyze import OUT, quant, rows, local

def main():
    d=json.loads((OUT/'evidence.json').read_text());orders=list(csv.DictReader((OUT/'orders.csv').open()))
    metrics=collections.defaultdict(list);examples=[];positions=[];checks=collections.Counter();rounds=collections.defaultdict(list)
    for se in d['sessions']:
        p=pathlib.Path(se['observer_dir']);snaps=list(rows(p/'snapshots.jsonl'));ev=list(rows(p/'events.jsonl'));journal=list(rows(pathlib.Path(se['journal'])))
        refs=list(rows(p/'strategy_refs.jsonl'));own=[o for o in orders if o['session_id']==se['session_id']]
        offset=next(r['recorded_wall']-r['recorded_monotonic'] for _,r in refs)
        for n,r in snaps:
            for key,read in r['last_good'].items():
                if r['attempts'][key]['read_id']!=read['read_id']:checks['attempt_different_from_last_good']+=1
        for n,e in ev:
            if e['kind']=='sync_round' and e['lane']=='orders' and not e['shutdown']:
                # Associate HTTP detail intervals by actual start inside this order round.
                details=[x for _,x in ev if x['kind']=='sync_http' and x['endpoint'].startswith('/data/order/') and e['started_monotonic']<=x['started_monotonic']<=e['completed_monotonic']]
                rounds['with_details' if details else 'no_details'].append(e['duration_s']*1000)
        for o in own:
            ret=float(o['post_returned']);oid=o['order_id']
            # Fixed periodic polling only: first publication in which every required read STARTED after POST completed.
            sources=['open_orders','trades','capital','positions']
            for n,s in snaps:
                good=s['last_good']
                if all(k in good and good[k]['started_monotonic']>=ret for k in sources):
                    metrics['post_return_to_all_four_sources_refreshed_ms'].append((s['published_monotonic']-ret)*1000)
                    break
            if o['placement']=='ACK' and o['detail_reads']=='0':
                checks['ack_never_detail']+=1
                # Before and after query boundaries, demonstrating a complete poll can miss a short-lived accepted order.
                before=[(n,e) for n,e in ev if e['kind']=='sync_http' and e['endpoint']=='/data/orders' and e['completed_monotonic']<float(o['post_started'])]
                after=[(n,e) for n,e in ev if e['kind']=='sync_http' and e['endpoint']=='/data/orders' and e['started_monotonic']>float(o['post_started'])]
                c=[(n,r['data']['payload']) for n,r in journal if r['kind']=='sm_cancel_ack' and oid in r['data']['payload'].get('ids',[]) and isinstance(r['data']['payload'].get('delete_ack'),(int,float))]
                c.sort(key=lambda x:x[1]['delete_ack'])
                row={k:o[k] for k in ['market','window_id','phase','order_id','journal_decision_line','journal_window_line','post_start_to_first_cancel_ack_ms']}
                row.update(post_start=local(float(o['post_started'])+offset),post_return=local(ret+offset),cancel_ack=local(c[0][1]['delete_ack']+offset) if c else None,cancel_journal_line=c[0][0] if c else None,poll_before_line=before[-1][0] if before else None,poll_before_completed=local(before[-1][1]['completed_wall']) if before else None,poll_after_line=after[0][0] if after else None,poll_after_started=local(after[0][1]['started_wall']) if after else None)
                examples.append(row)
        # Current-market token positions: first positive REST position versus first MATCHED/CONFIRMED read.
        for token in json.loads((p/'manifest.json').read_text())['market']['tokens']:
            trades=[(e['applied_monotonic'],e['status'],e['order_id']) for _,e in ev if e['kind']=='sync_trade_observed' and any(o['order_id']==e['order_id'] and any(r['data']['payload'].get('token_id')==token and r['data']['payload'].get('maker_order_id')==e['order_id'] for _,r in journal if r['kind']=='sm_trade') for o in own)]
            matched=min((t for t,status,_ in trades if status=='MATCHED'),default=None);confirmed=min((t for t,status,_ in trades if status=='CONFIRMED'),default=None)
            posread=[];seen=set()
            for n,s in snaps:
                r=s['last_good'].get('positions')
                if not r or r['read_id'] in seen:continue
                seen.add(r['read_id'])
                if any(row['token_id']==token and row['size']>0 for row in r['data']):posread.append((r['completed_monotonic'],n))
            if matched is not None:
                pt=posread[0][0] if posread else None;row={'market':se['market'],'token_id':token,'first_matched':local(matched+offset),'first_position':local(pt+offset) if pt else None,'position_minus_matched_ms':(pt-matched)*1000 if pt else None,'position_minus_confirmed_ms':(pt-confirmed)*1000 if pt and confirmed else None,'snapshot_line':posread[0][1] if posread else None};positions.append(row)
    out={'checks':dict(checks),'metrics':{k:quant(v) for k,v in metrics.items()},'rounds_by_detail':{k:quant(v) for k,v in rounds.items()},'missing_orders':examples,'first_position_visibility':positions,'limitations':['Position timings are first positive token-level REST observation, not proof of quantity reconciliation, close readiness, or first exchange execution.','Four-source refresh alone does not include signed order identity, detail completion or consistency checks.']}
    (OUT/'edges.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(out,ensure_ascii=False,indent=2))

if __name__=='__main__':main()

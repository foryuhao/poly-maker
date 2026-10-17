"""Read-only audit of completed companion sessions; no API calls or strategy imports."""
import bisect
import collections
import csv
import hashlib
import json
import math
import pathlib
import statistics
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parents[4]
OUT = pathlib.Path(__file__).resolve().parent
TZ = timezone(timedelta(hours=8))

def local(t):
    return datetime.fromtimestamp(t, TZ).isoformat(timespec='milliseconds')

def quant(v):
    v = sorted(v)
    return {'n':len(v), **({'p50':statistics.median(v), 'p95':v[max(0, math.ceil(len(v)*.95)-1)], 'p99':v[max(0, math.ceil(len(v)*.99)-1)], 'max':v[-1]} if v else {})}

def rows(p):
    with p.open() as f:
        for n,l in enumerate(f,1):
            if l.strip():
                yield n,json.loads(l)

def endpoint(e):
    return '/data/order/:id' if e.startswith('/data/order/') else e

def savecsv(name, data):
    if not data:return
    fields = list(dict.fromkeys(k for r in data for k in r))
    with (OUT/name).open('w') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(data)


def main():
    selected=[]
    for p in (ROOT/'research_outputs/periodic_sync').glob('*/manifest.json'):
        m=json.loads(p.read_text())
        if (p.parent/'summary.json').exists():selected.append((m['started_wall'],p.parent,m))
    selected=sorted(selected)[-12:]
    assert len(selected)==12
    provenance=[]
    counter=collections.Counter();samples=collections.defaultdict(list)
    sessions=[];orders=[];fills=[];issues=[];detail_differences=[];postrefs=[]
    strategies=collections.defaultdict(lambda:collections.defaultdict(list));strategycounts=collections.defaultdict(collections.Counter)
    source_hashes=set();first_wall=selected[0][0]
    for _,p,m in selected:
        sid=m['session_id'];market=m['market'];label=local(market['start'])[11:16]
        su=json.loads((p/'summary.json').read_text())
        source_hashes.add(tuple(sorted(m['source_sha256'].items())))
        jpath=pathlib.Path(m['strategy_journal'])
        for f in [p/'manifest.json',p/'summary.json',p/'events.jsonl',p/'snapshots.jsonl',p/'strategy_refs.jsonl',jpath]:
            provenance.append({'path':str(f.resolve()),'bytes':f.stat().st_size,'sha256':hashlib.sha256(f.read_bytes()).hexdigest()})
        events=list(rows(p/'events.jsonl'));snaps=list(rows(p/'snapshots.jsonl'));refs=list(rows(p/'strategy_refs.jsonl'));journal=list(rows(jpath))
        versions={s['version']:(n,s) for n,s in snaps};pub=[s['published_monotonic'] for _,s in snaps]
        readsets=collections.defaultdict(dict)
        for n,s in snaps:
            if s['session_id']!=sid or (s['market'] and s['market']!=market):issues.append([label,'wrong_context',n])
            if s['version'] != n:issues.append([label,'version_gap',n])
            if s.get('trading_authorized') is not False:issues.append([label,'authority',n])
            for name,r in s['last_good'].items():
                readsets[name].setdefault(r['read_id'],(n,r,s['published_monotonic']))
                if not r['complete'] or r['completed_monotonic']>s['published_monotonic']+1e-6:issues.append([label,'invalid_last_good',n,name])
        assert snaps[-1][1]['status']=='STOPPED'
        counter['snapshots']+=len(snaps);counter['references']+=len(refs)
        for k in ['dropped_events','dropped_references','reference_market_or_history_gaps']:
            counter[k]+=su[k]
        counter['summary_recorded']+=su['status']=='RECORDED'
        counter['io_drained']+=su['io_drained'] is True
        for n,r in refs:
            for milestone,link in r['snapshot_links'].items():
                counter['reference_links']+=1
                if not link['matching_main_market']:counter['nonmatching_links']+=1
                v=link['snapshot_version'];t=link['event_monotonic'];i=bisect.bisect_right(pub,t)-1
                if v is not None:
                    s=versions[v][1]
                    if s['published_monotonic']>t or (i>=0 and snaps[i][1]['version']!=v):issues.append([label,'not_asof_latest',n,milestone])
                    for name,source in link['sources'].items():
                        if source['read_id']!=s['last_good'][name]['read_id'] or not source['completed_before_event']:issues.append([label,'bad_source_link',n,milestone,name])
                if milestone=='post_started':
                    item={'market':label,'window_id':r['fields'].get('window_id'),'phase':r['fields'].get('phase'),'ref_line':n,'snapshot_version':v,'matching':link['matching_main_market'],'snapshot_age_ms':link['snapshot_age_ms']}
                    for source in ['open_orders','trades','capital','positions']:
                        age=link['sources'].get(source,{}).get('age_ms');item[source+'_age_ms']=age
                        if age is not None:samples['post_age:'+source].append(age)
                    postrefs.append(item)
        lane_starts=collections.defaultdict(list);overruns=[];httpcount=0
        observed=collections.defaultdict(list)
        for n,e in events:
            kind=e['kind']
            if kind=='sync_http':
                ep=endpoint(e['endpoint']);d=(e['completed_monotonic']-e['started_monotonic'])*1000
                samples['observer_http:'+ep].append(d);httpcount+=1
                counter['observer_http:'+str(e.get('http_status'))+':'+str(e.get('error_type'))]+=1
                counter['observer_method:'+e['method']]+=1
            elif kind=='sync_round' and not e.get('shutdown'):
                lane=e['lane'];lane_starts[lane].append(e['started_monotonic']);samples['round:'+lane].append(e['duration_s']*1000)
                counter['round:'+lane]+=1;counter['round_complete:'+lane]+=e['complete'];counter['overrun:'+lane]+=e['overrun']
                if e['overrun']:overruns.append({'line':n,'start':local(e['started_wall']),'lane':lane,'ms':e['duration_s']*1000})
            elif kind=='sync_detail_coverage':samples['detail_backlog'].append(e['unread_pending'] if isinstance(e['unread_pending'], int) else len(e['unread_pending']))
            elif kind=='sync_consistency':
                counter['consistency']+=1;counter['consistency_agree']+=e['observations_agree']
                if not e['observations_agree']:detail_differences.append({'market':label,'event_line':n,**e})
            elif kind=='sync_trade_observed':
                key=(e['trade_id'],e['order_id'],e['status']);observed[key].append((n,e))
                if e['status']=='CONFIRMED':samples['confirmed_read_to_apply_ms'].append((e['applied_monotonic']-e['observed_monotonic'])*1000)
        for lane,ts in lane_starts.items():
            samples['interval:'+lane].extend((b-a)*1000 for a,b in zip(ts,ts[1:]))
        for source in ['open_orders','trades','capital','positions']:
            rs=sorted((r for _,r,_ in readsets[source].values()),key=lambda r:r['started_monotonic'])
            samples['source_start_interval:'+source].extend((b['started_monotonic']-a['started_monotonic'])*1000 for a,b in zip(rs,rs[1:]))
        # Ages sampled on a fixed 100ms grid, rather than biased by publication frequency.
        valid=[s for _,s in snaps if s['market'] and all(k in s['last_good'] for k in ['open_orders','trades','capital','positions'])]
        if valid:
            t=valid[0]['published_monotonic'];end=snaps[-1][1]['published_monotonic']
            while t<end:
                s=snaps[bisect.bisect_right(pub,t)-1][1]
                for source in ['open_orders','trades','capital','positions']:
                    if source in s['last_good']:samples['timegrid_age:'+source].append((t-s['last_good'][source]['started_monotonic'])*1000)
                t+=.1
        jr={};windows={};decisions={};close={};mainfills={};first_cancel={};networkpauses=collections.Counter()
        for n,r in journal:
            q=r['data']['payload'];kind=r['kind']
            if kind=='sm_run_result':jr=q
            elif kind=='sm_window_result':windows[q['window_id']]=(n,q)
            elif kind=='sm_obi_final_decision':
                intent=q.get('intent',{});decisions[intent.get('window_id')]=(n,intent)
            elif kind=='sm_obi_close_requested':close.setdefault(q['window_id'],(n,q))
            elif kind=='sm_trade':
                key=(q['trade_id'],q['maker_order_id'],q['status'])
                ref=next(((rn,rr) for rn,rr in refs if rr['kind']=='sm_trade' and all(rr['fields'].get(k)==q.get(k) for k in ['trade_id','maker_order_id','status'])),None)
                if ref:mainfills.setdefault(key,(n,q,ref[1]['recorded_monotonic'],ref[0]))
            elif kind=='sm_cancel_ack':
                if isinstance(q.get('delete_ack'),(int,float)):
                    for oid in q.get('ids',[]):first_cancel.setdefault(oid,(q['delete_ack'],n))
            if kind=='sm_network_transition':strategycounts['attached']['transition:'+str(q.get('state'))]+=1
            if kind=='sm_network_request':
                method=q['method'];ep=endpoint(q['endpoint']);strategycounts['attached'][method+':'+ep+':'+q['outcome']]+=1
                if q['outcome']=='SUCCESS':strategies['attached'][method+':'+ep].append(q['duration_ms'])
            if kind.startswith('sm_network') and kind!='sm_network_request':networkpauses[kind+':'+str(q.get('state',q.get('reason','')))]+=1
        assert jr and jr['session_id']==sid and jr['market_id']==market['condition_id']
        posted=0;acks=0;miss=[];ownids=set()
        for wid,(wn,w) in windows.items():
            dn,intent=decisions.get(wid,(None,{}));phase=intent.get('phase','UNKNOWN')
            for placement in w.get('placement_results',[]):
                if not placement.get('post_started'):continue
                posted+=1;oid=placement.get('order_id') or placement.get('expected_order_id');ownids.add(oid)
                isack=placement['outcome']=='ACK';acks+=isack
                rdetail=readsets.get('order:'+str(oid),{});detailreads=sorted(rdetail.values(),key=lambda x:x[2]);term=[]
                for sn,r,pt in detailreads:
                    data=r.get('data') or {};status=data.get('status','').upper()
                    if status in ['CANCELED','CANCELLED','EXPIRED'] or (status=='MATCHED' and float(data.get('remaining_size',1))==0):term.append((sn,r,pt))
                postreturn=intent.get('post_returned');cancel=first_cancel.get(oid)
                item={'market':label,'session_id':sid,'window_id':wid,'phase':phase,'order_id':oid,'placement':placement['outcome'],'post_started':intent.get('post_started'),'post_returned':postreturn,'journal_decision_line':dn,'journal_window_line':wn,'detail_reads':len(detailreads),'terminal_seen':bool(term),'detail_first_snapshot_line':detailreads[0][0] if detailreads else None,'terminal_first_snapshot_line':term[0][0] if term else None,'known_ids':len(w['known_order_ids'])}
                if cancel and intent.get('post_started') is not None:item['post_start_to_first_cancel_ack_ms']=(cancel[0]-intent['post_started'])*1000
                item['open_ever_seen']=any(any(row['order_id']==oid for row in read['data']) for _,read,_ in readsets['open_orders'].values())
                item['trade_ever_seen']=any(key[1]==oid for key in observed)
                if postreturn and detailreads:item['post_return_to_first_detail_ms']=(detailreads[0][2]-postreturn)*1000;samples['post_return_to_first_detail_ms'].append(item['post_return_to_first_detail_ms'])
                if cancel and term:
                    ts=[x for x in term if x[1]['started_monotonic']>=cancel[0]]
                    if ts:item['cancel_ack_to_post_fence_terminal_ms']=(ts[0][2]-cancel[0])*1000;samples['cancel_ack_to_post_fence_terminal_ms'].append(item['cancel_ack_to_post_fence_terminal_ms'])
                if wid in close:
                    q=close[wid][1];start=q.get('requested_monotonic');end=w.get('close_monotonic')
                    if start is not None and end is not None:item['close_ms']=(end-start)*1000;samples['strategy_close:'+phase].append(item['close_ms'])
                orders.append(item)
                if isack and not detailreads:miss.append(oid)
        for (tid,oid,status),(jn,q,t,rn) in mainfills.items():
            if status!='CONFIRMED':continue
            ob=observed.get((tid,oid,status),[])
            row={'market':label,'session_id':sid,'trade_id':tid,'order_id':oid,'shares':q['size'],'main_recorded_monotonic':t,'main_journal_line':jn,'main_reference_line':rn,'observer_seen':bool(ob)}
            if ob:
                en,e=min(ob,key=lambda x:x[1]['applied_monotonic']);row.update({'observer_event_line':en,'observer_applied_monotonic':e['applied_monotonic'],'main_minus_observer_ms':(t-e['applied_monotonic'])*1000})
                samples['main_minus_observer_confirmed_ms'].append(row['main_minus_observer_ms'])
            published=[]
            for sn,read,pt in readsets['trades'].values():
                for trade in read['data']:
                    if trade['trade_id']==tid and trade['status']=='CONFIRMED' and any(leg['order_id']==oid for leg in trade['maker_legs']):published.append((pt,sn,read))
            if published:
                pt,sn,read=min(published,key=lambda z:z[0]);row.update({'snapshot_published_monotonic':pt,'snapshot_line':sn,'main_minus_snapshot_ms':(t-pt)*1000})
                samples['main_minus_snapshot_confirmed_ms'].append((t-pt)*1000)
                samples['confirmed_read_to_snapshot_publish_ms'].append((pt-read['completed_monotonic'])*1000)
            fills.append(row)
        se={'market':label,'slug':market['slug'],'session_id':sid,'start':local(m['started_wall']),'duration_s':su['actual_duration_s'],'http_count':httpcount,'snapshots':len(snaps),'references':len(refs),'status':su['status'],'dropped':su['dropped_events']+su['dropped_references'],'posted':posted,'ack':acks,'ack_missing_detail':len(miss),'ack_missing_detail_ids':miss,'confirmed_records':jr['confirmed_fills'],'confirmed_yes':jr['confirmed_yes'],'confirmed_no':jr['confirmed_no'],'residual':jr['residual_shares'],'merged_pairs':jr['merged_pairs'],'merge_pnl':jr['realized_merge_pnl'],'execution_status':jr['execution_status'],'barrier':jr['final_close_barrier'],'run_result_line':next(n for n,r in journal if r['kind']=='sm_run_result'),'whole_process_cpu_s':su['cpu_seconds_process_delta'],'whole_process_rss_mib':su['process_peak_rss_kib']/1024,'overruns':overruns,'network_events':dict(networkpauses),'journal':str(jpath),'observer_dir':str(p.resolve()),'bytes':sum(f.stat().st_size for f in p.glob('*'))}
        sessions.append(se)
    # Closest prior completed sessions are the comparison cohort; no current-market lookups.
    attached_sids={m['session_id'] for _,_,m in selected};prior=[];bad_prior=[]
    for p in (ROOT/'journal').glob('rdp_sm_*.jsonl'):
        if any(p.stem.endswith(sid) for sid in attached_sids):continue
        result=None
        try:
            for n,r in rows(p):
                if r['kind']=='sm_run_result':result=r['data']['payload']
        except json.JSONDecodeError:
            bad_prior.append(str(p));continue
        if result and result.get('execution_status')=='COMPLETED' and result.get('stop_time',0)<first_wall:
            prior.append((result['stop_time'],p,result))
    baselines=[]
    for _,p,result in sorted(prior)[-12:]:
        provenance.append({'path':str(p.resolve()),'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
        baselines.append({'session_id':result['session_id'],'start':local(result['start_time']),'stop':local(result['stop_time']),'journal':str(p.resolve()),'windows':result['total_windows']})
        for n,r in rows(p):
            q=r['data']['payload']
            if r['kind']=='sm_network_transition':strategycounts['prior12']['transition:'+str(q.get('state'))]+=1
            if r['kind']!='sm_network_request':continue
            method=q['method'];ep=endpoint(q['endpoint']);strategycounts['prior12'][method+':'+ep+':'+q['outcome']]+=1
            if q['outcome']=='SUCCESS':strategies['prior12'][method+':'+ep].append(q['duration_ms'])
    result={'scope':'12 completed attached observer sessions; single market per invocation; read-only log audit','market_range':[sessions[0]['market'],sessions[-1]['market']],'quantiles':'median; p95/p99 nearest rank ceil(n*p)-1; milliseconds unless stated','freshness_definition':'as-of time minus source request START, not snapshot publication; timegrid 100ms after all four main sources first available','counter':dict(counter),'metrics':{k:quant(v) for k,v in samples.items()},'sessions':sessions,'source_context_issues':issues,'consistency_differences':detail_differences,'source_hash_sets':len(source_hashes),'strategy_latencies':{cohort:{ep:quant(vals) for ep,vals in eps.items()} for cohort,eps in strategies.items()},'strategy_request_counts':{k:dict(v) for k,v in strategycounts.items()},'baseline':baselines,'excluded_malformed_prior_files':bad_prior,'provenance':provenance}
    (OUT/'evidence.json').write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n')
    savecsv('sessions.csv',[{k:v for k,v in s.items() if k not in ['overruns','network_events','ack_missing_detail_ids']} for s in sessions])
    savecsv('orders.csv',orders);savecsv('fills.csv',fills);savecsv('post_references.csv',postrefs)
    print(json.dumps({k:result[k] for k in ['counter','metrics','source_context_issues','consistency_differences']},ensure_ascii=False,indent=2))
    print('SESSIONS',json.dumps([{k:v for k,v in s.items() if k in ['market','posted','ack','ack_missing_detail','confirmed_records','residual','merge_pnl','execution_status','overruns']} for s in sessions],ensure_ascii=False))
    print('BASELINE',json.dumps(baselines,ensure_ascii=False))
    print('LATENCIES',json.dumps(result['strategy_latencies'],ensure_ascii=False))

if __name__=='__main__':main()

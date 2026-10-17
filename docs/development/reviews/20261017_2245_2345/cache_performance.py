"""Measure cache cadence, source age and real shared GET usage from frozen logs."""
import bisect
import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path

OUT=Path(__file__).resolve().parent


def quant(values):
    v=sorted(values)
    return dict(n=len(v),p50=statistics.median(v),p95=v[math.ceil(len(v)*.95)-1],max=v[-1]) if v else dict(n=0)


def main():
    data=json.loads((OUT/'analysis.json').read_text())
    values=defaultdict(list)
    counter=Counter()
    permarket=[]
    for m in data['markets']:
        root=Path('research_outputs/periodic_sync')/m['session_id']
        events=[json.loads(l) for l in (root/'events.jsonl').open()]
        readids={e['read_id'] for e in events if e['kind']=='sync_http' and e['read_id']!='bootstrap'}
        sourcehttp=Counter(e['endpoint'] for e in events if e['kind']=='sync_http')
        lanes=defaultdict(list)
        for e in events:
            if e['kind']=='sync_round' and not e.get('shutdown'):
                lane=e['lane'];lanes[lane].append(e['started_monotonic'])
                values['round_ms:'+lane].append(e['duration_s']*1000)
                counter['rounds:'+lane]+=1
                counter['incomplete:'+lane]+=not e['complete']
                counter['overruns:'+lane]+=e['overrun']
        for lane,ts in lanes.items():values['interval_ms:'+lane].extend((b-a)*1000 for a,b in zip(ts,ts[1:]))
        # Fixed 100 ms grid avoids overweighting frames published by a faster lane.
        snaps=[json.loads(l) for l in (root/'snapshots.jsonl').open()]
        pub=[s['published_monotonic'] for s in snaps]
        names=['open_orders','trades','capital','positions']
        valid=[s for s in snaps if s['market'] and all(n in s['last_good'] for n in names)]
        if valid:
            t=valid[0]['published_monotonic']
            while t<pub[-1]:
                snap=snaps[bisect.bisect_right(pub,t)-1]
                for name in names:
                    values['timegrid_age_ms:'+name].append((t-snap['last_good'][name]['started_monotonic'])*1000)
                t+=.1
        mainhttp=Counter();native=Counter();shared=Counter()
        for l in Path(m['source']).open():
            r=json.loads(l);q=r['data']['payload']
            if r['kind']=='sm_network_request' and q['method']=='GET':
                mainhttp[q['endpoint']]+=1
                if q['operation_id'] in readids:shared[q['endpoint']]+=1
                else:native[q['endpoint']]+=1
            if r['kind']=='sm_sync_cache_read':
                for n,s in q['sources'].items():
                    values['consumed_age_ms:'+q['purpose']+':'+n].append((r['data']['occurred_monotonic']-s['started_monotonic'])*1000)
        counter['main_gets']+=sum(mainhttp.values())
        counter['main_gets_shared']+=sum(shared.values())
        counter['main_gets_native']+=sum(native.values())
        counter['collector_gets']+=sum(sourcehttp.values())
        for ep,n in sourcehttp.items():counter['collector:'+('/data/order/:id' if ep.startswith('/data/order/') else ep)]+=n
        for ep,n in native.items():counter['native:'+('/data/order/:id' if ep.startswith('/data/order/') else ep)]+=n
        permarket.append(dict(market=m['market'],main_gets=sum(mainhttp.values()),shared_gets=sum(shared.values()),native_gets=sum(native.values())))
    old=json.loads((OUT.parent/'20261017_sync_attached_12/evidence.json').read_text())
    previous_main=sum(n for k,n in old['strategy_request_counts']['attached'].items() if k.startswith('GET:'))
    previous_observer=old['counter']['observer_method:GET']
    current_union=counter['collector_gets']+counter['main_gets_native']
    result=dict(scope='Frozen 12 markets, 2026-10-17 22:45–23:45 Asia/Shanghai',
        counters=dict(counter),metrics={k:quant(v) for k,v in values.items()},per_market=permarket,
        comparison=dict(previous_scope='2026-10-17 20:20–21:20, observer only',
            previous_main_timed_gets=previous_main,previous_collector_gets=previous_observer,
            previous_union_covered_gets=previous_main+previous_observer,
            current_union_covered_gets=current_union,
            covered_get_reduction_fraction=1-current_union/(previous_main+previous_observer),
            caveat='Descriptive different market cohorts, not A/B. Main timing excludes some startup, settlement and positions calls. Shared collector requests deduplicated by read_id/operation_id.'),
        source_files=['analysis.json','evidence.json','../20261017_sync_attached_12/evidence.json',
            'each selected session events.jsonl, snapshots.jsonl and strategy journal'])
    (OUT/'cache_performance.json').write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n')
    print(json.dumps(result,indent=2,ensure_ascii=False))


if __name__=='__main__':main()

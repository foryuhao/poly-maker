"""Read-only wall-clock attribution for 71 REBALANCE windows in 36 markets."""
import csv
import hashlib
import json
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

OUT=Path(__file__).resolve().parent
TZ=timezone(timedelta(hours=8))
CATEGORIES=('poll_sleep','post_return','get_current_order','get_old_order','get_trades','get_other','delete_only','local_or_unobserved')


def stamp(t):return datetime.fromtimestamp(t,TZ).strftime('%H:%M:%S.%f')[:-3]


def csv_write(name,rows):
    if not rows:return
    with (OUT/name).open('w') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)


def stats(values):
    values=sorted(values)
    return dict(n=len(values),median=statistics.median(values),p95=values[int((len(values)-1)*.95)],max=max(values),sum=sum(values)) if values else dict(n=0)


def partition(lo,hi,intervals):
    clipped=[(max(lo,a),min(hi,b),c) for a,b,c in intervals if b>lo and a<hi]
    points=sorted({lo,hi,*[v for a,b,_ in clipped for v in (a,b)]})
    out={c:0.0 for c in CATEGORIES}
    for a,b in zip(points,points[1:]):
        midpoint=(a+b)/2
        active={c for aa,bb,c in clipped if aa<=midpoint<bb}
        chosen=next((c for c in CATEGORIES if c in active),'local_or_unobserved')
        out[chosen]+=b-a
    assert abs(sum(out.values())-(hi-lo))<1e-6
    return out


def analyze():
    windows,markets,provenance=[],[],[]
    for folder,regime in [('20261017_1300_1500','before'),('20261017_1540_1640','after')]:
        data=json.loads((OUT.parent/folder/'analysis.json').read_text())
        markets.extend([dict(regime=regime,**m) for m in data['markets']])
        windows.extend([dict(regime=regime,**w) for w in data['windows']])
        provenance.extend(data['provenance'])
    assert len(markets)==36 and len({m['session_id'] for m in markets})==36
    assert all(hashlib.sha256(Path(p['path']).read_bytes()).hexdigest()==p['sha256'] for p in provenance)
    repairs=[w for w in windows if w['phase']=='REBALANCE' and w['submitted']]
    results,confirmation_evidence,request_rows,round_rows,ack_rows=[],[],[],[],[]
    for m in markets:
        selected=[w for w in repairs if w['session_id']==m['session_id']]
        if not selected:continue
        rows=[(i,json.loads(s)) for i,s in enumerate(Path(m['source']).read_text().splitlines(),1)]
        by=defaultdict(list)
        for line,r in rows:by[r['kind']].append((line,r['data'].get('occurred_monotonic'),r['ts'],r['data']['payload']))
        net=[dict(line=line,**q) for line,_,_,q in by['sm_network_request']]
        offset=statistics.median(q['completed_wall']-q['completed_monotonic'] for q in net)
        intents={q['intent']['window_id']:q['intent'] for _,_,_,q in by['sm_obi_final_decision']}
        raw_results={q['window_id']:q for _,_,_,q in by['sm_window_result']}
        raw_close={q['window_id']:q for _,_,_,q in by['sm_obi_close_requested']}
        for w in selected:
            wid=w['session_id']+':'+w['window'];intent=intents[wid];res=raw_results[wid]
            lo=raw_close[wid]['requested_monotonic'];hi=res['end_time']-offset
            post=intent['post_started'];returned=intent['post_returned'];oid=w['order_id']
            batch=[q for q in net if q.get('batch_id')==wid and q['started_monotonic']<hi+.01 and q['completed_monotonic']>post-.01]
            gets=sorted([q for q in batch if q['method']=='GET' and q['endpoint']!='/time'],key=lambda q:q['started_monotonic'])
            deletes=[q for q in batch if q['method']=='DELETE']
            close_gets=[q for q in gets if q['completed_monotonic']>lo]
            barriers=sorted([(line,mono,wall,q) for line,mono,wall,q in by['sm_close_barrier'] if lo<=mono<=hi+.005],key=lambda z:z[1])
            intervals=[(post,returned,'post_return')]
            sleeps=[]
            for line,mono,wall,q in barriers:
                reason=q.get('reason') or ''
                parts=reason.split(';')
                pure_pending=q['result']=='BLOCKED' and all(p.endswith(':TRADE_CONFIRMATION_UNRESOLVED') for p in parts)
                next_get=next((q1 for q1 in gets if q1['started_monotonic']>mono),None)
                if pure_pending and next_get and next_get['started_monotonic']-mono>=1.9:
                    # Existing _wait_for_confirmation sleeps 2 s, then replays the read-only full close.
                    sleep=min(2.0,next_get['started_monotonic']-mono)
                    sleeps.append((mono,mono+sleep))
                    intervals.append((mono,mono+sleep,'poll_sleep'))
                round_rows.append(dict(market=m['market'],regime=m['regime'],window=w['window'],time=stamp(wall),
                                       epoch=wall,result=q['result'],reason=reason,pure_pending=pure_pending,source=m['source'],source_line=line))
            for q in batch:
                a,b=q['started_monotonic'],q['completed_monotonic'];method=q['method'];endpoint=q['endpoint']
                if method=='GET' and endpoint=='/time':continue
                if method=='GET':
                    category='get_current_order' if endpoint=='/data/order/'+oid else ('get_old_order' if endpoint.startswith('/data/order/') else ('get_trades' if endpoint=='/data/trades' else 'get_other'))
                elif method=='DELETE':category='delete_only'
                elif method=='POST':category='post_return'
                else:continue
                intervals.append((a,b,category))
                request_rows.append(dict(market=m['market'],regime=m['regime'],window=w['window'],category=category,
                                         method=method,endpoint=endpoint,start=stamp(q['started_wall']),end=stamp(q['completed_wall']),
                                         duration_s=b-a,after_close=b>lo,outcome=q['outcome'],source=m['source'],source_line=q['line']))
            attributed=partition(lo,hi,intervals)
            reconciled=sorted([(line,mono,wall,q) for line,mono,wall,q in by['sm_official_reconciliation'] if post<=mono<=hi],key=lambda z:z[1])
            current_rows=[(line,mono,wall,q) for line,mono,wall,q in reconciled if q['order_id']==oid]
            terminal=None
            for line,mono,wall,q in current_rows:
                row=q['order']
                identity=(row.get('asset_id')==res['requested_quotes'][0]['token_id'] and row.get('market')==res['market_id'] and row.get('side')=='BUY' and abs(float(row.get('original_size',0))-w['size'])<1e-9 and abs(float(row.get('price',0))-w['price'])<1e-9)
                status=row.get('status') in {'CANCELED','CANCELLED'} or row.get('status')=='MATCHED' and abs(float(row.get('original_size',0))-float(row.get('size_matched',0)))<=.1
                if identity and status:terminal=(line,mono,wall);break
            cancels=[]
            for line,mono,wall,q in by['sm_cancel_ack']:
                if not post<=mono<=hi+.01:continue
                if q.get('ids')!=[oid] or 'result' not in q:continue
                sdk=q.get('delete_sdk_started');end=q.get('delete_ack')
                if sdk is None or end is None:continue
                cancels.append((line,sdk,end,q['result']))
                ack_rows.append(dict(market=m['market'],window=w['window'],started=stamp(sdk+offset),ack=stamp(end+offset),
                                     positive=oid in q['result'].get('canceled',[]),after_terminal=bool(terminal and sdk>=max(returned,terminal[1])),source=m['source'],source_line=line))
            positive=[end for _,_,end,q in cancels if oid in q.get('canceled',[])]
            after_positive=sum(sdk>=max(returned,min(positive)) for _,sdk,_,_ in cancels) if positive else 0
            after_terminal=sum(sdk>=max(returned,terminal[1]) for _,sdk,_,_ in cancels) if terminal else 0
            # Conservative availability: CONFIRMED already appears in a logged full trade page,
            # even though reconciliation of another order has not applied it to this order yet.
            local_confirmed=[(line,mono,wall,q) for line,mono,wall,q in by['sm_trade'] if q['maker_order_id']==oid and q['status']=='CONFIRMED']
            for line,mono,wall,fill in local_confirmed:
                earlier=[]
                for rline,rmono,rwall,q in reconciled:
                    for trade in q['trades']:
                        if trade.get('id')==fill['trade_id'] and trade.get('status')=='CONFIRMED' and any(leg.get('order_id')==oid for leg in trade.get('maker_orders',[])):
                            earlier.append((rline,rmono,rwall,q['order_id'],trade.get('last_update')))
                first=min(earlier,key=lambda z:z[1]) if earlier else None
                confirmation_evidence.append(dict(market=m['market'],regime=m['regime'],window=w['window'],trade_id=fill['trade_id'],
                    local_confirmed=stamp(wall),local_confirmed_epoch=wall,local_line=line,
                    earliest_logged_page=stamp(first[2]) if first else None,page_line=first[0] if first else None,
                    page_owner_is_current=first[3]==oid if first else None,official_last_update=first[4] if first else None,
                    page_to_local_lag_s=max(0,mono-first[1]) if first else None,source=m['source']))
            previous=max((ww for ww in windows if ww['session_id']==w['session_id'] and ww['post_epoch']<w['post_epoch']),key=lambda ww:ww['post_epoch'],default=None)
            previous_end=previous['post_epoch']+previous['post_to_safe_s'] if previous and previous['post_to_safe_s'] is not None else None
            overlap=0;max_overlap=0;points=[]
            for q in close_gets:points.extend([(q['started_monotonic'],1),(q['completed_monotonic'],-1)])
            for _,delta in sorted(points):overlap+=delta;max_overlap=max(max_overlap,overlap)
            row=dict(market=m['market'],regime=m['regime'],window=w['window'],outcome=w['outcome_final'],filled=w['fill_quantity']>0,
                     side=w['side'],size=w['size'],price=w['price'],close_reason=w['close_reason'],post=w['post_time'],
                     close=w['close_time'],safe_end=w['final_close_time'],close_s=hi-lo,lease_s=lo-post,
                     known_orders=len(res['known_order_ids']),get_order_requests=sum(q['endpoint'].startswith('/data/order/') for q in close_gets),
                     get_current_order_requests=sum(q['endpoint']=='/data/order/'+oid for q in close_gets),
                     get_trade_requests=sum(q['endpoint']=='/data/trades' for q in close_gets),delete_requests=len(deletes),
                     max_nonwarmer_get_overlap=max_overlap,barrier_rounds=len(barriers),blocked_rounds=sum(q['result']=='BLOCKED' for _,_,_,q in barriers),
                     pure_confirmation_rounds=sum(bool(q.get('reason')) and all(p.endswith(':TRADE_CONFIRMATION_UNRESOLVED') for p in q['reason'].split(';')) for _,_,_,q in barriers),
                     terminal_seen=stamp(terminal[2]) if terminal else None,first_delete_lag_s=min((q['started_monotonic']-lo for q in deletes if q['started_monotonic']>=lo),default=None),
                     positive_cancel_to_end_s=hi-max(returned,min(positive)) if positive else None,
                     watcher_cancel_after_positive=after_positive,watcher_cancel_after_terminal=after_terminal,
                     previous_window_end_to_post_s=w['post_epoch']-previous_end if previous_end else None,
                     source=m['source'],source_line=w['source_line'],**attributed)
            results.append(row)
    def summarize(rs):
        totals={c:sum(r[c] for r in rs) for c in CATEGORIES}
        return dict(n=len(rs),close=stats([r['close_s'] for r in rs]),lease=stats([r['lease_s'] for r in rs]),
                    wall_attribution_s=totals,wall_attribution_share={c:v/sum(totals.values()) for c,v in totals.items()},
                    requests=dict(order=sum(r['get_order_requests'] for r in rs),current_order=sum(r['get_current_order_requests'] for r in rs),
                                  trades=sum(r['get_trade_requests'] for r in rs),delete=sum(r['delete_requests'] for r in rs)),
                    max_get_parallelism=max(r['max_nonwarmer_get_overlap'] for r in rs),
                    watcher_cancel_after_positive=sum(r['watcher_cancel_after_positive'] for r in rs),
                    watcher_cancel_after_terminal=sum(r['watcher_cancel_after_terminal'] for r in rs))
    delete_durations=[r['duration_s'] for r in request_rows if r['method']=='DELETE']
    budget=[dict(known_orders=n,current_gets=4*n,shared_snapshot_gets=2*n+2,
                 current_serial_s=4*n*.25,shared_serial_s=(2*n+2)*.25,
                 bounded_4_read_phases_s=(2*((n+3)//4)+2)*.25,
                 assumption='Single-page trade collections, 250ms/GET; request budget only, not replayed profit or measured implementation.') for n in (5,10,12,15)]
    summary=dict(markets=36,rebalance_windows=len(results),rebalance_markets=len({r['market'] for r in results}),
                 groups={label:summarize(rs) for label,rs in [('all',results),('before',[r for r in results if r['regime']=='before']),
                     ('after',[r for r in results if r['regime']=='after']),('unfilled',[r for r in results if not r['filled']]),
                     ('filled',[r for r in results if r['filled']]),('ack_unfilled',[r for r in results if not r['filled'] and r['outcome']=='ACK'])]},
                 unfilled_by_known={label:stats([r['close_s'] for r in results if not r['filled'] and r['outcome']=='ACK' and lo<=r['known_orders']<=hi])
                      for label,lo,hi in [('1-4',1,4),('5-8',5,8),('9+',9,999)]},
                 confirmation_page_lag=stats([r['page_to_local_lag_s'] for r in confirmation_evidence if r['page_to_local_lag_s'] is not None]),
                 confirmation_page_lag_positive=[r for r in confirmation_evidence if (r['page_to_local_lag_s'] or 0)>.1],
                 first_delete_start_lag=stats([r['first_delete_lag_s'] for r in results if r['first_delete_lag_s'] is not None]),
                 delete_http_duration=stats(delete_durations),request_budget=budget,
                 provenance=provenance)
    assert len(results)==71
    assert sum(r['get_order_requests'] for r in results)==3*sum(r['known_orders']*r['barrier_rounds'] for r in results)
    assert all(r['max_nonwarmer_get_overlap']==1 for r in results)
    csv_write('windows.csv',results);csv_write('requests.csv',request_rows);csv_write('rounds.csv',round_rows)
    csv_write('confirmation.csv',confirmation_evidence);csv_write('cancellations.csv',ack_rows)
    csv_write('request_budget.csv',budget)
    (OUT/'analysis.json').write_text(json.dumps(dict(summary=summary,windows=results,confirmation=confirmation_evidence),ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in summary.items() if k not in {'provenance','confirmation_page_lag_positive'}},ensure_ascii=False,indent=2))
    print('CONFIRMED page seen before local application',json.dumps(summary['confirmation_page_lag_positive'],ensure_ascii=False,indent=2))


if __name__=='__main__':analyze()

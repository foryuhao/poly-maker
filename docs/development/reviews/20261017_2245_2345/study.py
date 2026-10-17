"""Read-only reconciliation and cache evidence; no trading imports or network."""
import hashlib
import json
import statistics
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

from analyze import OUT, percentile, stamp, write_csv


def quant(values):
    return dict(n=len(values), median=statistics.median(values),
                p95=percentile(values, .95), max=max(values)) if values else dict(n=0)


def main():
    data = json.loads((OUT / 'analysis.json').read_text())
    windows = data['windows']
    sent = [w for w in windows if w['submitted']]
    merges, barriers, gaps, observer, source_hashes, changes = [], [], [], [], [], []
    counts = Counter()
    requests = []
    issues = []
    latencies = defaultdict(list)
    cache_invariants = []
    previous = None
    for m in data['markets']:
        rows = [(i, json.loads(l)) for i, l in enumerate(Path(m['source']).read_text().splitlines(), 1)]
        ownfills = [f for f in data['fills'] if f['session_id'] == m['session_id']]
        network=[r['data']['payload'] for _,r in rows if r['kind']=='sm_network_request']
        offset=statistics.median(q['completed_wall']-q['completed_monotonic'] for q in network)
        closes={r['data']['payload']['window_id']:r['data']['payload']['requested_monotonic']
                for _,r in rows if r['kind']=='sm_obi_close_requested'}
        for line, r in rows:
            q = r['data']['payload']
            kind = r['kind']
            if kind == 'sm_obi_signal_changes':
                for change in q['changes']:
                    changes.append(dict(market=m['market'],window=q['window_id'].rsplit(':',1)[-1],
                        time=stamp(change[1]+offset),epoch=change[1]+offset,signal=change[3],
                        before_close=change[1]<=closes[q['window_id']],source_line=line))
            if kind == 'sm_merge_result' and q['status'] == 'CONFIRMED':
                merges.append(dict(market=m['market'], time=stamp(r['ts']), epoch=r['ts'],
                    pairs=int(q['amount_raw'])/1e6, released_cost=q['released_cost_usdc'],
                    balance_before=int(q['collateral_before_raw'])/1e6,
                    balance_after=int(q['collateral_after_raw'])/1e6,
                    source=m['source'], source_line=line))
            if kind == 'sm_close_barrier':
                reason=q.get('reason') or ''
                category = reason.rsplit(':', 1)[-1]
                counts['barrier:' + q['result'] + ':' + category] += 1
                barriers.append(dict(market=m['market'], time=stamp(r['ts']), epoch=r['ts'],
                    result=q['result'], reason=reason, source_line=line))
            if kind == 'sm_network_request':
                ep=q['endpoint']
                if ep.startswith('/data/order/'): ep='/data/order/:id'
                latencies[q['method']+':'+ep].append(q['duration_ms'])
            if kind == 'sm_sync_cache_read':
                boundary=q['operation_after_monotonic']
                required={'time', 'open_orders', 'trades'}
                if q['purpose']=='account': required|={'capital', 'positions'}
                valid=all(q['sources'][name]['started_monotonic'] >= boundary for name in required)
                counts['cache_scopes'] += 1
                if not valid: cache_invariants.append([m['market'], line])
        if previous:
            gap=Decimal(str(m['starting_balance']))-Decimal(str(previous['starting_balance']))-Decimal(str(previous['scoped_cash_flow']))
            gaps.append(dict(previous=previous['market'], next=m['market'], unexplained=float(gap)))
        previous=m
        root=Path('research_outputs/periodic_sync') / m['session_id']
        manifest=json.loads((root/'manifest.json').read_text())
        summary=json.loads((root/'summary.json').read_text())
        source_hashes.append(manifest['source_sha256'])
        for name in ['manifest.json', 'summary.json', 'events.jsonl', 'snapshots.jsonl', 'strategy_refs.jsonl']:
            f=root/name
            h=hashlib.sha256()
            with f.open('rb') as stream:
                while chunk:=stream.read(1024*1024):h.update(chunk)
            data['provenance'].append(dict(path=str(f.resolve()), bytes=f.stat().st_size, sha256=h.hexdigest()))
        assert manifest['session_id']==m['session_id']
        assert manifest['market']['slug']==m['slug']
        evcounts=Counter()
        for line,l in enumerate((root/'events.jsonl').open(),1):
            e=json.loads(l)
            if e['kind']=='sync_http':
                evcounts['http']+=1
                evcounts['http:'+str(e.get('http_status'))+':'+str(e.get('error_type'))]+=1
                requests.append(dict(market=m['market'], line=line, **e))
            if e['kind']=='sync_round' and not e.get('shutdown'):
                evcounts['round:'+e['lane']]+=1
                evcounts['incomplete:'+e['lane']]+=not e['complete']
                evcounts['overrun:'+e['lane']]+=e['overrun']
            if e['kind']=='sync_consistency':
                evcounts['consistency']+=1
                if not e['observations_agree']:
                    issues.append(dict(market=m['market'], line=line, **e))
        # Verify persisted snapshot versions, market scope, and local fills.
        persisted_confirmed={}
        max_timestamp=0
        snapshot_issues=[]
        for line,l in enumerate((root/'snapshots.jsonl').open(),1):
            s=json.loads(l)
            if s['version']!=line or s['session_id']!=m['session_id']:
                snapshot_issues.append([line,'identity_or_version'])
            if s.get('market') and s['market']!=manifest['market']:
                snapshot_issues.append([line,'market'])
            if s.get('trading_authorized') is not False:
                snapshot_issues.append([line,'authority'])
            trades=s.get('last_good',{}).get('trades',{}).get('data') or []
            for t in trades:
                for leg in t['maker_legs']:
                    key=(t['trade_id'],leg['order_id'])
                    if t['status']=='CONFIRMED' and any(f['trade_id']==key[0] and f['order_id']==key[1] for f in ownfills):
                        persisted_confirmed[key]=leg
                        max_timestamp=max(max_timestamp,t['timestamp'])
        assert not snapshot_issues, snapshot_issues
        assert {(f['trade_id'],f['order_id']) for f in ownfills}==set(persisted_confirmed)
        assert all(Decimal(str(persisted_confirmed[(f['trade_id'],f['order_id'])]['matched_amount']))==Decimal(str(f['quantity']))
                   and Decimal(str(persisted_confirmed[(f['trade_id'],f['order_id'])]['price']))==Decimal(str(f['price'])) for f in ownfills)
        observer.append(dict(market=m['market'], session_id=m['session_id'],
            execution_reads_from_cache=manifest['execution_reads_from_cache'],
            source_sha256=manifest['source_sha256'], snapshots=summary['snapshot_count'],
            status=summary.get('status'), observer_status=summary['observer_status'],
            dropped_events=summary['dropped_events'], dropped_references=summary['dropped_references'],
            reference_market_or_history_gaps=summary['reference_market_or_history_gaps'],
            io_drained=summary['io_drained'], failure=summary['failure'],
            confirmed_snapshot_keys=len(persisted_confirmed), max_trade_timestamp=max_timestamp,
            counters=dict(evcounts)))
    unknown=[w for w in sent if w['outcome_final']=='UNKNOWN']
    canceled=[w for w in sent if 'Already canceled' in w['raw_error']]
    rejected=[w for w in sent if w['outcome_final']=='REJECTED']
    last_merge=merges[-1]
    balance_end=last_merge['balance_after']
    balance_change=balance_end-data['markets'][0]['starting_balance']
    evidence=dict(scope='2026-10-17 22:45–23:45 Asia/Shanghai; latest 12 logged BTC five-minute markets',
        sent_outcomes=dict(Counter(w['outcome_final'] for w in sent)),
        canceled_count=len(canceled), canceled_outcomes=dict(Counter(w['outcome_final'] for w in canceled)),
        post_only_cross_count=sum('crosses book' in w['raw_error'] for w in rejected),
        unknown_windows=[{k:w[k] for k in ['market','window','post_time','phase','outcome_final','close_barrier','block_reason','final_close_time']} for w in unknown],
        blocked_windows=[{k:w[k] for k in ['market','window','post_time','outcome_final','block_reason','final_close_time']} for w in windows if w['close_barrier']=='BLOCKED'],
        phase_metrics={phase:dict(
            submitted=len(ws), outcomes=dict(Counter(w['outcome_final'] for w in ws)),
            filled_orders=sum(w['fill_quantity']>0 for w in ws),
            close_reasons=dict(Counter(w['close_reason'] for w in ws)),
            post_to_close_s=quant([w['post_to_close_s'] for w in ws]),
            close_to_window_end_s=quant([w['close_to_safe_s'] for w in ws]),
            unfilled_ack_post_to_close_s=quant([w['post_to_close_s'] for w in ws if w['outcome_final']=='ACK' and not w['fill_quantity']]),
            ack_stale=sum(w['close_reason']=='WS_BOOK_STALE' and w['outcome_final']=='ACK' for w in ws))
            for phase in ['NORMAL','REBALANCE'] for ws in [[w for w in sent if w['phase']==phase]]},
        starting_balance=data['markets'][0]['starting_balance'], final_observed_balance=balance_end,
        final_balance_time=last_merge['time'], observed_balance_change=balance_change,
        scoped_cash_flow=data['totals']['scoped_cash_flow'],
        unexplained_cash_flow=balance_change-data['totals']['scoped_cash_flow'],
        between_market_gaps=gaps, merges=merges, observer=observer,
        same_sync_source_hashes=all(h==source_hashes[0] for h in source_hashes),
        current_source_matches={name:hashlib.sha256((Path('src/polymaker')/name).read_bytes()).hexdigest()==digest for name,digest in source_hashes[0].items()},
        counts=dict(counts), cache_source_boundary_failures=cache_invariants,
        consistency_disagreements=issues,
        request_metrics={k:quant(v) for k,v in latencies.items()},
        http_observer_counts=dict(sum((Counter(o['counters']) for o in observer),Counter())),
        non_200_observer_requests=[r for r in requests if r.get('http_status')!=200],
        matched_to_confirmed_observation_s=quant([f['confirmed_epoch']-f['matched_observed_epoch'] for f in data['fills'] if f['matched_observed_epoch']]),
        max_writer_queue=max(m['max_writer_queue'] for m in data['markets']),
        diagnostics_dropped=max(m['dropped_diagnostics'] for m in data['markets']),
        provenance=data['provenance'])
    write_csv('merges.csv',merges)
    write_csv('close_barriers.csv',barriers)
    write_csv('balance_gaps.csv',gaps)
    write_csv('signal_changes.csv',sorted(changes,key=lambda r:r['epoch']))
    (OUT/'evidence.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in evidence.items() if k not in ['observer','non_200_observer_requests','consistency_disagreements','provenance','merges','request_metrics']},ensure_ascii=False,indent=2))
    print('NON200',len(evidence['non_200_observer_requests']), Counter((r.get('http_status'),r.get('error_type')) for r in evidence['non_200_observer_requests']))
    print('CONSISTENCY',len(issues))
    print('OBSERVER',[(o['market'],o['status'],o['snapshots'],o['counters']) for o in observer])
    return evidence


if __name__=='__main__':main()

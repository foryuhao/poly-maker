"""Historical observations supporting a profit-aware repair proposal, not a backtest."""
import csv
import hashlib
import json
import statistics
from decimal import Decimal
from pathlib import Path

OUT=Path(__file__).resolve().parent
BASE=OUT.parent/'20261017_2245_2345'
D=lambda x:Decimal(str(x))


def main():
    data=json.loads((BASE/'analysis.json').read_text())
    paired=json.loads((OUT.parent/'20261017_2245_2345_pairing/evidence.json').read_text())
    observations=[]
    sources=[]
    for m in data['markets']:
        p=Path(m['source'])
        raw=p.read_bytes()
        digest=hashlib.sha256(raw).hexdigest()
        assert digest==next(s['sha256'] for s in data['provenance'] if s['path']==m['source'])
        sources.append(dict(path=str(p),sha256=digest))
        rows=[json.loads(l) for l in raw.decode().splitlines()]
        intents={r['data']['payload']['intent']['window_id']:r['data']['payload']['intent']
                 for r in rows if r['kind']=='sm_obi_final_decision'}
        windows=[w for w in data['windows'] if w['session_id']==m['session_id']]
        for c in [c for c in paired['cycles'] if c['session_id']==m['session_id']]:
            w=next(w for w in windows if w['window']==c['window'])
            start=windows.index(w)
            end=next((i for i in range(start+1,len(windows))
                      if windows[i]['phase']=='NORMAL' and windows[i]['fill_quantity']),len(windows))
            repairs=[w for w in windows[start+1:end] if w['phase']=='REBALANCE' and w['submitted']]
            first=repairs[0]
            intent=intents[w['session_id']+':'+w['window']]
            snapshot=intent.get('final_snapshot') or intent['snapshot']
            other=snapshot['no' if w['side']=='YES' else 'yes']
            price,qty=D(w['price']),D(w['fill_quantity'])
            bid,ask=D(other['best_bid']),D(other['best_ask'])
            first_price=D(first['price'])
            normalfills=[f for f in data['fills'] if f['order_id']==w['order_id']]
            lag=min(f['matched_observed_epoch'] for f in normalfills)-w['post_epoch']
            observations.append(dict(market=m['market'],window=w['window'],side=w['side'],
                entry_price=float(price),quantity=float(qty),entry_obi=c['entry_signal'],
                opposite_bid_at_entry=float(bid),opposite_ask_at_entry=float(ask),
                entry_plus_opposite_bid=float(price+bid),entry_plus_opposite_ask=float(price+ask),
                same_quantity_bid_repair_meets_one_dollar_minimum=bid*qty>=1,
                first_repair_window=first['window'],first_repair_price=float(first_price),
                first_repair_outcome=first['outcome_final'],
                first_repair_hypothetical_gross=float(qty*(1-price-first_price)),
                final_fifo_pair_gross=c['fifo_acquisition_pair_gross'],
                pair_seconds=c['matched_to_matched_s'],repair_submissions=c['repair_submissions'],
                entry_post_to_first_matched_observation_s=lag,
                first_repair_actual_filled_quantity=first['fill_quantity'],
                source=m['source'],entry_decision_line=w['source_line'],first_repair_decision_line=first['source_line']))
    old=[]
    for name in ['20261017_1300_1500','20261017_1540_1640']:
        p=OUT.parent/name/'analysis.json'
        a=json.loads(p.read_text())
        old.append(dict(cohort=name,markets=a['totals']['markets'],
            rebalance_posts=a['totals']['phases']['REBALANCE'],
            stale_closes=a['totals']['phase_close_reasons']['REBALANCE']['WS_BOOK_STALE'],
            residual_markets=a['totals']['residual_markets']))
        sources.append(dict(path=str(p.resolve()),sha256=hashlib.sha256(p.read_bytes()).hexdigest()))
    fast=[o for o in observations if o['pair_seconds']<=15]
    result=dict(scope='Latest 12 markets and preceding 36 reviewed markets, 2026-10-17; observed historical outcomes only',
        latest=dict(cycles=len(observations),first_repair_quotes_lock_negative_gross=sum(o['first_repair_hypothetical_gross']<0 for o in observations),
            final_positive_cycles=sum(o['final_fifo_pair_gross']>0 for o in observations),
            final_negative_cycles=sum(o['final_fifo_pair_gross']<0 for o in observations),
            fast_within_15_s=dict(cycles=len(fast),negative_cycles=sum(o['final_fifo_pair_gross']<0 for o in fast),
                fifo_pair_gross_sum=sum(o['final_fifo_pair_gross'] for o in fast)),
            entry_plus_opposite_bid_levels=sorted(set(o['entry_plus_opposite_bid'] for o in observations)),
            entry_plus_opposite_ask_levels=sorted(set(o['entry_plus_opposite_ask'] for o in observations)),
            entry_snapshots_with_exact_quantity_bid_repair_below_minimum=sum(not o['same_quantity_bid_repair_meets_one_dollar_minimum'] for o in observations),
            opening_post_to_matched_observation_mean_s=statistics.mean(o['entry_post_to_first_matched_observation_s'] for o in observations),
            opening_matched_observation_at_least_2_s_after_post=sum(o['entry_post_to_first_matched_observation_s']>=2 for o in observations),
            opening_matched_to_confirmed_mean_s=paired['all']['metrics']['entry_confirmation_wait_s']['mean'],
            reversed_filled_normal_orders=paired['counts']['signal_reversed_closes'],
            reversed_close_post_latency_median_s=paired['reversed_normal_post_to_close_s']['median'],
            rebalance_posts=data['totals']['phases']['REBALANCE'],
            stale_closes=data['totals']['phase_close_reasons']['REBALANCE']['WS_BOOK_STALE']),
        previous=old,observations=observations,sources=sources,
        caveats=['First repair quoted PnL is arithmetic under full hypothetical fill, not an actual outcome.',
            'Entry snapshot bid/ask sums do not prove depth, queue priority or fill availability when the opening later matched.',
            'FIFO pair gross excludes fees/rebates and differs slightly from average-cost merge accounting for dust.',
            'No complete event-by-event executable order-book and queue replay covers the first two seconds after every fill.',
            'A two-second horizon and proposed target/stop levels are design hypotheses, not fitted profitable parameters.'])
    assert result['latest']['first_repair_quotes_lock_negative_gross']==10
    assert result['latest']['entry_plus_opposite_bid_levels']==[.99]
    assert result['latest']['entry_plus_opposite_ask_levels']==[1.0]
    (OUT/'evidence.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    with (OUT/'observations.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(observations[0]),lineterminator='\n')
        writer.writeheader();writer.writerows(observations)
    print(json.dumps({k:result[k] for k in ['scope','latest','previous','caveats']},ensure_ascii=False,indent=2))
    return result


if __name__=='__main__':
    RESULT=main()

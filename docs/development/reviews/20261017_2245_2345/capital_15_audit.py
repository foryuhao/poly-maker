"""Replay 23:25 capital admission arithmetic from persisted evidence only."""
import csv
import json
from decimal import Decimal, ROUND_CEILING
from pathlib import Path

OUT = Path(__file__).resolve().parent
D = lambda v: Decimal(str(v))


def main():
    data = json.loads((OUT / 'analysis.json').read_text())
    market = next(m for m in data['markets'] if m['market'] == '23:25')
    rows = [(i, json.loads(l)) for i, l in enumerate(Path(market['source']).open(), 1)]
    decisions = {r['data']['payload']['intent']['window_id']: (i, r['data']['payload'])
                 for i, r in rows if r['kind'] == 'sm_obi_final_decision'}
    windows = [w for w in data['windows'] if w['session_id'] == market['session_id']]
    results = {r['data']['payload']['window_id']: r['data']['payload']
               for _, r in rows if r['kind'] == 'sm_window_result'}
    cap = D(15)
    previous = D(0)
    audit = []
    # Before the first merge, confirmed cost remains reserved after pair balance.
    for w in windows:
        if w['window'] == 'w8':
            break
        line, q = decisions[market['session_id'] + ':' + w['window']]
        intent = q['intent']
        quote = intent['quotes'][0]
        cost = D(quote['price']) * D(quote['size'])
        snapshot = intent.get('final_snapshot') or intent['snapshot']
        opposite = 'no' if quote['outcome'] == 'YES' else 'yes'
        opposite_price = D(snapshot[opposite]['best_bid'])
        # All NORMAL entries in this case start at delta=0 and buy 5 shares.
        repair_size = max(D(5), (D(1)/opposite_price*100).to_integral_value(rounding=ROUND_CEILING)/100)
        opposite_cost = repair_size * opposite_price
        record = dict(window=w['window'], phase=w['phase'], post_time=w['post_time'],
            side=quote['outcome'], price=str(quote['price']), shares=str(quote['size']),
            committed_before=str(previous), entry_commitment=str(cost),
            hypothetical_committed_after_entry=str(previous+cost),
            hypothetical_cap_headroom_after_entry=str(cap-previous-cost),
            existing_cap_check_passes=previous+cost<=cap,
            actual_outcome=w['outcome_final'], actual_filled_shares=w['fill_quantity'],
            actual_committed_at_window_end=str(results[w['session_id']+':'+w['window']]['capital_committed']),
            source_line=line, source=market['source'])
        if w['phase'] == 'NORMAL':
            record.update(opposite_bid=str(opposite_price), repair_shares_at_current_bid=str(repair_size),
                repair_cost_at_current_bid=str(opposite_cost),
                hypothetical_full_cycle_committed=str(previous+cost+opposite_cost),
                passes_current_bid_repair_budget=previous+cost+opposite_cost<=cap,
                cannot_afford_even_one_dollar_repair=cap-previous-cost<D(1),
                repair_reserve_at_tick_max=str(D(quote['size'])*(1-D(snapshot[opposite]['tick']))))
        audit.append(record)
        previous=D(results[w['session_id']+':'+w['window']]['capital_committed'])
    normals=[r for r in audit if r['phase']=='NORMAL']
    assert all(r['existing_cap_check_passes'] for r in audit)
    assert [r['window'] for r in normals if r['cannot_afford_even_one_dollar_repair']]==['w5','w6','w7']
    assert [r['window'] for r in normals if not r['passes_current_bid_repair_budget']]==['w5','w6','w7']
    assert D(audit[-1]['actual_committed_at_window_end'])==D('14.7')
    result=dict(scope='2026-10-17 23:25 BTC five-minute market; local POST times Asia/Shanghai',
        starting_wallet_balance=str(market['starting_balance']), session_cap='15',
        actual_cycle_entry_that_created_stranded_gap='w7', first_counterfactual_rejection='w5',
        assumptions=['NORMAL entries in this sample start at zero delta and request 5 shares.',
            'Full fill is a capital exposure scenario, not a prediction of fill.',
            'Opposite current bid sizes/costs illustrate immediate budget feasibility only; they do not guarantee future execution or price.',
            'Minimum legal BUY notional is 1 USDC; all cases require an additional opposite BUY after a full NORMAL fill.',
            'Existing committed amounts are journal window results, independently reconciled against confirmed fills in analysis.json.'],
        windows=audit)
    (OUT/'capital_15.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    fields=list(dict.fromkeys(k for r in audit for k in r))
    with (OUT/'capital_15.csv').open('w') as f:
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader();writer.writerows(audit)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()

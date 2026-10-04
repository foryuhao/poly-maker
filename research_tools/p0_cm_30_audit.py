"""Independent local-file audit of the frozen P0-CM-30 replay output.

Run after p0_cm_30.py: python3 research_tools/p0_cm_30_audit.py
"""
from __future__ import annotations

import csv
import json
import math
from collections import defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'research_outputs/p0_cm_30'
DATA=ROOT/'research_data/m4a'


def rows(name):
    with (OUT/name).open(newline='') as f:return list(csv.DictReader(f))


def close(a,b):return math.isclose(float(a),float(b),abs_tol=1e-6)


def main():
    markets=rows('market_results.csv');orders=rows('orders.csv');ledger=rows('ledger.csv');cover=rows('sample_coverage.csv')
    frozen=rows_from_p0()
    assert len(markets)==len(cover)==len(frozen)==30
    assert [x['market'] for x in markets]==[x['market'] for x in frozen]
    assert [x['market'] for x in cover]==[x['market'] for x in frozen]
    order_by_market=defaultdict(list);money_by_market=defaultdict(list)
    for r in orders:order_by_market[r['market']].append(r)
    for r in ledger:money_by_market[r['market']].append(r)
    fill_count=cancel_wait=partial_count=0
    for m in markets:
        slug=m['market'];ev=money_by_market[slug];o=order_by_market[slug]
        fills=[x for x in ev if x['event']=='FILL']
        fill_count+=len(fills);partial_count+=sum(x['event']=='PARTIAL_FILL' for x in o)
        gross=sum(float(x['gross']) for x in fills);fees=sum(float(x['fee']) for x in fills)
        qty=sum(float(x['qty']) for x in fills)
        merge=sum(float(x['qty']) for x in ev if x['event']=='MERGE')
        release_merge=sum(float(x['qty']) for x in ev if x['event']=='MERGE_RELEASE')
        settle=next(x for x in ev if x['event']=='SETTLEMENT')
        redeem=next(x for x in ev if x['event']=='REDEEM_RELEASE')
        assert close(gross,m['gross_spent']) and close(fees,m['fees']) and close(qty,m['buy_qty'])
        assert close(merge,m['merge_income']) and close(release_merge,merge)
        assert close(settle['qty'],m['residual_income']) and close(redeem['qty'],settle['qty'])
        assert close(merge+float(settle['qty'])-gross-fees,m['pnl'])
        assert close(float(m['pair_pnl'])+float(m['residual_pnl']),m['pnl'])
        assert close(float(redeem['cash']),30+float(m['pnl']))
        assert all(x['side'] in ('','YES','NO') for x in o+ev)
        assert all(float(x['cash'])>=-1e-8 and float(x['reserved'] or 0)>=-1e-8 for x in ev)
        starts={x['order_id']:x for x in o if x['event']=='SUBMIT'}
        assert len(starts)==sum(x['event']=='SUBMIT' for x in o)==int(m['submitted'])
        terminal={}
        for x in o:
            oid=x['order_id']
            assert oid in starts
            if x['event'] in ('REJECT','CANCEL_CONFIRM','FILL'):terminal[oid]=int(x['time_us'])
            if x['event']=='SUBMIT':
                q=float(x['qty']);net=float(x['actual_net']);side=x['side'];sign=1 if side=='YES' else -1
                assert q>=5-1e-8 and close(q,math.floor((q+1e-9)*100)/100)
                assert q<=float(x['full_delta'])+1e-8
                assert 0<float(x['price'])<1 and abs(net+sign*q)<=15+1e-8
                assert float(x['reserved'])<=float(x['cash'])+1e-8
                if x['order_class']=='pure_reduce':
                    assert (net>0 and side=='NO') or (net<0 and side=='YES')
                    assert q<=abs(net)+1e-8
        assert len(terminal)==len(starts)
        submits=sorted(starts.values(),key=lambda x:int(x['time_us']))
        for left,right in zip(submits,submits[1:]):
            assert terminal[left['order_id']]<=int(right['time_us'])
        assert sum(x['event']=='ACCEPT' for x in o)==int(m['accepted'])
        assert sum(x['event']=='REJECT' for x in o)==int(m['rejected'])
        assert sum(x['event']=='CANCEL_CONFIRM' for x in o)==int(m['canceled'])
        assert sum(x['event']=='FILL' for x in o)==int(m['filled_orders'])
        assert sum(x['event']=='PARTIAL_FILL' for x in o)==int(m['partial_events'])
        assert len(fills)==int(m['fill_events'])
        for x in fills:
            own=float(x['qty']);trade=float(x['trade_qty']);ahead=float(x['queue_before'])
            assert own<=trade-min(ahead,trade)+1e-8
            assert int(x['exchange_at_us'])>=int(starts[x['order_id']]['arrival_us'])
            assert int(x['received_at_us'])>=int(starts[x['order_id']]['arrival_us'])
            assert int(x['available_at_us'])==int(x['time_us'])
            req=next((z for z in o if z['order_id']==x['order_id'] and z['event']=='CANCEL_REQUEST'),None)
            if req and int(req['time_us'])<=int(x['time_us']):cancel_wait+=1
        if fills:
            cv=next(z for z in cover if z['market']==slug)
            wanted={int(x['time_us']) for x in fills};prints=defaultdict(list)
            with (DATA/cv['session']/'events.jsonl').open() as source:
                for line in source:
                    event=json.loads(line);t=event.get('available_at_us')
                    if t in wanted and event['kind']=='TRADE':prints[t].append(event)
            tokens={'YES':cv['yes_token_id'],'NO':cv['no_token_id']}
            for x in fills:
                candidates=[e for e in prints[int(x['time_us'])]
                    if e['payload'].get('token_id')==tokens[x['side']]
                    and e['payload'].get('side')=='SELL'
                    and close(e['payload'].get('size'),x['trade_qty'])
                    and e['exchange_at_us']==int(x['exchange_at_us'])
                    and e['received_at_us']==int(x['received_at_us'])]
                assert len(candidates)==1,(slug,x['order_id'],x['time_us'])
        assert float(m['peak_capital_occupancy'])<=30+1e-8
        assert float(m['peak_exit_reserve'])<=15+1e-8
        assert int(cover[[z['market'] for z in cover].index(slug)]['timestamp_inversions'])==0
    assert fill_count==sum(int(x['fill_events']) for x in markets)
    assert partial_count==sum(int(x['partial_events']) for x in markets)
    summary=dict(markets=30,fill_events=fill_count,partial_fill_events=partial_count,
        cancel_wait_fills=cancel_wait,pnl=sum(float(x['pnl']) for x in markets),
        total_final_cash=sum(30+float(x['pnl']) for x in markets),
        checks='sample, all-market money, order lifecycle, net cap, precision, cash reserve, raw print identity/volume/clocks, releases')
    (OUT/'audit.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary,indent=2))


def rows_from_p0():
    with (ROOT/'research_outputs/p0_probability_inventory/sample_coverage.csv').open(newline='') as f:
        return list(csv.DictReader(f))


if __name__=='__main__':main()

"""Read-only diagnosis of the frozen P0 C-M output and causal local books.
Run: python3 research_tools/p0_maker_path_diagnostic.py
"""
from __future__ import annotations
import csv
import json
import math
from collections import defaultdict,Counter
from datetime import datetime,timezone
from pathlib import Path
import p0_probability_inventory as base

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'research_outputs/p0_maker_only'
DATA=ROOT/'research_data/m4a'
OUT=ROOT/'research_outputs/p0_maker_path_diagnostic_20261004'
KEY='btc-updown-5m-1791015900'
CONTROL='btc-updown-5m-1790962500'
FRESH=2_000_000
CUT=15_000_000


def rows(name):
    with (SOURCE/name).open(newline='') as f:return list(csv.DictReader(f))


def write(name,records,fields):
    fields=list(dict.fromkeys([*fields,*(k for r in records for k in r)]))
    with (OUT/name).open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(records)


def timestamp(us):
    return datetime.fromtimestamp(int(us)/1_000_000,timezone.utc).isoformat(timespec='microseconds') if us not in ('',None) else ''


def fnum(x):return float(x) if x not in ('',None) else None


def book_state(books,t):
    if not all(b.valid() for b in books.values()):return {'state':'invalid_book'}
    if any(b.time is None or t-b.time>FRESH for b in books.values()):return {'state':'stale_book'}
    p=base.price_prob(books)
    if not 0<p<1:return {'state':'probability_bounds'}
    return {'state':'valid','p':p,'target':max(-25,min(25,50*(p-.5))),
            'yes_bid':books['YES'].bid/10000,'yes_ask':books['YES'].ask/10000,
            'no_bid':books['NO'].bid/10000,'no_ask':books['NO'].ask/10000,
            'yes_updated_us':books['YES'].time,'no_updated_us':books['NO'].time}


def causal_market(coverage,fill_timestamps,collect_path=False):
    slug=coverage['market'];manifest=json.loads((DATA/coverage['session']/'manifest.json').read_text())
    token={manifest['yes_token_id']:'YES',manifest['no_token_id']:'NO'}
    books={s:base.Book() for s in ('YES','NO')}
    fill_pre={};path=[];start=int(coverage['start_us']);end=int(coverage['end_us'])
    with (DATA/coverage['session']/'events.jsonl').open() as f:
        for line in f:
            e=json.loads(line);t=e.get('available_at_us')
            if t is None:continue
            kind=e['kind'];p=e.get('payload') or {};side=token.get(p.get('token_id'))
            if kind=='TRADE' and t in fill_timestamps:
                state=book_state(books,t)
                fill_pre.setdefault(t,[]).append(dict(state,trade_seq=e.get('event_seq'),trade_side=p.get('side'),
                    trade_token_side=side,trade_qty=p.get('size'),trade_price=p.get('price'),
                    exchange_at_us=e.get('exchange_at_us'),received_at_us=e.get('received_at_us')))
            if kind in ('BOOK','REST_BOOK','DELTA') and side:
                changed=books[side].update(kind,p,t)
                if collect_path and changed and start<=t<end-CUT:
                    state=book_state(books,t)
                    path.append(dict(market=slug,time_us=t,time_utc=timestamp(t),event_seq=e.get('event_seq'),**state))
            elif kind=='TICK' and side and p.get('tick_size'):
                books[side].tick=round(float(p['tick_size'])*10000)
            if t>=end:break
    return fill_pre,path


def public_trades_between(coverage,start_us,end_us):
    result=[]
    with (DATA/coverage['session']/'events.jsonl').open() as f:
        for line in f:
            e=json.loads(line);t=e.get('available_at_us')
            if t is None or t<start_us:continue
            if t>end_us:break
            if e['kind']=='TRADE':result.append(e)
    return result


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    ledger=rows('ledger.csv');orders=rows('orders.csv');coverage=rows('sample_coverage.csv');markets=rows('market_results.csv')
    by_market={r['market']:r for r in markets};cover={r['market']:r for r in coverage}
    fill=[r for r in ledger if r['event']=='FILL'];assert len(fill)==23
    by_order=defaultdict(list)
    for r in orders:by_order[r['order_id']].append(r)
    causal={};rawpath={}
    for slug in {r['market'] for r in fill}|{KEY,CONTROL}:
        times={int(r['time_us']) for r in fill if r['market']==slug}
        if slug==KEY:times.add(int(cover[KEY]['last_valid_us']))
        causal[slug],rawpath[slug]=causal_market(cover[slug],times,slug in (KEY,CONTROL))

    all_fills=[];net=defaultdict(float)
    for r in fill:
        slug=r['market'];oid=r['order_id'];o=by_order[oid]
        sub=next(x for x in o if x['event']=='SUBMIT')
        cancel=next((x for x in o if x['event']=='CANCEL_REQUEST'),None)
        conf=next((x for x in o if x['event']=='CANCEL_CONFIRM'),None)
        accept=next((x for x in o if x['event']=='ACCEPT'),None)
        t=int(r['time_us']);side=r['side'];qty=float(r['qty'])
        matches=[x for x in causal[slug].get(t,[]) if x['trade_token_side']==side and x['trade_side']=='SELL'
                 and math.isclose(float(x['trade_qty']),float(r['trade_qty']),abs_tol=1e-6)]
        if len(matches)!=1:raise AssertionError(f'raw print match {slug} {oid} {t}: {len(matches)}')
        state=matches[0];before=net[slug];after=before+(qty if side=='YES' else -qty);net[slug]=after
        if state['state']=='valid':
            target=state['target'];p=state['p'];eb=abs(before-target);ea=abs(after-target)
            movement='decrease' if ea<eb-1e-8 else 'increase' if ea>eb+1e-8 else 'unchanged'
            need='YES' if target-before>1e-8 else 'NO' if target-before< -1e-8 else 'NONE'
            legal_need='YES' if target-before>=5-1e-8 else 'NO' if target-before<=-5+1e-8 else 'BELOW_MIN'
            same=(side==need)
            if abs(float(r['p'])-p)>1e-8 or abs(float(r['target'])-target)>1e-7:
                raise AssertionError(f'ledger/raw probability disagreement {slug} {oid}')
        else:
            target=p=eb=ea=None;movement=need=legal_need=same='UNKNOWN'
        cancel_pending=bool(cancel and int(cancel['time_us'])<=t and (conf is None or t<int(conf['time_us'])))
        all_fills.append(dict(market=slug,order_id=oid,submit_us=sub['time_us'],submit_utc=timestamp(sub['time_us']),
            arrival_us=accept['time_us'] if accept else '',arrival_utc=timestamp(accept['time_us']) if accept else '',
            cancel_request_us=cancel['time_us'] if cancel else '',cancel_request_utc=timestamp(cancel['time_us']) if cancel else '',
            fill_us=t,fill_utc=timestamp(t),cancel_confirm_us=conf['time_us'] if conf else '',
            cancel_confirm_utc=timestamp(conf['time_us']) if conf else '',
            cancel_confirm_status='confirmed' if conf else 'full_fill_before_confirmation' if cancel else 'no_cancel_request',
            side=side,qty=qty,order_price=float(r['price']),trade_price=float(state['trade_price']),trade_qty=float(r['trade_qty']),
            queue_before=float(r['queue_before']),queue_after=float(r['queue_after']),submit_p=float(sub['p']),
            submit_target=float(sub['target']),fill_book_state=state['state'],fill_p=p,fill_target=target,
            net_before=before,net_after=after,error_before=eb,error_after=ea,error_movement=movement,
            needed_side=need,legal_adjustment_side=legal_need,direction_matches_need=same,
            cancel_pending=cancel_pending,trade_event_seq=state['trade_seq'],exchange_at_us=state['exchange_at_us'],
            received_at_us=state['received_at_us'],fill_cash=float(r['cash']),fill_stop_metric=float(r['stop_metric']),
            winner=by_market[slug]['winner'],market_pnl=float(by_market[slug]['pnl'])))
    assert len(all_fills)==23
    assert math.isclose(sum(r['qty'] for r in all_fills),sum(float(r['buy_qty']) for r in markets),abs_tol=1e-7)
    for mr in markets:
        slug=mr['market'];lf=[r for r in fill if r['market']==slug]
        gross=sum(float(r['gross']) for r in lf);fees=sum(float(r['fee']) for r in lf)
        assert math.isclose(gross,float(mr['gross_spent']),abs_tol=1e-7)
        assert math.isclose(fees,float(mr['fees']),abs_tol=1e-7)
        assert math.isclose(sum(float(r['qty']) for r in lf),float(mr['buy_qty']),abs_tol=1e-7)
        assert math.isclose(float(mr['pair_pnl'])+float(mr['residual_pnl']),float(mr['pnl']),abs_tol=1e-7)
        assert math.isclose(float(mr['merge_income'])+float(mr['residual_income'])-gross-fees,float(mr['pnl']),abs_tol=1e-7)
    write('fill_event_diagnosis.csv',all_fills,list(all_fills[0]))

    # Explain every losing market, with the positive control at the same grain.
    summaries=[]
    for slug in [r['market'] for r in markets if float(r['pnl'])<0]+[CONTROL]:
        fs=[r for r in all_fills if r['market']==slug];mr=by_market[slug]
        summaries.append(dict(market=slug,winner=mr['winner'],pnl=float(mr['pnl']),pair_pnl=float(mr['pair_pnl']),
            residual_pnl=float(mr['residual_pnl']),fill_events=len(fs),fill_qty=sum(r['qty'] for r in fs),
            cancel_pending_fills=sum(r['cancel_pending'] for r in fs),cancel_pending_qty=sum(r['qty'] for r in fs if r['cancel_pending']),
            error_decrease=sum(r['error_movement']=='decrease' for r in fs),error_increase=sum(r['error_movement']=='increase' for r in fs),
            direction_mismatch=sum(r['direction_matches_need'] is False for r in fs),unknown_fill_state=sum(r['fill_book_state']!='valid' for r in fs),
            submitted=int(mr['submitted']),accepted=int(mr['accepted']),rejected=int(mr['rejected']),canceled=int(mr['canceled']),
            cash_skips=int(mr['cash_skips']),min_waits=int(mr['min_waits']),metadata=mr['metadata'],reasons=mr['reasons']))
    write('market_mechanisms.csv',summaries,list(summaries[0]))

    # Worst-market book path with actual inventory, in-flight order, and observed risk gate.
    key_fills=sorted([r for r in all_fills if r['market']==KEY],key=lambda r:r['fill_us'])
    assert len(key_fills)==1
    fill_key=key_fills[0];fill_at=fill_key['fill_us'];fill_qty=fill_key['qty']
    fill_ledger=next(r for r in fill if r['market']==KEY)
    before_metric=float(fill_ledger['stop_metric'])+float(fill_ledger['gross'])+float(fill_ledger['fee'])
    after_metric=float(fill_ledger['stop_metric'])
    key_order_rows=[r for r in orders if r['market']==KEY]
    order_intervals={}
    for oid,items in by_order.items():
        if items[0]['market']!=KEY:continue
        submit=next(x for x in items if x['event']=='SUBMIT')
        terminal=next((x for x in reversed(items) if x['event'] in ('REJECT','CANCEL_CONFIRM','FILL')),None)
        order_intervals[oid]=(int(submit['time_us']),int(terminal['time_us']) if terminal else int(cover[KEY]['end_us']))
    decisions=[]
    for x in rawpath[KEY]:
        t=int(x['time_us']);net_at=0 if t<fill_at else -fill_qty
        outstanding=[oid for oid,(a,b) in order_intervals.items() if a<=t<b]
        row=dict(x,actual_net=net_at,outstanding_order=','.join(outstanding),stop_metric_observed=(before_metric if t<fill_at else after_metric))
        if x['state']=='valid':
            delta=x['target']-net_at;side='YES' if delta>=5-1e-8 else 'NO' if delta<=-5+1e-8 else 'NONE'
            row.update(delta=delta,needed_side=side,min_qty_pass=side!='NONE',error=abs(delta))
            if side!='NONE':
                bid=x['yes_bid'] if side=='YES' else x['no_bid'];ask=x['yes_ask'] if side=='YES' else x['no_ask']
                cost=abs(delta)*bid
                row.update(candidate_bid=bid,candidate_ask=ask,candidate_cost=cost,
                    risk_gate_pass=row['stop_metric_observed']-cost>-20+1e-8,
                    cash_gate_pass=cost<=float(fill_ledger['cash'])+1e-8,
                    order_free=not outstanding)
            else:row.update(candidate_bid='',candidate_ask='',candidate_cost='',risk_gate_pass='',cash_gate_pass='',order_free=not outstanding)
        else:row.update(delta='',needed_side='UNKNOWN',min_qty_pass='',error='',candidate_bid='',candidate_ask='',candidate_cost='',risk_gate_pass='',cash_gate_pass='',order_free=not outstanding)
        decisions.append(row)
    write('key_market_book_decisions.csv',decisions,list(decisions[0]))
    # Event chronology combines all persisted order and money events for the key market.
    timeline=[]
    for x in key_order_rows:
        timeline.append(dict(time_us=int(x['time_us']),time_utc=timestamp(x['time_us']),source='order',event=x['event'],order_id=x['order_id'],
            side=x['side'],qty=x['remaining'],price=x['price'],p=x['p'],target=x['target'],actual_net=x['actual_net'],cash=x['cash'],reason=x['reason']))
    for x in ledger:
        if x['market']==KEY and x['event'] in ('FILL','MERGE','SETTLEMENT','MERGE_RELEASE','REDEEM_RELEASE'):
            timeline.append(dict(time_us=int(x['time_us']),time_utc=timestamp(x['time_us']),source='ledger',event=x['event'],order_id=x['order_id'],
                side=x['side'],qty=x['qty'],price=x['price'],p=x['p'],target=x['target'],actual_net=x['actual_net'],cash=x['cash'],reason=x['reason']))
    timeline.sort(key=lambda x:(x['time_us'],0 if x['source']=='order' else 1))
    write('key_market_timeline.csv',timeline,list(timeline[0]))
    # Order-level outcome for all key-market orders, including the opposite-side repair attempt.
    key_orders=[]
    for oid,items in by_order.items():
        if items[0]['market']!=KEY:continue
        sub=next(x for x in items if x['event']=='SUBMIT')
        frows=[r for r in all_fills if r['order_id']==oid]
        key_orders.append(dict(order_id=oid,side=sub['side'],qty=float(sub['qty']),price=float(sub['price']),
            submit_us=sub['time_us'],submit_utc=timestamp(sub['time_us']),submit_p=sub['p'],submit_target=sub['target'],
            accepted=any(x['event']=='ACCEPT' for x in items),arrival_us=next((x['time_us'] for x in items if x['event']=='ACCEPT'),''),
            initial_queue=next((x['queue_ahead'] for x in items if x['event']=='ACCEPT'),''),
            cancel_request_us=next((x['time_us'] for x in items if x['event']=='CANCEL_REQUEST'),''),
            cancel_reason=next((x['reason'] for x in items if x['event']=='CANCEL_REQUEST'),''),
            cancel_confirm_us=next((x['time_us'] for x in items if x['event']=='CANCEL_CONFIRM'),''),
            rejected_reason=next((x['reason'] for x in items if x['event']=='REJECT'),''),
            filled_qty=sum(r['qty'] for r in frows),fill_events=len(frows)))
    key_orders.sort(key=lambda x:int(x['submit_us']))
    write('key_market_orders.csv',key_orders,list(key_orders[0]))
    opposite_order=next(r for r in key_orders if r['side']=='YES' and int(r['submit_us'])>fill_at)
    live_public_trades=public_trades_between(cover[KEY],int(opposite_order['arrival_us']),int(opposite_order['cancel_confirm_us']))
    opposite=[r for r in decisions if r['time_us']>fill_at and r['state']=='valid' and r['needed_side']=='YES']
    legal=[r for r in opposite if r['risk_gate_pass'] and r['cash_gate_pass'] and r['order_free']]
    last_valid=[r for r in decisions if r['time_us']>fill_at and r['state']=='valid']
    last_t=int(cover[KEY]['last_valid_us'])
    last_raw=causal[KEY].get(last_t,[])
    assert len(last_raw)==1 and last_raw[0]['state']=='valid'
    last_actionable=dict(time_us=last_t,time_utc=timestamp(last_t),p=last_raw[0]['p'],target=last_raw[0]['target'],
        actual_net=-fill_qty,cash=float(fill_ledger['cash']),reserved=0.0,stop_metric=after_metric,
        yes_bid=last_raw[0]['yes_bid'],yes_ask=last_raw[0]['yes_ask'],no_bid=last_raw[0]['no_bid'],no_ask=last_raw[0]['no_ask'],
        cutoff_us=int(cover[KEY]['end_us'])-CUT)
    summary=dict(fill_events=len(all_fills),valid_fill_states=sum(x['fill_book_state']=='valid' for x in all_fills),
        error_decrease=sum(x['error_movement']=='decrease' for x in all_fills),error_increase=sum(x['error_movement']=='increase' for x in all_fills),
        error_unchanged=sum(x['error_movement']=='unchanged' for x in all_fills),error_unknown=sum(x['error_movement']=='UNKNOWN' for x in all_fills),
        direction_mismatch=sum(x['direction_matches_need'] is False for x in all_fills),
        cancel_pending_fills=sum(x['cancel_pending'] for x in all_fills),cancel_pending_qty=sum(x['qty'] for x in all_fills if x['cancel_pending']),
        cancel_pending_exchange_before_request=sum(x['cancel_pending'] and int(x['exchange_at_us'])<int(x['cancel_request_us']) for x in all_fills),
        cancel_pending_exchange_before_request_qty=sum(x['qty'] for x in all_fills if x['cancel_pending'] and int(x['exchange_at_us'])<int(x['cancel_request_us'])),
        key_market=KEY,key_fill_us=fill_at,key_fill_p=fill_key['fill_p'],key_fill_target=fill_key['fill_target'],
        key_fill_error_before=fill_key['error_before'],key_fill_error_after=fill_key['error_after'],
        key_opposite_valid_observations=len(opposite),key_opposite_legal_free_observations=len(legal),
        key_first_opposite_valid_us=opposite[0]['time_us'] if opposite else None,
        key_first_legal_free_us=legal[0]['time_us'] if legal else None,
        key_last_legal_free=legal[-1] if legal else None,key_last_valid_book_change=last_valid[-1] if last_valid else None,
        key_last_valid_simulator_observation=last_actionable,
        key_orders_after_fill=sum(int(x['submit_us'])>fill_at for x in key_orders),
        key_opposite_orders_after_fill=sum(x['side']=='YES' and int(x['submit_us'])>fill_at for x in key_orders),
        key_opposite_live_public_trades=len(live_public_trades),
        positive_control=CONTROL)
    (OUT/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps({k:v for k,v in summary.items() if not isinstance(v,dict)},indent=2))

if __name__=='__main__':main()

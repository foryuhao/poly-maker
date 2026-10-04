"""Read-only M0B audit. Reproduce with: python3 research_tools/dpm0_fill_audit.py.

The strict proxy is unchanged from dpm0_pilot.py: local-available public SELL
on the selected token at/below the bid or BUY on its complement at/above 1-bid,
before the first selected-token best-bid change, within five seconds of ACK.
"""
from __future__ import annotations
import argparse
import csv
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

H = 5_000_000
EPS = 1e-8

def stamp(us):
    return datetime.fromtimestamp(us / 1e6, timezone.utc).isoformat(timespec='microseconds') if us is not None else ''

def pct(v, q):
    if not v: return None
    x = sorted(v); z = (len(x)-1)*q; i = int(z)
    return x[i] + (x[min(i+1,len(x)-1)]-x[i])*(z-i)

def top(levels, bid):
    if not levels: return (None,None)
    p = max(levels) if bid else min(levels)
    return p,levels[p]

def first(events, predicate):
    return next((e['t'] for e in events if predicate(e)),None)

def session(path):
    m=json.loads((path/'manifest.json').read_text())
    toks={m['yes_token_id']:'YES',m['no_token_id']:'NO'}
    books={tok:{'BUY':{},'SELL':{}} for tok in toks}
    requests={}; orders={}; revs=defaultdict(list); down=[]; kinds=Counter(); trade_lags_ms=[]
    for line in (path/'events.jsonl').open():
        e=json.loads(line); k=e['kind']; p=e.get('payload') or {}; t=e.get('available_at_us'); tok=p.get('token_id'); kinds[k]+=1
        if k=='CONNECTION' and p.get('stream')=='market' and p.get('state')=='DOWN': down.append(t)
        if k=='ORDER_SUBMIT': requests[p['request_id']]={'t':t,'quotes':p.get('quotes') or []}
        elif k=='ORDER_ACK' and p.get('outcome')=='ACK':
            req=requests.get(p.get('request_id'),{}); ids=p.get('order_ids') or []; quotes=req.get('quotes') or []
            for i,oid in enumerate(ids):
                q=quotes[i] if i<len(quotes) else {}; ot=q.get('token_id'); b=books.get(ot)
                bp,bq=top(b['BUY'],True) if b else (None,None)
                ap,aq=top(b['SELL'],False) if b else (None,None)
                price=float(q['price']) if q.get('price') is not None else None
                size=float(q['size']) if q.get('size') is not None else None
                pos=('missing' if None in (bp,ap,price) else 'at_bbo' if abs(price-bp)<=EPS else
                     'inside_spread' if bp<price<ap else 'behind_bbo' if price<bp else 'crossed_or_invalid')
                near=bp*bq if bp is not None else None; opp=ap*aq if ap is not None else None
                orders[oid]=dict(market=m['market_slug'],token=ot,outcome=toks.get(ot),order_id=oid,
                    submitted_us=req.get('t'),accepted_us=t,limit_price=price,requested_size=size,
                    side=q.get('side'),size_at_level_at_accept=b['BUY'].get(price) if b and price is not None else None,
                    q_near=near,q_opp=opp,imbalance=(near-opp)/(near+opp) if near is not None and opp is not None and near+opp else None,
                    best_bid=bp,best_ask=ap,position=pos,
                    market_time_remaining_s=(int(m['market_slug'].rsplit('-',1)[1])*1_000_000+300_000_000-t)/1e6,
                    first_bid_change_us=None,first_l2_reduction_us=None,first_l2_depletion_us=None,
                    first_l2_reappearance_us=None,first_bbo_cross_us=None,l2_reduction_sum=0.,
                    trades=[],public_events=[],cancel_request_us=None,cancel_ack_us=None,
                    mapping='exact' if len(ids)==len(quotes) else 'ambiguous')
        elif k=='FILL_REVISION' and p.get('trade_id') and p.get('maker_order_id'):
            revs[(p['trade_id'],p['maker_order_id'])].append(dict(t=t,exchange_t=e.get('exchange_at_us'),
                receive_t=e.get('received_at_us'),source=e.get('source'),status=p.get('status'),
                accepted=p.get('accepted'),quarantined=p.get('quarantined'),qty=float(p.get('qty') or 0),
                price=float(p.get('price') or 0),role=p.get('role'),token=tok))
        elif k in ('CANCEL_REQUEST','CANCEL_ACK'):
            field='cancel_request_us' if k=='CANCEL_REQUEST' else 'cancel_ack_us'
            ids=set(p.get('order_ids') or []); asset=p.get('token_id')
            for o in orders.values():
                if t>o['accepted_us'] and (o['order_id'] in ids or asset==o['token']) and o[field] is None:
                    o[field]=t
        if tok in books and k in ('BOOK','REST_BOOK','DELTA'):
            book=books[tok]; oldbid,_=top(book['BUY'],True)
            old={oid:book['BUY'].get(o['limit_price'],0) for oid,o in orders.items() if o['token']==tok and o['limit_price'] is not None}
            if k in ('BOOK','REST_BOOK'):
                book['BUY']={float(x[0]):float(x[1]) for x in p.get('bids') or []}
                book['SELL']={float(x[0]):float(x[1]) for x in p.get('asks') or []}
            else:
                side=p.get('side')
                if side in book:
                    px=float(p['price']); sz=float(p['size'])
                    if sz>0: book[side][px]=sz
                    else: book[side].pop(px,None)
            newbid,_=top(book['BUY'],True)
            for oid,o in orders.items():
                if o['token']!=tok or t<=o['accepted_us'] or o['limit_price'] is None: continue
                px=o['limit_price']; before=old.get(oid,0); after=book['BUY'].get(px,0)
                if abs(before-after)>EPS or newbid!=oldbid:
                    o['public_events'].append(dict(t=t,exchange_t=e.get('exchange_at_us'),
                        event_kind=k,route='L2',public_price=px,mapped_price=px,
                        size='',level_before=before,level_after=after,
                        best_bid_before=oldbid,best_bid_after=newbid))
                if before>after+EPS:
                    o['l2_reduction_sum']+=before-after
                    if o['first_l2_reduction_us'] is None: o['first_l2_reduction_us']=t
                    if after<=EPS and o['first_l2_depletion_us'] is None: o['first_l2_depletion_us']=t
                elif before<=EPS<after and o['first_l2_depletion_us'] is not None and o['first_l2_reappearance_us'] is None:
                    o['first_l2_reappearance_us']=t
                if newbid!=oldbid and o['first_bid_change_us'] is None: o['first_bid_change_us']=t
                if newbid is not None and newbid<px-EPS and o['first_bbo_cross_us'] is None: o['first_bbo_cross_us']=t
        if k=='TRADE' and e.get('received_at_us') is not None and e.get('exchange_at_us') is not None:
            trade_lags_ms.append((e['received_at_us']-e['exchange_at_us'])/1000)
        if k=='TRADE' and tok in toks and t is not None:
            side=p.get('side'); px=float(p['price']); size=float(p.get('size') or 0)
            for o in orders.values():
                if t<=o['accepted_us'] or o['limit_price'] is None: continue
                direct=tok==o['token'] and side=='SELL'
                complement=tok!=o['token'] and side=='BUY'
                if not (direct or complement): continue
                mapped=px if direct else 1-px
                if mapped<=o['limit_price']+EPS:
                    o['trades'].append(dict(t=t,exchange_t=e.get('exchange_at_us'),size=size,
                        route='direct' if direct else 'complement',public_price=px,mapped_price=mapped,
                        exact=abs(mapped-o['limit_price'])<=EPS,through=mapped<o['limit_price']-EPS))
                    o['public_events'].append(dict(t=t,exchange_t=e.get('exchange_at_us'),
                        event_kind='TRADE',route='direct' if direct else 'complement',
                        public_price=px,mapped_price=mapped,size=size,level_before='',
                        level_after='',best_bid_before='',best_bid_after=''))
    fills=[]
    for (tid,oid),rs in revs.items():
        if not rs[-1]['accepted'] or rs[-1]['quarantined']: continue
        initial=next((r for r in rs if r['status']=='MATCHED' and r['accepted']),None)
        if initial is None: initial=next((r for r in rs if r['accepted']),None)
        if initial: fills.append(dict(trade_id=tid,order_id=oid,initial=initial,final=rs[-1],revisions=len(rs)))
    fills.sort(key=lambda f:(f['initial']['t'],f['trade_id']))
    grouped=defaultdict(list)
    for f in fills: grouped[f['order_id']].append(f)
    orderrows=[]; fillrows=[]; publicrows=[]
    for oid,o in orders.items():
        fs=grouped.get(oid,[]); total=sum(f['initial']['qty'] for f in fs); req=o['requested_size']
        outcome=('full_fill' if req is not None and total>=req-1e-6 else
            'partial_fill_canceled' if total>0 and o['cancel_ack_us'] else
            'partial_fill_unresolved' if total>0 else
            'canceled_no_fill' if o['cancel_ack_us'] else 'no_fill_terminal_unknown')
        tr=o['trades']; ack=o['accepted_us']; stop=o['first_bid_change_us'] or 2**63
        first_fill_us=min((f['initial']['t'] for f in fs),default=None)
        end_us=max((f['initial']['t'] for f in fs),default=o['cancel_ack_us'])
        for event in o['public_events']:
            if end_us is not None and event['t']>end_us: continue
            publicrows.append(dict(market=o['market'],order_id=oid,outcome=o['outcome'],
                event_kind=event['event_kind'],route=event['route'],
                available_time=stamp(event['t']),exchange_time=stamp(event['exchange_t']),
                public_price=event['public_price'],mapped_price=event['mapped_price'],
                size=event['size'],level_before=event['level_before'],level_after=event['level_after'],
                best_bid_before=event['best_bid_before'],best_bid_after=event['best_bid_after'],
                before_or_at_first_fill=first_fill_us is not None and event['t']<=first_fill_us))
        strict_event=next((x for x in tr if x['t']<=min(ack+H,stop)),None)
        strict=strict_event['t'] if strict_event else None
        same=first(tr,lambda x:x['exact']); through=first(tr,lambda x:x['through'])
        direct=first(tr,lambda x:x['route']=='direct'); comp=first(tr,lambda x:x['route']=='complement')
        same5=same is not None and same<=ack+H; through5=through is not None and through<=ack+H
        dep5=o['first_l2_depletion_us'] is not None and o['first_l2_depletion_us']<=ack+H
        orderrows.append(dict(market=o['market'],outcome_token=o['outcome'],order_id=oid,
            submitted_time=stamp(o['submitted_us']),accepted_time=stamp(ack),limit_price=o['limit_price'],
            requested_size=req,fill_event_count=len(fs),filled_size=round(total,8),final_order_outcome=outcome,
            cancel_ack_time=stamp(o['cancel_ack_us']),position_at_accept=o['position'],
            strict_proxy_5s=strict is not None,strict_proxy_time=stamp(strict),
            strict_proxy_exchange_time=stamp(strict_event['exchange_t']) if strict_event else '',
            strict_proxy_route=strict_event['route'] if strict_event else '',same_level_5s=same5,
            trade_through_5s=through5,l2_depletion_5s=dep5,ack_quote_mapping=o['mapping']))
        cumulative=0.
        for f in fs:
            r=f['initial']; ft=r['t']; cumulative+=r['qty']; delta=(strict-ft)/1e6 if strict is not None else None
            if strict is None:
                reason=('G: not at BBO at ACK' if o['position']!='at_bbo' else
                    'H: bid moved before qualifying print' if o['first_bid_change_us'] and o['first_bid_change_us']<ft else
                    'A/J: no qualifying public print observed' if not tr else 'J: print outside strict five-second window')
            elif delta>0: reason='F/H: public signal after own fill'
            else: reason=''
            if any(ack<x<=ft for x in down): reason=(reason+'; I: market WS disconnect').strip('; ')
            fillrows.append(dict(market=o['market'],token=o['token'],outcome=o['outcome'],order_id=oid,trade_id=f['trade_id'],
                role=r['role'],side=o['side'],submitted_time=stamp(o['submitted_us']),accepted_time=stamp(ack),
                limit_price=o['limit_price'],requested_size=req,size_at_level_at_accept=o['size_at_level_at_accept'],
                q_near=o['q_near'],q_opp=o['q_opp'],imbalance=o['imbalance'],best_bid=o['best_bid'],best_ask=o['best_ask'],
                position_at_accept=o['position'],market_time_remaining_s=o['market_time_remaining_s'],
                actual_fill_exchange_time=stamp(r['exchange_t']),actual_fill_receive_time=stamp(r['receive_t']),
                actual_fill_time=stamp(ft),actual_fill_size=r['qty'],actual_fill_price=r['price'],
                partial_or_full='full' if req is not None and cumulative>=req-1e-6 else 'partial',
                final_order_outcome=outcome,cancel_time=stamp(o['cancel_ack_us']),
                first_same_level_trade_time=stamp(same),first_trade_through_time=stamp(through),
                first_same_token_sell_time=stamp(direct),first_complement_buy_time=stamp(comp),
                first_l2_reduction_time=stamp(o['first_l2_reduction_us']),
                first_l2_depletion_time=stamp(o['first_l2_depletion_us']),
                first_l2_reappearance_time=stamp(o['first_l2_reappearance_us']),
                first_bbo_cross_time=stamp(o['first_bbo_cross_us']),first_bid_change_time=stamp(o['first_bid_change_us']),
                l2_reduction_sum_raw_shares=round(o['l2_reduction_sum'],8),
                public_direct_size_before_fill_raw=round(sum(x['size'] for x in tr if x['t']<=ft and x['route']=='direct'),8),
                public_complement_size_before_fill_raw=round(sum(x['size'] for x in tr if x['t']<=ft and x['route']=='complement'),8),
                strict_proxy_time=stamp(strict),
                strict_proxy_exchange_time=stamp(strict_event['exchange_t']) if strict_event else '',
                strict_proxy_route=strict_event['route'] if strict_event else '',
                strict_proxy_detected=strict is not None,
                strict_proxy_before_or_at_fill=strict is not None and strict<=ft,
                delta_t_proxy_vs_fill_s=round(delta,6) if delta is not None else '',
                same_level_5s=same5,trade_through_5s=through5,l2_depletion_5s=dep5,
                notes=reason,fill_revision_count=f['revisions'],fill_initial_source=r['source'],
                fill_final_status=f['final']['status'],ack_quote_mapping=o['mapping']))
    unmatched=[f['order_id'] for f in fills if f['order_id'] not in orders]
    audit=dict(market=m['market_slug'],acked_orders=len(orders),accepted_fill_keys=len(fills),
        unmatched_fill_keys=len(unmatched),unmatched_order_ids=unmatched,market_ws_down_count=len(down),
        manifest_gap_count=m['gap_count'],manifest_lost_event_count=m['lost_event_count'],
        public_trade_receive_minus_exchange_ms_median=median(trade_lags_ms) if trade_lags_ms else None,
        event_counts=dict(kinds))
    return orderrows,fillrows,publicrows,audit

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,default=Path('research_data/m4a'))
    parser.add_argument('--output',type=Path,default=Path('research_outputs/dpm0b'))
    args=parser.parse_args(); orders=[]; fills=[]; public=[]; audits=[]
    for path in sorted(args.input.iterdir()):
        if not path.is_dir() or not (path/'manifest.json').exists(): continue
        m=json.loads((path/'manifest.json').read_text())
        if m['close_status']!='COMPLETE' or m['gap_count'] or m['lost_event_count'] or m['persisted_count']<50_000: continue
        o,f,p,a=session(path); orders.extend(o); fills.extend(f); public.extend(p); audits.append(a)
    orders.sort(key=lambda r:(r['market'],r['accepted_time'],r['order_id']))
    fills.sort(key=lambda r:(r['market'],r['actual_fill_time'],r['trade_id']))
    public.sort(key=lambda r:(r['market'],r['available_time'],r['order_id']))
    args.output.mkdir(parents=True,exist_ok=True)
    for name,rows in [('orders.csv',orders),('fills.csv',fills),('public_events.csv',public)]:
        if rows:
            with (args.output/name).open('w',newline='') as stream:
                writer=csv.DictWriter(stream,fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    deltas=[float(r['delta_t_proxy_vs_fill_s']) for r in fills if r['delta_t_proxy_vs_fill_s']!='']
    filled_ids={r['order_id'] for r in fills}
    eligible=[r for r in orders if r['position_at_accept']=='at_bbo' and r['final_order_outcome'] in ('full_fill','partial_fill_canceled','canceled_no_fill')]
    cm=Counter()
    for r in eligible:
        actual=r['order_id'] in filled_ids; proxy=r['strict_proxy_5s']
        cm['TP' if actual and proxy else 'FN' if actual else 'FP' if proxy else 'TN']+=1
    proxy_cms={}
    for label in ('same_level_5s','trade_through_5s','l2_depletion_5s'):
        counts=Counter()
        for r in eligible:
            actual=r['order_id'] in filled_ids; proxy=r[label]
            counts['TP' if actual and proxy else 'FN' if actual else 'FP' if proxy else 'TN']+=1
        proxy_cms[label]=dict(counts)
    fill_lags_ms=[]
    for r in fills:
        if r['actual_fill_exchange_time'] and r['actual_fill_receive_time']:
            fill_lags_ms.append((datetime.fromisoformat(r['actual_fill_receive_time'])-
                                 datetime.fromisoformat(r['actual_fill_exchange_time'])).total_seconds()*1000)
    ack_lags_ms=[]
    for r in orders:
        if r['submitted_time'] and r['accepted_time']:
            ack_lags_ms.append((datetime.fromisoformat(r['accepted_time'])-
                                datetime.fromisoformat(r['submitted_time'])).total_seconds()*1000)
    n=len(fills); hits=sum(r['strict_proxy_detected'] for r in fills)
    summary=dict(scope='20 long COMPLETE zero-gap M4A sessions; local persisted official Polymarket data',
        clock_for_delta='public available_at_us minus first accepted own MATCHED available_at_us',
        strict_proxy_horizon_s=5,markets=len({r['market'] for r in orders}),acked_orders=len(orders),
        accepted_fill_events=n,filled_orders=len(filled_ids),fill_markets=len({r['market'] for r in fills}),
        yes_fill_events=sum(r['outcome']=='YES' for r in fills),no_fill_events=sum(r['outcome']=='NO' for r in fills),
        partial_fill_events=sum(r['partial_or_full']=='partial' for r in fills),
        full_fill_events=sum(r['partial_or_full']=='full' for r in fills),
        strict_detected_fill_events=hits,strict_missed_fill_events=n-hits,
        strict_detection_rate=hits/n if n else None,
        delta_t_s=dict(p25=pct(deltas,.25),median=median(deltas) if deltas else None,p75=pct(deltas,.75),
                       min=min(deltas) if deltas else None,max=max(deltas) if deltas else None),
        strict_before_or_at_fill=sum(r['strict_proxy_before_or_at_fill'] for r in fills),
        same_level_5s_detected_fill_events=sum(r['same_level_5s'] for r in fills),
        trade_through_5s_detected_fill_events=sum(r['trade_through_5s'] for r in fills),
        l2_depletion_5s_detected_fill_events=sum(r['l2_depletion_5s'] for r in fills),
        fill_receive_minus_exchange_ms=dict(min=min(fill_lags_ms) if fill_lags_ms else None,
            median=median(fill_lags_ms) if fill_lags_ms else None,max=max(fill_lags_ms) if fill_lags_ms else None),
        submit_to_ack_available_ms_median=median(ack_lags_ms) if ack_lags_ms else None,
        public_trade_lag_ms_market_medians=[a['public_trade_receive_minus_exchange_ms_median'] for a in audits
                                             if a['public_trade_receive_minus_exchange_ms_median'] is not None],
        eligible_at_bbo_terminal_orders=len(eligible),confusion_order_level=dict(cm),
        comparison_proxy_confusion_order_level=proxy_cms,
        precision_order_level=cm['TP']/(cm['TP']+cm['FP']) if cm['TP']+cm['FP'] else None,
        recall_order_level=cm['TP']/(cm['TP']+cm['FN']) if cm['TP']+cm['FN'] else None,
        specificity_order_level=cm['TN']/(cm['TN']+cm['FP']) if cm['TN']+cm['FP'] else None,
        false_positive_rate_order_level=cm['FP']/(cm['FP']+cm['TN']) if cm['FP']+cm['TN'] else None,
        unmatched_fill_keys=sum(a['unmatched_fill_keys'] for a in audits),audits=audits)
    summary['relevant_public_event_rows']=len(public)
    (args.output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps({k:v for k,v in summary.items() if k!='audits'},indent=2))

if __name__=='__main__': main()

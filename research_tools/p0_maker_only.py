"""Independent P0 C-M maker-only conditional replay; local persisted inputs only.

Run from repository root: python3 research_tools/p0_maker_only.py
"""
from __future__ import annotations
import csv
import json
import math
from collections import Counter
from pathlib import Path
import p0_probability_inventory as base

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'research_outputs/p0_maker_only'
P0=ROOT/'research_outputs/p0_probability_inventory'
DATA=ROOT/'research_data/m4a'
PARAM={'capital':100.0,'min_qty':5.0,'net_cap':25.0,'submit_us':500_000,
       'cancel_us':500_000,'fresh_us':2_000_000,'cutoff_us':15_000_000,
       'merge_us':60_000_000,'redeem_us':900_000_000,'loss_stop':20.0,
       'maker_fee':0.0,'maker_rebate':0.0,'taker_rebate':0.0,
       'queue_rule':'arrival visible same-price bid ahead; same-token SELL at/through price consumes ahead then own remaining; unknown cancels never advance queue; exchange and local receipt after arrival'}


def readcsv(path):
    with path.open(newline='') as f:return list(csv.DictReader(f))


def writecsv(path,rows,fields):
    with path.open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(rows)


def consume_queue(ahead, remaining, trade_qty):
    """Consume one public print exactly once, ahead first and own order second."""
    used_ahead=min(ahead,trade_qty)
    next_ahead=ahead-used_ahead
    own=min(remaining,trade_qty-used_ahead)
    return next_ahead,own


def post_only_accept(price, ask):
    return ask is not None and price < ask


class MakerReplay:
    def __init__(self):
        self.cash=PARAM['capital'];self.release_queue=[];self.conservative=0.0
        self.final_pnl=0.0;self.stopped=False;self.stop_at=None;self.stop_market=None
        self.stop_metric_at_trigger=None
        self.orders=[];self.ledger=[];self.markets=[];self.cover=[];self.next_id=0

    def release(self,t):
        remain=[]
        for due,amt,slug,kind in self.release_queue:
            if due<=t:
                self.cash+=amt
                if kind=='REDEEM_RELEASE':self.conservative+=amt
                self.ledger.append(dict(market=slug,time_us=due,event=kind,order_id='',side='',qty=amt,price='',gross='',fee='',p='',target='',actual_net='',queue_before='',queue_after='',trade_qty='',cash=self.cash,reserved='',stop_metric=self.conservative,realized_pair_pnl='',reason='confirmed'))
            else:remain.append((due,amt,slug,kind))
        self.release_queue=remain

    def market(self,sample,label,source):
        slug=sample['market'];start=int(sample['start_us']);end=int(sample['end_us']);cutoff=end-PARAM['cutoff_us']
        manifest=json.loads((DATA/sample['session']/'manifest.json').read_text())
        if (label['condition_id'].lower()!=manifest['condition_id'].lower() or label['yes_token_id']!=manifest['yes_token_id']
                or label['no_token_id']!=manifest['no_token_id'] or label['winner'] not in ('YES','NO')):raise ValueError('label identity '+slug)
        if slug in source:
            g=source[slug]['gamma'];c=source[slug]['clob'];fs=g.get('feeSchedule') or {}
            if (g.get('conditionId','').lower()!=manifest['condition_id'].lower() or
                json.loads(g.get('clobTokenIds','[]'))!=[manifest['yes_token_id'],manifest['no_token_id']] or
                c.get('minimum_order_size')!=5 or fs.get('takerOnly') is not True):raise ValueError('market metadata identity '+slug)
        self.release(start)
        books={'YES':base.Book(),'NO':base.Book()};token={manifest['yes_token_id']:'YES',manifest['no_token_id']:'NO'}
        inv={'YES':0.0,'NO':0.0};cost={'YES':0.0,'NO':0.0}
        gross=fees=merge_income=0.0
        order=None;reason=Counter();submitted=accepted=rejected=canceled=filled_orders=0
        partial_events=cancel_race_events=cancel_race_qty=fill_qty=fill_events=0
        switches=0;last_fill_side=None;cash_skips=0;min_waits=0;max_occupancy=0.0
        target_obs=err_sum=err_max=0.0;last_p=None;last_target=None
        min_stop_metric=math.inf
        now=start;cutoff_seen=False;stop_here=self.stopped
        first_valid=None;last_valid=None;last_event=None;timestamp_inversions=0
        order_touched=set()

        def actual_net():return inv['YES']-inv['NO']
        def reserved():return (order['remaining']*order['price']/10000) if order else 0.0
        def available():return self.cash-reserved()
        def potential_net():
            if not order:return actual_net()
            return actual_net()+(order['remaining'] if order['side']=='YES' else -order['remaining'])
        def conserv():return self.conservative+merge_income-gross-fees
        def log(t,kind,o=None,qty='',price='',fee='',reason_text='',queue_before='',queue_after='',trade_qty=''):
            nonlocal min_stop_metric
            metric=conserv();min_stop_metric=min(min_stop_metric,metric)
            realized_pair=merge_income-(gross+fees-sum(cost.values()))
            self.ledger.append(dict(market=slug,time_us=t,event=kind,order_id=o['id'] if o else '',side=o['side'] if o else '',
                qty=qty,price=price,gross=(float(qty)*float(price) if qty!='' and price!='' and kind=='FILL' else ''),fee=fee,
                p=last_p if last_p is not None else '',target=last_target if last_target is not None else '',actual_net=actual_net(),
                queue_before=queue_before,queue_after=queue_after,trade_qty=trade_qty,cash=self.cash,reserved=reserved(),
                stop_metric=metric,realized_pair_pnl=realized_pair,reason=reason_text))
        def snapshot_order(t,kind,o,why=''):
            self.orders.append(dict(market=slug,order_id=o['id'],event=kind,time_us=t,side=o['side'],qty=o['qty'],remaining=o['remaining'],
                price=o['price']/10000,submit_us=o['submitted'],arrival_us=o['arrival'],cancel_request_us=o.get('cancel_request',''),
                cancel_confirm_us=o.get('cancel_due',''),queue_ahead=o.get('ahead',''),p=o['p'],target=o['target'],actual_net=actual_net(),
                cash=self.cash,reserved=reserved(),reason=why))
        def book_ok(t):
            if not all(b.valid() for b in books.values()):return False,'invalid_book'
            if any(b.time is None or t-b.time>PARAM['fresh_us'] for b in books.values()):return False,'stale_book'
            return True,''
        def update_target(t):
            nonlocal last_p,last_target,first_valid,last_valid,target_obs,err_sum,err_max
            ok,why=book_ok(t)
            if not ok:return False,why
            p=base.price_prob(books)
            if not 0<p<1:return False,'probability_bounds'
            last_p=p;last_target=max(-PARAM['net_cap'],min(PARAM['net_cap'],50*(p-.5)))
            first_valid=first_valid or t;last_valid=t
            err=abs(last_target-actual_net());target_obs+=1;err_sum+=err;err_max=max(err_max,err)
            return True,''
        def merge(t):
            nonlocal merge_income
            q=min(inv.values())
            if q<=1e-9:return
            for side in ('YES','NO'):
                avg=cost[side]/inv[side] if inv[side] else 0
                cost[side]-=q*avg;inv[side]-=q
            merge_income+=q
            self.release_queue.append((t+PARAM['merge_us'],q,slug,'MERGE_RELEASE'))
            log(t,'MERGE',qty=q,price=1,reason_text='confirmed inventory; release pending')
        def submit(t):
            nonlocal order,submitted,cash_skips,min_waits,max_occupancy,stop_here
            if order is not None or t+PARAM['submit_us']>=cutoff:return
            ok,why=update_target(t)
            if not ok:reason[why]+=1;return
            if self.stopped:stop_here=True;reason['global_stop']+=1;return
            if conserv()<=-PARAM['loss_stop']+1e-8:
                self.stopped=True;self.stop_at=t;self.stop_market=slug;self.stop_metric_at_trigger=conserv()
                stop_here=True;reason['loss_stop']+=1;return
            delta=last_target-actual_net()
            if abs(delta)<PARAM['min_qty']-1e-8:min_waits+=1;return
            side='YES' if delta>0 else 'NO';qty=abs(delta)
            b=books[side];tick=b.tick
            if not tick or tick<=0:reason['tick_unknown']+=1;return
            px=(b.bid//tick)*tick
            if px<=0 or px>=10000 or px>=b.ask:reason['illegal_or_crossing_quote']+=1;return
            projected=actual_net()+(qty if side=='YES' else -qty)
            if abs(projected)>PARAM['net_cap']+1e-8:reason['net_cap']+=1;return
            need=qty*px/10000
            if need>self.cash+1e-8:cash_skips+=1;reason['cash']+=1;return
            # The full pending fill is an exposure, even though the order may not fill.
            if conserv()-need<=-PARAM['loss_stop']+1e-8:
                reason['potential_stop_risk']+=1;return
            self.next_id+=1;submitted+=1
            order=dict(id=f'CM{self.next_id:05d}',side=side,qty=qty,remaining=qty,price=px,
                submitted=t,arrival=t+PARAM['submit_us'],arrived=False,status='SUBMITTING',ahead=None,p=last_p,target=last_target)
            max_occupancy=max(max_occupancy,gross-resume_income()+reserved())
            snapshot_order(t,'SUBMIT',order);log(t,'SUBMIT',order,qty,px/10000,reason_text='GTC post-only')
        def resume_income():
            return sum(float(x['qty']) for x in self.ledger if x['market']==slug and x['event']=='MERGE_RELEASE')
        def request_cancel(t,why):
            nonlocal order
            if order is None or order.get('cancel_due') is not None:return
            order['cancel_request']=t;order['cancel_due']=t+PARAM['cancel_us'];order['status']='CANCELING'
            snapshot_order(t,'CANCEL_REQUEST',order,why);log(t,'CANCEL_REQUEST',order,order['remaining'],order['price']/10000,reason_text=why)
        def check_order(t):
            nonlocal stop_here
            if order is None:return
            if t>=cutoff:request_cancel(t,'last_15_seconds');return
            ok,why=book_ok(t)
            if not ok:request_cancel(t,why);return
            update_target(t)
            if self.stopped:stop_here=True;request_cancel(t,'global_stop');return
            if conserv()<=-PARAM['loss_stop']+1e-8:
                self.stopped=True;self.stop_at=t;self.stop_market=slug;self.stop_metric_at_trigger=conserv()
                stop_here=True;request_cancel(t,'loss_stop');return
            b=books[order['side']];tick=b.tick or 0
            if tick and abs(b.bid-order['price'])>=tick:request_cancel(t,'best_bid_moved');return
            delta=last_target-actual_net()
            if (delta>=0 and order['side']=='NO') or (delta<=0 and order['side']=='YES'):
                request_cancel(t,'direction_changed');return
            if abs(potential_net())>PARAM['net_cap']+1e-8:request_cancel(t,'potential_net_cap');return
            if reserved()>self.cash+1e-8:request_cancel(t,'reserve_exceeds_cash');return
            if conserv()-reserved()<=-PARAM['loss_stop']+1e-8:request_cancel(t,'potential_stop_risk')
        def maker_fill(t,e):
            nonlocal order,gross,fees,fill_qty,fill_events,partial_events,cancel_race_events,cancel_race_qty,last_fill_side,switches,max_occupancy,filled_orders
            if order is None or order['status'] not in ('ACTIVE','CANCELING') or order.get('ahead') is None:return
            p=e.get('payload') or {};side=token.get(p.get('token_id'))
            if side!=order['side'] or p.get('side')!='SELL':return
            trade_px=round(float(p.get('price',2))*10000);trade_qty=float(p.get('size') or 0)
            if trade_px>order['price'] or trade_qty<=0:return
            # Require all three recorded clocks to be no earlier than activation.
            if any(e.get(k) is None or e[k]<order['arrival'] for k in ('exchange_at_us','received_at_us','available_at_us')):return
            before=order['ahead'];order['ahead'],qty=consume_queue(before,order['remaining'],trade_qty)
            if qty>1e-9:
                before_remaining=order['remaining'];order['remaining']-=qty
                paid=qty*order['price']/10000;self.cash-=paid;gross+=paid;fill_qty+=qty;fill_events+=1
                inv[side]+=qty;cost[side]+=paid
                if last_fill_side and last_fill_side!=side:switches+=1
                last_fill_side=side;order_touched.add(order['id'])
                if qty<before_remaining-1e-8:partial_events+=1
                if order['status']=='CANCELING':cancel_race_events+=1;cancel_race_qty+=qty
                snapshot_order(t,'PARTIAL_FILL' if order['remaining']>1e-9 else 'FILL',order,'public SELL volume after visible queue')
                log(t,'FILL',order,qty,order['price']/10000,fee=0.0,reason_text='same-token SELL; conditional queue',
                    queue_before=before,queue_after=order['ahead'],trade_qty=trade_qty)
                merge(t)
                max_occupancy=max(max_occupancy,gross-resume_income()+reserved())
                if order['remaining']<=1e-9:
                    filled_orders+=1;order=None
                if conserv()<=-PARAM['loss_stop']+1e-8 and not self.stopped:
                    self.stopped=True;self.stop_at=t;self.stop_market=slug;self.stop_metric_at_trigger=conserv()
            else:
                # Volume consumed ahead only. It may never be applied again.
                if before>order['ahead']:reason['queue_ahead_consumed']+=1
        def timer(t):
            nonlocal order,accepted,rejected,canceled,cutoff_seen,stop_here
            self.release(t)
            if not cutoff_seen and t>=cutoff:
                cutoff_seen=True
                if order:request_cancel(cutoff,'last_15_seconds')
            if order and not order['arrived'] and order['arrival']<=t:
                at=order['arrival'];ok,why=book_ok(at)
                if not ok:
                    rejected+=1;snapshot_order(at,'REJECT',order,why);log(at,'REJECT',order,order['remaining'],order['price']/10000,reason_text=why);order=None
                elif not post_only_accept(order['price'],books[order['side']].ask):
                    rejected+=1;snapshot_order(at,'REJECT',order,'post_only_crosses_ask');log(at,'REJECT',order,order['remaining'],order['price']/10000,reason_text='post_only_crosses_ask');order=None
                else:
                    order['ahead']=books[order['side']].bids.get(order['price'],0.0)
                    order['arrived']=True
                    if order['status']!='CANCELING':order['status']='ACTIVE'
                    accepted+=1
                    snapshot_order(at,'ACCEPT',order,'visible queue ahead');log(at,'ACCEPT',order,order['remaining'],order['price']/10000,reason_text='queue_ahead='+str(order['ahead']))
            if order and order.get('cancel_due') is not None and order['cancel_due']<=t:
                at=order['cancel_due'];canceled+=1;snapshot_order(at,'CANCEL_CONFIRM',order,'remaining canceled')
                log(at,'CANCEL_CONFIRM',order,order['remaining'],order['price']/10000,reason_text='remaining canceled')
                order=None
            if order:check_order(t)
            elif t<cutoff:submit(t)
        def advance(t):
            nonlocal now
            # Timers are processed before a public event at the same local time.
            while True:
                dues=[]
                if order and not order['arrived']:dues.append(order['arrival'])
                if order and order.get('cancel_due') is not None:dues.append(order['cancel_due'])
                if order and order.get('cancel_due') is None and all(b.time is not None for b in books.values()):
                    dues.append(min(b.time for b in books.values())+PARAM['fresh_us']+1)
                if not cutoff_seen:dues.append(cutoff)
                if not dues or min(dues)>t:break
                due=min(dues)
                if due<now:raise AssertionError('timer reversal')
                now=due;timer(due)
            now=t;self.release(t)

        with (DATA/sample['session']/'events.jsonl').open() as f:
            for line in f:
                e=json.loads(line);t=e.get('available_at_us')
                if t is None:continue
                if last_event is not None and t<last_event:timestamp_inversions+=1
                last_event=t
                if t<start:
                    if e['kind'] in ('BOOK','REST_BOOK','DELTA'):
                        s=token.get((e.get('payload') or {}).get('token_id'))
                        if s:books[s].update(e['kind'],e['payload'],t)
                    elif e['kind']=='TICK':
                        s=token.get((e.get('payload') or {}).get('token_id'))
                        if s and e['payload'].get('tick_size'):books[s].tick=round(float(e['payload']['tick_size'])*10000)
                    continue
                if t>=end:break
                advance(t)
                kind=e['kind'];p=e.get('payload') or {}
                if kind in ('BOOK','REST_BOOK','DELTA'):
                    s=token.get(p.get('token_id'))
                    if s:
                        changed=books[s].update(kind,p,t)
                        if changed:
                            if order:check_order(t)
                            elif t<cutoff:submit(t)
                elif kind=='TICK':
                    s=token.get(p.get('token_id'))
                    if s and p.get('tick_size'):
                        books[s].tick=round(float(p['tick_size'])*10000)
                        if order:check_order(t)
                elif kind=='TRADE':
                    maker_fill(t,e)
                    if order:check_order(t)
                    elif t<cutoff:submit(t)
        advance(end)
        if order:
            # Capture closes after market end; a cancel must resolve in the model.
            request_cancel(end,'market_end');advance(end+PARAM['cancel_us'])
        residual_income=inv[label['winner']];residual_cost=sum(cost.values())
        pair_pnl=merge_income-(gross+fees-residual_cost)
        residual_pnl=residual_income-residual_cost
        pnl=merge_income+residual_income-gross-fees
        if abs(pair_pnl+residual_pnl-pnl)>1e-6:raise AssertionError('attribution mismatch')
        self.release_queue.append((end+PARAM['redeem_us'],residual_income,slug,'REDEEM_RELEASE'))
        self.release(end)
        log(end,'SETTLEMENT',qty=residual_income,price=1,reason_text=label['winner'])
        self.conservative+=merge_income-gross-fees;self.final_pnl+=pnl
        if self.conservative<=-PARAM['loss_stop']+1e-8 and not self.stopped:
            self.stopped=True;self.stop_at=end;self.stop_market=slug;self.stop_metric_at_trigger=self.conservative
            stop_here=True
        self.markets.append(dict(market=slug,winner=label['winner'],pnl=pnl,final_settlement_pnl=pnl,
            realized_pair_pnl=pair_pnl,pair_pnl=pair_pnl,residual_pnl=residual_pnl,min_conservative_stop_metric=min_stop_metric,
            gross_spent=gross,fees=fees,merge_income=merge_income,residual_income=residual_income,
            yes_residual=inv['YES'],no_residual=inv['NO'],final_net=actual_net(),buy_qty=fill_qty,
            submitted=submitted,accepted=accepted,rejected=rejected,canceled=canceled,filled_orders=filled_orders,
            fill_events=fill_events,partial_events=partial_events,cancel_race_events=cancel_race_events,cancel_race_qty=cancel_race_qty,
            cash_skips=cash_skips,min_waits=min_waits,tracking_obs=target_obs,mean_abs_tracking_error=(err_sum/target_obs if target_obs else None),
            max_abs_tracking_error=err_max,peak_capital_occupancy=max_occupancy,stopped=stop_here or self.stopped,
            zero_trade_reason=('global_stop' if fill_qty==0 and self.stopped else 'no_fill' if fill_qty==0 else ''),
            metadata='historical' if slug in source else 'assumed_maker_zero_min5',reasons=json.dumps(reason,sort_keys=True)))
        self.cover.append(dict(market=slug,session=sample['session'],condition_id=manifest['condition_id'],
            yes_token_id=manifest['yes_token_id'],no_token_id=manifest['no_token_id'],start_us=start,end_us=end,
            capture_start_us=manifest['started_at_us'],capture_end_us=manifest['ended_at_us'],winner=label['winner'],
            grade='A_conditional' if sample['grade']=='A_conditional' else sample['grade'],
            metadata='historical' if slug in source else 'maker_fee_min_assumed',first_valid_us=first_valid,last_valid_us=last_valid,
            timestamp_inversions=timestamp_inversions,reasons=json.dumps(reason,sort_keys=True)))


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    sample=readcsv(P0/'sample_coverage.csv')
    audited=sorted([r for r in readcsv(base.AUDIT) if r['execution_replay_usable']=='True'],key=lambda r:int(r['market'].split('-')[-1]))[:30]
    if [x['market'] for x in sample]!=[x['market'] for x in audited]:raise AssertionError('P0 sample mismatch')
    labels={r['market']:r for r in readcsv(base.LABELS)}
    source=json.loads(base.SOURCES.read_text());sim=MakerReplay()
    for row in sample:sim.market(row,labels[row['market']],source)
    sim.release(int(sample[-1]['end_us'])+PARAM['redeem_us']+PARAM['merge_us'])
    m=sim.markets
    summary=dict(markets=len(m),traded_markets=sum(x['buy_qty']>0 for x in m),zero_trade_markets=sum(x['buy_qty']==0 for x in m),
        stopped_markets=sum(x['stopped'] for x in m),stop_market=sim.stop_market,stop_at_us=sim.stop_at,
        stop_metric_at_trigger=sim.stop_metric_at_trigger,min_conservative_stop_metric=min(x['min_conservative_stop_metric'] for x in m),
        pnl=sum(x['pnl'] for x in m),pair_pnl=sum(x['pair_pnl'] for x in m),residual_pnl=sum(x['residual_pnl'] for x in m),
        fees=sum(x['fees'] for x in m),worst_market=min(x['pnl'] for x in m),
        submitted=sum(x['submitted'] for x in m),accepted=sum(x['accepted'] for x in m),rejected=sum(x['rejected'] for x in m),
        canceled=sum(x['canceled'] for x in m),filled_orders=sum(x['filled_orders'] for x in m),
        fill_events=sum(x['fill_events'] for x in m),partial_events=sum(x['partial_events'] for x in m),
        orders_with_any_fill=len({x['order_id'] for x in sim.ledger if x['event']=='FILL'}),
        cancel_race_events=sum(x['cancel_race_events'] for x in m),cancel_race_qty=sum(x['cancel_race_qty'] for x in m),
        buy_qty=sum(x['buy_qty'] for x in m),peak_capital_occupancy=max(x['peak_capital_occupancy'] for x in m),
        cash_skips=sum(x['cash_skips'] for x in m),min_waits=sum(x['min_waits'] for x in m),
        mean_market_tracking_error=sum(x['mean_abs_tracking_error'] for x in m if x['mean_abs_tracking_error'] is not None)/sum(x['mean_abs_tracking_error'] is not None for x in m),
        max_tracking_error=max(x['max_abs_tracking_error'] for x in m),final_cash=sim.cash,
        conditional_full_fill_rate=sum(x['filled_orders'] for x in m)/max(1,sum(x['accepted'] for x in m)),
        conditional_any_fill_rate=len({x['order_id'] for x in sim.ledger if x['event']=='FILL'})/max(1,sum(x['accepted'] for x in m)))
    writecsv(OUT/'sample_coverage.csv',sim.cover,list(sim.cover[0]))
    writecsv(OUT/'orders.csv',sim.orders,list(sim.orders[0]) if sim.orders else ['market','order_id','event'])
    writecsv(OUT/'ledger.csv',sim.ledger,list(sim.ledger[0]))
    writecsv(OUT/'market_results.csv',m,list(m[0]))
    (OUT/'params.json').write_text(json.dumps(PARAM,indent=2)+'\n')
    (OUT/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary,indent=2))

if __name__=='__main__':main()

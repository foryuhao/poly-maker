"""P0 probability-following BUY-only replay. Local persisted files only; no network.
Run: python3 research_tools/p0_probability_inventory.py
"""
from __future__ import annotations
import csv
import json
import math
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'research_data/m4a'
OUT = ROOT / 'research_outputs/p0_probability_inventory'
AUDIT = ROOT / 'research_outputs/execution_replay_audit_20261003/dataset_audit.csv'
LABELS = ROOT / 'research_outputs/r0_rolling_pair_replay_20261003/settlement_labels.csv'
SOURCES = ROOT / 'research_outputs/dp_backtest_01/settlement_sources.json'
PARAM = {'markets': 30, 'capital': 100.0, 'min_qty': 5.0, 'net_cap': 25.0,
         'latency_us': 500_000, 'merge_delay_us': 60_000_000,
         'redeem_delay_us': 900_000_000, 'cutoff_us': 15_000_000,
         'fresh_us': 2_000_000, 'loss_stop': 20.0, 'fee_rate': 0.07,
         'fee_exponent': 1, 'rebate': 0.0}


def rows(path):
    with path.open(newline='') as f:
        return list(csv.DictReader(f))


def csvout(name, data, fields):
    with (OUT / name).open('w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        w.writeheader(); w.writerows(data)


class Book:
    def __init__(self):
        self.bids = {}; self.asks = {}; self.bid = None; self.ask = None
        self.tick = None; self.time = None; self.seen = False

    def update(self, kind, p, t):
        before = (self.bid, self.ask)
        if kind in ('BOOK', 'REST_BOOK'):
            self.bids = {round(float(k)*10000): float(v) for k,v in p.get('bids',[]) if float(v)>0}
            self.asks = {round(float(k)*10000): float(v) for k,v in p.get('asks',[]) if float(v)>0}
            self.seen = True
            if p.get('tick_size'): self.tick = round(float(p['tick_size'])*10000)
        else:
            levels = self.bids if p.get('side') == 'BUY' else self.asks
            px = round(float(p['price'])*10000); sz = float(p['size'])
            if sz > 0: levels[px] = sz
            else: levels.pop(px,None)
        self.bid = max(self.bids,default=None); self.ask = min(self.asks,default=None)
        self.time = t
        return before != (self.bid,self.ask)

    def valid(self):
        return self.seen and self.bid is not None and self.ask is not None and self.bid < self.ask


def price_prob(book):
    y,n = book['YES'],book['NO']
    return ((y.bid+y.ask)/20000 + 1 - (n.bid+n.ask)/20000)/2


def fee(q, px):
    return q * PARAM['fee_rate'] * px * (1-px)


class Run:
    def __init__(self, mode):
        self.mode=mode; self.cash=PARAM['capital'] if mode=='C' else math.inf
        self.pending=[]; self.ledger=[]; self.market=[]; self.cover=[]
        self.total_pnl=0.0; self.stopped=False; self.peak_locked=0.0
        self.conservative_pnl=0.0
        self.stats=Counter()

    def release(self,t):
        future=[]
        for due,amt,market,kind in self.pending:
            if due<=t:
                self.cash+=amt
                if kind=='REDEEM_RELEASE':self.conservative_pnl+=amt
                self.ledger.append(dict(mode=self.mode,market=market,time_us=due,kind=kind,side='',qty=amt,price='',cost='',fee='',cash=self.cash))
            else: future.append((due,amt,market,kind))
        self.pending=future

    def book_ok(self,b,t):
        if not all(x.valid() for x in b.values()): return False,'invalid_book'
        if any(t-x.time>PARAM['fresh_us'] for x in b.values()): return False,'stale_book'
        return True,''

    def execute(self, side, qty, b, limit):
        left=qty; spent=0.0; got=0.0; fee_sum=0.0
        for px,size in sorted(b[side].asks.items()):
            if px>limit or left<=1e-9: break
            q=min(left,size); p=px/10000
            total=q*p+fee(q,p)
            if self.cash != math.inf and total>self.cash+1e-9:
                unit=p+fee(1,p); q=max(0.0,min(q,self.cash/unit))
                total=q*unit
            got+=q; spent+=q*p; fee_sum+=fee(q,p); left-=q
            if q<size and self.cash != math.inf: break
        return got,spent,fee_sum

    def market_run(self, audit, label, source):
        slug=audit['market']; start=int(slug.split('-')[-1])*1_000_000; end=start+300_000_000
        path=DATA/audit['session']; m=json.loads((path/'manifest.json').read_text())
        if (label['condition_id'].lower()!=m['condition_id'].lower() or
            label['yes_token_id']!=m['yes_token_id'] or label['no_token_id']!=m['no_token_id'] or
            label['winner'] not in ('YES','NO')): raise ValueError('settlement identity mismatch '+slug)
        if slug in source:
            gamma=source[slug]['gamma'];clob=source[slug]['clob']
            if (gamma.get('slug')!=slug or gamma.get('conditionId','').lower()!=m['condition_id'].lower()
                or json.loads(gamma.get('clobTokenIds','[]'))!=[m['yes_token_id'],m['no_token_id']]
                or clob.get('condition_id','').lower()!=m['condition_id'].lower()
                or clob.get('minimum_order_size')!=PARAM['min_qty']):
                raise ValueError('historical market metadata identity mismatch '+slug)
        self.release(start)
        book={s:Book() for s in ('YES','NO')}; tok={m['yes_token_id']:'YES',m['no_token_id']:'NO'}
        inv={'YES':0.0,'NO':0.0}; costs={'YES':0.0,'NO':0.0}; spent=fees=merge_income=0.0
        buy_qty=adjust=switch=unfilled=capital_skip=min_skip=0; last_side=None
        reasons=Counter(); pending_order=None; low_cash=self.cash; peak_locked=0.0
        stopped_here=self.stopped
        last_t=None; event_seq=0; tick_seen=set(); first_valid=None; last_valid=None

        def log(t,kind,side='',qty='',price='',cost='',fee_val=''):
            self.ledger.append(dict(mode=self.mode,market=slug,time_us=t,kind=kind,side=side,qty=qty,price=price,cost=cost,fee=fee_val,cash=self.cash if self.mode=='C' else ''))

        def merge(t):
            nonlocal merge_income
            q=min(inv.values())
            if q<=1e-9:return
            for s in ('YES','NO'):
                avg=costs[s]/inv[s] if inv[s] else 0
                costs[s]-=q*avg; inv[s]-=q
            merge_income+=q
            if self.mode=='C': self.pending.append((t+PARAM['merge_delay_us'],q,slug,'MERGE_RELEASE'))
            log(t,'MERGE','BOTH',q,1,q,0)

        def fill(t,side,qty,px,fee_val):
            nonlocal spent,fees,buy_qty,adjust,switch,last_side,low_cash,peak_locked
            gross=qty*px; total=gross+fee_val
            if self.mode=='C': self.cash-=total
            inv[side]+=qty; costs[side]+=total; spent+=gross;fees+=fee_val
            buy_qty+=qty; adjust+=1
            if last_side and last_side!=side:switch+=1
            last_side=side
            low_cash=min(low_cash,self.cash);peak_locked=max(peak_locked,PARAM['capital']-self.cash if self.mode=='C' else spent)
            log(t,'BUY',side,qty,px,gross,fee_val);merge(t)

        def arrival(t):
            nonlocal pending_order,unfilled,capital_skip
            o=pending_order; pending_order=None
            ok,reason=self.book_ok(book,t)
            if not ok: reasons['arrival_'+reason]+=1;log(t,'EXPIRED',o['side'],o['qty']);return
            got,gross,f=o['fill'](book,o)
            if got+1e-9<o['qty']:unfilled+=1;reasons['depth_or_limit_short']+=1
            if got>1e-9:fill(t,o['side'],got,gross/got,f)
            else:log(t,'NO_FILL',o['side'],o['qty'],o['limit']/10000)

        with (path/'events.jsonl').open() as f:
            for line in f:
                e=json.loads(line);t=e.get('available_at_us'); k=e['kind'];p=e.get('payload') or {}
                if t is None:continue
                if last_t is not None and t<last_t:reasons['out_of_order']+=1
                last_t=t;event_seq=e['event_seq']
                if pending_order is not None and t>=pending_order['due']:
                    # The order arrives at its own clock time. The current
                    # event has not entered either book yet.
                    if self.mode=='C':self.release(pending_order['due'])
                    arrival(pending_order['due'])
                if self.mode=='C':self.release(t)
                if k not in ('BOOK','REST_BOOK','DELTA','TICK'):continue
                side=tok.get(p.get('token_id'))
                if side is None:continue
                if k=='TICK':
                    if p.get('tick_size'):book[side].tick=round(float(p['tick_size'])*10000)
                    continue
                changed=book[side].update(k,p,t)
                if book[side].tick:tick_seen.add(book[side].tick)
                if not changed or t<start or t>=end-PARAM['cutoff_us']:continue
                if pending_order is not None:continue
                ok,reason=self.book_ok(book,t)
                if not ok:reasons[reason]+=1;continue
                first_valid=first_valid or t;last_valid=t
                prob=price_prob(book)
                if not 0<prob<1:reasons['probability_bounds']+=1;continue
                if self.mode=='C' and self.stopped:
                    stopped_here=True;reasons['loss_stop']+=1;continue
                net=inv['YES']-inv['NO']
                scale=10 if self.mode=='A' else 50
                target=max(-PARAM['net_cap'],min(PARAM['net_cap'],scale*(prob-.5))) if self.mode!='A' else scale*(prob-.5)
                delta=target-net
                if abs(delta)<(1e-8 if self.mode=='A' else PARAM['min_qty']-1e-8):
                    if self.mode!='A':min_skip+=1
                    continue
                side='YES' if delta>0 else 'NO';qty=abs(delta)
                if self.mode=='C':
                    conservative=self.conservative_pnl + merge_income - spent - fees
                    if conservative<=-PARAM['loss_stop']+1e-9:
                        self.stopped=True;stopped_here=True;reasons['loss_stop']+=1;continue
                    if t+PARAM['latency_us']>=end-PARAM['cutoff_us']:
                        reasons['arrival_after_cutoff']+=1;continue
                    tick=book[side].tick
                    if not tick:reasons['tick_unknown']+=1;continue
                    legal_max=((10000-1)//tick)*tick
                    lim=min(legal_max,book[side].ask+tick)
                    if lim<book[side].ask:
                        reasons['no_legal_limit']+=1;continue
                    if qty*lim/10000+fee(qty,lim/10000)>self.cash+1e-8:
                        capital_skip+=1;reasons['capital']+=1;continue
                    def taker(bs,o):return self.execute(o['side'],o['qty'],bs,o['limit'])
                    pending_order=dict(side=side,qty=qty,limit=lim,due=t+PARAM['latency_us'],fill=taker)
                    log(t,'SUBMIT',side,qty,lim/10000)
                else:
                    px=prob if side=='YES' else 1-prob
                    fill(t,side,qty,px,0.0)
        if pending_order is not None:
            if pending_order['due']<end-PARAM['cutoff_us']: arrival(pending_order['due'])
            else:log(end-PARAM['cutoff_us'],'EXPIRED',pending_order['side'],pending_order['qty']);unfilled+=1
        residual_income=inv[label['winner']]
        residual_cost=sum(costs.values())
        pair_pnl=merge_income-(spent+fees-residual_cost)
        residual_pnl=residual_income-residual_cost
        pnl=merge_income+residual_income-spent-fees
        if abs(pnl-pair_pnl-residual_pnl)>1e-6:raise AssertionError('attribution')
        if self.mode=='C':
            self.pending.append((end+PARAM['redeem_delay_us'],residual_income,slug,'REDEEM_RELEASE'))
            self.release(end)
        log(end,'SETTLEMENT',label['winner'],residual_income,1,residual_income,0)
        self.total_pnl+=pnl
        if self.mode=='C':self.conservative_pnl+=merge_income-spent-fees
        self.peak_locked=max(self.peak_locked,peak_locked)
        self.market.append(dict(mode=self.mode,market=slug,winner=label['winner'],pnl=pnl,pair_pnl=pair_pnl,residual_pnl=residual_pnl,
            fees=fees,buy_qty=buy_qty,adjustments=adjust,switches=switch,final_net=inv['YES']-inv['NO'],
            yes_qty=inv['YES'],no_qty=inv['NO'],gross_spent=spent,merge_income=merge_income,residual_income=residual_income,
            peak_locked=peak_locked,low_cash=low_cash if self.mode=='C' else '',capital_skips=capital_skip,min_qty_waits=min_skip,
            unfilled=unfilled,stopped=stopped_here,first_valid_us=first_valid,last_valid_us=last_valid,
            metadata='historical' if slug in source else 'fee_min_assumed',reason_counts=json.dumps(reasons,sort_keys=True)))
        self.cover.append(dict(market=slug,session=audit['session'],condition_id=m['condition_id'],yes_token_id=m['yes_token_id'],no_token_id=m['no_token_id'],
            start_us=start,end_us=end,capture_start_us=m['started_at_us'],capture_end_us=m['ended_at_us'],
            window_covered=m['started_at_us']<=start and m['ended_at_us']>=end,
            records=audit['records'],public_trades=audit['public_trade_records'],winner=label['winner'],
            label_identity='MATCH',market_metadata='historical' if slug in source else 'missing_assumed',ticks=','.join(map(str,sorted(tick_seen))),
            first_valid_us=first_valid,last_valid_us=last_valid,
            grade='A_conditional' if first_valid and last_valid and m['started_at_us']<=start and m['ended_at_us']>=end else 'C',
            reasons=json.dumps(reasons,sort_keys=True)))


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    audited=sorted([r for r in rows(AUDIT) if r['execution_replay_usable']=='True'],key=lambda r:int(r['market'].split('-')[-1]))[:PARAM['markets']]
    labels={r['market']:r for r in rows(LABELS)};sources=json.loads(SOURCES.read_text())
    if len({r['market'] for r in audited})!=len(audited):raise AssertionError('duplicate market')
    for slug,v in sources.items():
        g=v['gamma'];fs=g.get('feeSchedule') or {}
        if (g.get('orderMinSize')!=5 or fs.get('rate')!=.07 or fs.get('exponent')!=1 or not fs.get('takerOnly')):raise AssertionError('metadata varies')
    runs={m:Run(m) for m in ('A','B','C')}
    for r in audited:
        for run in runs.values():run.market_run(r,labels[r['market']],sources)
    # Per-market capital occupancy excludes losses carried from prior markets.
    for run in runs.values():
        if run.mode!='C':continue
        by_market={r['market']:r for r in run.market}
        outstanding=Counter();peak=Counter()
        for entry in run.ledger:
            market=entry['market'];kind=entry['kind']
            if kind=='BUY':outstanding[market]+=float(entry['cost'])+float(entry['fee'])
            elif kind in ('MERGE_RELEASE','REDEEM_RELEASE'):outstanding[market]-=float(entry['qty'])
            peak[market]=max(peak[market],outstanding[market])
        # Release rows store returned cash in qty rather than cost.
        for market,row in by_market.items():row['peak_locked']=peak[market]
        run.peak_locked=max(peak.values(),default=0.0)
    cover=runs['C'].cover
    csvout('sample_coverage.csv',cover,list(cover[0]))
    allmarket=[r for run in runs.values() for r in run.market]
    csvout('market_results.csv',allmarket,list(allmarket[0]))
    ledger=[r for run in runs.values() for r in run.ledger]
    csvout('ledger.csv',ledger,list(ledger[0]))
    summaries={}
    for mode,run in runs.items():
        ms=run.market
        summaries[mode]={'markets':len(ms),'pnl':sum(x['pnl'] for x in ms),'pair_pnl':sum(x['pair_pnl'] for x in ms),
            'residual_pnl':sum(x['residual_pnl'] for x in ms),'fees':sum(x['fees'] for x in ms),
            'worst_market':min(x['pnl'] for x in ms),'profitable_markets':sum(x['pnl']>0 for x in ms),
            'losing_markets':sum(x['pnl']<0 for x in ms),'buy_qty':sum(x['buy_qty'] for x in ms),
            'adjustments':sum(x['adjustments'] for x in ms),'switches':sum(x['switches'] for x in ms),
            'sum_final_net':sum(x['final_net'] for x in ms),'max_abs_final_net':max(abs(x['final_net']) for x in ms),
            'peak_locked':run.peak_locked,'capital_skips':sum(x['capital_skips'] for x in ms),
            'min_qty_waits':sum(x['min_qty_waits'] for x in ms),'unfilled':sum(x['unfilled'] for x in ms),
            'stop_markets':sum(x['stopped'] for x in ms)}
    (OUT/'params.json').write_text(json.dumps(PARAM,indent=2)+'\n')
    (OUT/'summary.json').write_text(json.dumps(summaries,indent=2)+'\n')
    print(json.dumps(summaries,indent=2))

if __name__=='__main__':main()

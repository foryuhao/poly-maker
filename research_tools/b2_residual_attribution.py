"""Research-only causal diagnostics for frozen CIP-B2 residual inventory."""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path
from statistics import mean, median

import cip_b1_backtest as b1
import cip_b2_two_phase_backtest as b2
from r0_rolling_pair_replay import percentile

OUT = b2.ROOT / 'research_outputs/b2_residual_attribution_20261003'
SOURCE = b2.OUT
EPS = 1e-8


def read_csv(path):
    with path.open(newline='') as f:
        return list(csv.DictReader(f))


def num(value):
    return float(value) if value not in ('', None) else None


@contextmanager
def replay_capital():
    original_b1, original_b2 = b1.CAPITAL, b2.CAPITAL
    try:
        yield
    finally:
        b1.CAPITAL, b2.CAPITAL = original_b1, original_b2


class DiagnosticReplay(b2.TwoPhaseReplay):
    def __init__(self, *args, target_start=None, all_capital=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.target_start = target_start
        self.all_capital = all_capital
        self.extra_capital = 0.0
        self.injected = False
        self.injections = []
        self.target_order_id = None
        self.block_events = []
        self.episodes = []
        self.active_episode = None
        self.end_state = None
        self.execution_blocked_us = 0
        self.invalid_decision_times = []

    def integrate(self, t):
        dt = t - self.last_t
        if dt > 0 and self.phase == 'REDUCE_ONLY' and self.values()[0] != 0 and not self.valid_books():
            self.execution_blocked_us += dt
        super().integrate(t)

    def _need(self):
        net = self.values()[0]
        if self.phase != 'REDUCE_ONLY' or net == 0 or self.reduce_stopped or not self.valid_books():
            return None
        side = 'NO' if net > 0 else 'YES'
        if self.orders[side] is not None:
            return None
        quote = self.desired_price_int(side, net)
        if quote is None:
            return None
        price = quote / 10000
        filled = sum(self.cost.values())
        reservation = self.capital() - filled
        required = b1.CLIP * price
        return dict(net_inventory=net, lighter_side=side, desired_price=price,
                    required_notional=required, filled_cost=filled,
                    existing_open_order_reservation=reservation,
                    available_cash=b2.CAPITAL-filled-reservation,
                    capital_shortfall=max(0.0, filled+reservation+required-b2.CAPITAL))

    def reconcile(self, t, event_kind, inventory_changed=False):
        if self.phase == 'REDUCE_ONLY' and t < self.end and not self.valid_books():
            self.invalid_decision_times.append(t)
        if t == self.end and self.end_state is None:
            net = self.values()[0]
            lighter = 'NO' if net > 0 else 'YES' if net < 0 else None
            order = self.orders[lighter] if lighter else None
            self.end_state = dict(book_valid=self.valid_books(), net=net,
                                  lighter_side=lighter, lighter_order_id=order['order_id'] if order else None,
                                  lighter_order_status=order['status'] if order else None,
                                  lighter_order_accepted=order['accepted_us'] is not None if order else False,
                                  lighter_order_activation_us=order['activation_us'] if order else None,
                                  lighter_order_submitted_us=order['submitted_us'] if order else None,
                                  desired_price=self.desired_price_int(lighter, net)/10000
                                  if lighter and self.valid_books() and self.desired_price_int(lighter,net) is not None else None)
        need = self._need()
        if need and need['capital_shortfall'] > EPS and self.phase == 'REDUCE_ONLY':
            if (self.all_capital or (self.target_start == t and not self.injected)):
                bump = need['capital_shortfall'] + 1e-7
                b1.CAPITAL += bump
                b2.CAPITAL += bump
                self.extra_capital += bump
                self.injected = True
                self.injections.append(dict(timestamp_us=t, added_capital=bump,
                                            cumulative_extra_capital=self.extra_capital, **need))
        before_orders = len(self.order_rows)
        before_actions = len(self.action_rows)
        super().reconcile(t, event_kind, inventory_changed)
        if self.target_start == t and self.target_order_id is None:
            placed = [o for o in self.order_rows[before_orders:] if o['submitted_us'] == t]
            if placed:
                self.target_order_id = placed[0]['order_id']
        skips = [a for a in self.action_rows[before_actions:]
                 if a['reason_code'] == 'REDUCE_CAPITAL_BLOCKED' and a['action'] == 'SKIP']
        if skips:
            assert len(skips) == 1
            assert need and need['capital_shortfall'] > EPS
            event = dict(market=self.slug, timestamp_us=t, event_kind=event_kind,
                         capital_limit=b2.CAPITAL, **need)
            self.block_events.append(event)
            if self.active_episode is None:
                self.active_episode = dict(market=self.slug, episode_id=f'{self.slug}:E{len(self.episodes)+1}',
                                           start_us=t, end_us=None, event_count=0,
                                           start_net=need['net_inventory'], lighter_side=need['lighter_side'],
                                           start_price=need['desired_price'], start_shortfall=need['capital_shortfall'],
                                           min_shortfall=need['capital_shortfall'], max_shortfall=need['capital_shortfall'],
                                           max_abs_net=abs(need['net_inventory']))
            ep=self.active_episode
            ep['event_count'] += 1
            ep['min_shortfall'] = min(ep['min_shortfall'], need['capital_shortfall'])
            ep['max_shortfall'] = max(ep['max_shortfall'], need['capital_shortfall'])
            ep['max_abs_net'] = max(ep['max_abs_net'], abs(need['net_inventory']))
            event['episode_id'] = ep['episode_id']
        elif self.active_episode is not None:
            self.active_episode['end_us'] = t
            self.active_episode['duration_s'] = (t-self.active_episode['start_us'])/1e6
            self.episodes.append(self.active_episode)
            self.active_episode = None


def run_market(row, latency, target_start=None, all_capital=False):
    path = b2.DATA / row['session']
    manifest = json.loads((path/'manifest.json').read_text())
    orders, fills, paths, actions, phases = [], [], [], [], []
    start=int(row['market'].rsplit('-',1)[1])*1_000_000
    sim=DiagnosticReplay(row['market'],start,row['winner'],int(latency['submit']),int(latency['cancel']),
                         orders,fills,paths,actions,phases,target_start=target_start,all_capital=all_capital)
    with replay_capital():
        result=sim.replay(path,{manifest['yes_token_id']:'YES',manifest['no_token_id']:'NO'})
    return sim,result,orders,fills,actions


def no_fill_detail(row, orders, fills, actions):
    slug=row['market']; start=int(slug.rsplit('-',1)[1])*1_000_000; end=start+300_000_000
    fills_by_id={f['order_id'] for f in fills}
    accepts={a['order_id']:a for a in actions if a['action']=='ACCEPT'}
    event_path=b2.DATA/row['session']/'events.jsonl'
    trade_by_side=defaultdict(list)
    with event_path.open() as stream:
        for line in stream:
            e=json.loads(line); t=e.get('available_at_us')
            if t is None or t < start+150_000_000 or t >= end or e.get('kind')!='TRADE':
                continue
            p=e.get('payload') or {}
            if p.get('side')!='SELL':continue
            trade_by_side[p.get('token_id')].append(e)
    manifest=json.loads((b2.DATA/row['session']/'manifest.json').read_text())
    side_token={'YES':manifest['yes_token_id'],'NO':manifest['no_token_id']}
    records=[]
    for o in orders:
        if o['submitted_us']<start+150_000_000 or o['accepted_us'] is None or o['order_id'] in fills_by_id:
            continue
        side=o['side']; accept=accepts.get(o['order_id']); bid=accept[f'{side.lower()}_best_bid'] if accept else None
        lo=o['accepted_us']; hi=min(end,o['closed_us'] or end)
        prints=[e for e in trade_by_side[side_token[side]] if lo<e['available_at_us']<hi]
        prices=[float(e['payload']['price']) for e in prints]
        records.append(dict(market=slug,order_id=o['order_id'],side=side,order_price=o['price'],
                            accepted_us=lo,closed_us=hi,status=o['status'],cancel_reason=o['cancel_reason'],
                            resting_duration_s=(hi-lo)/1e6,time_remaining_at_place_s=(end-o['submitted_us'])/1e6,
                            best_bid_at_accept=bid,distance_from_best_bid_at_accept=(bid-o['price']) if bid is not None else None,
                            book_valid_at_accept=accept['book_valid'] if accept else None,
                            later_sell_prints_during_rest=len(prices),min_later_sell_price=min(prices) if prices else None,
                            later_trade_through_count=sum(p<o['price'] for p in prices)))
    return records,trade_by_side,side_token


def categorize(row, end_state, episodes, nofills, recoverable):
    residual=num(row['residual_final'])
    if residual==0:return 'NONE',None
    end=int(row['market'].rsplit('-',1)[1])*1_000_000+300_000_000
    # Capital is primary only when removing that gate recovers at least half
    # this market's final residual. Otherwise an accepted unfilled reduce order
    # is direct no-fill evidence, including one later invalidated by the book.
    if recoverable>=residual/2-EPS:
        return 'REDUCE_CAPITAL_BLOCKED', ('REDUCE_ORDER_WORKING_NO_FILL' if nofills else 'REDUCE_BOOK_OR_EXECUTION_BLOCKED')
    if nofills or end_state['lighter_order_accepted']:
        return 'REDUCE_ORDER_WORKING_NO_FILL', ('REDUCE_CAPITAL_BLOCKED' if episodes else 'REDUCE_BOOK_OR_EXECUTION_BLOCKED')
    if end_state['lighter_order_id'] and end_state['lighter_order_activation_us']>=end:
        return 'TIME_EXHAUSTED_AFTER_VALID_REDUCE',None
    if not end_state['book_valid'] or end_state['desired_price'] is None:
        return 'REDUCE_BOOK_OR_EXECUTION_BLOCKED',('REDUCE_CAPITAL_BLOCKED' if episodes else None)
    if episodes and episodes[-1]['end_us']==end:
        return 'REDUCE_CAPITAL_BLOCKED',None
    if episodes and num(row['reduce_capital_blocked_seconds'])>0:
        return 'REDUCE_CAPITAL_BLOCKED','REDUCE_BOOK_OR_EXECUTION_BLOCKED'
    if end_state['lighter_order_id']:
        return 'TIME_EXHAUSTED_AFTER_VALID_REDUCE',None
    return 'OTHER',None


def bucket_residual(x):
    return '0' if x==0 else '1-5' if x<=5 else '6-10' if x<=10 else '11-15' if x<=15 else '16-20' if x<=20 else '>20'


def bucket_capital(x):
    return '<15' if x<15 else '15-20' if x<20 else '20-23' if x<23 else '23-24' if x<=24 else '>24'


def group_buckets(rows,func,field,labels):
    result=[]
    for label in labels:
        subset=[r for r in rows if func(num(r[field]))==label]
        if not subset:continue
        t=sum(num(r['residual_t150']) for r in subset); f=sum(num(r['final_residual']) for r in subset)
        result.append(dict(bucket=label,markets=len(subset),t150_residual_shares=t,final_residual_shares=f,
                           residual_reduction_rate=(1-f/t) if t else None,
                           mean_t150_residual=t/len(subset),
                           capital_blocked_markets=sum(r['capital_blocked_episodes']>0 for r in subset),
                           capital_blocked_rate=mean(r['capital_blocked_episodes']>0 for r in subset),
                           flat_markets=sum(num(r['final_residual'])==0 for r in subset),
                           flat_rate=mean(num(r['final_residual'])==0 for r in subset),
                           reduce_success_rate=mean(num(r['final_residual'])<num(r['residual_t150']) for r in subset)))
    return result


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    baseline=json.loads((SOURCE/'summary.json').read_text())
    assert baseline['sample']['markets']==32 and baseline['sample']['b1_gamma_fill_prefix_match']
    rows=read_csv(SOURCE/'market_results.csv')
    assert len(rows)==32 and len({r['market'] for r in rows})==32
    latency={'submit':baseline['latencies_us']['submit'],'cancel':baseline['latencies_us']['cancel']}
    market_rows=[]; episodes=[]; events=[]; nofill=[]; cf_rows=[]; pnl_rows=[]; all_cf=[]
    actual_reduce_times=[]
    source_fills=read_csv(SOURCE/'fills.csv')
    source_orders=read_csv(SOURCE/'orders.csv')
    order_by_id={o['order_id']:o for o in source_orders}
    for f in source_fills:
        if f['phase']=='REDUCE_ONLY':actual_reduce_times.append((int(f['fill_available_us'])-int(order_by_id[f['order_id']]['accepted_us']))/1e6)
    for row in rows:
        sim,result,orders,fills,actions=run_market(row,latency)
        for key in ('residual_final','residual_pnl','total_pnl','net_t150'):
            assert abs(result[key]-float(row[key]))<1e-7,(row['market'],key)
        assert len(sim.block_events)==int(row['reduce_capital_blocked_events'])
        assert abs(sum(ep['duration_s'] for ep in sim.episodes)-float(row['reduce_capital_blocked_seconds']))<1e-5
        assert len(fills)==int(row['raw_fill_records']) and not result['data_error']
        episodes.extend(sim.episodes);events.extend(sim.block_events)
        nf,trades,side_token=no_fill_detail(row,orders,fills,actions);nofill.extend(nf)
        final_res=float(row['residual_final'])
        per_ep=[]
        for ep in sim.episodes:
            activation=ep['start_us']+int(latency['submit'])
            next_invalid=next((t for t in sim.invalid_decision_times if t>ep['start_us']),None)
            first_cancel_effective=max(next_invalid+int(latency['cancel']),activation) if next_invalid is not None else None
            eligible_print=any(
                e['available_at_us']>activation and e.get('exchange_at_us') is not None and
                e['exchange_at_us']>activation and e.get('received_at_us') is not None and
                e['received_at_us']>activation and
                (first_cancel_effective is None or e['available_at_us']<first_cancel_effective) and
                int(round(float(e['payload']['price'])*10000))<int(round(ep['start_price']*10000))
                for e in trades[side_token[ep['lighter_side']]])
            if eligible_print:
                alt,alt_result,alt_orders,alt_fills,_=run_market(row,latency,target_start=ep['start_us'])
                order=next((o for o in alt_orders if o['order_id']==alt.target_order_id),None)
                fill=next((f for f in alt_fills if f['order_id']==alt.target_order_id),None)
                assert not alt_result['data_error'] and alt.injected and order
                final_cf_residual=alt_result['residual_final']
                added_capital=alt.extra_capital
                order_id=alt.target_order_id
            else:
                # The frozen B1 trade predicate requires one such public SELL
                # print. With none through T300, this order cannot fill under
                # any subsequent cancellation or inventory path.
                order=activation<int(row['market'].rsplit('-',1)[1])*1_000_000+300_000_000
                fill=None
                final_cf_residual=final_res
                added_capital=ep['start_shortfall']+1e-7
                order_id=None
            cf=dict(market=row['market'],episode_id=ep['episode_id'],episode_start_us=ep['start_us'],
                    extra_capital_injected=added_capital,
                    counterfactual_order_placeable=bool(order),
                    counterfactual_order_id=order_id,
                    counterfactual_fill=bool(fill),
                    counterfactual_fill_time_us=fill['fill_available_us'] if fill else None,
                    counterfactual_fill_price=fill['price'] if fill else None,
                    counterfactual_fill_shares=fill['size'] if fill else 0,
                    counterfactual_residual_reduced_shares=final_res-final_cf_residual,
                    counterfactual_final_residual=final_cf_residual,
                    counterfactual_data_error='' if not eligible_print else alt_result['data_error'],
                    no_eligible_print_pruned=not eligible_print)
            cf_rows.append(cf);per_ep.append(cf)
        if sim.episodes:
            alt,alt_result,alt_orders,alt_fills,_=run_market(row,latency,all_capital=True)
            assert not alt_result['data_error']
            combined=max(0.0,final_res-alt_result['residual_final'])
            all_cf.append(dict(market=row['market'],baseline_final_residual=final_res,
                               counterfactual_final_residual=alt_result['residual_final'],
                               recoverable_shares=combined,extra_capital_injections=len(alt.injections),
                               max_extra_capital=alt.extra_capital))
        else:combined=0.0
        primary,secondary=categorize(row,sim.end_state,sim.episodes,nf,combined)
        mr=dict(market=row['market'],net_t150=float(row['net_t150']),residual_t150=float(row['residual_t150']),
                capital_used_t150=float(row['capital_used_t150']),reduce_fills=int(row['reduce_fills_150_end']),
                reduce_shares=float(row['reduce_shares_150_end']),capital_blocked_episodes=len(sim.episodes),
                capital_blocked_events=len(sim.block_events),
                capital_blocked_seconds=float(row['reduce_capital_blocked_seconds']),
                working_no_fill_orders=len(nf),execution_blocked_seconds=sim.execution_blocked_us/1e6,
                final_net=float(row['net_final']),final_residual=final_res,residual_pnl=float(row['residual_pnl']),
                primary_cause=primary,secondary_cause=secondary,
                counterfactual_capital_recoverable_shares=combined,
                final_book_valid=sim.end_state['book_valid'],
                final_lighter_order_status=sim.end_state['lighter_order_status'])
        market_rows.append(mr)
        pnl_rows.append(dict(market=row['market'],primary_cause=primary,final_residual_shares=final_res,
                             residual_pnl=float(row['residual_pnl'])))
    assert len(events)==4075 and abs(sum(r['final_residual'] for r in market_rows)-245)<EPS
    assert abs(sum(r['residual_pnl'] for r in pnl_rows)+72.44666666666667)<1e-7
    assert abs(sum(r['residual_t150']-r['final_residual'] for r in market_rows)-135)<EPS
    by_cause={c:dict(shares=sum(r['final_residual'] for r in market_rows if r['primary_cause']==c),
                     residual_pnl=sum(r['residual_pnl'] for r in market_rows if r['primary_cause']==c),
                     markets=sum(r['primary_cause']==c and r['final_residual']>0 for r in market_rows))
              for c in ('REDUCE_CAPITAL_BLOCKED','REDUCE_ORDER_WORKING_NO_FILL','REDUCE_BOOK_OR_EXECUTION_BLOCKED',
                        'MIN_ORDER_OR_SIGN_FLIP_CONSTRAINT','TIME_EXHAUSTED_AFTER_VALID_REDUCE','OTHER')}
    assert abs(sum(v['shares'] for v in by_cause.values())-245)<EPS
    assert abs(sum(v['residual_pnl'] for v in by_cause.values())+72.44666666666667)<1e-7
    recoverable=sum(r['recoverable_shares'] for r in all_cf)
    def stats(vals):return dict(mean=mean(vals) if vals else None,median=median(vals) if vals else None,
                                p75=percentile(vals,.75),p90=percentile(vals,.90),max=max(vals) if vals else None)
    primary_noncapital=by_cause['REDUCE_ORDER_WORKING_NO_FILL']['shares']+by_cause['REDUCE_BOOK_OR_EXECUTION_BLOCKED']['shares']
    classification=('EXECUTION / NO-FILL IS PRIMARY BOTTLENECK' if primary_noncapital>122.5 and recoverable<122.5 else
                    'CAPITAL IS PRIMARY REDUCE BOTTLENECK' if recoverable>122.5 else 'MIXED BOTTLENECK')
    summary=dict(classification=classification,sample_markets=32,
                 reconciliation=dict(t150_residual=380,reduced=135,final_residual=245,residual_pnl=-72.44666666666667),
                 primary_attribution=by_cause,
                 capital_blocks=dict(events=len(events),episodes=len(episodes),affected_markets=len({e['market'] for e in episodes}),
                                     total_seconds=sum(e['duration_s'] for e in episodes),
                                     duration_s=stats([e['duration_s'] for e in episodes]),
                                     shortfall_usd=stats([e['capital_shortfall'] for e in events])),
                 capital_counterfactual=dict(individual_episode_orders_placeable=sum(c['counterfactual_order_placeable'] for c in cf_rows),
                                             individual_episode_orders_filled=sum(c['counterfactual_fill'] for c in cf_rows),
                                             jointly_recoverable_shares=recoverable,recoverable_pct_of_245=recoverable/245,
                                             joint_final_residual=245-recoverable,
                                             note='Per-episode effects overlap; joint serial minimal-capital-injection replay is the non-double-counted estimate.'),
                 no_fill=dict(reduce_phase_submissions=sum(int(o['submitted_us'])>=(int(o['market'].rsplit('-',1)[1])+150)*1_000_000 for o in source_orders),
                              actual_reduce_fills=len(actual_reduce_times),
                              accepted_unfilled_orders=len(nofill),orders_with_no_qualifying_trade=sum(n['later_trade_through_count']==0 for n in nofill),
                              resting_s=stats([n['resting_duration_s'] for n in nofill]),
                              time_remaining_at_place_s=stats([n['time_remaining_at_place_s'] for n in nofill]),
                              actual_reduce_fill_accept_to_fill_s=stats(actual_reduce_times)),
                 t150_residual_buckets=group_buckets(market_rows,bucket_residual,'residual_t150',['0','1-5','6-10','11-15','16-20','>20']),
                 t150_capital_buckets=group_buckets(market_rows,bucket_capital,'capital_used_t150',['<15','15-20','20-23','23-24','>24']))
    b2.write_csv(OUT/'market_attribution.csv',market_rows)
    b2.write_csv(OUT/'blocked_episodes.csv',episodes)
    b2.write_csv(OUT/'blocked_events.csv',events)
    b2.write_csv(OUT/'capital_counterfactual.csv',cf_rows)
    b2.write_csv(OUT/'joint_capital_counterfactual.csv',all_cf)
    b2.write_csv(OUT/'no_fill_orders.csv',nofill)
    b2.write_csv(OUT/'residual_pnl_attribution.csv',pnl_rows)
    (OUT/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary,indent=2))


if __name__=='__main__':main()

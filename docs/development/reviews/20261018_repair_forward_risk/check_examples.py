"""Decimal arithmetic checks for the v3 design, not a trading implementation."""
import json
from decimal import Decimal as D
from pathlib import Path

OUT = Path(__file__).resolve().parent


def values(yes, no, cost, p):
    value = p * yes + (1 - p) * no
    guaranteed = min(yes, no)
    return dict(yes= yes, no=no, cost=cost, reference_value=value,
                mark_pnl=value-cost, pnl_yes=yes-cost, pnl_no=no-cost,
                cumulative_worst_loss=max(D(0),cost-guaranteed),
                forward_hold=value-guaranteed)


def buy_no(before, quantity, price, p):
    spend = quantity * price
    after = values(before['yes'], before['no'] + quantity,
                   before['cost'] + spend, p)
    incremental_yes = after['pnl_yes'] - before['mark_pnl']
    incremental_no = after['pnl_no'] - before['mark_pnl']
    forward_action = max(D(0),-min(incremental_yes,incremental_no))
    mark_gain = after['reference_value']-before['reference_value']-spend
    assert forward_action == max(D(0),after['forward_hold']-mark_gain)
    return dict(after=after, spend=spend,
                incremental_yes=incremental_yes,incremental_no=incremental_no,
                forward_action=forward_action,mark_gain_if_filled=mark_gain)


def run_checks():
    cases = []
    for entry,p in [('0.15','0.15'),('0.35','0.30'),('0.35','0.40'),
                    ('0.15','0.70'),('0.75','0.15'),('0.85','0.85')]:
        before=values(D(5),D(0),D(5)*D(entry),D(p))
        cases.append(dict(entry_price=D(entry),p=D(p),before=before,
                          forward_budget=D('1.50'),
                          forward_gate_pass=before['forward_hold']<=D('1.50')))
    fallen = values(D(5),D(0),D('3.75'),D('.15'))
    cheap = values(D(5),D(0),D('.75'),D('.15'))
    assert fallen['forward_hold']==cheap['forward_hold']==D('.75')
    assert fallen['mark_pnl']==D('-3.00')
    assert fallen['cumulative_worst_loss']==D('3.75')
    no85=buy_no(fallen,D(5),D('.85'),D('.15'))
    assert no85['spend']==D('4.25')
    assert no85['after']['pnl_yes']==no85['after']['pnl_no']==D('-3.00')
    assert no85['forward_action']==no85['after']['forward_hold']==0
    assert no85['incremental_yes']==no85['incremental_no']==0
    lock=values(D(5),D(5),D(8),D('.15'))
    assert lock['forward_hold']==0 and lock['cumulative_worst_loss']==3
    risen=values(D(5),D(0),D('.75'),D('.70'))
    take=buy_no(risen,D(5),D('.30'),D('.70'))
    assert risen['forward_hold']==D('3.50')
    assert take['after']['pnl_yes']==take['after']['pnl_no']==D('2.75')
    assert take['forward_action']==0
    high=values(D(5),D(0),D('4.25'),D('.85'))
    over=buy_no(high,D('6.67'),D('.15'),D('.85'))
    assert over['forward_action']==over['after']['forward_hold']==D('.2505')
    assert over['after']['pnl_yes']==D('-.2505')
    assert over['after']['pnl_no']==D('1.4195')
    # A maker quote below the reference opposite value creates a conditional
    # mark gain, but must not hide the after-fill residual risk.
    cheap_quote=buy_no(high,D('7.15'),D('.14'),D('.85'))
    assert cheap_quote['mark_gain_if_filled']==D('.0715')
    assert cheap_quote['after']['forward_hold']==D('.3225')
    assert cheap_quote['forward_action']==D('.2510')
    # Same inventory/current price => same marginal scoring, regardless of cost.
    for c in [D('.75'),D('3.75')]:
        before=values(D(5),D(0),c,D('.15'))
        a=buy_no(before,D(5),D('.85'),D('.15'))
        assert (a['incremental_yes'],a['incremental_no'])==(0,0)
    # Partial fills use the same decision reference. No early claim of full repair.
    partial=[]
    for x in [D(0),D(1),D('2.5'),D(5)]:
        a=buy_no(fallen,x,D('.85'),D('.15'))
        partial.append(dict(filled=x,forward_action=a['forward_action'],
                            residual=a['after']['forward_hold']))
        assert a['forward_action']==D('.75')-D('.15')*x
    return dict(policy='REPAIR_RISK_POLICY_V3',
                scope='illustrative exact arithmetic; no real order or new-policy backtest',
                cases=cases,fallen_pair=no85,risen_pair=take,high_overbuy=over,
                conditional_mark_gain=cheap_quote,partial_fills=partial,
                checks='passed')


if __name__=='__main__':
    result=run_checks()
    OUT.joinpath('examples.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str)+'\n')
    print('V3 scenario arithmetic and identities passed; examples.json written.')

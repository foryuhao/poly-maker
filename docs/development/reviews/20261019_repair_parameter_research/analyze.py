"""Price-path parameter sensitivity, NOT an executable fill/PnL backtest.

Inputs are immutable archived event streams (see extract.py), actual opening
fills for scale checks, and one previously discussed case. Results use entire
markets as the uncertainty / chronological validation unit. Synthetic entries
are explicitly labelled and never presented as fixed-NORMAL executions.
"""
import csv
import hashlib
import json
import math
from collections import Counter
from pathlib import Path

import numpy as np

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[3]
RATES = [.0005, .001, .002, .003, .005, .01]
SL_SPECS = [
    {'name': 'fast_only_1.5', 'z': 1.5, 'floor': .02, 'dd': None, 'dd_k': None},
    {'name': 'fixed04', 'z': 1.5, 'floor': .02, 'dd': .04, 'dd_k': None},
    {'name': 'fixed06', 'z': 1.5, 'floor': .02, 'dd': .06, 'dd_k': None},
    {'name': 'fixed08', 'z': 1.5, 'floor': .02, 'dd': .08, 'dd_k': None},
    {'name': 'adaptive3', 'z': 1.5, 'floor': .02, 'dd': .04, 'dd_k': 3.},
    {'name': 'adaptive4', 'z': 1.5, 'floor': .02, 'dd': .04, 'dd_k': 4.},
    {'name': 'adaptive3_z2', 'z': 2., 'floor': .02, 'dd': .04, 'dd_k': 3.},
    {'name': 'adaptive3_floor03', 'z': 1.5, 'floor': .03, 'dd': .04, 'dd_k': 3.},
    {'name': 'dd_only06', 'z': None, 'floor': .02, 'dd': .06, 'dd_k': None},
]
SOURCES = []


def read_csv(path):
    raw = path.read_bytes()
    SOURCES.append({'path': str(path.resolve()), 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)})
    return list(csv.DictReader(raw.decode().splitlines()))


def read_json(path):
    raw = path.read_bytes()
    SOURCES.append({'path': str(path.resolve()), 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)})
    return json.loads(raw)


def dump_csv(name, rows):
    if not rows:
        return
    with (OUT/name).open('w') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def finite(x):
    return x is not None and math.isfinite(float(x))


def quantiles(values):
    values = [float(x) for x in values if finite(x)]
    return {'n': len(values), **({str(q): float(np.quantile(values, q))
                                for q in [.1, .25, .5, .75, .9, .95]} if values else {})}


def load_markets():
    results = []
    for path in sorted((OUT/'cache').glob('*.csv')):
        rows = read_csv(path)
        for r in rows:
            for k in ['t', 'p', 'sigma', 'yes_bid', 'yes_ask', 'no_bid', 'no_ask', 'tick', 'epoch', 'generation']:
                r[k] = float(r[k]) if r[k] and r[k] != 'UNKNOWN' else None
        results.append({'rows': rows, 'slug': rows[0]['slug'], 'sid': rows[0]['session_id'], 'epoch': rows[0]['epoch']})
    results.sort(key=lambda m: (m['epoch'], m['sid']))
    assert len(results) == 47
    for i, m in enumerate(results):
        m['split'] = 'exploration_31' if i < 31 else 'chronological_check_16'
    return results


def entry_cases(markets):
    cases = []
    for m in markets:
        for t0 in range(40, 120, 10):
            r0 = m['rows'][t0]
            if r0['p'] is None or r0['sigma'] is None:
                continue
            for direction in [1, -1]:
                for cost_basis in ['fair', 'best_bid']:
                    u0 = r0['p'] if direction == 1 else 1-r0['p']
                    entry = u0 if cost_basis == 'fair' else r0['yes_bid' if direction == 1 else 'no_bid']
                    # Quotes near terminal endpoints cannot support both sides'
                    # minimum order size in a useful standard 5-share probe.
                    cases.append({'market': m, 't0': t0, 'time0': float(t0), 'direction': direction,
                                  'entry': entry, 'entry_mark': u0, 'basis': cost_basis,
                                  'q0': 5., 'cohort': 'standardized_path_probe'})
    return cases


def actual_opening_anchors(markets):
    by_sid = {m['sid']: m for m in markets}
    fills = read_csv(ROOT/'docs/development/reviews/20261018_repair_ev_calibration/fills.csv')
    orders = read_csv(ROOT/'docs/development/reviews/20261018_repair_ev_calibration/orders.csv')
    result = []
    for order in orders:
        if order['session_id'] not in by_sid or order['phase'] != 'NORMAL' or float(order['confirmed_qty']) <= 0:
            continue
        m = by_sid[order['session_id']]
        legs = [f for f in fills if f['session_id'] == order['session_id'] and f['order_id'] == order['order_id']]
        time0 = min(float(f['matched_observed']) for f in legs)-m['epoch']
        t0 = int(math.ceil(time0))
        if not 0 <= t0 < 120 or m['rows'][t0]['p'] is None or m['rows'][t0]['sigma'] is None:
            continue
        qty = sum(float(f['quantity']) for f in legs)
        entry = sum(float(f['quantity'])*float(f['price']) for f in legs)/qty
        r0 = m['rows'][t0]
        d = 1 if order['side'] == 'YES' else -1
        result.append({'market': m, 't0': t0, 'time0': time0, 'direction': d, 'entry': entry,
                       'entry_mark': r0['p'] if d == 1 else 1-r0['p'], 'basis': 'actual_opening',
                       'q0': qty, 'cohort': 'older_normal_opening_anchor'})
    return result


def run_case(case, rate, spec):
    m, t0, d = case['market'], case['t0'], case['direction']
    rows = m['rows']
    start = rows[t0]
    u = lambda r: r['p'] if d == 1 else 1-r['p']
    peak = u(start)
    peak_sigma = start['sigma']
    mode, tp_t, tp_price, tp_q, tp_feasible, opportunity = 'HD', None, None, None, None, None
    sl_t, sl_reason, trigger_base, drawdown, dd_bar = None, None, None, 0., None
    invalid = None
    for t in range(t0+1, 181):
        r = rows[t]
        if r['p'] is None or r['generation'] != start['generation']:
            invalid = t
            break
        if u(r) > peak+1e-12:
            peak, peak_sigma = u(r), r['sigma']
        drawdown = max(0., peak-u(r))
        dd_bar = (max(spec['dd'], spec['dd_k']*peak_sigma) if spec['dd_k'] is not None and peak_sigma is not None
                  else spec['dd'] if spec['dd_k'] is None else None)
        # Exposure fixed at the original side in a price-path probe. Real code
        # must integrate delta * dp / Q0 after each actual partial fill.
        base = rows[t-2] if t >= t0+2 else None
        fast = (base is not None and base['p'] is not None and base['sigma'] is not None
                and spec['z'] is not None
                and u(base)-u(r)+1e-12 >= max(spec['floor'], spec['z']*base['sigma']*math.sqrt(2)))
        slow = dd_bar is not None and drawdown+1e-12 >= dd_bar
        if t >= 180 or fast or slow:
            sl_t, sl_reason = t, 'TIME' if t >= 180 else 'SHOCK' if fast else 'DRAWDOWN'
            trigger_base = u(base) if fast else peak
            mode = 'SL'
            break
        if mode == 'HD' and u(r)-case['entry'] > rate*(t-case['time0'])+1e-12:
            mode, tp_t = 'TP', t
            if r['sigma'] is not None and r['tick']:
                ticks = max(1, math.ceil(r['sigma']/r['tick']-1e-12))
                tp_price = round(r['no_bid' if d == 1 else 'yes_bid']-ticks*r['tick'], 6)
                if tp_price > 0:
                    tp_q = max(case['q0'], 5., math.ceil((1/tp_price)*100-1e-9)/100)
                    # Isolated FIRST episode, no prior session commitments.
                    tp_feasible = (abs(case['q0']-tp_q) <= 5+1e-9 and
                                   case['q0']*case['entry']+tp_q*tp_price <= 15+1e-9)
                else:
                    tp_feasible = False
        if mode == 'TP' and tp_price and tp_feasible and t > tp_t:
            ask = r['no_ask' if d == 1 else 'yes_ask']
            # Price crossing is an opportunity diagnostic only; do NOT assume
            # queue position, entry time, quantity or actual maker execution.
            if ask is not None and ask <= tp_price+1e-12 and opportunity is None:
                opportunity = t
    post = []
    if sl_t is not None and sl_reason != 'TIME' and sl_t+10 <= 300:
        post = rows[sl_t+1:sl_t+11]
        if any(r['p'] is None or r['generation'] != start['generation'] for r in post):
            post = []
    recovered = (max(u(r) for r in post) >= trigger_base-1e-12) if post else None
    worse = (min(u(r) for r in post) <= u(rows[sl_t])-.01+1e-12) if post else None
    terminal = sl_t if sl_t is not None else invalid
    return {'sid': m['sid'], 'slug': m['slug'], 'split': m['split'], 'cohort': case['cohort'],
            't0': case['time0'], 'side': 'YES' if d == 1 else 'NO', 'basis': case['basis'],
            'q0': case['q0'], 'entry': case['entry'], 'r_tp': rate, 'sl_spec': spec['name'],
            'tp_t': tp_t, 'tp_age': tp_t-case['time0'] if tp_t is not None else None,
            'tp_price': tp_price, 'tp_min_qty': tp_q, 'tp_feasible': tp_feasible,
            'tp_quote_cross_t': opportunity,
            'sl_t': sl_t, 'sl_age': sl_t-case['time0'] if sl_t is not None else None,
            'sl_reason': sl_reason, 'dd_at_sl': drawdown if sl_t is not None else None,
            'dd_bar_at_sl': dd_bar if sl_t is not None else None,
            'recovered_pre_sl_reference_10s': recovered, 'worse_one_tick_10s': worse,
            'censored_t': invalid, 'observed_to_age': terminal-case['time0'] if terminal is not None else None,
            'first_exit_intent': 'TP' if tp_t is not None else 'SL' if sl_t is not None else 'CENSORED'}


def bootstrap(rows, field, eligible=lambda r: True):
    # Equal market weight; paired directions / overlapping entries stay together.
    grouped = {}
    for r in rows:
        if eligible(r):
            grouped.setdefault(r['sid'], []).append(float(r[field]))
    values = np.array([np.mean(v) for v in grouped.values()])
    if not len(values):
        return None
    rng = np.random.default_rng(20261019)
    samples = np.mean(rng.choice(values, size=(3000, len(values)), replace=True), axis=1)
    return {'markets': len(values), 'mean': float(np.mean(values)),
            'lo95': float(np.quantile(samples, .025)), 'hi95': float(np.quantile(samples, .975))}


def summarize(rows):
    tp = [r for r in rows if r['tp_t'] is not None]
    path_sl = [r for r in rows if r['sl_reason'] in ['SHOCK', 'DRAWDOWN']]
    known_post = [r for r in path_sl if r['recovered_pre_sl_reference_10s'] is not None]
    return {'cases': len(rows), 'markets': len({r['sid'] for r in rows}),
            'tp_first_pct': 100*sum(r['first_exit_intent'] == 'TP' for r in rows)/len(rows),
            'sl_first_pct': 100*sum(r['first_exit_intent'] == 'SL' for r in rows)/len(rows),
            'censored_first_pct': 100*sum(r['first_exit_intent'] == 'CENSORED' for r in rows)/len(rows),
            'tp_age_median': float(np.median([r['tp_age'] for r in tp])) if tp else None,
            'tp_by_2s_pct': 100*sum(r['tp_age'] <= 2 for r in tp)/len(rows),
            'tp_feasible_pct': 100*sum(r['tp_feasible'] is True for r in tp)/len(tp) if tp else None,
            'tp_quote_cross_pct': 100*sum(r['tp_quote_cross_t'] is not None for r in tp)/len(tp) if tp else None,
            'path_sl_pct': 100*len(path_sl)/len(rows),
            'shock_pct': 100*sum(r['sl_reason'] == 'SHOCK' for r in rows)/len(rows),
            'dd_pct': 100*sum(r['sl_reason'] == 'DRAWDOWN' for r in rows)/len(rows),
            'time_sl_pct': 100*sum(r['sl_reason'] == 'TIME' for r in rows)/len(rows),
            'path_sl_age_median': float(np.median([r['sl_age'] for r in path_sl])) if path_sl else None,
            'path_sl_by_10s_pct': 100*sum(r['sl_age'] <= 10 for r in path_sl)/len(rows),
            'post_sl_10s_known_n': len(known_post),
            'recovered_pre_sl_10s_pct': 100*sum(r['recovered_pre_sl_reference_10s'] for r in known_post)/len(known_post) if known_post else None,
            'worse_one_tick_10s_pct': 100*sum(r['worse_one_tick_10s'] for r in known_post)/len(known_post) if known_post else None,
            'tp_first_market_bootstrap': bootstrap([{**r, 'tp_first': r['first_exit_intent']=='TP'} for r in rows], 'tp_first')}


def special_case():
    rows = read_csv(ROOT/'docs/development/reviews/20261018_repair_live_1810/decisions.csv')
    origin = 35.494
    peak, peak_sigma, out = None, None, []
    for i, r in enumerate(rows):
        t, p, s = [float(r[k]) for k in ['model_input_T', 'p_yes', 'sigma_per_sqrt_second']]
        if peak is None or p > peak+1e-12:
            peak, peak_sigma = p, s
        previous = rows[i-1] if i else None
        a, z, h = None, None, None
        if previous:
            h = t-float(previous['model_input_T'])
            a = float(previous['p_yes'])-p
            denom = float(previous['sigma_per_sqrt_second'])*math.sqrt(h)
            z = max(0., a)/denom if denom > 0 else None
        out.append({'decision': i+1, 't': t, 'p': p, 'sigma': s, 'age_proxy': t-origin,
                    'floating_per_share': p-.53, 'r_observed': (p-.53)/(t-origin),
                    'previous_h': h, 'adverse': a, 'z': z, 'dd': peak-p,
                    'adaptive3_bar': max(.04, 3*peak_sigma),
                    'fast_z15': a is not None and a >= .02-1e-12 and z is not None and z >= 1.5,
                    'no_bid': float(r['no_best_bid']), 'no_ask': float(r['no_best_ask'])})
    dump_csv('special_case.csv', out)
    return out


def main():
    markets = load_markets()
    cases = entry_cases(markets)
    anchors = actual_opening_anchors(markets)
    results = [run_case(c, rate, spec) for c in cases+anchors for rate in RATES for spec in SL_SPECS]
    dump_csv('path_probe_results.csv', results)
    aggregates = []
    full = {}
    for basis in ['fair', 'best_bid', 'actual_opening']:
        for split in ['all', 'exploration_31', 'chronological_check_16']:
            for spec in SL_SPECS:
                for rate in RATES:
                    subset = [r for r in results if r['basis']==basis and (split=='all' or r['split']==split)
                              and r['sl_spec']==spec['name'] and r['r_tp']==rate]
                    if subset:
                        agg = summarize(subset)
                        key = f"{basis}|{split}|{spec['name']}|{rate}"
                        full[key] = agg
                        aggregates.append({'basis': basis, 'split': split, 'sl_spec': spec['name'], 'r_tp': rate,
                                           **{k: v for k, v in agg.items() if k != 'tp_first_market_bootstrap'}})
    dump_csv('candidate_summary.csv', aggregates)
    specials = special_case()
    evidence = read_json(ROOT/'docs/development/reviews/20261017_2245_2345_pairing/evidence.json')
    actual = [{'session_id': r['session_id'], 'market': r['market'], 'side': r['side'],
               'entry_price': r['entry_price'], 'q0': r['entry_quantity'],
               'holding_observed_s': r['matched_to_matched_s'], 'pair_gross': r['fifo_acquisition_pair_gross'],
               'realized_pair_rate': r['fifo_acquisition_pair_gross']/r['entry_quantity']/r['matched_to_matched_s']}
              for r in evidence['cycles']]
    dump_csv('actual_fixed_normal_scale.csv', actual)
    z_values, sigma_values = [], []
    for m in markets:
        for i, r in enumerate(m['rows']):
            if i < 240 and r['sigma'] is not None:
                sigma_values.append(r['sigma'])
            if 2 <= i < 180 and r['p'] is not None:
                b, mid = m['rows'][i-2], m['rows'][i-1]
                if b['p'] is not None and mid['p'] is not None and b['generation']==r['generation'] and b['sigma'] and b['sigma']>0:
                    z_values.append(abs(r['p']-b['p'])/(b['sigma']*math.sqrt(2)))
    manifest = read_json(OUT/'raw_manifest.json')
    comparisons = Counter()
    issues = Counter()
    for r in manifest:
        comparisons.update(r['comparison'])
        issues.update(r['issues'])
    data = {'scope': {'markets': len(markets), 'standardized_fair_cases': sum(c['basis']=='fair' for c in cases),
                      'old_actual_opening_anchors': len(anchors), 'actual_fixed_normal_episodes': len(actual),
                      'raw_bytes': sum(r['bytes'] for r in manifest), 'raw_events': sum(sum(r['events'].values()) for r in manifest),
                      'grid_valid': sum(r['p'] is not None for m in markets for r in m['rows']),
                      'grid_sigma_valid': sum(r['sigma'] is not None for m in markets for r in m['rows']),
                      'archive_comparison': dict(comparisons), 'issues': dict(issues)},
            'units': 'r_TP = USDC / original_share / second; sigma = price / sqrt(second)',
            'sigma_before240': quantiles(sigma_values), 'absolute_z2_before180': quantiles(z_values),
            'sl_specs': SL_SPECS, 'r_grid': RATES, 'candidates': full,
            'actual_positive_pair_rates': quantiles([r['realized_pair_rate'] for r in actual if r['pair_gross']>0]),
            'actual_pair_rates_all': quantiles([r['realized_pair_rate'] for r in actual]),
            'special_case_first_fast_signal': next(r for r in specials if r['fast_z15']),
            'source_manifest': SOURCES,
            'limitations': ['Standardized entries are price-path probes, not fixed NORMAL signals or fills.',
                            'Older NORMAL anchors use observed MATCHED time, not a known exchange execution time.',
                            'Right censor at invalid/generation-changing observation. No forward fill over unreliable quotes.',
                            'Quote crossing is NOT a maker fill and is NOT a final-PnL/EV label.',
                            'First-episode feasibility only; additional session budget commitments are not simulated.',
                            '1s grid can miss subsecond excursions; production must review every valid price change.',
                            'Chronological check is sensitivity of signals, not independent positive-EV validation.',
                            'No optimization objective for r is identifiable without execution / session payoff counterfactuals.']}
    (OUT/'evidence.json').write_text(json.dumps(data, indent=2, ensure_ascii=False)+'\n')
    print(json.dumps({k:v for k,v in data.items() if k in ['scope', 'sigma_before240', 'absolute_z2_before180', 'actual_positive_pair_rates', 'special_case_first_fast_signal']}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()

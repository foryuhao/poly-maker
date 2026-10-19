"""Read-only historical budget sizing; no orders, credentials, or strategy replay."""
import collections
import csv
import datetime
import hashlib
import json
import math
import statistics
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent
TZ = ZoneInfo('Asia/Shanghai')


def quantile(values, probability):
    values = sorted(values)
    position = (len(values) - 1) * probability
    lower, upper = math.floor(position), math.ceil(position)
    return values[lower] + (values[upper] - values[lower]) * (position - lower)


def distribution(values):
    return dict(n=len(values), minimum=min(values), maximum=max(values),
                **{f'p{int(p * 100)}': quantile(values, p)
                   for p in [.1, .25, .5, .75, .9, .95]})


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def analyze():
    coverage = collections.Counter()
    valid, actual, run_medians, manifests = [], [], [], []
    slots_seen, markets = set(), set()
    for summary_path in sorted((ROOT / 'research_outputs/rdp_risk_shadow').glob('*/*/market_summary.json')):
        risk_path = summary_path.parent / 'risk_snapshots.jsonl'
        summary = json.loads(summary_path.read_text())
        market = summary['market_slug']
        markets.add(market)
        values = []
        for line in risk_path.read_text().splitlines():
            row = json.loads(line)
            if not 15 <= row.get('elapsed_sec', -1) < 240:
                continue
            if row.get('record_type') == 'DECISION':
                coverage['excluded_decision_records'] += 1
                continue
            identity = (market, row['session_id'], row['risk_snapshot_id'])
            assert identity not in slots_seen, identity
            slots_seen.add(identity)
            coverage['base_slots_pre240'] += 1
            if row.get('volatility_validity') != 'VALID' or row.get('probability_validity') != 'VALID':
                coverage['unknown_or_invalid_vol_probability'] += 1
                continue
            variance = row['v_hat']
            assert isinstance(variance, (int, float)) and variance >= 0 and math.isfinite(variance)
            values.append(variance)
            valid.append(row)
            if row.get('inventory_validity') == 'VALID' and isinstance(row.get('delta'), (int, float)) and abs(row['delta']) >= .1:
                actual.append(row)
        if values:
            run_medians.append(5 * math.sqrt(statistics.median(values)))
        manifests.append(dict(path=str(risk_path.relative_to(ROOT)), sha256=digest(risk_path),
                              market=market, session_id=summary['session_id']))
    coverage['valid_vol_probability_slots'] = len(valid)
    coverage['valid_actual_nonzero_inventory_slots'] = len(actual)
    coverage['runs'] = len(manifests)
    coverage['unique_markets'] = len(markets)
    starts = [int(m.rsplit('-', 1)[1]) for m in markets]
    scope = dict(start=datetime.datetime.fromtimestamp(min(starts), TZ).isoformat(),
                 last_market_start=datetime.datetime.fromtimestamp(max(starts), TZ).isoformat(),
                 filter='15 <= elapsed_sec < 240; exclude DECISION; volatility and probability VALID',
                 grain='market / session / base risk_snapshot_id',
                 fixed_inventory_shares=5, timezone='Asia/Shanghai')
    variances = [row['v_hat'] for row in valid]
    sd1 = [5 * math.sqrt(v) for v in variances]
    wait_candidates = []
    for budget in [.1, .15, .2]:
        horizons = [math.inf if v == 0 else (budget / 5) ** 2 / v for v in variances]
        wait_candidates.append(dict(budget=budget, median_h_seconds=statistics.median(horizons),
                                    fraction_allowing_1s=sum(h >= 1 for h in horizons) / len(horizons),
                                    fraction_allowing_2s=sum(h >= 2 for h in horizons) / len(horizons),
                                    p10_h_seconds=quantile(horizons, .1)))
    grouped = {}
    for label, predicate in [('entry_period_15_120', lambda r: r['elapsed_sec'] < 120),
                             ('later_period_120_240', lambda r: r['elapsed_sec'] >= 120)]:
        grouped[label] = distribution([5 * math.sqrt(r['v_hat']) for r in valid if predicate(r)])

    # Verify the latest 13 entry lots against unique CONFIRMED fills in original journals.
    evidence_path = ROOT / 'docs/development/reviews/20261018_repair_design/evidence.json'
    observations = json.loads(evidence_path.read_text())['observations']
    journal_cache, entry_rows = {}, []
    for observation in observations:
        source = Path(observation['source'])
        if source not in journal_cache:
            results = [json.loads(line) for line in source.read_text().splitlines()]
            runs = [r['data']['payload'] for r in results if r.get('kind') == 'sm_run_result']
            assert len(runs) == 1
            journal_cache[source] = runs[0]
        run = journal_cache[source]
        window = next(w for w in run['windows'] if w['result']['window_id'].endswith(':' + observation['window']))
        assert window['state'] == 'NORMAL'
        order_ids = {p['order_id'] for p in window['result']['placement_results'] if p.get('order_id')}
        fills = {}
        for w in run['windows']:
            for fill in w['result'].get('confirmed_fills', []):
                if fill['maker_order_id'] in order_ids and fill['status'] == 'CONFIRMED':
                    key = (fill['trade_id'], fill['maker_order_id'])
                    if key in fills:
                        assert fills[key] == fill
                    fills[key] = fill
        quantity = sum(Decimal(str(f['size'])) for f in fills.values())
        cost = sum(Decimal(str(f['size'])) * Decimal(str(f['price'])) for f in fills.values())
        assert quantity == Decimal(str(observation['quantity']))
        assert cost == quantity * Decimal(str(observation['entry_price']))
        entry_rows.append(dict(market=observation['market'], window=observation['window'],
                               quantity=float(quantity), entry_price=observation['entry_price'],
                               initial_worst_loss=float(cost), source=source.name))
    entry_costs = [r['initial_worst_loss'] for r in entry_rows]
    loss_candidates = [dict(budget=b, below_budget=sum(c <= b for c in entry_costs),
                            total=len(entry_costs), above_budget=sum(c > b for c in entry_costs))
                       for b in [1.0, 1.5, 2.0]]
    conclusion = dict(B_loss=1.5, B_wait=.15, scope='per-episode acceptance / per-wait standard-deviation proxy',
                      stage='provisional shadow and replay candidates, not production defaults',
                      max_recheck_seconds=2.0,
                      rationale='B_loss is a chosen risk tolerance informed by entry costs; B_wait is sized from historical fixed-5-share local variance.')
    result = dict(scope=scope, coverage=dict(coverage),
                  fixed5_sd_1s=distribution(sd1),
                  fixed5_sd_2s=distribution([s * math.sqrt(2) for s in sd1]),
                  market_median_fixed5_sd_1s=distribution(run_medians),
                  actual_inventory_sd_1s=distribution([abs(r['delta']) * math.sqrt(r['v_hat']) for r in actual]),
                  period_sensitivity=grouped, wait_candidates=wait_candidates,
                  latest_entries=dict(date='2026-10-17 22:45-23:45 Asia/Shanghai',
                                      markets=len(journal_cache), count=len(entry_rows),
                                      initial_costs=distribution(entry_costs), loss_candidates=loss_candidates,
                                      rows=entry_rows),
                  recommendation=conclusion,
                  caveats=[
                      'Volatility data cover October 14-15; the latest entry cohort is October 17. They are separate populations.',
                      'Base slots are serially correlated; 8589 slots are not 8589 independent trades.',
                      'The fixed-5-share series is a standardized scenario based on observed variance, not actual inventory or realized loss.',
                      'Excluded missing/invalid risk slots are not zero risk; actual inventory is often UNKNOWN while orders are active.',
                      'Original matched timestamps were not preserved; local observations cannot establish exact execution latency.',
                      'Loss-budget counts describe initial isolated entry cost only, not all risk gates or counterfactual HOLD profitability.',
                      'No new-policy fill simulation, settlement-HOLD outcome backtest or parameter optimality test was performed.',
                      'B_loss is an acceptance budget, not a guaranteed realized stop; B_wait is a standard-deviation proxy, not VaR.',
                      'One-second and two-second projections assume locally constant variance and inventory.',
                  ],
                  source_manifests=manifests,
                  journal_manifests=[dict(filename=p.name, sha256=digest(p)) for p in sorted(journal_cache)])
    return result


if __name__ == '__main__':
    evidence = analyze()
    OUT.joinpath('evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')
    for filename, rows in [('entry_costs.csv', evidence['latest_entries']['rows']),
                           ('wait_candidates.csv', evidence['wait_candidates']),
                           ('loss_candidates.csv', evidence['latest_entries']['loss_candidates'])]:
        with OUT.joinpath(filename).open('w', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    print(json.dumps({k: evidence[k] for k in ['scope','coverage','fixed5_sd_1s','period_sensitivity','wait_candidates','recommendation']}, ensure_ascii=False, indent=2))
    print('Verified entry costs:', evidence['latest_entries']['initial_costs'])
    print('Loss budget sensitivities:', evidence['latest_entries']['loss_candidates'])

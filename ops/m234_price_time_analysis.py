"""Reusable, read-only M2/M3/M4 price and time analysis template."""

import argparse
import hashlib
import json
import os
import re
import sqlite3
import time
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import numpy as np

if __package__:
    from .m234_watch_metrics import strategy_dependency_drift
else:
    from m234_watch_metrics import strategy_dependency_drift

ROOT = Path(__file__).resolve().parents[1]
STUDY = ROOT / 'research_outputs/m2_virtual'
RUNS = STUDY / 'analysis/price_time_runs'
TEMPLATE_ID = 'm234-price-time-v2-text'
ZERO = Decimal(0)
TIMEZONE = ZoneInfo('Asia/Shanghai')



def summarize_rows(rows, split_epoch, rng, bootstrap_draws=20000):
    signals = [row for row in rows if Decimal(row['quantity']) > 0]
    fills = [row for row in rows if row['status'] == 'ASSUMED_FILL']
    settled = [row for row in fills if row['trade_pnl'] is not None]
    cost = sum((Decimal(row['cost']) for row in settled), ZERO)
    pnl = sum((Decimal(row['trade_pnl']) for row in settled), ZERO)
    by_market = defaultdict(lambda: [ZERO, ZERO])
    for row in settled:
        by_market[row['slug']][0] += Decimal(row['trade_pnl'])
        by_market[row['slug']][1] += Decimal(row['cost'])
    early = [row for row in settled if split_epoch is not None and int(row['slug'].rsplit('-', 1)[1]) < split_epoch]
    late = [row for row in settled if split_epoch is not None and int(row['slug'].rsplit('-', 1)[1]) >= split_epoch]
    best_market = max((values[0] for values in by_market.values()), default=ZERO)
    roi_interval = None
    wins = sum(Decimal(row['trade_pnl']) > 0 for row in settled)
    if len(by_market) >= 30 and 0 < wins < len(settled):
        values = np.array([[float(p), float(c)] for p, c in by_market.values()])
        draws = rng.integers(0, len(values), size=(bootstrap_draws, len(values)))
        sums = values[draws].sum(axis=1)
        estimates = sums[:, 0] / sums[:, 1]
        roi_interval = np.quantile(estimates, [.025, .975]).tolist()
    return {
        'signals': len(signals),
        'signal_markets': len({row['slug'] for row in signals}),
        'assumed_fills': len(fills),
        'settled_fills': len(settled),
        'pending_fills': len(fills) - len(settled),
        'settled_markets': len(by_market),
        'direction_skips': sum(row['reason'] == 'DIRECTION_ENTRY_LIMIT_REACHED' for row in rows),
        'wins': wins,
        'losses': sum(Decimal(row['trade_pnl']) < 0 for row in settled),
        'cost': str(cost),
        'pnl': str(pnl),
        'roi': float(pnl / cost) if cost else None,
        'mean_pnl_per_market': float(pnl / len(by_market)) if by_market else None,
        'roi_cluster_bootstrap_95': roi_interval,
        'early_pnl': str(sum((Decimal(row['trade_pnl']) for row in early), ZERO)),
        'late_pnl': str(sum((Decimal(row['trade_pnl']) for row in late), ZERO)),
        'early_markets': len({row['slug'] for row in early}),
        'late_markets': len({row['slug'] for row in late}),
        'pnl_without_best_market': str(pnl - best_market) if by_market else None,
        'best_market_pnl': str(best_market) if by_market else None,
        'worst_market_pnl': str(min(values[0] for values in by_market.values())) if by_market else None,
    }


def analyze(version=None, *, bootstrap_draws=20000, seed=20261106):
    """Read one consistent SQLite snapshot and retain version-specific evidence."""
    if version is None:
        latest = json.loads((STUDY / 'latest.json').read_text())
        version = latest['active_version']
    captured_at = time.time()
    rng = np.random.default_rng(seed)
    connection = sqlite3.connect((STUDY / 'state.sqlite3').as_uri() + '?mode=ro', uri=True, timeout=3)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA query_only=ON')
    connection.execute('BEGIN')
    market_query = '''SELECT m.*,r.id run_id,r.state run_state,r.started,r.ended,r.counts
        FROM runs r JOIN markets m ON m.slug=r.slug WHERE r.version=? ORDER BY m.epoch'''
    markets = [dict(row) for row in connection.execute(market_query, (version,))]
    sample_queries = {
        'M2': "SELECT s.* FROM samples s WHERE s.version=? AND s.rule='all_candidates' ORDER BY wall,id",
        'M3': "SELECT s.* FROM strategy_samples s WHERE s.version=? AND s.strategy='M3' ORDER BY wall,id",
        'M4': "SELECT s.* FROM strategy_samples s WHERE s.version=? AND s.strategy='M4' ORDER BY wall,id",
    }
    all_rows = {
        name: [dict(row) for row in connection.execute(query, (version,))]
        for name, query in sample_queries.items()
    }
    connection.rollback()
    connection.close()
    market_index = {market['slug']: market for market in markets}
    complete = [market for market in markets if market['run_state'] == 'CLOSED' and market['full_window'] == 1]
    eligible = [market for market in complete if market['winner'] is not None]
    eligible_slugs = {market['slug'] for market in eligible}
    split_epoch = None
    if eligible:
        first_epoch = min(market['epoch'] for market in eligible)
        last_epoch = max(market['epoch'] for market in eligible)
        split_epoch = (first_epoch + last_epoch + 300) / 2

    for market in eligible:
        resolution = json.loads(market['resolution'])
        metadata = json.loads(market['metadata'])
        tokens = {'YES': metadata['tokens'][0]['token_id'], 'NO': metadata['tokens'][1]['token_id']}
        prices = [Decimal(str(value)) for value in resolution['outcome_prices']]
        assert sorted(prices) == [ZERO, Decimal(1)]
        assert resolution['closed'] is True and resolution['resolution_status'] == 'resolved'
        assert resolution['condition_id'] == metadata['condition_id']
        assert resolution['token_ids'] == tokens and set(resolution['clob_token_ids']) == set(tokens.values())
        assert resolution['clob_token_ids'][prices.index(Decimal(1))] == tokens[market['winner']]
        assert resolution['winner'] == market['winner'] and resolution['slug'] == market['slug']




    results = {}
    for name, history in all_rows.items():
        root_field = 'ap_id' if name == 'M2' else 'root'
        roots = [(row['slug'], row[root_field]) for row in history]
        assert len(roots) == len(set(roots))
        directions = Counter()
        spent = defaultdict(lambda: ZERO)
        for row in history:
            assert 0 < Decimal(row['price']) < 1 and 0 <= row['elapsed'] < 300
            assert Decimal(row['price']) * Decimal(row['quantity']) == Decimal(row['cost']) <= 5
            market = market_index[row['slug']]
            if row['status'] == 'ASSUMED_FILL':
                directions[(row['slug'], row['outcome'])] += 1
                spent[row['slug']] += Decimal(row['cost'])
                if market['winner'] is None:
                    assert row['trade_pnl'] is None
                else:
                    expected = (Decimal(row['quantity']) if row['outcome'] == market['winner'] else ZERO) - Decimal(row['cost'])
                    assert Decimal(row['trade_pnl']) == expected
            else:
                assert row['trade_pnl'] is None
        assert max(directions.values(), default=0) <= 1
        assert all(value <= 20 for value in spent.values())
        rows = [row for row in history if row['slug'] in eligible_slugs]
        price_groups = []
        time_groups = []
        joint_groups = []
        for price_bin in range(10):
            selected = [row for row in rows if int(Decimal(row['price']) * 10) == price_bin]
            price_groups.append({'bin': price_bin, 'label': f'{price_bin/10:.1f}–{(price_bin+1)/10:.1f}', **summarize_rows(selected, split_epoch, rng, bootstrap_draws)})
            for time_bin in range(10):
                selected_joint = [row for row in selected if int(row['elapsed'] // 30) == time_bin]
                joint_groups.append({'price_bin': price_bin, 'time_bin': time_bin, **summarize_rows(selected_joint, split_epoch, rng, bootstrap_draws)})
        for time_bin in range(10):
            selected = [row for row in rows if int(row['elapsed'] // 30) == time_bin]
            time_groups.append({'bin': time_bin, 'label': f'{time_bin*30}–{(time_bin+1)*30}s', **summarize_rows(selected, split_epoch, rng, bootstrap_draws)})
        total = summarize_rows(rows, split_epoch, rng, bootstrap_draws)
        for groups in [price_groups, time_groups, joint_groups]:
            assert sum(group['settled_fills'] for group in groups) == total['settled_fills']
            assert sum((Decimal(group['pnl']) for group in groups), ZERO) == Decimal(total['pnl'])
            assert sum((Decimal(group['cost']) for group in groups), ZERO) == Decimal(total['cost'])
            assert sum(group['signals'] for group in groups) == total['signals']
        results[name] = {'current_inventory': summarize_rows(history, split_epoch, rng, bootstrap_draws), 'eligible_summary': total, 'price': price_groups, 'time': time_groups, 'joint': joint_groups, 'eligible_rows': rows, 'pending_rows': [row for row in history if row['status'] == 'ASSUMED_FILL' and row['trade_pnl'] is None], 'zero_signal_eligible_markets': len(eligible_slugs - {row['slug'] for row in rows if Decimal(row['quantity']) > 0})}

    definition = json.loads((STUDY / f'definition-{version[:16]}.json').read_text())
    source_drift = []
    relevant_drift, excluded_dashboard_drift = strategy_dependency_drift(
        ROOT / 'src/polymaker', definition['sources'],
        ROOT / 'research_outputs/m2_watch/source_baselines',
    )
    for name in relevant_drift:
        expected = definition['sources'][name]
        actual = hashlib.sha256((ROOT / 'src/polymaker' / name).read_bytes()).hexdigest()
        if actual != expected:
            source_drift.append({'file': name, 'frozen_sha256': expected, 'disk_sha256': actual})
    analysis = {
        'captured_at': captured_at,
        'as_of_beijing': datetime.fromtimestamp(captured_at, ZoneInfo('Asia/Shanghai')).isoformat(),
        'version': version,
        'period_start': min((market['epoch'] for market in eligible), default=None),
        'last_eligible_market_epoch': max((market['epoch'] for market in eligible), default=None),
        'split_epoch': split_epoch,
        'markets': {'observed': len(markets), 'closed_full': len(complete), 'closed_full_officially_settled': len(eligible), 'full_pending': len(complete)-len(eligible)},
        'settings': definition['settings'],
        'source_drift': source_drift,
        'excluded_dashboard_only_drift': excluded_dashboard_drift,
        'strategies': results,
        'queries': {'markets': market_query, 'samples': sample_queries},
        'methodology': {'price_buckets': '[0.0,0.1),…,[0.9,1.0), legal prices strictly between 0 and 1', 'time_buckets': 'market elapsed seconds [0,30),…,[270,300), stored elapsed lower bound', 'pnl_population': 'current version, closed full-window markets with official outcome, ASSUMED_FILL only', 'signal_population': 'positive quantity first-qualified root, includes direction-cap skips', 'pending': 'kept separate; no zero PnL imputation', 'bootstrap': f'{bootstrap_draws} fixed-seed percentile draws resampling whole active markets within each bucket, ROI as ratio of summed PnL to cost; only >=30 markets and both wins and losses; exploratory and unadjusted for multiple comparisons', 'halves': 'one global chronological midpoint of eligible market windows', 'stability': 'early/late PnL and leave-best-market-out PnL', 'coverage': 'zero-signal eligible markets retained in cohort counts; full_window does not establish gap-free input', 'execution': 'assumed full fill at observed anchor price and official hold-to-settlement'},
    }
    return analysis


def validate_snapshot(analysis):
    """Check saved inputs and additive totals before creating a report."""
    if not re.fullmatch(r'[0-9a-f]{64}', analysis['version']):
        raise ValueError('Invalid collector version')
    for strategy in ('M2', 'M3', 'M4'):
        result = analysis['strategies'][strategy]
        summary = result['eligible_summary']
        for dimension, expected_count in (('price', 10), ('time', 10), ('joint', 100)):
            groups = result[dimension]
            if len(groups) != expected_count:
                raise ValueError(f'{strategy} has incomplete {dimension} buckets')
            for metric in ('settled_fills', 'signals'):
                if sum(group[metric] for group in groups) != summary[metric]:
                    raise ValueError(f'{strategy} {dimension} {metric} does not reconcile')
            for metric in ('pnl', 'cost'):
                total = sum((Decimal(group[metric]) for group in groups), ZERO)
                if total != Decimal(summary[metric]):
                    raise ValueError(f'{strategy} {dimension} {metric} does not reconcile')


def select_minimum_price(analysis, minimum):
    """Select recorded entries while retaining their original direction-cap status."""
    if not minimum.is_finite() or not ZERO <= minimum < 1:
        raise ValueError('Minimum price must be finite and satisfy 0 <= price < 1')
    existing_minimum = Decimal(analysis.get('selection', {}).get('minimum_price', '0'))
    if minimum < existing_minimum:
        raise ValueError('A lower price threshold requires the original unfiltered snapshot')
    selected = deepcopy(analysis)
    rng = np.random.default_rng(20261106)
    split_epoch = analysis['split_epoch']
    eligible_markets = analysis['markets']['closed_full_officially_settled']
    selected['selection'] = {
        'minimum_price': str(minimum),
        'boundary': 'inclusive',
        'scope': 'Filter existing recorded rows and preserve ASSUMED_FILL/SKIPPED status',
    }
    selected['before_price_selection'] = {
        name: result['eligible_summary'] for name, result in analysis['strategies'].items()
    }
    for result in selected['strategies'].values():
        rows = [row for row in result['eligible_rows'] if Decimal(row['price']) >= minimum]
        pending = [row for row in result['pending_rows'] if Decimal(row['price']) >= minimum]
        result['eligible_rows'] = rows
        result['pending_rows'] = pending
        result['selected_pending_fills'] = len(pending)
        result['current_inventory_scope'] = 'Unfiltered source-version inventory'
        result['eligible_summary'] = summarize_rows(rows, split_epoch, rng)
        result['zero_signal_eligible_markets'] = eligible_markets - len({
            row['slug'] for row in rows if Decimal(row['quantity']) > 0
        })
        result['price'] = []
        result['time'] = []
        result['joint'] = []
        for price_bin in range(10):
            price_rows = [row for row in rows if int(Decimal(row['price']) * 10) == price_bin]
            result['price'].append({
                'bin': price_bin,
                'label': f'{price_bin/10:.1f}–{(price_bin+1)/10:.1f}',
                **summarize_rows(price_rows, split_epoch, rng),
            })
            for time_bin in range(10):
                joint_rows = [row for row in price_rows if int(row['elapsed'] // 30) == time_bin]
                result['joint'].append({
                    'price_bin': price_bin, 'time_bin': time_bin,
                    **summarize_rows(joint_rows, split_epoch, rng),
                })
        for time_bin in range(10):
            time_rows = [row for row in rows if int(row['elapsed'] // 30) == time_bin]
            result['time'].append({
                'bin': time_bin, 'label': f'{time_bin*30}–{(time_bin+1)*30}s',
                **summarize_rows(time_rows, split_epoch, rng),
            })
    validate_snapshot(selected)
    return selected


def display_number(value, *, signed=False):
    if value is None:
        return '—'
    return format(Decimal(str(value)), '+.2f' if signed else '.2f')


def summary_markdown(analysis):
    """Keep the approved comparison layout and explicit sample denominators."""
    lines = [
        '# M2 / M3 / M4 价格与时间分桶分析',
        '',
        f"数据快照为 {analysis['as_of_beijing']}。",
        f"采集版本为 `{analysis['version']}`。",
        f"官方已结算完整窗口市场共 {analysis['markets']['closed_full_officially_settled']} 场。",
        '',
        '| 策略 | 已结算开仓 | 独立交易市场 | PnL（USDC） | ROI | 当前版本待结算 |',
        '| --- | ---: | ---: | ---: | ---: | ---: |',
    ]
    if analysis.get('selection'):
        minimum = analysis['selection']['minimum_price']
        lines[5:5] = ['', f'本次筛选既有模拟开仓记录，价格 ≥ {minimum}。']
        lines = [line.replace('当前版本待结算', '筛选范围待结算') for line in lines]
    for name, result in analysis['strategies'].items():
        summary = result['eligible_summary']
        roi = '—'
        if summary['roi'] is not None:
            roi = display_number(summary['roi'] * 100, signed=True) + '%'
        pending = result.get('selected_pending_fills', result['current_inventory']['pending_fills'])
        lines.append(f"| {name} | {summary['settled_fills']} | {summary['settled_markets']} | "
                     f"{display_number(summary['pnl'], signed=True)} | {roi} | {pending} |")
    for dimension, title in (('price', '价格分桶'), ('time', '开场后时间分桶')):
        lines.extend(['', f'## {title}', '', '各桶左闭右开。单元格为已结算笔数 / PnL（USDC）。', '',
                      '| 分桶 | M2 | M3 | M4 |', '| --- | ---: | ---: | ---: |'])
        for index in range(10):
            label = analysis['strategies']['M2'][dimension][index]['label']
            cells = []
            for result in analysis['strategies'].values():
                group = result[dimension][index]
                pnl = display_number(group['pnl'], signed=True) if group['settled_fills'] else '—'
                cells.append(f"{group['settled_fills']} / {pnl}")
            lines.append('| ' + ' | '.join([label, *cells]) + ' |')
    lines.extend(['', '## 稳定性', '',
                  '| 策略 | 前半段 PnL | 后半段 PnL | 最佳单场 PnL | 去最佳场后 PnL |',
                  '| --- | ---: | ---: | ---: | ---: |'])
    for name, result in analysis['strategies'].items():
        summary = result['eligible_summary']
        values = [display_number(summary[key], signed=True)
                  for key in ('early_pnl', 'late_pnl', 'best_market_pnl', 'pnl_without_best_market')]
        lines.append('| ' + ' | '.join([name, *values]) + ' |')
    lines.extend(['',
                  'PnL 采用锚定价足额成交、持有至官方结算的模拟假设。',
                  '同方向限笔跳过的根信号计入研究库存，模拟 PnL 仅计 ASSUMED_FILL。',
                  '前后半段按共同的市场时间中点划分。各桶独立市场数允许重叠。',
                  '完整窗口包含输入被判无效或重连的片段，分桶结果属于探索分析。'])
    if analysis.get('source_drift'):
        files = ', '.join(item['file'] for item in analysis['source_drift'])
        lines.extend(['', f'公共依赖文件与冻结版本存在哈希差异，涉及 `{files}`。'])
    return '\n'.join(lines) + '\n'



def write_run(analysis, output):
    """Use a new directory for each run and publish the manifest last."""
    output.mkdir(parents=True, exist_ok=False)
    contents = {
        'analysis.json': json.dumps(analysis, ensure_ascii=False, indent=2, allow_nan=False) + '\n',
        'summary.md': summary_markdown(analysis),
    }
    hashes = {}
    for name, content in contents.items():
        destination = output / name
        temporary = output / (name + '.tmp')
        temporary.write_text(content)
        os.replace(temporary, destination)
        hashes[name] = hashlib.sha256(destination.read_bytes()).hexdigest()
    manifest = {
        'template_id': TEMPLATE_ID,
        'created_at': datetime.now(TIMEZONE).isoformat(),
        'source_snapshot_at': analysis['as_of_beijing'],
        'collector_version': analysis['version'],
        'files_sha256': hashes,
        'status': 'COMPLETE',
    }
    temporary = output / 'manifest.json.tmp'
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    os.replace(temporary, output / 'manifest.json')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument('--version', help='full frozen collector version; defaults to the active version')
    source.add_argument('--from-snapshot', type=Path, help='render an existing frozen analysis JSON')
    parser.add_argument('--min-price', help='inclusive analysis-only price floor on recorded rows')
    parser.add_argument('--output', type=Path, help='new output directory; existing directories are rejected')
    args = parser.parse_args()
    if args.version and not re.fullmatch(r'[0-9a-f]{64}', args.version):
        parser.error('--version requires a full 64-character collector version')
    analysis = json.loads(args.from_snapshot.read_text()) if args.from_snapshot else analyze(args.version)
    validate_snapshot(analysis)
    if args.min_price is not None:
        try:
            analysis = select_minimum_price(analysis, Decimal(args.min_price))
        except (InvalidOperation, ValueError) as error:
            parser.error(str(error))
    name = datetime.now(TIMEZONE).strftime('%Y%m%dT%H%M%S')
    output = args.output or RUNS / f"{name}-{analysis['version'][:8]}-{uuid4().hex[:8]}"
    manifest = write_run(analysis, output)
    print(json.dumps({'output': str(output.resolve()), 'manifest': manifest}, ensure_ascii=False))


if __name__ == '__main__':
    main()

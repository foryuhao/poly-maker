"""Owned CONFIRMED fills + exact public final market results, no account APIs."""
import argparse
import asyncio
import json
import sqlite3
import time
from collections import defaultdict
from contextlib import closing
from datetime import datetime
from decimal import Decimal as D
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from polymaker.dashboard.settlement import validate_resolution
from polymaker.m4_control import atomic_json
from polymaker.m4_types import M4_LEDGER, M6_LEDGER

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output-dir', type=Path, required=True)
parser.add_argument('--project-dir', type=Path, default=Path(__file__).resolve().parents[1])
parser.add_argument('--replay-dir', type=Path, help='Use saved snapshot/results only; no network or live ledger reads.')
parser.add_argument('--strategy', choices=('M4', 'M6'), default='M4')
args = parser.parse_args()
PROJECT = args.project_dir.resolve()
OUT = args.output_dir.resolve()
if not OUT.is_relative_to(PROJECT / 'research_outputs'):
    parser.error('--output-dir must be inside project research_outputs')
OUT.mkdir(parents=True, exist_ok=True)
ROOT = PROJECT / 'research_outputs/m4_live'
SECONDARY = args.strategy == 'M6'
STRATEGY = M6_LEDGER if SECONDARY else M4_LEDGER
BJ = ZoneInfo('Asia/Shanghai')
F = '0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d'
if args.replay_dir:
    snapshot = json.loads((args.replay_dir / 'snapshot.json').read_text())
    if snapshot.get('strategy', 'M4') != args.strategy:
        parser.error('replay snapshot belongs to a different strategy')
    markets = snapshot['markets']
    ledger_count = snapshot['owned_ledgers_scanned']
    atomic_json(OUT / 'snapshot.json', snapshot)
else:
    began = time.time()
    markets, unresolved, ignored, orders = [], [], [], set()
    ledger_count = 0
    for path in sorted((ROOT / ('m6-ledgers' if SECONDARY else 'ledgers')).glob('*.sqlite3')):
        if path.is_symlink() or path.resolve() != path:
            raise ValueError('TAKER_PNL_LEDGER_PATH_INVALID')
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=2)) as db:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN')
            meta = {r['key']: json.loads(r['value']) for r in db.execute('SELECT * FROM metadata')}
            identity = meta.get('identity', {})
            if (identity.get('mode') != 'LIVE' or identity.get('strategy') != STRATEGY
                    or identity.get('funder', '').lower() != F
                    or meta.get('settings', {}).get('execution_revision') != 'm4-taker-budget-v1'):
                ignored.append(str(path))
                continue
            ledger_count += 1
            attempts = [dict(r) for r in db.execute('SELECT * FROM taker_attempts ORDER BY seq')]
            evidence = {r['trade_id']: dict(r) for r in db.execute('SELECT * FROM taker_trade_evidence')}
        market = meta['market']
        epoch = int(market['slug'].rsplit('-', 1)[1])
        tokens = {'YES': market['tokens'][0]['token_id'], 'NO': market['tokens'][1]['token_id']}
        assert [z['outcome'] for z in market['tokens']] == ['Up', 'Down']
        confirmed = []
        for a in attempts:
            if a['state'] in ('ACK', 'UNKNOWN', 'SETTLING'):
                unresolved.append({'epoch': epoch, 'order_id': a['order_id'], 'state': a['state'],
                                   'filled': a['filled'], 'spent': a['spent']})
            if D(a['filled']) <= 0:
                continue
            assert a['order_id'] not in orders
            orders.add(a['order_id'])
            proof = json.loads(a['proof'])
            assert a['state'] == 'TERMINAL' and proof['kind'] == 'OWNED_TERMINAL_ORDER_AND_TRADES'
            owned_token = proof['order']['asset_id']
            assert proof['order']['id'] == a['order_id'] and owned_token in tokens.values()
            quantity = principal = fee = D(0)
            trade_keys = set()
            for t in proof['trades']:
                assert t['status'] in {'CONFIRMED', 'FAILED'} and t['our_side'] == 'BUY'
                assert t['maker_order_id'] == a['order_id'] and t['token_id'] == owned_token
                assert t['market_id'].lower() == market['condition_id'].lower()
                key = (a['order_id'], t['trade_id'])
                assert key not in trade_keys
                trade_keys.add(key)
                ev = evidence[t['trade_id']]
                ec = json.loads(ev['economics'])
                assert ev['status'] == t['status'] and ev['order_id'] == a['order_id']
                assert all(str(ec[k]) == str(t[k]) for k in ['maker_order_id', 'token_id', 'principal_usdc', 'fee_usdc', 'size'])
                if t['status'] == 'FAILED':
                    continue
                quantity += D(str(t['size']))
                principal += D(t['principal_usdc'])
                fee += D(t['fee_usdc'])
            assert quantity == D(a['filled']) and principal + fee == D(a['spent'])
            confirmed.append({'task_id': a['task_id'], 'order_id': a['order_id'],
                              'quantity': str(quantity), 'principal_usdc': str(principal),
                              'fee_usdc': str(fee), 'all_in_usdc': a['spent'],
                              'token_id': owned_token, 'proof': proof})
        if confirmed:
            markets.append({'epoch': epoch, 'label': datetime.fromtimestamp(epoch, BJ).strftime('%m-%d %H:%M'),
                            'ledger_path': str(path.resolve()), 'expected': {'slug': market['slug'],
                            'condition_id': market['condition_id'], 'token_ids': tokens}, 'confirmed_orders': confirmed})
    snapshot = {'observed_at': began, 'read_completed_at': time.time(),
                'strategy': args.strategy,
                'beijing_time': datetime.fromtimestamp(began, BJ).isoformat(),
                'scope': ('Local live M6 exact-owned CONFIRMED fills' if SECONDARY else
                          'All local LIVE m4-taker-budget-v1 exact-owned CONFIRMED fills, including the historical first-fill trial; no baseline, Maker or other executor.'),
                'read_mode': 'mode=ro,query_only,BEGIN', 'owned_ledgers_scanned': ledger_count,
                'markets': markets, 'unresolved_attempts_excluded': unresolved,
                'excluded_other_ledgers': ignored, 'production_ledger_writes': False,
                'account_trade_or_position_queries': [], 'service_actions': [], 'order_actions': []}
    atomic_json(OUT / 'snapshot.json', snapshot)
    print('snapshot', snapshot['beijing_time'], 'owned_ledgers', ledger_count, 'filled_markets', len(markets), flush=True)

async def resolve_all():
    if not markets:
        return []
    semaphore = asyncio.Semaphore(4)
    async with httpx.AsyncClient(timeout=8, follow_redirects=False) as client:
        async def read(m):
            url = 'https://gamma-api.polymarket.com/markets/slug/' + m['expected']['slug']
            for attempt in range(2):
                async with semaphore:
                    try:
                        response = await client.get(url)
                        response.raise_for_status()
                        raw = response.json()
                        result = validate_resolution(raw, m['expected'])
                        return {'epoch': m['epoch'], 'url': url, 'observed_at': time.time(),
                                'http_status': response.status_code, 'raw': raw, 'resolution': result, 'error': None}
                    except (httpx.HTTPError, ValueError, KeyError) as exc:
                        if attempt:
                            return {'epoch': m['epoch'], 'url': url, 'observed_at': time.time(),
                                    'resolution': None, 'error': type(exc).__name__}
            raise AssertionError('unreachable')
        return await asyncio.gather(*(read(m) for m in markets))


if args.replay_dir:
    saved_results = json.loads((args.replay_dir / 'official-resolutions.json').read_text())
    if saved_results.get('strategy', 'M4') != args.strategy:
        parser.error('replay results belong to a different strategy')
    resolutions = saved_results['markets']
else:
    resolutions = asyncio.run(resolve_all())
atomic_json(OUT / 'official-resolutions.json', {'observed_at': max((r['observed_at'] for r in resolutions), default=snapshot['read_completed_at']), 'markets': resolutions,
            'strategy': args.strategy,
            'scope': ('Offline replay of saved official results; no network or live ledger reads.'
                      if args.replay_dir else 'Public exact-owned-market result GET only; no account, trade, position or order API call.')})
results = {r['epoch']: r for r in resolutions}
rows, pending, resolved_signals, pending_signals = [], [], [], []
for m in sorted(markets, key=lambda item: item['epoch']):
    result = results[m['epoch']]
    quantity = sum((D(a['quantity']) for a in m['confirmed_orders']), D(0))
    principal = sum((D(a['principal_usdc']) for a in m['confirmed_orders']), D(0))
    fee = sum((D(a['fee_usdc']) for a in m['confirmed_orders']), D(0))
    cost = sum((D(a['all_in_usdc']) for a in m['confirmed_orders']), D(0))
    assert principal + fee == cost
    row = {'epoch': m['epoch'], 'label': m['label'], 'confirmed_quantity': str(quantity),
           'principal_usdc': str(principal), 'fee_usdc': str(fee), 'all_in_usdc': str(cost),
           'resolution_url': result['url'], 'resolution_observed_at': result['observed_at']}
    resolution = result['resolution']
    winner = resolution['token_ids'][resolution['winner']] if resolution else None
    by_signal = defaultdict(list)
    for order in m['confirmed_orders']:
        by_signal[order['task_id']].append(order)
    signal_rows = []
    for task_id, orders in by_signal.items():
        token, = {order['token_id'] for order in orders}
        shares = sum((D(order['quantity']) for order in orders), D(0))
        principal_paid = sum((D(order['principal_usdc']) for order in orders), D(0))
        fee_paid = sum((D(order['fee_usdc']) for order in orders), D(0))
        payout = (shares if token == winner else D(0)) if resolution else None
        signal_rows.append({'strategy': args.strategy, 'epoch': m['epoch'], 'label': m['label'],
                            'task_id': task_id, 'outcome': next(k for k, v in m['expected']['token_ids'].items() if v == token),
                            'order_ids': [order['order_id'] for order in orders], 'confirmed_quantity': str(shares),
                            'principal_usdc': str(principal_paid), 'fee_usdc': str(fee_paid),
                            'all_in_usdc': str(principal_paid + fee_paid),
                            'payout_usdc': str(payout) if payout is not None else None,
                            'pnl_usdc': str(payout - principal_paid - fee_paid) if payout is not None else None,
                            'error': result['error']})
    assert sum((D(signal['all_in_usdc']) for signal in signal_rows), D(0)) == cost
    if resolution is None:
        pending.append({**row, 'pnl_usdc': None, 'error': result['error']})
        pending_signals.extend(signal_rows)
        continue
    payout = sum((D(a['quantity']) for a in m['confirmed_orders'] if a['token_id'] == winner), D(0))
    assert sum((D(signal['pnl_usdc']) for signal in signal_rows), D(0)) == payout - cost
    rows.append({**row, 'winner': resolution['winner'], 'payout_usdc': str(payout), 'pnl_usdc': str(payout - cost)})
    resolved_signals.extend(signal_rows)

balance = peak = max_dd = D(0)
peak_row = max_peak = trough = None
curve = [D(0)]
for row in rows:
    balance += D(row['pnl_usdc'])
    if balance > peak:
        peak, peak_row = balance, row.copy()
    dd = peak - balance
    if dd > max_dd:
        max_dd, max_peak, trough = dd, peak_row, row.copy()
    row.update(cumulative_usdc=str(balance), running_peak_usdc=str(peak), drawdown_usdc=str(dd))
    curve.append(balance)
assert max_dd == max((curve[i] - curve[j] for i in range(len(curve)) for j in range(i, len(curve))), default=D(0))
assert balance == sum((D(r['payout_usdc']) - D(r['all_in_usdc']) for r in rows), D(0))
assert len(rows) + len(pending) == len(markets)
summary = {'strategy': args.strategy,
           'pnl_status': ('NO_CONFIRMED_FILLS' if not markets else 'RESOLVED_SUBSET' if rows and pending
                          else 'AWAITING_RESULTS' if pending else 'RESOLVED'),
           'snapshot_beijing': snapshot['beijing_time'], 'resolution_read_completed_at': max((r['observed_at'] for r in resolutions), default=snapshot['read_completed_at']),
           'owned_ledgers_scanned': ledger_count, 'confirmed_filled_markets': len(markets),
           'confirmed_filled_signals': len(resolved_signals) + len(pending_signals),
           'resolved_signals': len(resolved_signals), 'pending_signals': len(pending_signals),
           'positive_signals': sum(D(s['pnl_usdc']) > 0 for s in resolved_signals),
           'negative_signals': sum(D(s['pnl_usdc']) < 0 for s in resolved_signals),
           'resolved_markets': len(rows), 'pending_markets': len(pending),
           'result_read_errors': sum(r['error'] is not None for r in pending),
           'official_result_coverage_complete': not pending,
           'drawdown_scope': ('COMPLETE_CONFIRMED_MARKET_HISTORY' if not pending
                              else 'RESOLVED_SUBSET_ONLY_NOT_COMPLETE_HISTORY'),
           'positive_markets': sum(D(r['pnl_usdc']) > 0 for r in rows),
           'negative_markets': sum(D(r['pnl_usdc']) < 0 for r in rows),
           'resolved_net_pnl_usdc': str(balance), 'maximum_drawdown_usdc': str(max_dd),
           'current_drawdown_usdc': str(peak - balance), 'peak': max_peak, 'trough': trough,
           'resolved_cost_usdc': str(sum((D(r['all_in_usdc']) for r in rows), D(0))),
           'resolved_fee_usdc': str(sum((D(r['fee_usdc']) for r in rows), D(0))),
           'pending_cost_usdc': str(sum((D(r['all_in_usdc']) for r in pending), D(0))),
           'drawdown_percentage': None,
           'definition': 'Final official binary-result payout of owned CONFIRMED fills minus actual principal and fee, grouped chronologically by five-minute market. Not account NAV or verified redemption receipts; no unresolved mark-to-market PnL; no percentage without a funding baseline.',
           'execution_mode': 'OFFLINE_REPLAY' if args.replay_dir else 'OWNED_LOCAL_AND_PUBLIC_FINAL_RESULTS',
           'validation': {'owned_order_token_trade_cost_reconciliation': 'PASS', 'official_slug_condition_token_final_result': 'PASS_FOR_RESOLVED', 'independent_quadratic_drawdown': 'PASS'},
           'account_trade_or_position_queries': [], 'production_ledger_writes': False}
atomic_json(OUT / 'analysis.json', {'summary': summary, 'resolved_markets': rows, 'pending_resolution_markets': pending,
            'resolved_signals': resolved_signals, 'pending_resolution_signals': pending_signals})
print(json.dumps(summary, ensure_ascii=False), flush=True)

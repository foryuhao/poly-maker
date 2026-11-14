"""Public resolution of modeled M4 receipts only. Never reads an account."""
import asyncio
import json
import time
from contextlib import suppress
from decimal import Decimal

from polymaker.m4_config import STRATEGY, M4Config
from polymaker.m4_control import atomic_json
from polymaker.m4_fill_model import EVIDENCE_POLICY, INSTANT_MODEL, LEGACY_POLICY, POLICY, recover_observations
from polymaker.m4_state import M4Ledger


def pending(root, completed_through=None, excluded=()):
    """Recover expired local sessions using official time; never submit an order."""
    result = []
    for path in sorted((root/'ledgers').glob('btc-updown-5m-*.sqlite3')):
        if path in excluded:
            continue
        if path.is_symlink() or path.resolve() != path:
            raise ValueError('M4_BASELINE_SYMLINK_FORBIDDEN')
        metadata = M4Ledger.metadata(path)
        if not metadata:
            # Opening SQLite can create an empty file before an ENOSPC failure.
            # metadata() has verified that it has no tables, hence no order or
            # risk facts. Preserve the file without blocking later settlements.
            continue
        identity = metadata.get('identity', {})
        if identity.get('strategy') != STRATEGY or identity.get('mode') != 'VIRTUAL':
            raise ValueError('M4_BASELINE_FOREIGN_IDENTITY')
        market = metadata.get('market')
        if market is None and not metadata.get('session_id'):
            continue  # A crash before atomic session initialization created no signals.
        if not market or market['slug'] != path.stem or market['condition_id'] != identity.get('market_id'):
            raise ValueError('M4_BASELINE_MARKET_IDENTITY')
        if not metadata.get('complete') and completed_through is not None:
            settings = M4Config.model_validate(metadata['settings'])
            if settings.fingerprint != identity.get('settings_hash'):
                raise ValueError('M4_RECOVERY_SETTINGS_INVALID')
            if int(market['slug'].rsplit('-', 1)[1])+settings.close_sec <= completed_through:
                book = M4Ledger(path, required_identity(identity))
                try:
                    if book.get('baseline_fill_model') in (POLICY, LEGACY_POLICY):
                        recover_observations(book, wall=completed_through)
                    with book.lock, book.db:
                        book.db.execute("""UPDATE opportunities SET state=CASE WHEN state='OBSERVED'
                            THEN 'SKIPPED' ELSE 'ABORTED' END, reason='M4_EXPIRED_SESSION_RECOVERY'
                            WHERE started=0 AND state IN ('OBSERVED','RESERVED','PREPARED')""")
                        values = {'complete': True, 'completion_reason': 'M4_EXPIRED_SESSION_RECOVERY',
                            'settlement_policy':'PUBLIC_RESOLUTION_OF_MODELED_RECEIPTS',
                            'settlement_status':'AWAITING_PUBLIC_RESOLUTION', 'market_end_actions':'PLATFORM_MANAGED'}
                        book.db.executemany('INSERT OR REPLACE INTO metadata VALUES (?,?)',
                                            [(k, json.dumps(v)) for k,v in values.items()])
                    metadata['complete'] = True
                finally:
                    book.close()
        exported = root/'modeled-settlements'/f'{market["slug"]}.json'
        if metadata.get('complete') and (not metadata.get('modeled_settlement') or not exported.exists()):
            result.append((path, identity, market))
    return result


def required_identity(identity):
    return {k:v for k,v in identity.items() if k not in {'limit','order_budget','prepost_policy'}}


def settle(path, identity, resolution):
    required = required_identity(identity)
    book = M4Ledger(path, required)
    try:
        if required.get('mode') != 'VIRTUAL':
            raise ValueError('M4_MODELED_SETTLEMENT_REQUIRES_BASELINE')
        market = book.get('market')
        ids = {'YES':market['tokens'][0]['token_id'], 'NO':market['tokens'][1]['token_id']}
        if (resolution['slug'] != market['slug'] or resolution['condition_id'] != market['condition_id']
                or resolution['token_ids'] != ids or resolution['winner'] not in ids):
            raise ValueError('M4_BASELINE_RESOLUTION_IDENTITY')
        receipts = book.facts('virtual_fill')
        cost = sum((Decimal(str(r['quote']['price']))*Decimal(str(r['quote']['size'])) for r in receipts), Decimal(0))
        payout = sum((Decimal(str(r['quote']['size'])) for r in receipts
                      if r['quote']['outcome'] == resolution['winner']), Decimal(0))
        policy = book.get('baseline_fill_model')
        if policy is not None and policy not in (POLICY, LEGACY_POLICY):
            raise ValueError('M4_BASELINE_FILL_MODEL_UNSUPPORTED')
        result = {'mode':'VIRTUAL', 'model':policy['model'] if policy else INSTANT_MODEL,
            'resolution':resolution, 'modeled_receipts':len(receipts), 'modeled_cost_usdc':str(cost),
            'modeled_payout_usdc':str(payout), 'modeled_pnl_usdc':str(payout-cost),
            'real_orders':0, 'actual_fills_queried':False}
        if policy:
            posts = book.facts('virtual_post')
            observations = book.facts('virtual_observation')
            states = {r['order_id']: r['status'] for r in observations}
            if (len(states) != len(posts) or set(states) != {p['order_id'] for p in posts}
                    or set(states.values())-{'FILLED', 'NOT_TOUCHED', 'UNKNOWN'}
                    or {r['order_id'] for r in receipts} != {k for k,v in states.items() if v == 'FILLED'}):
                raise ValueError('M4_BASELINE_OBSERVATION_INCOMPLETE_OR_CONFLICTING')

            def pnl(rows):
                return sum((Decimal(str(r['quote']['size'])) *
                            ((1 if r['quote']['outcome'] == resolution['winner'] else 0)
                             - Decimal(str(r['quote']['price']))) for r in rows), Decimal(0))

            unknown = sum(s == 'UNKNOWN' for s in states.values())
            result.update(baseline_fill_model=policy, fill_evidence_policy=EVIDENCE_POLICY,
                observation_counts={'posted':len(posts), 'confirmed_fills':len(receipts),
                    'not_touched':len(posts)-len(receipts), 'unknown':0, 'legacy_unknown':unknown,
                    'gap_no_fill':sum(r['status'] == 'UNKNOWN' or
                        (r['status'] == 'NOT_TOUCHED' and bool(r.get('gaps'))) for r in observations)},
                pnl_completeness='COMPLETE',
                instant_fill_comparison={'model':INSTANT_MODEL, 'modeled_receipts':len(posts),
                    'modeled_pnl_usdc':str(pnl(posts)), 'comparable_posts':len(posts),
                    'comparable_pnl_usdc':str(pnl(posts)),
                    'touch_minus_instant_comparable_usdc':str(payout-cost-pnl(posts)),
                    'touch_minus_instant_all_posts_usdc':str(payout-cost-pnl(posts))})
        # This records a model result without releasing any gross commitments.
        return book.modeled_settlement(market['slug'], result)
    finally:
        book.close()


async def resolutions(root, api, stop, *, active_path=lambda: None):
    while not stop.is_set():
        try:
            server, _, _ = await api.clock()
            waiting = await asyncio.to_thread(pending, root, server, (active_path(),))
            unresolved = 0
            for path, identity, market in waiting:
                if stop.is_set():
                    break
                expected = {'slug':market['slug'], 'condition_id':market['condition_id'],
                    'token_ids':{'YES':market['tokens'][0]['token_id'], 'NO':market['tokens'][1]['token_id']}}
                saved = (await asyncio.to_thread(M4Ledger.metadata, path)).get('modeled_settlement')
                if saved:
                    await asyncio.to_thread(atomic_json, root/'modeled-settlements'/f'{market["slug"]}.json', saved)
                    continue
                resolution = await api.resolution(expected)
                if resolution:
                    result = await asyncio.to_thread(settle, path, identity, resolution)
                    await asyncio.to_thread(atomic_json, root/'modeled-settlements'/f'{market["slug"]}.json', result)
                else:
                    unresolved += 1
            if not stop.is_set():
                await asyncio.to_thread(atomic_json, root/'resolution-status.json',
                    {'state':'OK', 'observed_at':time.time(),
                     'unresolved_markets':unresolved, 'actual_fills_queried':False})
        except Exception as exc:
            await asyncio.to_thread(atomic_json, root/'resolution-status.json',
                {'state':'PUBLIC_RESOLUTION_UNAVAILABLE','observed_at':time.time(),
                 'error_type':type(exc).__name__, 'modeled_pnl_usdc':None})
        with suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=30)

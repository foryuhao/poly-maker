"""Execution adapters for the shared M1Market, never a second entry policy.

The account is explicitly modeled and signatures use a throwaway, unfunded key.
The real SDK prepares/signs/validates orders. Only the final native order POST
is replaced by a durable modeled fill. No private configuration is loaded.
"""

import asyncio
import json
import time
from collections import deque
from dataclasses import asdict
from decimal import Decimal
from types import SimpleNamespace

from eth_account import Account
from py_clob_client_v2.clob_types import ApiCreds

from polymaker.config import RDPM1Config, RDPSMConfig
from polymaker.execution.gateway import ExecutionGateway
from polymaker.m1_live import M1Market
from polymaker.m1_live_state import M1Ledger, decimal
from polymaker.rdp_sm_ca import plain
from polymaker.rdp_sm_execution import ExecutionClobClient, SMWindowExecutor


class VirtualExecutionClient(ExecutionClobClient):
    """Same SDK signing and final _post guards, different native POST effect."""

    def __init__(self, executor):
        self.executor = executor
        # This disposable identity is not a wallet/account belonging to the user.
        super().__init__('https://clob.polymarket.com', 137, key=Account.create().key.hex(),
                         creds=ApiCreds('virtual-only', 'dmlydHVhbA==', 'virtual-only'))
        self._pagination_failures = {}
        self.known_ids = set()
        self.observed_open_rows = {}
        self.sm_config = RDPSMConfig(normal_entry_mode='m1_v1',
                                     m1=RDPM1Config(observation_only=False))

    def get_version(self):
        return 2

    def get_tick_size(self, token_id):
        return format(self.executor.reads.feed.books[token_id].tick, 'f')

    def _get(self, *args, **kwargs):
        raise RuntimeError('M1_VIRTUAL_SIGNER_NETWORK_READ_DENIED')

    def _delete(self, *args, **kwargs):
        raise RuntimeError('M1_VIRTUAL_SIGNER_NETWORK_WRITE_DENIED')

    def _native_order_post(self, endpoint, headers=None, data=None, params=None):
        # Every preceding check, including SDK signature/economic equality,
        # is ExecutionClobClient._post, exactly as for the live adapter.
        attempts = self._operation_value('batch_attempts')
        results = []
        for attempt in attempts:
            fact = {'opportunity_id': attempt.quote.window_id,
                    'order_id': attempt.expected_order_id, 'quote': asdict(attempt.quote),
                    'wall': time.time(), 'model': 'FULL_FILL_AT_INSTRUCTION_PRICE'}
            self.executor.ledger.fact('virtual_fill', attempt.expected_order_id, fact)
            results.append({'success': True, 'orderID': attempt.expected_order_id})
        return results


class VirtualExecutionGateway(ExecutionGateway):
    # prepare(), send_prepared() and response parsing are inherited unchanged.
    def __init__(self, executor):
        super().__init__(SimpleNamespace(wallet=SimpleNamespace(data_api_host='https://data-api.polymarket.com'),
                                         execution=SimpleNamespace(rate_budget_fraction=1)))
        self._client = VirtualExecutionClient(executor)


class VirtualJournal:
    ready = True

    async def flush(self):
        # Shared risk transitions already synchronously commit to M1Ledger.
        await asyncio.sleep(0)


class VirtualExecutor:
    """Modeled account/receipts implementing the live executor's input ports."""

    _placement_outcomes = SMWindowExecutor._placement_outcomes

    def __init__(self, feed, settings, store, *, stop=None):
        self.meta, self.slug = feed.meta, feed.meta.slug
        self.store = store
        self.stop = stop or asyncio.Event()
        self.monotonic = feed.monotonic
        self.official_time = int(self.slug.rsplit('-', 1)[1])
        self.time_anchor = self.monotonic()
        self.official_time_uncertainty_s = 0.
        self.fatal, self.closed = None, False
        self.attempts, self.known = [], set()
        self.records = deque()
        self.journal = VirtualJournal()
        self.reads = SimpleNamespace(feed=feed, network=SimpleNamespace(
            snapshot=self.network_snapshot,
            recent_writes=lambda: SimpleNamespace(post=SimpleNamespace(max_ms=0))))
        path = store.directory/'prepost'/f'{self.slug}.sqlite3'
        self.ledger = M1Ledger(path, {'mode': 'VIRTUAL_ONLY', 'market': self.slug,
                                    'settings_hash': settings.fingerprint})
        known = {r['id'] for r in self.ledger.rows()}
        history = [o for o in store.instructions(self.slug)
                   if o['status'] == 'VIRTUAL_SUCCESS'
                   and json.loads(o['evidence']).get('source_opportunity_id', o['id']) not in known]
        if self.ledger.get('historical_orders') is None:
            self.ledger.set('historical_orders', history)
        self.gateway = VirtualExecutionGateway(self)

    def network_snapshot(self):
        return SimpleNamespace(allowed=self.reads.feed.connected,
                               reason=None if self.reads.feed.connected else 'WS_DISCONNECTED')

    def record(self, kind, payload, **kwargs):
        # SDK workers may record here. Only the market thread writes VirtualStore.
        self.records.append((kind, plain(payload), time.time()))

    async def account_snapshot(self):
        # Same refresh_account validation/freshness/spendable/guard code consumes
        # this explicit paper source. It never claims to validate a real wallet.
        spent = sum((decimal(o['cost']) for o in self.ledger.get('historical_orders', [])), Decimal(0))
        spent += sum((decimal(f['quote']['price'])*decimal(f['quote']['size'])
                      for f in self.ledger.facts('virtual_fill')), Decimal(0))
        return {'official_balance_usd': str(Decimal(20)-spent), 'applicable_allowance_usd': '20',
                'source': 'MODELED_ACCOUNT_20_USDC_PER_MARKET'}

    def close(self):
        self.gateway.close()
        self.ledger.close()


class SharedVirtualPolicy:
    """Drive M1Market unchanged; translate only its modeled execution receipts."""

    def __init__(self, feed, settings, store, emit, *, stop=None):
        self.feed, self.settings, self.store, self.emit = feed, settings, store, emit
        detector = feed.m1
        self.executor = VirtualExecutor(feed, settings, store, stop=stop)
        try:
            self.market = M1Market(self.executor, settings, self.executor.stop, display=lambda _: None)
            self.market.restore()
        except BaseException:
            self.executor.close()
            raise
        finally:
            # Retain bounded diagnostics, with the identical M1LiveSignals decisions.
            feed.m1 = detector

    async def poll(self, lower, upper):
        e, market = self.executor, self.market
        e.official_time = int(e.slug.rsplit('-', 1)[1])+lower
        e.time_anchor = e.monotonic()
        e.official_time_uncertainty_s = upper-lower
        await market.advance()
        self.flush()

    def instruction(self, opportunity, *, price, quantity, eligible, reason, elapsed, wall=None,
                    execution_id=None, duplicate_of=None):
        evidence = dict(opportunity)
        evidence.pop('elapsed_s', None)
        evidence['slug'] = self.executor.slug
        row = self.store.issue(evidence, price=price, quantity=quantity, eligible=eligible,
                               reason=reason, elapsed=elapsed, wall=wall,
                               execution_id=execution_id, duplicate_of=duplicate_of)
        if row:
            self.emit('VIRTUAL_POST' if row['status'] == 'VIRTUAL_SUCCESS' else 'SIGNAL_SKIPPED', row)

    def flush(self):
        e = self.executor
        seen = set()
        for fact in sorted(e.ledger.facts('virtual_fill'), key=lambda f: (f['wall'], f['order_id'])):
            r = e.ledger.row(fact['opportunity_id'])
            q = fact['quote']
            self.instruction(json.loads(r['evidence']), price=q['price'], quantity=q['size'],
                             eligible=True, reason=None, elapsed=json.loads(r['evidence'])['elapsed_s'],
                             wall=fact['wall'], execution_id=fact['order_id'],
                             duplicate_of=r['id'] if r['id'] in seen else None)
            seen.add(r['id'])
        while e.records:
            kind, payload, wall = e.records.popleft()
            self.store.event(kind, payload, e.slug, wall=wall)
            if kind == 'sm_m1_candidate_skipped':
                self.instruction(payload['opportunity'], price=payload['opportunity']['anchor_bid_price'],
                                 quantity=0, eligible=False, reason=payload['reason'],
                                 elapsed=payload['elapsed'], wall=wall)
        for r in e.ledger.rows():
            if r['state'] in {'SKIPPED', 'ABORTED'} and r['id'] not in self.market.pending:
                self.instruction(json.loads(r['evidence']), price=r['price'], quantity=r['quantity'],
                                 eligible=True, reason=r['reason'] or r['state'],
                                 elapsed=json.loads(r['evidence'])['elapsed_s'])

    async def close(self, *, completed=False):
        await self.market.drain()
        self.market.finish_pending()
        if completed and not self.executor.stop.is_set():
            self.market.complete_window()
        self.flush()
        self.executor.close()

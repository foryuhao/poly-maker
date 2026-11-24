"""Real Maker-only order lifecycle on the existing signed Gateway boundary."""

import asyncio
import json
import time
from dataclasses import asdict
from decimal import Decimal

from polymaker.domain import Quote, Side
from polymaker.m1_live_account import account_snapshot
from polymaker.m1_live_retry import post_exception_rejection, rejection
from polymaker.rdp_sm_execution import Placement, SMWindowExecutor, WindowQuote
from polymaker.rdp_sm_m1_signal import M1Signals

D = Decimal


class LiveExecution:
    def __init__(self, reads, feed, ledger, clock_bounds, stop):
        self.reads, self.feed, self.ledger = reads, feed, ledger
        self.gateway, self.stop, self.clock_bounds = reads.gateway, stop, clock_bounds
        self.account, self.account_at = None, 0.
        self.account_barrier = 0.
        self.reason = 'ACCOUNT_WARMUP'
        self.submit_task = self.account_task = self.reconcile_task = None
        self.cancel_tasks, self.cancel_at = {}, {}
        self.last_account = self.last_reconcile = 0.
        self.fatal = None
        self.gateway._client.known_ids.update(r['order_id'] for r in ledger.rows() if r['order_id'])

    async def refresh_account(self):
        began = time.monotonic()
        try:
            account, orders = await asyncio.gather(account_snapshot(self.reads, self.feed.meta),
                                                  self.gateway.open_orders())
            owned = {r['order_id'] for r in self.ledger.rows() if r['order_id']}
            if any(o.order_id not in owned for o in orders):
                raise ValueError('FOREIGN_OPEN_ORDERS')
            if time.monotonic()-began > 2:
                raise ValueError('ACCOUNT_READ_TOO_OLD')
            if self.gateway._client.pagination_failure is not None:
                raise ValueError('ACCOUNT_PAGINATION_INCOMPLETE')
            if began < self.account_barrier:
                raise ValueError('ACCOUNT_READ_BEFORE_RECONCILIATION')
            self.account, self.account_at, self.reason = account, began, None
        except Exception as exc:
            self.reason = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
            self.ledger.event('ACCOUNT_READ_UNAVAILABLE', {'reason': self.reason})

    def guard(self, candidate, quote=None):
        now = time.monotonic()
        lower, upper = self.clock_bounds(now)
        with self.feed.lock:
            book = self.feed.books[candidate['token']]
            checks = (
                ('STOPPING', not self.stop.is_set()),
                ('EXECUTION_FATAL', self.fatal is None),
                ('ACCOUNT_STALE', self.reason is None and self.account is not None
                 and self.account_at >= self.account_barrier and now-self.account_at <= 2),
                ('ENTRY_WINDOW', lower >= 15 and upper < 240),
                ('SIGNAL_ENDED', self.feed.hub.active(candidate)),
                ('OBSERVATION_INVALID', candidate.get('observation_valid', True)),
                ('INPUT_BACKLOG', now-self.feed.m1_watermark() <= .020+1e-9),
                ('BOOK_UNAVAILABLE', M1Signals.legal(self.feed.m1, book, now)),
                ('ANCHOR_UNAVAILABLE', any(p == D(str(candidate['price'])) and q > 0 for p, q in book.bids)),
                ('MAKER_PRICE_INVALID', book.best_ask is not None
                 and 0 < D(str(candidate['price'])) < book.best_ask
                 and not D(str(candidate['price'])) % book.tick),
                ('SIGNAL_POST_BUDGET', candidate['deadline']-now >= .400),
                ('NETWORK_UNHEALTHY', self.reads.network.snapshot().allowed),
                ('PAGINATION_INCOMPLETE', self.gateway._client.pagination_failure is None),
                ('OTHER_ORDER_UNKNOWN', not self.ledger.uncertain()),
                ('QUOTE_CHANGED', quote is None or D(str(quote.price)) == D(str(candidate['price']))),
            )
            self.guard_reason = next((reason for reason, ok in checks if not ok), None)
            return self.guard_reason is None

    async def submit(self, candidate):
        if not self.guard(candidate):
            return
        cash = min(D(self.account['official_balance_usd']), D(self.account['applicable_allowance_usd']))
        try:
            row = self.ledger.reserve(self.feed.meta.slug, candidate, self.feed.meta.min_order_size, cash)
        except ValueError as exc:
            self.ledger.event('ENTRY_SKIPPED', {'candidate': candidate, 'reason': str(exc)})
            return
        if not row:
            return
        key = row['id']
        attempt = None
        try:
            quote = WindowQuote(self.feed.meta.condition_id, row['token'], row['outcome'],
                Side.BUY, float(row['price']), float(row['quantity']), key)
            prepared = await self.gateway.prepare([Quote(quote.token_id, Side.BUY, quote.price, quote.size)],
                self.feed.meta, {t: float(b.tick) for t, b in self.feed.books.items()})
            if not prepared.post_only or len(prepared.order_ids) != 1:
                raise ValueError('M234_PREPARED_NOT_SINGLE_MAKER')
            oid = prepared.order_ids[0]
            self.ledger.prepare(key, oid, asdict(quote))
            self.gateway._client.known_ids.add(oid)
            attempt = Placement(key, quote, expected_order_id=oid, order_id=oid)
            raw = await self.gateway.send_prepared(prepared,
                submit_guard=lambda: self.guard(candidate, quote),
                operation_context={'attempt': attempt, 'batch_attempts': [attempt],
                    'network_batch_id': None, 'on_post_start': lambda: self.ledger.post_start(key)})
            if not self.ledger.row(key)['started']:
                self.ledger.outcome(key, 'ABORTED', getattr(self, 'guard_reason', 'FINAL_GUARD_ENDED'))
                return
            outcome, order = SMWindowExecutor._placement_outcomes(self, raw, [quote])[0]
            proof = rejection(raw, D(row['cost'])) if outcome == 'REJECTED' else None
            if outcome == 'REJECTED' and proof:
                self.ledger.outcome(key, 'REJECTED', proof['kind'])
            elif outcome == 'ACK' and order is not None and order.order_id == oid:
                self.ledger.outcome(key, 'ACK')
            else:
                self.ledger.outcome(key, 'UNKNOWN', 'POST_RESPONSE_UNPROVEN')
        except Exception as exc:
            row = self.ledger.row(key)
            if row['started']:
                proof = post_exception_rejection(exc, D(row['cost']))
                self.ledger.outcome(key, 'REJECTED' if proof else 'UNKNOWN', type(exc).__name__)
            else:
                self.ledger.outcome(key, 'ABORTED', type(exc).__name__)

    async def cancel(self, row):
        try:
            result = await self.gateway.cancel_detailed([row['order_id']])
            self.ledger.event('CANCEL_RESULT', {'id': row['id'], 'order_id': row['order_id'],
                                             'result': asdict(result)})
        except Exception as exc:
            self.ledger.event('CANCEL_UNKNOWN', {'id': row['id'], 'error_type': type(exc).__name__})

    async def reconcile(self):
        # Continue checking closed prior markets too: a market boundary does
        # not prove that a canceled order has no additional late matches.
        rows = [r for r in self.ledger.rows() if r['started'] and r['state'] != 'REJECTED']
        by_market = {}
        for r in rows:
            by_market.setdefault(r['slug'], []).append(r)
        for slug, entries in by_market.items():
            metadata = json.loads(self.ledger.db.execute('SELECT metadata FROM markets WHERE slug=?',
                                                        (slug,)).fetchone()[0])
            try:
                raw_trades = await self.gateway.get_trades(market=metadata['condition_id'])
                if self.gateway._client.pagination_failure is not None:
                    raise ValueError('TRADES_PAGINATION_INCOMPLETE')
            except Exception as exc:
                for row in entries:
                    self.ledger.outcome(row['id'], 'RECONCILE_UNKNOWN', type(exc).__name__)
                continue
            async def one(row, raw_trades=raw_trades):
                try:
                    order = await self.gateway.get_order(row['order_id'])
                    self.ledger.reconcile(row['id'], order, raw_trades)
                    current = self.ledger.row(row['id'])
                    if any(current[k] != row[k] for k in ('terminal', 'reserved', 'filled', 'spent')):
                        # An in-flight balance read begun before this update
                        # cannot authorize spending newly released reservations.
                        self.account_barrier = time.monotonic()
                except ValueError as exc:
                    self.fatal = str(exc)
                    self.ledger.outcome(row['id'], 'RECONCILE_UNKNOWN', self.fatal)
                except Exception as exc:
                    self.ledger.outcome(row['id'], 'RECONCILE_UNKNOWN', type(exc).__name__)
            await asyncio.gather(*(one(row) for row in entries))

    async def advance(self):
        now = time.monotonic()
        for name in ('submit_task', 'account_task', 'reconcile_task'):
            task = getattr(self, name)
            if task is not None and task.done():
                task.result()
                setattr(self, name, None)
        for key, task in list(self.cancel_tasks.items()):
            if task.done():
                task.result()
                self.cancel_tasks.pop(key)
        if self.account_task is None and now-self.last_account >= 1:
            self.last_account = now
            self.account_task = asyncio.create_task(self.refresh_account())
        if self.reconcile_task is None and now-self.last_reconcile >= 1:
            self.last_reconcile = now
            self.reconcile_task = asyncio.create_task(self.reconcile())
        for row in self.ledger.rows():
            if not row['started'] or row['terminal']:
                continue
            candidate = json.loads(row['candidate'])
            ended = row['slug'] != self.feed.meta.slug or self.stop.is_set()
            if not ended:
                ended = not self.feed.hub.active(candidate) or self.clock_bounds(now)[1] >= 240
            if ended and row['id'] not in self.cancel_tasks and now-self.cancel_at.get(row['id'], 0) >= 1:
                self.cancel_at[row['id']] = now
                self.cancel_tasks[row['id']] = asyncio.create_task(self.cancel(row))
        if self.submit_task is None:
            for candidate in self.feed.hub.candidates():
                if self.guard(candidate):
                    slot = any(r['strategy'] == candidate['strategy'] and r['outcome'] == candidate['outcome']
                               for r in self.ledger.rows(self.feed.meta.slug))
                    if not slot:
                        self.submit_task = asyncio.create_task(self.submit(candidate))
                        break

    async def drain(self):
        # Joining SDK work is essential: cancelling an await does not cancel POST.
        tasks = [t for t in (self.submit_task, self.account_task, self.reconcile_task) if t]
        tasks.extend(self.cancel_tasks.values())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=False)
        for row in self.ledger.rows():
            if row['started'] and not row['terminal']:
                await self.cancel(row)
        await self.reconcile()

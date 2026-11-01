"""Cover an expired CA cycle with reconciled opposite Taker BUY orders.

The opening and TP must have positive terminal/trade proof first.
FAK orders may top up to the marketable minimum. Excess reverse inventory is
explicit; an unhedged original position never becomes a successful exit.
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import asdict
from decimal import ROUND_CEILING, Decimal
from math import gcd

from polymaker.domain import Quote, Side
from polymaker.execution.amounts import market_buy_quantity
from polymaker.rdp_sm_ca_taker import (
    buy_amount_fee_reserve,
    buy_fee_reserve,
    fee_parameters,
    fee_reserve,
)
from polymaker.rdp_sm_execution import Placement, WindowQuote
from polymaker.rdp_sm_recovery import PreparationReadRetry, retry_read
from polymaker.rdp_sm_sl import SLExecution
from polymaker.state.tracker import fill_cost

ZERO = Decimal(0)
GRID = Decimal('.01')


def executable_amount(remaining, price):
    """Budget only the remaining exposure, rounded up to cents, at least $1."""
    if remaining <= 0:
        return ZERO
    return max(Decimal(1), (remaining*price).quantize(GRID, rounding=ROUND_CEILING))


def executable_quantity(remaining, price):
    """Nominal shares on the signed budget BUY; actual fills may improve."""
    amount = executable_amount(remaining, price)
    return market_buy_quantity(amount, price) if amount else ZERO


def exit_fee_reserve(quantity, price, fees):
    return buy_fee_reserve(quantity, price, fees)


def exit_budget(quantity, tick, fees):
    # Preserve the existing conservative pre-entry capital admission bound.
    # Cent-budget exits cost no more than this legacy share-grid bound; changing
    # actual exit sizing must not silently change which openings are admitted.
    scale = 10**max(0, -tick.as_tuple().exponent)
    max_lot = Decimal(scale//gcd(int(tick*scale), scale))*GRID
    # Prior actual fills total <= the original quantity. A final signed dollar
    # order may receive arbitrarily more shares at better prices; reserve its
    # whole maker budget plus dollar-based fee margin, then stop on coverage.
    final_qty = (quantity/max_lot).to_integral_value(rounding=ROUND_CEILING)*max_lot
    final_cash = max(Decimal(2), final_qty*(1-tick))
    return quantity*(1-tick)+fee_reserve(quantity, fees)+final_cash*(1+2*Decimal(fees['rate']))+Decimal('.000001')


class CAExpiryExit(SLExecution):
    mode = 'CA_EXPIRY_EXIT'

    def __init__(self, owner, deadline):
        done = asyncio.Event()
        done.set()
        super().__init__(owner.controller, owner.window_id, inherited=owner.orders, parent_done=done)
        self.owner, self.deadline = owner, deadline
        self.recovery.cfg = self.recovery.cfg.model_copy(update={'enabled': False})
        self.baseline_ids = frozenset(self.e.known)
        self.baseline_revision = self.e.revision
        self.fees = None
        self.initial_cash = ZERO

    def check_abandonment(self):
        # A high-entry hold policy must not veto an explicitly configured timed exit.
        return False

    def snapshot(self):
        meta = self.e.meta
        token = meta.no.token_id if self.owner.intent.direction == 'YES' else meta.yes.token_id
        return self.lifecycle.feed.snapshot(ba_cfg=self.e.cfg.rdp_sm.ba, taker_buy_token=token)

    def remaining(self):
        opening = {self.oid(a) for a in self.owner.opening}
        tp = {self.oid(a) for a in self.owner.created+self.created}
        fills = self.e.economic_fills()
        return max(ZERO, sum((Decimal(str(f.size)) for f in fills if f.maker_order_id in opening), ZERO)
                   - sum((Decimal(str(f.size)) for f in fills if f.maker_order_id in tp), ZERO))

    def coverage(self):
        fills = self.e.economic_fills()
        opening = {self.oid(a) for a in self.owner.opening}
        tp = {self.oid(a) for a in self.owner.created}
        exits = {self.oid(a) for a in self.created}
        target = max(ZERO, sum((Decimal(str(f.size)) for f in fills if f.maker_order_id in opening), ZERO)
                     - sum((Decimal(str(f.size)) for f in fills if f.maker_order_id in tp), ZERO))
        uncovered, excess, excess_cost = target, ZERO, ZERO
        ordered = sorted((f for f in fills if f.maker_order_id in exits), key=lambda f:
            self.e.fill_observed_at.get((f.trade_id, f.maker_order_id), {}).get('observed_monotonic', 0))
        for f in ordered:
            qty = Decimal(str(f.size))
            used = min(uncovered, qty)
            uncovered -= used
            excess += qty-used
            excess_cost += fill_cost(f)*(qty-used)/qty
        return {'original_target_shares': str(target), 'hedged_shares': str(target-uncovered),
                'original_unhedged_shares': str(uncovered), 'extra_reverse_shares': str(excess),
                'extra_reverse_cost_usdc': str(excess_cost)}

    def require_active(self):
        if self.e.closed or self.e.fatal or not 15 <= self.elapsed() < self.cutoff:
            raise ValueError(self.e.fatal or 'CA_EXPIRY_EXIT_OUTSIDE_WINDOW')

    async def wait_frame(self):
        self.require_active()
        self.lifecycle.drain_ba()
        await self.lifecycle.feed.wait_after(self.lifecycle.feed.sequence, .1)

    def release_proven_transport(self):
        # Logical CA ownership stays here. Release only the drained transport
        # batch after ALL signed order/fill/account proofs, so the normal
        # circuit can move PAUSED -> PROBE and admit this owner's next FAK.
        if self.e.safe_to_continue:
            self.e.reads.network.complete_batch(self.group, safe=True)

    async def wait_close_proof(self):
        while not self.e.safe_to_continue:
            self.require_active()
            self.wait('CA_EXIT_WAIT_CLOSE_PROOF:'+(self.e.reason or 'UNPROVEN'))
            try:
                await self.e._verify_close(cancel=True)
            except Exception as exc:
                if not retry_read(exc):
                    raise
            if not self.e.safe_to_continue:
                await self.wait_frame()
        self.release_proven_transport()

    def admission_reason(self, goal, *, reserved=None):
        e = self.e
        side, price, qty, original = goal
        cfg = e.cfg.rdp_sm.ca_matched_tp
        if not cfg.enabled or not cfg.taker_exit_on_expiry or e.cfg.rdp_sm.repair.ba_enabled:
            return 'CA_EXPIRY_EXIT_DISABLED'
        if self.closed or e.closed or e.fatal:
            return e.fatal or 'CA_EXPIRY_EXIT_CLOSED'
        if e.monotonic() < self.deadline or not 15 <= self.elapsed() < self.cutoff:
            return 'CA_EXPIRY_EXIT_OUTSIDE_WINDOW'
        if (not e.journal.ready or e.merge_pending or e.inventory_mismatch is not None
                or e.gateway._client.pagination_failure):
            return 'CA_EXPIRY_EXIT_EVIDENCE_UNAVAILABLE'
        if not self.health().allowed:
            return self.health().reason
        if e.monotonic()-e.time_anchor > e.cfg.risk.ws_stale_halt_s:
            return 'OFFICIAL_CLOCK_STALE'
        oid = self.oid(reserved) if reserved is not None else None
        if (e.revision != self.baseline_revision or e.known-{oid} != self.baseline_ids
                or not self.baseline_ids <= set(e.proofs) & set(e.terminal)
                or any(a.outcome not in {'ACK', 'VERIFIED', 'REJECTED', 'ABORTED'}
                       for a in self.orders if a is not reserved)):
            return 'CA_EXPIRY_EXIT_CLOSE_PROOF_CHANGED'
        if side != ('NO' if self.owner.intent.direction == 'YES' else 'YES'):
            return 'CA_EXPIRY_EXIT_WRONG_SIDE'
        y, n = e.economic_inventory()
        delta = y-n
        remaining = self.remaining()
        if (remaining <= 0 or remaining > abs(delta)
                or (delta > 0) != (side == 'NO')
                or qty != executable_quantity(remaining, price)):
            return 'CA_EXPIRY_EXIT_QUANTITY_CHANGED'
        amount = executable_amount(remaining, price)
        if reserved is not None and reserved.buy_amount_usdc != str(amount):
            return 'CA_EXPIRY_EXIT_AMOUNT_CHANGED'
        current = self.snapshot()
        if not current.valid:
            return current.reason
        book = current.no if side == 'NO' else current.yes
        old = original.no if side == 'NO' else original.yes
        if (current.generation != original.generation or book.tick != old.tick
                or price != book.best_ask):
            return 'CA_EXPIRY_EXIT_QUOTE_INVALID'
        cost = amount+buy_amount_fee_reserve(amount, self.fees)
        committed = e.committed()-(reserved.maximum if reserved is not None else ZERO)
        if committed+cost > e.capital_limit or cost > self.initial_cash:
            return 'CA_EXPIRY_EXIT_INSUFFICIENT_FUNDS'
        if reserved is not None and (reserved not in self.created or reserved.post_started
                or reserved.outcome != 'INTENT' or reserved.entry_execution != 'taker'):
            return 'CA_EXPIRY_EXIT_RESERVATION_CHANGED'
        if e.shared_reads is not None and not e.shared_reads.admission_current(
                pending_batch=[reserved] if reserved is not None else []):
            return 'SYNC_CACHE_EVIDENCE_CHANGED'
        return None

    async def prepare_order(self, attempt, goal, ticks):
        return await self.e.gateway.prepare(
            [Quote(attempt.quote.token_id, Side.BUY, float(goal[1]), float(goal[2]))],
            self.e.meta, ticks, immediate_order_type='FAK', buy_amount_usdc=attempt.buy_amount_usdc)

    def register_prepared_order(self, attempt, goal, signed):
        e, q, oid = self.e, attempt.quote, attempt.expected_order_id
        client = e.gateway._client
        e.processor.taker_orders = getattr(e.processor, 'taker_orders', {})
        e.processor.taker_orders[oid] = {
            'market_id': e.meta.condition_id, 'token_id': q.token_id,
            'opposite_token': e.meta.no.token_id if q.outcome == 'YES' else e.meta.yes.token_id,
            'price': Decimal(str(q.price)), 'quantity': Decimal(str(q.size)), 'fees': dict(self.fees),
            'buy_amount_usdc': Decimal(attempt.buy_amount_usdc)}
        if not hasattr(client, 'expiry_buy_amounts'):
            client.expiry_buy_amounts = {}
        client.expiry_buy_amounts[oid] = (q.token_id, Decimal(str(q.price)), Decimal(str(q.size)),
                                        Decimal(attempt.buy_amount_usdc))
        for name in ('ca_taker_ids', 'ca_expiry_taker_ids'):
            if not hasattr(client, name):
                setattr(client, name, set())
            getattr(client, name).add(oid)

    async def run(self):
        e = self.e
        reason = None
        try:
            await self.wait_close_proof()
            while e.monotonic() < self.deadline and self.remaining() > GRID:
                await self.wait_frame()
            while self.remaining() > GRID and self.fees is None:
                self.require_active()
                try:
                    raw = await self.lifecycle._read_preparation_constraint(
                        e.gateway._client.get_clob_market_info, e.meta.condition_id)
                    self.fees = fee_parameters(raw, (e.meta.yes.token_id, e.meta.no.token_id))
                except Exception as exc:
                    if not isinstance(exc, PreparationReadRetry) and not retry_read(exc):
                        raise
                    self.wait('CA_EXIT_WAIT_CONSTRAINTS:'+str(exc))
                    await self.wait_frame()
            while self.remaining() > GRID:
                self.require_active()
                try:
                    _, _, _, account = await e._snapshot(include_books=False)
                except Exception as exc:
                    if not retry_read(exc):
                        raise
                    self.wait('CA_EXIT_WAIT_ACCOUNT:'+str(exc))
                    await self.wait_close_proof()
                    await self.wait_frame()
                    continue
                if not e.safe_to_continue:
                    await self.wait_close_proof()
                    continue
                self.lifecycle.account = account
                self.initial_cash = Decimal(str(account['basic_spendable_budget_usd']))
                self.baseline_ids = frozenset(e.known)
                self.baseline_revision = e.revision
                snapshot = self.snapshot()
                health = self.health()
                if not snapshot.valid or not health.allowed:
                    self.wait(snapshot.reason if not snapshot.valid else health.reason)
                    await self.wait_frame()
                    continue
                side = 'NO' if self.owner.intent.direction == 'YES' else 'YES'
                book = snapshot.no if side == 'NO' else snapshot.yes
                price = book.best_ask
                qty = executable_quantity(self.remaining(), price)
                amount = executable_amount(self.remaining(), price)
                goal = side, price, qty, snapshot
                reason = self.admission_reason(goal)
                if reason:
                    raise ValueError(reason)
                quote = WindowQuote(e.meta.condition_id, book.token_id, side, Side.BUY,
                                    float(price), float(qty), self.window_id+':ca-expiry-exit')
                a = Placement(uuid.uuid4().hex, quote, entry_execution='taker',
                              fee_reserve_usdc=str(buy_amount_fee_reserve(amount, self.fees)),
                              buy_amount_usdc=str(amount))
                self.created.append(a)
                self.orders.append(a)
                e.attempts.append(a)
                self.signal_times[a.attempt_id] = self.deadline
                self.record('signal', {'window_id': quote.window_id, 'price_cap': str(price),
                    'quantity': str(qty), 'original_remaining_shares': str(self.remaining()),
                    'minimum_topup_shares': str(max(ZERO, qty-self.remaining())),
                    'budget_roundup_usdc': str(amount-self.remaining()*price),
                    'attempt_number': len(self.created), 'deadline_monotonic': self.deadline,
                    'dispatch_delay_ms': max(0, (e.monotonic()-self.deadline)*1000),
                    'method': 'OPPOSITE_BUY_FAK', 'cash_limit_usdc': str(amount), 'fees': self.fees})
                task = asyncio.create_task(self.submit(goal, a), name='ca-expiry-exit-post')
                self.posts[a.attempt_id] = task
                async def wait_post(task=task):
                    await asyncio.shield(task)

                await e._await_owned(wait_post(), propagate_cancel=False)
                await e._await_owned(self.wait_close_proof(), propagate_cancel=False)
                if self.remaining() > GRID:
                    self.record('retry', {'remaining_shares': str(self.remaining()),
                                'previous_order_id': self.oid(a), 'previous_outcome': a.outcome,
                                'terminal_proven': True})
                    retry_after = e.monotonic()+max(.1, self.controller.cfg.review_sec)
                    while e.monotonic() < retry_after and not e.fatal and not e.closed:
                        await self.lifecycle.feed.wait_after(self.lifecycle.feed.sequence,
                                                             min(.1, retry_after-e.monotonic()))
            coverage = self.coverage()
            reason = ('WITHIN_RESIDUAL_TOLERANCE' if Decimal(coverage['original_unhedged_shares']) > 0 else
                      'HEDGED_WITH_EXCESS' if Decimal(coverage['extra_reverse_shares']) > 0 else 'PAIRED')
        except Exception as exc:
            reason = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
            e.fatal = e.fatal or 'CA_EXPIRY_EXIT_FAILED:'+reason
            e.invalidate(e.fatal)
        finally:
            self.closed = True
            if self.posts:
                async def drain_posts():
                    await asyncio.gather(*self.posts.values(), return_exceptions=True)

                await e._await_owned(drain_posts(), propagate_cancel=False)
            self.reason = reason
            self.record('result', {'reason': reason, 'opening_window': self.window_id,
                'deadline_monotonic': self.deadline,
                'orders': [{'order_id': self.oid(a), 'complete': e.safe_to_continue,
                            'placement': asdict(a)} for a in self.created],
                'residual_delta': str(e.economic_inventory()[0]-e.economic_inventory()[1]),
                **self.coverage(),
                'safe': e.safe_to_continue})


async def exit_expired_ca(owner):
    """Keep ownership while cancellation/account proof recovers, then exit."""
    e = owner.e
    if not owner.cfg.taker_exit_on_expiry:
        return None
    ids = {owner.oid(a) for a in owner.opening}
    fills = [f for f in e.economic_fills() if f.maker_order_id in ids] or owner.opening_fills()
    if not fills and e.safe_to_continue:
        return None
    y, n = e.economic_inventory()
    if e.safe_to_continue and abs(y-n) <= GRID:
        if y != n:
            e.record('sm_ca_expiry_exit_result', {'reason': 'WITHIN_RESIDUAL_TOLERANCE',
                'opening_window': owner.window_id, 'orders': [], 'residual_delta': str(y-n),
                'original_unhedged_shares': str(abs(y-n)), 'tolerance_shares': str(GRID),
                'safe': e.safe_to_continue})
        return None
    deadline = owner.expires_at or min((e.fill_observed_at[(f.trade_id, f.maker_order_id)][
        'observed_monotonic'] for f in fills), default=owner.started_monotonic)+owner.cfg.ttl_sec
    actor = CAExpiryExit(owner, deadline)
    await actor.run()
    return actor

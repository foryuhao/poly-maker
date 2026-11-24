"""Historical single-POST M4 adapter and routing to live-only continuous tasks."""
import json
import re
from dataclasses import asdict
from decimal import Decimal

from polymaker.domain import Side
from polymaker.m4_execution import SUBMIT_PENDING_TOLERANCE_SEC, M4Market
from polymaker.m4_live_config import all_in_cost, gross_risk_cost, planned_fee, taker_quantity
from polymaker.m4_state import BASELINE_MAX_INSTRUCTIONS, M4Ledger, decimal
from polymaker.rdp_sm_execution import Placement, WindowQuote


class SinglePostM4LiveLedger(M4Ledger):
    def __init__(self, path, identity, *, settings, fees=None, **kwargs):
        if identity.get('mode') != 'LIVE':
            raise ValueError('M4_LIVE_LEDGER_REQUIRES_LIVE')
        if identity.get('settings_hash') != settings.fingerprint:
            raise ValueError('M4_LIVE_LEDGER_SETTINGS_CONFLICT')
        self.settings, self.fees = settings, fees
        previous = self.metadata(path, strategy=self.ledger_strategy)
        if self.is_taker and (fees is None or previous.get('taker_fees', fees) != fees):
            raise ValueError('M4_TAKER_FEES_CHANGED_OR_UNAVAILABLE')
        super().__init__(path, identity, **kwargs)
        try:
            if self.is_taker:
                self.set('taker_fees', fees)
                if self.uncertain_posts():
                    raise ValueError('M4_TAKER_EXTRA_POST_FORBIDDEN')
                for row in self.rows():
                    if row['posts'] > 1 or row['uncertain_retries'] or row['retry_pricing'] != 'SIGNAL':
                        raise ValueError('M4_TAKER_EXTRA_POST_FORBIDDEN')
                    if row['quote']:
                        plan = self.validate_quote(row, json.loads(row['quote']))
                        if (row['state'] in {'PREPARED', 'UNKNOWN', 'ACK'}
                                and decimal(row['reserved']) != decimal(plan['commitment_usdc'])):
                            raise ValueError('M4_TAKER_DURABLE_RISK_CHANGED')
        except BaseException:
            self.close()
            raise

    @property
    def is_taker(self):
        return self.settings.execution.mode == 'taker'

    @property
    def sizing_policy(self):
        return 'M4_SINGLE_FAK_PLANNED_MAX5_GROSS_MAX10_V1' if self.is_taker else super().sizing_policy

    def order_quantity(self, price, minimum):
        return taker_quantity(price, minimum, self.fees) if self.is_taker else super().order_quantity(price, minimum)

    def plan(self, key):
        plans = [x for x in self.facts('taker_plan') if x['opportunity_id'] == key]
        if len(plans) != 1:
            raise ValueError('M4_TAKER_PLAN_MISSING')
        return plans[0]

    def plan_signal(self, key, tick, minimum):
        row = self.row(key)
        anchor, tick = decimal(row['price']), decimal(tick)
        price = anchor+tick
        if not tick > 0 or anchor % tick or not self.minimum_price <= price < 1:
            raise ValueError('M4_TAKER_ANCHOR_PLUS_TICK_INVALID')
        quantity = self.order_quantity(price, minimum)
        plan = {'opportunity_id': key, 'anchor_price': str(anchor), 'tick_at_plan': str(tick),
                'limit_price': str(price), 'quantity': str(quantity), 'minimum': str(minimum),
                'principal_usdc': str(price*quantity),
                'planned_fee_usdc': str(planned_fee(price, quantity, self.fees)),
                'planned_cost_usdc': str(all_in_cost(price, quantity, self.fees)),
                'commitment_usdc': str(gross_risk_cost(price, quantity, self.fees)),
                'budget_policy': self.settings.execution.taker.budget_policy,
                'fees': dict(self.fees), 'order_type': 'FAK', 'max_native_posts': 1}
        self.fact('taker_plan', key, plan)
        return plan

    def validate_quote(self, row, quote):
        plan = self.plan(row['id'])
        price, quantity = decimal(quote['price']), decimal(quote['size'])
        tick, anchor = decimal(plan['tick_at_plan']), decimal(row['price'])
        if (quote['token_id'] != row['token'] or quote['side'] != 'BUY'
                or price != anchor+tick or price != decimal(plan['limit_price'])
                or decimal(plan['anchor_price']) != anchor or tick <= 0 or anchor % tick
                or not self.minimum_price <= price < 1
                or plan['fees'] != self.fees or plan['order_type'] != 'FAK' or plan['max_native_posts'] != 1
                or quantity != self.order_quantity(price, plan['minimum'])
                or quantity != decimal(plan['quantity']) or price*quantity != decimal(plan['principal_usdc'])
                or planned_fee(price, quantity, self.fees) != decimal(plan['planned_fee_usdc'])
                or all_in_cost(price, quantity, self.fees) != decimal(plan['planned_cost_usdc'])
                or plan['budget_policy'] != self.settings.execution.taker.budget_policy
                or gross_risk_cost(price, quantity, self.fees) != decimal(plan['commitment_usdc'])
                or decimal(plan['planned_cost_usdc']) > 5):
            raise ValueError('M4_TAKER_PLAN_ECONOMICS_CHANGED')
        return plan

    def quote_cost(self, row):
        if not self.is_taker:
            return super().quote_cost(row)
        quote = json.loads(row['quote']) if row['quote'] else None
        return (gross_risk_cost(quote['price'], quote['size'], self.fees) if quote
                else decimal(row['target']))

    def metric(self, *, winner=None):
        result = super().metric(winner=winner)
        if self.is_taker:
            result.update(budget_policy=self.settings.execution.taker.budget_policy,
                          target_risk_scope='LIMIT_PRICE_PLANNED_COST',
                          actual_order_debit_may_exceed_plan=True)
            plans = {p['opportunity_id']: p for p in self.facts('taker_plan')}
            for item in result['by_signal']:
                if plan := plans.get(item['id']):
                    item.update(planned_fee_usdc=plan['planned_fee_usdc'],
                                planned_cost_usdc=plan['planned_cost_usdc'],
                                gross_reserve_usdc=plan['commitment_usdc'])
        return result

    def reserve(self, key, *, spendable, direction_limit=1):
        if not self.is_taker:
            return super().reserve(key, spendable=spendable, direction_limit=direction_limit)
        with self.lock, self.db:
            row = self.row(key)
            if row['state'] != 'OBSERVED':
                return False
            # The user waived the per-order actual cap only. Market/cash
            # admission must still cover the entire possible debit, not $5.
            amount = decimal(self.plan(key)['commitment_usdc'])
            reason = ('SESSION_INSTRUCTION_LIMIT' if self.instruction_slots() >= BASELINE_MAX_INSTRUCTIONS else
                      'M4_DIRECTION_OPPORTUNITY_LIMIT' if direction_limit is not None
                          and self.direction_slots(key) >= direction_limit else
                      'M4_SESSION_BUDGET' if self.committed()+amount > self.limit else
                      'M4_ACCOUNT_BUDGET' if amount > decimal(spendable) else None)
            self.db.execute('UPDATE opportunities SET state=?,reason=?,reserved=? WHERE id=?',
                            ('SKIPPED' if reason else 'RESERVED', reason, str(amount if reason is None else 0), key))
            return reason is None

    def reserve_quote(self, key, *, price, quantity, spendable):
        if not self.is_taker:
            return super().reserve_quote(key, price=price, quantity=quantity, spendable=spendable)
        with self.lock, self.db:
            row = self.row(key)
            plan = self.validate_quote(row, {'token_id': row['token'], 'side': 'BUY',
                                             'price': price, 'size': quantity})
            if row['state'] != 'RESERVED' or row['posts'] or row['started']:
                raise ValueError('M4_TAKER_RESERVATION_STATE_INVALID')
            amount = decimal(plan['commitment_usdc'])
            delta = amount-decimal(row['reserved'])
            if self.committed()+delta > self.limit or delta > decimal(spendable):
                return False
            self.db.execute('UPDATE opportunities SET reserved=? WHERE id=?', (str(amount), key))
            return True

    def prepare(self, key, oid, quote):
        if not self.is_taker:
            return super().prepare(key, oid, quote)
        with self.lock, self.db:
            row = self.row(key)
            plan = self.validate_quote(row, quote)
            if (row['state'] != 'RESERVED' or row['posts'] or row['started']
                    or not isinstance(oid, str) or re.fullmatch(r'0x[0-9a-fA-F]{64}', oid) is None
                    or decimal(row['reserved']) != decimal(plan['commitment_usdc'])):
                raise ValueError('M4_TAKER_PREPARE_INVALID')
            self.db.execute("UPDATE opportunities SET order_id=?,quote=?,state='PREPARED' WHERE id=?",
                            (oid, json.dumps(quote), key))
            self.fact('signed_attempt', oid, {'opportunity_id': key, 'quote': quote,
                                            'execution_mode': 'taker', 'plan': plan})

    def post_start(self, key, *, baseline_post=None):
        if self.is_taker:
            row = self.row(key)
            if row['posts'] or baseline_post is not None:
                raise ValueError('M4_TAKER_EXTRA_POST_FORBIDDEN')
            self.validate_quote(row, json.loads(row['quote']))
        return super().post_start(key, baseline_post=baseline_post)

    def retry(self, key, *, proof, spendable=0, direction_limit=1):
        if not self.is_taker:
            return super().retry(key, proof=proof, spendable=spendable, direction_limit=direction_limit)
        with self.lock, self.db:
            row = self.row(key)
            if (proof.get('kind') != 'NOT_SENT' or row['posts'] or row['started']
                    or row['state'] != 'ABORTED'
                    or self.direction_slots(key) >= direction_limit):
                return False
            self.db.execute("UPDATE opportunities SET state='RESERVED',quote=NULL,order_id=NULL,"
                            "reason='M4_TAKER_UNSENT_RETRY' WHERE id=?", (key,))
            return True


class SinglePostM4TakerMarket(M4Market):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.ledger.is_taker or self.ledger.get('identity')['mode'] != 'LIVE':
            raise ValueError('M4_TAKER_REQUIRES_LIVE')

    def collect(self):
        if not self.entry_window_open():
            return
        with self.feed.lock:
            snapshot = self.feed.snapshot()
            for _ in range(2):
                self.feed.m1.evaluate(snapshot)
                opportunity = self.feed.m1.selected
                if opportunity is None:
                    break
                book = self.feed.books[opportunity.token_id]
                row = None
                try:
                    row = self.ledger.observe(opportunity, minimum=self.e.meta.min_order_size,
                                              elapsed=self.elapsed())
                    if row is not None:
                        self.ledger.plan_signal(row['id'], book.tick, self.e.meta.min_order_size)
                except ValueError as exc:
                    if str(exc) not in {'M4_TAKER_MINIMUM_EXCEEDS_ALL_IN_BUDGET',
                                       'M4_TAKER_ANCHOR_PLUS_TICK_INVALID', 'M4_PRICE_OUTSIDE_BAND'}:
                        raise
                    if row is not None and row['state'] == 'OBSERVED':
                        self.ledger.outcome(row['id'], 'SKIPPED', str(exc))
                    self.feed.m1.take(opportunity)
                    self.e.record('sm_m4_candidate_skipped', {'opportunity': asdict(opportunity),
                                  'reason': str(exc), 'elapsed': self.elapsed(), 'execution_mode': 'taker'})
                    continue
                if self.feed.m1.take(opportunity) and row is not None:
                    self.pending[row['id']] = opportunity
                    self.e.record('sm_m4_live_signal', self.ledger.row(row['id']))

    def restore(self):
        # No posted Taker is ever scheduled again, including UNKNOWN/REJECTED.
        for row in self.ledger.rows():
            if row['posts']:
                quote = json.loads(row['quote'])
                quote['side'] = Side(quote['side'])
                plan = self.ledger.plan(row['id'])
                self.register(Placement(row['id'], WindowQuote(**quote), outcome=row['state'],
                    expected_order_id=row['order_id'], order_id=row['order_id'], post_started=True,
                    entry_execution='taker', order_type='FAK',
                    fee_reserve_usdc=str(decimal(plan['commitment_usdc'])-decimal(plan['principal_usdc']))))
                continue
            if row['state'] not in {'OBSERVED', 'RESERVED', 'PREPARED', 'ABORTED'}:
                continue
            try:
                self.ledger.plan(row['id'])
            except ValueError:
                self.ledger.outcome(row['id'], 'ABORTED', 'M4_TAKER_INTENT_INCOMPLETE')
                continue
            if row['state'] == 'PREPARED':
                self.ledger.outcome(row['id'], 'ABORTED', 'M4_TAKER_RESTART_BEFORE_POST')
                row = self.ledger.row(row['id'])
            if row['state'] == 'ABORTED' and not self.ledger.retry(row['id'], proof={'kind': 'NOT_SENT'}):
                continue
            self.pending[row['id']] = self.restored_opportunity(row)

    def quote_price(self, opportunity):
        return decimal(self.ledger.plan(opportunity.episode_id)['limit_price'])

    def initial_post_budget_available(self, opportunity):
        # The original RF/EX deadline remains immutable signal evidence. Once
        # accepted, a single unsent FAK has this market's T240 deadline instead.
        remaining = (self.settings.cutoff_sec-self.elapsed()
                     -getattr(self.e, 'official_time_uncertainty_s', 0.))*1000
        return self.entry_window_open() and remaining >= self.post_budget_ms()

    def capital_covers(self, row, price):
        if not self.account_current():
            return False
        plan = self.ledger.plan(row['id'])
        own = decimal(row['reserved']) if row['state'] in {'RESERVED', 'PREPARED', 'UNKNOWN', 'ACK'} else Decimal(0)
        required = self.ledger.committed()-own+decimal(plan['commitment_usdc'])
        return required <= min(decimal(self.account['official_balance_usd']),
                               decimal(self.account['applicable_allowance_usd']))

    def may_retry(self, opportunity):
        row = self.ledger.row(opportunity.episode_id)
        return (not row['posts'] and not row['started'] and not self.stop.is_set()
                and not self.e.inhibited() and not self.e.closed and self.e.fatal is None
                and self.e.journal.ready and self.entry_window_open())

    def guard(self, opportunity, quote=None):
        e = self.e
        with self.feed.lock:
            row = self.ledger.row(opportunity.episode_id)
            plan = self.ledger.plan(row['id'])
            book, now = self.feed.books[opportunity.token_id], e.monotonic()
            pending = list(self.feed.m1_pending_received)
            if self.feed.m1_processing_received is not None:
                pending.append(self.feed.m1_processing_received)
            age = now-min(pending) if pending else 0.
            self.pending_input_age_ms = age*1000
            price = decimal(plan['limit_price'])
            health = e.reads.network.snapshot()
            checks = [
                ('M4_STOPPING', not self.stop.is_set() and not e.closed and not e.inhibited()),
                (e.fatal or 'M4_FATAL_EXECUTION_STATE', e.fatal is None),
                ('M4_JOURNAL_UNAVAILABLE', e.journal.ready),
                ('M4_TAKER_ALREADY_POSTED', not row['posts'] and not row['started']),
                (self.account_reason or 'M4_ACCOUNT_SNAPSHOT_STALE', self.account_current()),
                ('M4_ACCOUNT_BUDGET', self.capital_covers(row, price)),
                ('M4_ENTRY_WINDOW_ENDED', self.entry_window_open()),
                ('M4_SUBMIT_INPUT_BACKLOG', -1e-9 <= age <= SUBMIT_PENDING_TOLERANCE_SEC+1e-9),
                ('M4_BOOK_UNAVAILABLE', self.feed.m1.legal(book, now)),
                ('M4_TAKER_TICK_CHANGED', book.tick == decimal(plan['tick_at_plan'])),
                ('M4_TAKER_PRICE_CAP', book.best_ask is not None and book.best_ask <= price),
                ('M4_PREPARED_PRICE_CHANGED', quote is None or decimal(quote.price) == price),
                ('M4_SESSION_BUDGET', self.ledger.committed() <= self.settings.session_budget_usdc),
                (health.reason, health.allowed),
            ]
            self.guard_reason = next((reason for reason, valid in checks if not valid), None)
            return self.guard_reason is None


def M4LiveLedger(path, identity, *, settings, **kwargs):
    # Old saved markets keep their original no-fill-read execution contract.
    if settings.execution_revision == 'm4-taker-budget-v1':
        from polymaker.m4_live_budget import BudgetLedger
        return BudgetLedger(path, identity, settings=settings, **kwargs)
    if settings.execution_revision == 'm4-taker-task-v1':
        from polymaker.m4_live_task import TaskLedger
        return TaskLedger(path, identity, settings=settings, **kwargs)
    return SinglePostM4LiveLedger(path, identity, settings=settings, **kwargs)


def M4TakerMarket(executor, settings, stop, **kwargs):
    if settings.execution_revision == 'm4-taker-budget-v1':
        from polymaker.m4_live_budget_market import BudgetMarket
        return BudgetMarket(executor, settings, stop, **kwargs)
    if settings.execution_revision == 'm4-taker-task-v1':
        from polymaker.m4_live_task import TaskMarket
        return TaskMarket(executor, settings, stop, **kwargs)
    return SinglePostM4TakerMarket(executor, settings, stop, **kwargs)

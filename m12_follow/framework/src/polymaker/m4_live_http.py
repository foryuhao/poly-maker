"""M4-only FAK permissions and exact owned-order reads for continuous tasks."""
import json
import re
from dataclasses import asdict
from decimal import Decimal
from urllib.parse import urlsplit

from py_clob_client_v2.client import ClobClient
from py_clob_client_v2.constants import INITIAL_CURSOR

from polymaker.domain import Side
from polymaker.execution.gateway import ExecutionGateway
from polymaker.execution.taker_fees import buy_amount_fee_reserve
from polymaker.m4_live_config import all_in_cost, planned_fee, validated_fees
from polymaker.m4_types import M421_LEDGER
from polymaker.market_protocol import market_protocol
from polymaker.rdp_sm import OFFICIAL_HOSTS, _require
from polymaker.rdp_sm_execution import ExecutionClobClient, _ExecutionReads


class NativePostNotSent(RuntimeError):
    """Raised only by the local boundary before invoking the native transport."""

    def __init__(self, order_id, reason):
        super().__init__(reason)
        self.order_id = order_id


class M4LiveClobClient(ExecutionClobClient):
    def gtd_reconciliation_page(self, oid, path, cursor):
        _require(path in {'/data/orders', '/data/trades'}, 'M12_GTD_ACCOUNT_READ_SCOPE_INVALID')
        local = self.operation_local
        previous = getattr(local, 'gtd_reconciliation_order', None)
        local.gtd_reconciliation_order = oid
        try:
            return self._get(f'{self.host}{path}', headers=self._l2_headers('GET', path),
                             params={'market': self.session_market_id, 'next_cursor': cursor or INITIAL_CURSOR})
        finally:
            local.gtd_reconciliation_order = previous

    def get_tick_size(self, token_id):
        if getattr(getattr(self, 'm4_ledger', None), 'cash_target', False):
            # The SDK caches market-info ticks indefinitely. The active market
            # has already verified WS tick changes against the official REST
            # endpoint; signing must use that source, including finer ticks.
            provider = getattr(self, 'm4_verified_tick_provider', None)
            _require(callable(provider), 'M4_TAKER_TICK_PROVIDER_UNBOUND')
            tick = provider(token_id)
            _require(tick in {'0.1', '0.01', '0.001', '0.0001'},
                     'M4_TAKER_OFFICIAL_TICK_UNSUPPORTED')
            return tick
        return super().get_tick_size(token_id)

    def _observe_http(self, *args, error=None, **kwargs):
        if isinstance(error, NativePostNotSent):
            # Local refusal has its own journal event, and is not a failed HTTP call.
            return
        return super()._observe_http(*args, error=error, **kwargs)

    def _native_order_post(self, endpoint, headers=None, data=None, params=None):
        if getattr(getattr(self, 'm4_ledger', None), 'cash_target', False):
            attempt = self._operation_value('attempt')
            oid = attempt.expected_order_id if attempt is not None else None
            guard = getattr(self, 'm4_native_guard', None)
            try:
                reason = guard(attempt) if callable(guard) else 'M4_NATIVE_GUARD_UNBOUND'
            except Exception as exc:
                raise NativePostNotSent(oid, 'M4_NATIVE_GUARD_FAILED') from exc
            if reason:
                raise NativePostNotSent(oid, reason)
        # No persistence, logging, signing or account queries after this last check.
        if stamp := self._operation_value('capture_native_post'):
            stamp()
        return super()._native_order_post(endpoint, headers=headers, data=data, params=params)

    def _post(self, endpoint, headers=None, data=None, params=None):
        _require(not getattr(self, 'm4_read_only', False), 'M4_ARCHIVE_WRITE_FORBIDDEN')
        execution = getattr(self, 'm4_execution', None)
        _require(execution is not None, 'M4_LIVE_EXECUTION_NOT_BOUND')
        rows = json.loads(data)
        _require(isinstance(rows, list) and all(
            (r.get('postOnly') is False and r.get('orderType') in {'FAK', 'GTD'}) if execution.mode == 'taker'
            else (r.get('postOnly') is True and r.get('orderType') == 'GTC') for r in rows),
            'M4_LIVE_EXECUTION_MODE_CONFLICT')
        return super()._post(endpoint, headers=headers, data=data, params=params)

    def _execution_get(self, endpoint, headers=None, params=None):
        parsed = urlsplit(endpoint)
        if parsed.path.startswith('/data/'):
            ledger = getattr(self, 'm4_ledger', None)
            _require(ledger is not None and getattr(ledger, 'continuous', False)
                     and self.m4_execution.mode == 'taker'
                     and f'{parsed.scheme}://{parsed.netloc}' == OFFICIAL_HOSTS['clob_host']
                     and not parsed.query and not parsed.fragment,
                     'M4_LIVE_FILL_READ_FORBIDDEN')
            gtd_order = getattr(getattr(self, 'operation_local', None), 'gtd_reconciliation_order', None)
            if gtd_order is not None:
                _require(parsed.path in {'/data/orders', '/data/trades'}
                         and isinstance(params, dict) and set(params) == {'market', 'next_cursor'}
                         and params['market'] == self.session_market_id == ledger.get('market')['condition_id']
                         and isinstance(params['next_cursor'], str) and bool(params['next_cursor'])
                         and self.builder.funder.lower() == ledger.get('identity')['funder'].lower()
                         and ledger.owns_order(gtd_order), 'M12_GTD_ACCOUNT_READ_SCOPE_INVALID')
                saved = ledger.attempt(gtd_order)
                plan = json.loads(saved['plan'])
                _require(saved['state'] not in {'TERMINAL', 'UNSENT'} and plan.get('order_type') == 'GTD'
                         and plan.get('pricing_origin', {}).get('follow_limit') is True,
                         'M12_GTD_ACCOUNT_READ_SCOPE_INVALID')
                return ClobClient._get(self, endpoint, headers=headers, params=params)
            if parsed.path.startswith('/data/order/'):
                oid = parsed.path.removeprefix('/data/order/')
                allowed = (re.fullmatch(r'0x[0-9a-fA-F]{64}', oid) is not None
                           and not params and ledger.owns_order(oid))
            elif parsed.path == '/data/trades' and isinstance(params, dict):
                allowed = (set(params) in ({'id', 'market', 'asset_id', 'next_cursor'}, {'id', 'market', 'next_cursor'})
                           and params['market'] == self.session_market_id
                           and ledger.owns_trade(params['id']))
                if allowed:
                    with ledger.lock:
                        oid = ledger.db.execute('SELECT order_id FROM taker_trade_links WHERE trade_id=?',
                                                (params['id'],)).fetchone()[0]
                    attempt = ledger.attempt(oid)
                    if 'asset_id' in params:
                        allowed = params['asset_id'] == json.loads(attempt['quote'])['token_id']
                    else:
                        # A complementary trade's taker asset can be the other token.
                        # Only one already linked trade ID in this exact market is readable.
                        plan = json.loads(attempt['plan'])
                        allowed = plan.get('pricing_origin', {}).get('follow_limit') and plan['order_type'] == 'GTD'
            else:
                allowed = False
            _require(allowed, 'M4_OWNED_ORDER_READ_SCOPE_INVALID')
            return ClobClient._get(self, endpoint, headers=headers, params=params)
        if parsed.path.startswith('/clob-markets/'):
            _require(getattr(self, 'm4_execution', None) is not None
                     and self.m4_execution.mode == 'taker'
                     and parsed.path == f'/clob-markets/{self.session_market_id}'
                     and re.fullmatch(r'0x[0-9a-fA-F]{64}', self.session_market_id) is not None
                     and f'{parsed.scheme}://{parsed.netloc}' == OFFICIAL_HOSTS['clob_host']
                     and not parsed.query and not parsed.fragment and not params,
                     'M4_TAKER_MARKET_INFO_SCOPE_INVALID')
            return ClobClient._get(self, endpoint, headers=headers, params=params)
        return super()._execution_get(endpoint, headers=headers, params=params)

    def _delete(self, endpoint, headers=None, data=None, params=None):
        ids = json.loads(data)
        ledger = getattr(self, 'm4_ledger', None)
        _require(ledger is not None and ledger is getattr(self, 'm12_cancel_ledger', None) and not params
                 and isinstance(ids, list) and len(ids) == 1 and ledger.owns_order(ids[0]),
                 'M12_CANCEL_NOT_OWNED')
        saved = ledger.attempt(ids[0])
        plan = json.loads(saved['plan'])
        _require(saved['started'] and saved['state'] not in {'TERMINAL','UNSENT'}
                 and ledger.get('market')['condition_id'] == self.session_market_id
                 and plan.get('pricing_origin', {}).get('follow_limit')
                 and plan['order_type'] == 'GTD', 'M12_CANCEL_NOT_RESTING_ORDER')
        self.known_ids.add(ids[0])  # Includes an owned order recovered after restart.
        return super()._delete(endpoint, headers=headers, data=data, params=params)

    def m4_taker_permitted(self, wire, attempt):
        ledger = getattr(self, 'm4_ledger', None)
        if ledger is None or not ledger.is_taker:
            return False
        spec = getattr(self, 'm4_taker_ids', {}).get(attempt.expected_order_id)
        if spec is None:
            return False
        row = ledger.row(attempt.quote.window_id)
        plan = ledger.plan(row['id'])
        if market_protocol(plan) != getattr(self, 'session_market_protocol', 'v1'):
            return False
        continuous = getattr(ledger, 'continuous', False)
        cash_target = getattr(ledger, 'cash_target', False)
        amount_buy = bool(plan.get('taker_buy_amount_policy'))
        if amount_buy:
            signed = wire['order']
            maker = Decimal(str(signed['makerAmount']))
            taker = Decimal(str(signed['takerAmount']))
            if (attempt.buy_amount_usdc != plan['principal_usdc']
                    or not ledger.amount_price_allowed(Decimal(plan['limit_price']), maker, taker,
                                                       ledger.root_plan(row['id']), plan.get('pricing'))):
                return False
        elif attempt.buy_amount_usdc is not None:
            return False
        if continuous:
            saved = ledger.attempt(attempt.expected_order_id)
            ledger.validate_attempt(saved)
            budget_allowed = (ledger.signal_budget_allows(row['id'], plan) if cash_target else
                              Decimal(row['spent'])+Decimal(plan['planned_cost_usdc']) <= ledger.order_budget)
            if (saved['started'] or saved['state'] != 'PREPARED'
                    or not budget_allowed
                    or (not cash_target and Decimal(row['filled'])+Decimal(plan['quantity'])
                        > Decimal(ledger.root_plan(row['id'])['quantity']))
                    or (cash_target and ledger.unresolved(row['id']))):
                return False
        follow = plan.get('pricing_origin', {}).get('follow_limit') is True
        kind = plan['order_type'] if follow else 'FAK'
        return (wire.get('postOnly') is False and wire.get('orderType') == attempt.order_type == kind
                and int(wire['order'].get('expiration', 0)) == (plan['expiration'] if follow else 0)
                and attempt.entry_execution == 'taker'
                and attempt.quote.side is Side.BUY and not attempt.post_started
                and row['state'] == 'PREPARED' and not row['started'] and (continuous or row['posts'] == 0)
                and Decimal(row['reserved']) == Decimal(plan['commitment_usdc'])
                    + (ledger.pending_cost(row['id']) if cash_target else 0)
                and row['order_id'] == attempt.expected_order_id
                and spec == {'quote': asdict(attempt.quote), 'plan': plan}
                and Decimal(str(attempt.quote.price)) == Decimal(plan['limit_price'])
                and Decimal(str(attempt.quote.size)) == Decimal(plan['quantity'])
                and (amount_buy or all_in_cost(attempt.quote.price, attempt.quote.size, plan['fees'])
                     == Decimal(plan['planned_cost_usdc']) <= getattr(ledger, 'order_budget', 5))
                and attempt.maximum == Decimal(plan['commitment_usdc']) <= ledger.limit
                and ledger.committed() <= ledger.limit)


class M4LiveGateway(ExecutionGateway):
    async def prepare(self, quotes, meta, tick_sizes, *, buy_amount_usdc=None, follow_plan=None, **kwargs):
        ledger = getattr(self, 'm4_ledger', None)
        protocol = market_protocol(meta)
        if protocol == 'v2' or getattr(self._client, 'session_market_protocol', 'v1') == 'v2':
            _require(ledger is not None and ledger.ledger_strategy == M421_LEDGER
                     and ledger.settings.signal_profile == 'M42112' and ledger.amount_buy and ledger.is_taker,
                     'V2_MARKET_REQUIRES_M42112')
            saved = ledger.get('market')
            _require(protocol == market_protocol(saved) == getattr(self._client, 'session_market_protocol', None)
                     and saved['condition_id'] == meta.condition_id == self._client.session_market_id
                     and saved['tokens'] == [asdict(token) for token in meta.tokens],
                     'M42112_V2_SESSION_MARKET_CONFLICT')
        if ledger is not None and ledger.is_taker:
            if follow_plan is not None:
                key = follow_plan['opportunity_id']
                _require(not kwargs and ledger.amount_buy and ledger.ledger_strategy == M421_LEDGER
                         and ledger.settings.signal_profile == 'M42112'
                         and ledger.root_plan(key).get('pricing_origin', {}).get('follow_limit') is True
                         and ledger.get('next_plan:' + key) == follow_plan
                         and follow_plan['principal_usdc'] == buy_amount_usdc
                         and len(quotes) == 1 and quotes[0].token_id == ledger.row(key)['token']
                         and Decimal(str(quotes[0].price)) == Decimal(follow_plan['limit_price'])
                         and ledger.valid_follow_terms(follow_plan, Decimal(buy_amount_usdc),
                                                       Decimal(str(quotes[0].price)), Decimal(str(quotes[0].size))),
                         'M12_FOLLOW_UNSIGNED_PLAN_CONFLICT')
                return await super().prepare(quotes, meta, tick_sizes, immediate_order_type=follow_plan['order_type'],
                                             buy_amount_usdc=buy_amount_usdc, follow_plan=follow_plan)
            if kwargs:
                raise ValueError('M4_TAKER_PREPARATION_OVERRIDE_FORBIDDEN')
            if getattr(self.m4_ledger, 'amount_buy', False):
                _require(buy_amount_usdc is not None, 'M421_FAK_BUY_AMOUNT_REQUIRED')
                return await super().prepare(quotes, meta, tick_sizes, immediate_order_type='FAK',
                                             buy_amount_usdc=buy_amount_usdc)
            _require(buy_amount_usdc is None, 'M421_FAK_BUY_AMOUNT_FORBIDDEN')
            if getattr(self.m4_ledger, 'cash_target', False):
                from polymaker.m4_live_numeric import fak_amounts_compatible

                _require(all(fak_amounts_compatible(Decimal(str(q.price)), Decimal(str(q.size)))
                             for q in quotes), 'M4_FAK_AMOUNT_PRECISION_INVALID')
            return await super().prepare(quotes, meta, tick_sizes, immediate_order_type='FAK')
        _require(buy_amount_usdc is None, 'M421_FAK_BUY_AMOUNT_FORBIDDEN')
        return await super().prepare(quotes, meta, tick_sizes, **kwargs)

    async def send_prepared(self, prepared, *, submit_guard, operation_context=None):
        ledger = getattr(self, 'm4_ledger', None)
        if ledger is not None and ledger.is_taker:
            attempts = (operation_context or {}).get('batch_attempts', [])
            _require(len(attempts) == len(prepared.orders) == 1 and not prepared.post_only,
                     'M4_TAKER_SINGLE_ORDER_REQUIRED')
            attempt = attempts[0]
            _require(attempt.expected_order_id == prepared.order_ids[0],
                     'M4_TAKER_PREPARED_ID_CHANGED')
            plan = ledger.plan(attempt.quote.window_id)
            principal = attempt.principal_limit
            _require(principal + planned_fee(attempt.quote.price, attempt.quote.size, plan['fees'])
                     == Decimal(plan['planned_cost_usdc'])
                     and principal + buy_amount_fee_reserve(principal, plan['fees'])
                     == Decimal(plan['commitment_usdc'])
                     and (plan.get('taker_buy_amount_policy')
                          or Decimal(plan['planned_cost_usdc']) <= getattr(ledger, 'order_budget', 5)),
                     'M4_TAKER_FEE_RESERVE_CHANGED')
            attempt.entry_execution, attempt.order_type = 'taker', plan.get('order_type', 'FAK')
            attempt.fee_reserve_usdc = str(Decimal(plan['commitment_usdc'])-principal)
            self._client.m4_taker_ids[attempt.expected_order_id] = {
                'quote': asdict(attempt.quote), 'plan': plan}
        return await super().send_prepared(prepared, submit_guard=submit_guard,
                                           operation_context=operation_context)


class M4LiveReads(_ExecutionReads):
    def __init__(self, cfg):
        super().__init__(cfg)
        if self.network is not None:
            self.network.routine_read_log_interval = 30
        self.gateway.close()
        self.gateway = M4LiveGateway(cfg)
        self.taker_fees = None

    async def connect_account(self):
        await super().connect_account()
        old = self.gateway._client
        client = M4LiveClobClient(host=self.cfg.wallet.clob_host, chain_id=self.cfg.wallet.chain_id,
            key=self.cfg.secrets.pk, signature_type=self.cfg.wallet.signature_type,
            funder=self.gateway.funder, creds=old.creds, retry_on_error=False)
        for name in ('known_ids', 'sm_config', 'observed_open_rows', 'observed_trade_legs',
                     'account_policy', '_pagination_failures', 'pagination_revision', 'network'):
            setattr(client, name, getattr(old, name))
        client.m4_taker_ids = {}
        self.gateway._client = client

    async def bind_market(self, settings, meta):
        client = self.gateway._client
        client.m4_execution = settings.execution
        client.session_market_id = meta.condition_id
        client.session_market_protocol = market_protocol(meta)
        if settings.execution.mode == 'taker':
            raw = await self.gateway._io(client.get_clob_market_info, meta.condition_id)
            self.taker_fees = validated_fees(raw, (meta.yes.token_id, meta.no.token_id))

    def bind_ledger(self, ledger):
        self.gateway.m4_ledger = self.gateway._client.m4_ledger = ledger

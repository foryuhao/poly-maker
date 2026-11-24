"""Single-market SM execution composition; no strategy or Engine replacement.

Only this opt-in library path can write orders. M1 and FL entry points are unchanged.
Each executor and its Journal belong to one independent Session. Startup never
reads another Session's Journal, orders, recovery markers or settlement state.
The CLI uses the existing official starting-balance funding policy; current
Session placement, reconciliation, capital and close barriers remain enforced.
Time and HTTP/WS boundaries may be replaced in offline tests.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import time
import uuid
from contextlib import suppress
from dataclasses import asdict, dataclass, field, replace
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from pathlib import Path
from threading import RLock
from typing import Any
from urllib.parse import urlsplit

import httpx
from py_clob_client_v2.client import ClobClient
from py_clob_client_v2.constants import INITIAL_CURSOR
from py_clob_client_v2.exceptions import PolyApiException

from polymaker.catalog.gamma import GammaClient
from polymaker.config import Config, RDPSMConfig
from polymaker.domain import Quote, Side, TradeState
from polymaker.execution.filltruth import ReconciliationOutcome, reconcile_owned_order
from polymaker.execution.gateway import PlacementUnknown
from polymaker.journal import Journal
from polymaker.rdp_sm import (
    DUST_SHARES,
    MIN_REPAIR_EXPOSURE,
    OFFICIAL_HOSTS,
    TEST_CAPITAL_LIMIT_USDC,
    TRADING_END_S,
    ReadOnlyClobClient,
    SessionStatus,
    _account,
    _book,
    _Issue,
    _market,
    _Reads,
    _require,
    _token_id,
    inventory_delta,
    inventory_trading_state,
    repair_required,
    run_preflight,
    trading_start_sec,
)
from polymaker.rdp_sm_http_trace import sdk_write_timing
from polymaker.rdp_sm_recovery import pause_reason, reason_code, retry_read
from polymaker.rdp_sm_volatility import sigma_specification
from polymaker.state.store import StateStore
from polymaker.state.tracker import (
    OrderEvent,
    TradeEvent,
    UserEventProcessor,
    exceeds_tolerance,
    fill_cost,
    within_tolerance,
)
from polymaker.userstream.parse import normalize_order, parse_trade, strict_number

CAPITAL_LIMIT = Decimal(int(TEST_CAPITAL_LIMIT_USDC))
# Used only to recognize the venue's explicit marketable-BUY rejection.
# Passive maker orders are sized by shares, without a local dollar minimum.
MIN_MARKETABLE_BUY_NOTIONAL = Decimal("1")
MIN_MAKER_BUY_SHARES = Decimal("5")
RDP_TOLERANCE = Decimal(".01")
MATCHED_REMAINING_TOLERANCE_SHARES = RDPSMConfig().matched_remaining_tolerance_shares
INVENTORY_TOLERANCE_SHARES = DUST_SHARES  # Strictly below, unlike fill/price tolerance.
SAFE_QUOTE_SKIP_REASONS = frozenset({
    "QUOTE_WOULD_TAKE", "QUOTE_PRICE_TICK_INVALID", "QUOTE_PRICE_TICK_OR_SIZE_INVALID",
    "MARKET_SUBMISSION_CUTOFF",
    "MINIMUM_ORDER_EXCEEDS_MAXIMUM",
})
# Same conservative fixed shutdown buffer used by the existing BTC5m runner.
CANCEL_BUFFER_S = 15.0


class InventoryMismatch(_Issue):
    """A Data API inventory discrepancy pauses admission without losing order proofs."""

    def __init__(self, context):
        super().__init__(SessionStatus.BLOCKED, "ACCOUNT_INVENTORY_NOT_EXPLAINED_BY_SM",
                         context=context)


def maker_buy_size(price: Decimal, inventory_gap: Decimal,
                   min_order_size: Decimal = MIN_MAKER_BUY_SHARES) -> Decimal:
    """Round up the gap and share minimum to the SDK quantity grid.

    Keep the price argument for existing callers; passive maker quantity does
    not grow when its price falls below a dollar's worth of the share minimum.
    """
    return max(inventory_gap, MIN_MAKER_BUY_SHARES, min_order_size).quantize(
        Decimal(".01"), rounding=ROUND_CEILING)


def repair_buy_quantity(executor, side: str, price: Decimal, delta: Decimal,
                        cash: Decimal, *, minimum_notional: Decimal = Decimal(0),
                        max_reverse_residual: Decimal | None = None) -> Decimal:
    """Shared repair sizing: legal minimums, decreasing exposure and capital limits."""
    minimum = maker_buy_size(price, minimum_notional / price,
                             Decimal(str(executor.meta.min_order_size)))
    qty = max(minimum, maker_buy_size(price, abs(delta),
                                     Decimal(str(executor.meta.min_order_size))))
    if abs(delta) > executor.cfg.rdp_sm.repair.delta_limit:
        qty = min(qty, executor.cfg.rdp_sm.max_order_size,
                  (cash/price).quantize(Decimal(".01"), rounding=ROUND_FLOOR))
        if qty < minimum:
            raise ValueError("LIMIT_RECOVERY_MINIMUM_NOT_FEASIBLE")
    if qty > executor.cfg.rdp_sm.max_order_size:
        raise ValueError("MINIMUM_ORDER_EXCEEDS_MAXIMUM")
    after = delta+(qty if side == "YES" else -qty)
    if (max_reverse_residual is not None and delta * after < 0
            and abs(after) > max_reverse_residual):
        raise ValueError("BA_REVERSE_RESIDUAL_EXCEEDS_LIMIT")
    if abs(after) >= abs(delta):
        raise ValueError("REPAIR_WOULD_NOT_REDUCE_DELTA")
    if not executor.delta_candidate_allowed(delta, side, qty):
        raise ValueError("SESSION_DELTA_LIMIT")
    if price*qty > cash:
        raise ValueError("OFFICIAL_OR_SESSION_CAPITAL_INSUFFICIENT")
    return qty


def ba_repair_buy_quantity(executor, side: str, price: Decimal, delta: Decimal,
                           cash: Decimal) -> Decimal:
    """BA exits cover $1 without planning more than 2.5 reverse shares."""
    return repair_buy_quantity(executor, side, price, delta, cash,
                               minimum_notional=MIN_MARKETABLE_BUY_NOTIONAL,
                               max_reverse_residual=Decimal("2.5"))


def _explicit_maker_rejection(item, notional) -> bool:
    """Recognize the venue's observed error response despite its success flag.

    Require the complete rejection shape and absence of all acceptance/fill
    evidence. An empty order ID alone, or an arbitrary error, remains UNKNOWN.
    """
    if not isinstance(item, dict):
        return False
    error = item.get("errorMsg")
    minimum = re.fullmatch(
        r"invalid amount for a marketable BUY order \(\$([0-9]+(?:\.[0-9]+)?)\), min size: \$1",
        error if isinstance(error, str) else "")
    known_error = error == "invalid post-only order: order crosses book" or (
        minimum is not None and Decimal(minimum[1]) == notional
        and 0 < notional < MIN_MARKETABLE_BUY_NOTIONAL)
    return (
        isinstance(item, dict)
        and item.get("success") is True
        and known_error
        and all(item.get(k) == "" for k in ("orderID", "status", "makingAmount", "takingAmount"))
        and all(item.get(k) in (None, "") for k in ("order_id", "orderId", "id"))
        and all(item.get(k) in (None, []) for k in (
            "tradeIDs", "tradeIds", "trade_ids", "transactionsHashes", "transactionHashes",
            "transactions_hashes"))
    )


def _immediate_buy_not_filled_response(attempt: Placement) -> bool:
    """Observed FOK/FAK kill, bound to the validated wire type and signed ID."""
    raw = attempt.raw_response
    if isinstance(raw, list) and len(raw) == 1:
        raw = raw[0]
    oid = attempt.expected_order_id
    errors = {
        'FOK': "order couldn't be fully filled. FOK orders are fully filled or killed.",
        'FAK': 'no orders found to match with FAK order. FAK orders are partially filled or killed if no match is found.',
    }
    order_type = getattr(attempt, 'order_type', None)
    return (
        isinstance(raw, dict) and attempt.post_started
        and getattr(attempt, "entry_execution", None) == "taker"
        and order_type in errors
        and attempt.quote.side is Side.BUY
        and isinstance(oid, str) and re.fullmatch(r"0x[0-9a-fA-F]{64}", oid) is not None
        and raw.get("orderID") == oid == attempt.order_id
        and raw.get("success") is True
        and raw.get("errorMsg") == errors[order_type]
        and all(raw.get(k) == "" for k in ("status", "makingAmount", "takingAmount"))
        and all(raw.get(k) in (None, "") for k in ("order_id", "orderId", "id"))
        and all(raw.get(k) in (None, []) for k in (
            "tradeIDs", "tradeIds", "trade_ids", "transactionsHashes", "transactionHashes",
            "transactions_hashes"))
    )


def _canceled_submission_response(attempt: Placement) -> bool:
    """An exact, identity-bound POST rejection, never a DELETE not-found reply."""
    if _immediate_buy_not_filled_response(attempt):
        return True
    raw = attempt.raw_response
    if isinstance(raw, list) and len(raw) == 1:
        raw = raw[0]
    if not isinstance(raw, dict) or not attempt.post_started:
        return False
    error = raw.get("errorMsg")
    match = re.fullmatch(
        r"order (0x[0-9a-fA-F]{64}) is invalid\. Already canceled\.",
        error if isinstance(error, str) else "",
    )
    return (
        match is not None
        and match[1] == attempt.expected_order_id == attempt.order_id
        and raw.get("success") is True
        and all(raw.get(k) == "" for k in ("orderID", "status", "makingAmount", "takingAmount"))
        and all(raw.get(k) in (None, "") for k in ("order_id", "orderId", "id"))
        and all(raw.get(k) in (None, []) for k in (
            "tradeIDs", "tradeIds", "trade_ids", "transactionsHashes", "transactionHashes",
            "transactions_hashes"))
    )


@dataclass(frozen=True)
class WindowQuote:
    market_id: str
    token_id: str
    outcome: str
    side: Side
    price: float
    size: float
    window_id: str


@dataclass
class Placement:
    attempt_id: str
    quote: WindowQuote
    outcome: str = "INTENT"
    order_id: str | None = None
    post_started: bool = False
    request_hash: str | None = None
    raw_response: Any = None
    expected_order_id: str | None = None
    unknown_recovery_eligible: bool = False
    entry_execution: str = "maker"
    fee_reserve_usdc: str = "0"
    order_type: str = "GTC"
    buy_amount_usdc: str | None = None

    @property
    def principal_limit(self) -> Decimal:
        return (Decimal(self.buy_amount_usdc) if self.buy_amount_usdc is not None else
                Decimal(str(self.quote.price))*Decimal(str(self.quote.size)))

    @property
    def maximum(self) -> Decimal:
        return self.principal_limit + Decimal(self.fee_reserve_usdc)


@dataclass
class WindowResult:
    session_id: str
    window_id: str
    market_id: str
    start_time: float
    end_time: float = 0.0
    requested_quotes: list[WindowQuote] = field(default_factory=list)
    placement_results: list[dict[str, Any]] = field(default_factory=list)
    known_order_ids: list[str] = field(default_factory=list)
    confirmed_fills: list[dict[str, Any]] = field(default_factory=list)
    yes_inventory: float = 0.0
    no_inventory: float = 0.0
    delta: float = 0.0
    capital_committed: float = 0.0
    close_barrier: str = "BLOCKED"
    block_reason: str | None = None
    journal_references: list[str] = field(default_factory=list)
    t0_monotonic: float | None = None
    fill_deadline_monotonic: float | None = None
    close_monotonic: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RiskJournal(Journal):
    """Durable append-only records for one independent Session; no history reads."""

    def __init__(self, directory: str | Path, funder: str, *, session_id=None):
        session_id = uuid.uuid4().hex if session_id is None else session_id
        _require(isinstance(session_id, str)
                 and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", session_id) is not None,
                 "SESSION_ID_INVALID")
        _require(isinstance(funder, str)
                 and re.fullmatch(r"0x[0-9a-fA-F]{40}", funder) is not None,
                 "ACCOUNT_IDENTITY_MISMATCH")
        super().__init__(directory, enabled=False)
        directory = Path(directory).resolve()
        directory.mkdir(parents=True, exist_ok=True)
        self.session_id, self.sequence = session_id, 0
        self.path = str(directory / f"rdp_sm_{funder.lower()}_{session_id}.jsonl")
        # Exclusive creation prevents a second writer/reused ID from appending
        # to this Session. Other Sessions' paths are never opened or parsed.
        self._fh = Path(self.path).open("x", buffering=1, encoding="utf-8")  # noqa: SIM115 - Session owns the handle.
        self.enabled = True
        try:
            self._fh.flush()
            os.fsync(self._fh.fileno())
            for parent in (directory, directory.parent):
                fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
        except BaseException:
            self.close()
            raise

    def write(self, kind: str, payload: Any, ts: float) -> None:
        if self._fh is None:
            raise RuntimeError("SM_JOURNAL_CLOSED")
        _require(payload["session_id"] == self.session_id, "SESSION_JOURNAL_IDENTITY_CONFLICT")
        super().write(kind, payload, ts)
        self._fh.flush()
        os.fsync(self._fh.fileno())
        self.sequence += 1


class ExecutionClobClient(ReadOnlyClobClient):
    """Actual FL SDK with SM-specific transport permissions and POST observation.

    A window issues one POST containing one or two orders; only known-ID cancellation is enabled.
    All other SDK writes (including key creation, RFQ and settlement) are denied
    on this transport. No success inference is made from Gateway's empty list.
    """

    def __getattribute__(self, name):
        if name in {"attempt", "batch_attempts", "network_batch_id", "submit_guard",
                    "on_post_start", "expected_body_hash"}:
            local = object.__getattribute__(self, "__dict__").get("operation_local")
            context = getattr(local, "context", None)
            if context is not None and name in context:
                return context[name]
        return super().__getattribute__(name)

    def _operation_value(self, name, default=None):
        context = getattr(getattr(self, "operation_local", None), "context", None)
        return context.get(name, default) if context is not None else getattr(self, name, default)

    def create_order(self, order_args, options=None):
        # The SDK floors size*100. Binary float 5.02*100 is below 502 and
        # otherwise signs 5.01 shares. Preserve the validated 0.01-share size;
        # the existing signed-quote equality check remains unchanged.
        return super().create_order(
            replace(order_args, size=Decimal(str(order_args.size))), options=options)

    def _observe_http(self, method, endpoint, began, started_wall, *, operation_id=None, error=None,
                      batch_id=None, transport=None):
        network = getattr(self, "network", None)
        if network is None:
            return
        outcome = "SUCCESS"
        if error is not None:
            status = getattr(error, "status_code", None)
            if status in {401, 403}:
                outcome = "AUTHENTICATION_ERROR"
            elif status == 429:
                outcome = "RATE_LIMITED"
            elif status == 408 or isinstance(status, int) and status >= 500:
                outcome = "SERVER_ERROR"
            elif status is not None:
                outcome = "HTTP_REJECTION"
            elif isinstance(error, (PolyApiException, httpx.RequestError)):
                # The SDK wraps httpx transport errors in a status-less exception.
                outcome = "TRANSPORT_ERROR"
            else:
                outcome = "LOCAL_ERROR"
        network.observe(
            method, time.monotonic() - began, error is None, operation_id=operation_id,
            endpoint=urlsplit(endpoint).path, started_at=began, started_wall=started_wall,
            outcome=outcome, batch_id=batch_id,
            http_status=getattr(error, "status_code", None) if error else 200,
            error_type=type(error).__name__ if error else None,
            **({"transport": transport} if transport is not None else {}),
        )

    def _get(self, endpoint, headers=None, params=None):
        path = urlsplit(endpoint).path
        if getattr(self, "cache_reads_only", False) and (
            path in {"/time", "/data/orders", "/data/trades", "/balance-allowance"}
            or path.startswith("/data/order/")
        ):
            raise RuntimeError("SYNC_DIRECT_READ_PROHIBITED")
        if getattr(getattr(self, "operation_local", None), "no_get", False):
            raise ValueError("OPERATION_EXTRA_GET_FORBIDDEN")
        began, started_wall = time.monotonic(), time.time()
        batch_id = getattr(self, "network_batch_id", None)
        try:
            raw = self._execution_get(endpoint, headers, params)
        except BaseException as exc:
            self._observe_http("GET", endpoint, began, started_wall, error=exc, batch_id=batch_id)
            raise
        self._observe_http("GET", endpoint, began, started_wall, batch_id=batch_id)
        return raw

    def _execution_get(self, endpoint, headers=None, params=None):
        parsed = urlsplit(endpoint)
        cfg = getattr(self, "sm_config", None)
        market_id = getattr(self, "session_market_id", None)
        market_info = (
            cfg is not None and cfg.normal_entry_mode == "ca_v1"
            and (cfg.ca_entry_execution == "taker" or cfg.ca_matched_tp.taker_exit_on_expiry)
            and isinstance(market_id, str) and re.fullmatch(r"0x[0-9a-fA-F]{64}", market_id) is not None
            and parsed.path == f"/clob-markets/{market_id}"
            and not parsed.query and not params
        )
        extra = parsed.path == "/data/trades" or parsed.path in {
            f"/data/order/{oid}"
            for oid in getattr(self, "known_ids", set())
            | getattr(self, "observed_open_rows", {}).keys()
        }
        if not extra and not market_info:
            raw = super()._get(endpoint, headers=headers, params=params)
            if parsed.path == "/data/orders":
                self._observe_order_page(raw["data"])
            return raw
        _require(
            f"{parsed.scheme}://{parsed.netloc}" == OFFICIAL_HOSTS["clob_host"]
            and not parsed.fragment,
            "UNAPPROVED_EXECUTION_READ",
        )
        if parsed.path == "/data/trades":
            cursor = (params or {}).get("next_cursor")
            _require(cursor not in self.trade_cursors, "TRADE_PAGINATION_UNRESOLVED")
            self.trade_cursors.add(cursor)
        raw = ClobClient._get(self, endpoint, headers=headers, params=params)
        if market_info:
            _require(isinstance(raw, dict), "CA_TAKER_MARKET_INFO_UNKNOWN")
        elif parsed.path == "/data/trades":
            _require(
                isinstance(raw, dict) and isinstance(raw.get("data"), list),
                "TRADE_PAGE_UNKNOWN",
            )
            for trade in raw["data"]:
                parsed_trade = parse_trade(trade)
                for leg in parsed_trade.maker_legs:
                    if leg.order_id in self.observed_open_rows or leg.order_id in self.known_ids:
                        self.observed_trade_legs.setdefault(
                            (parsed_trade.trade_id, leg.order_id), (parsed_trade.status, leg)
                        )
                        if leg.order_id in self.observed_open_rows:
                            self.observed_open_rows[leg.order_id]["_sm_fill_observed"] = True
                if self.account_policy is not None:
                    self.account_policy.observe_trade(parsed_trade, self.known_ids)
            _require(
                isinstance(raw.get("next_cursor"), str) and bool(raw["next_cursor"]),
                "TRADE_PAGE_UNKNOWN",
            )
        else:
            _require(isinstance(raw, dict), "ORDER_STATE_UNKNOWN")
            _token_id(raw.get("asset_id"))
        return raw

    @property
    def pagination_failure(self):
        for kind, _ in self._pagination_failures:
            return ("TRADES_PAGINATION_INCOMPLETE" if kind == "get_trades"
                    else "OPEN_ORDERS_PAGINATION_INCOMPLETE")
        return None

    def _read_pages(self, kind, params, only_first_page, next_cursor):
        # A complete read clears only its own collection AND filter scope.
        # Keep page observations independently; an empty retry cannot erase risk.
        params = replace(params) if params is not None else None
        scope = tuple(sorted((k, v) for k, v in asdict(params).items() if v)) if params else ()
        key = (kind, scope)
        if kind == "get_trades":
            self.trade_cursors = set()
        else:
            self._sm_order_cursors = set()
        try:
            rows = getattr(super(), kind)(params, only_first_page, next_cursor)
        except Exception:
            self._pagination_failures[key] = params
            self.pagination_revision += 1
            raise
        if not only_first_page and next_cursor in (None, "", INITIAL_CURSOR):
            setattr(self, "last_trades" if kind == "get_trades" else "last_open_rows", rows)
            self._pagination_failures.pop(key, None)
        return rows

    def get_trades(self, params=None, only_first_page=False, next_cursor=None):
        return self._read_pages("get_trades", params, only_first_page, next_cursor)

    def get_open_orders(self, params=None, only_first_page=False, next_cursor=None):
        return self._read_pages("get_open_orders", params, only_first_page, next_cursor)

    def retry_incomplete_reads(self):
        # Restart at page one with the original scope, even when no owned order
        # exists to trigger reconciliation. Never retry a mutation here.
        for (kind, _), params in list(self._pagination_failures.items()):
            getattr(self, kind)(params)

    def _observe_order_page(self, rows):
        # Keep valid page evidence before the SDK requests any later page.
        # Observations are account facts, not SM ownership or inventory.
        for row in rows:
            oid = next(
                row[k].strip()
                for k in ("id", "orderID", "order_id")
                if isinstance(row.get(k), str) and row[k].strip()
            )
            observed = self.observed_open_rows.setdefault(oid, dict(row))
            matched = strict_number(row["size_matched"])
            _require(
                0 <= matched <= strict_number(row["original_size"]),
                "OPEN_ORDER_CUMULATIVE_UNKNOWN",
            )
            observed["_sm_fill_observed"] = (
                observed.get("_sm_fill_observed", False)
                or matched > 0
                or bool(row.get("associate_trades"))
            )

    def _post(self, endpoint, headers=None, data=None, params=None):
        sm_config = getattr(self, "sm_config", None)
        _require(not (sm_config is not None and sm_config.normal_entry_mode == "m1_v1"
                      and sm_config.m1.observation_only), "M1_OBSERVATION_ONLY")
        attempt = self._operation_value("attempt")
        attempts = self._operation_value("batch_attempts") or ([attempt] if attempt is not None else [])
        _require(
            endpoint == f"{OFFICIAL_HOSTS['clob_host']}/orders"
            and bool(attempts)
            and all(not a.post_started for a in attempts)
            # M1 reads cash only and has no order/trade pagination lifecycle.
            and (sm_config is not None and sm_config.normal_entry_mode in {"m1_v1", "m4_v2"}
                 or self.pagination_failure is None)
            and self._operation_value("submit_guard")(),
            "SM_POST_NOT_PERMITTED",
        )
        rows = json.loads(data)
        _require(
            len(rows) == len(attempts) and 1 <= len(rows) <= 2
            and all((r.get("postOnly") is True and r.get("orderType") == "GTC"
                     and a.entry_execution == "maker") or
                    (sm_config is not None and sm_config.normal_entry_mode == "m4_v2"
                     and callable(getattr(self, "m4_taker_permitted", None))
                     and self.m4_taker_permitted(r, a)) or
                    (sm_config is not None and sm_config.normal_entry_mode == "ca_v1"
                     and a.entry_execution == "taker" and r.get("postOnly") is False
                     and ((sm_config.ca_entry_execution == "taker"
                           and (not sm_config.ca_taker.entry_price_ceiling_enabled
                                or Decimal(str(a.quote.price)) < sm_config.ca_taker.entry_price_ceiling)
                           and re.fullmatch(r".+:w\d+", a.quote.window_id) is not None
                           and r.get("orderType") == sm_config.ca_taker.order_type)
                          or (sm_config.ca_matched_tp.taker_exit_on_expiry
                              and a.quote.side is Side.BUY
                              and re.fullmatch(r".+:w\d+:ca-expiry-exit", a.quote.window_id) is not None
                              and r.get("orderType") == "FAK"
                              and a.expected_order_id in getattr(self, "ca_expiry_taker_ids", set())))
                     and a.expected_order_id in getattr(self, "ca_taker_ids", set()))
                    for r, a in zip(rows, attempts, strict=True)),
            "SM_MAKER_ONLY",
        )
        for row, current in zip(rows, attempts, strict=True):
            signed, quote = row["order"], current.quote
            if current.buy_amount_usdc is not None:
                from polymaker.execution.amounts import market_buy_quantity

                amount = Decimal(current.buy_amount_usdc)
                spec = getattr(self, 'expiry_buy_amounts', {}).get(current.expected_order_id)
                _require(current.entry_execution == 'taker' and row.get('orderType') == 'FAK'
                         and row.get('postOnly') is False
                         and current.expected_order_id in getattr(self, 'ca_expiry_taker_ids', set())
                         and spec == (quote.token_id, Decimal(str(quote.price)),
                                      Decimal(str(quote.size)), amount)
                         and amount >= 1
                         and Decimal(str(quote.size)) == market_buy_quantity(amount, Decimal(str(quote.price))),
                         'SIGNED_BUY_AMOUNT_IDENTITY_CHANGED')
            _require(
                signed["side"] == "BUY"
                and signed["signatureType"] == self.builder.signature_type
                and signed["tokenId"] == quote.token_id
                and signed["maker"].lower() == self.builder.funder.lower()
                and signed["signer"].lower() == self.get_address().lower()
                and Decimal(str(signed["takerAmount"])) == Decimal(str(quote.size)) * 1_000_000
                and Decimal(str(signed["makerAmount"])) == current.principal_limit*1_000_000,
                "SIGNED_QUOTE_ECONOMICS_CHANGED",
            )
        expected = self._operation_value("expected_body_hash")
        _require(
            expected is None or hashlib.sha256(data.encode()).hexdigest() == expected,
            "PREPARED_BODY_CHANGED",
        )
        if self._operation_value("on_post_start") is not None:
            self._operation_value("on_post_start")()
        for current, row in zip(attempts, rows, strict=True):
            current.order_type = row["orderType"]
            current.post_started = True
            current.request_hash = hashlib.sha256(data.encode()).hexdigest()
        began, started_wall = time.monotonic(), time.time()
        batch_id = self._operation_value("network_batch_id")
        operation_id = "post:" + hashlib.sha256(data.encode()).hexdigest()
        with sdk_write_timing() as timing:
            try:
                raw = self._native_order_post(endpoint, headers=headers, data=data, params=params)
            except BaseException as exc:
                self._observe_http("POST", endpoint, began, started_wall,
                                   operation_id=operation_id, error=exc, batch_id=batch_id,
                                   transport=timing.snapshot())
                raise
            self._observe_http("POST", endpoint, began, started_wall,
                               operation_id=operation_id, batch_id=batch_id,
                               transport=timing.snapshot())
        for current in attempts:
            current.raw_response = raw
        return raw

    def _native_order_post(self, endpoint, headers=None, data=None, params=None):
        """Execution boundary after every shared signature and admission check."""
        return ClobClient._post(self, endpoint, headers=headers, data=data, params=params)

    def _delete(self, endpoint, headers=None, data=None, params=None):
        ids = json.loads(data)
        _require(
            endpoint == f"{OFFICIAL_HOSTS['clob_host']}/orders"
            and isinstance(ids, list)
            and bool(ids)
            and set(ids) <= self.known_ids,
            "SM_CANCEL_SCOPE_INVALID",
        )
        began, started_wall = time.monotonic(), time.time()
        batch_id = getattr(self, "network_batch_id", None)
        if batch_id is None and getattr(self, "network", None) is not None:
            # An unresolved batch stays owned after the controller returns.
            # Later explicit cleanup must retain that same logical identity.
            batch_id = self.network.snapshot().active_batch
        operation_id = "cancel:" + hashlib.sha256(json.dumps(sorted(ids)).encode()).hexdigest()
        with sdk_write_timing() as timing:
            try:
                raw = ClobClient._delete(self, endpoint, headers=headers, data=data, params=params)
            except BaseException as exc:
                self._observe_http("DELETE", endpoint, began, started_wall,
                                   operation_id=operation_id, error=exc, batch_id=batch_id,
                                   transport=timing.snapshot())
                raise
            self._observe_http("DELETE", endpoint, began, started_wall,
                               operation_id=operation_id, batch_id=batch_id,
                               transport=timing.snapshot())
        return raw


class _ExecutionReads(_Reads):
    def __init__(
        self, cfg: Config, *, risk_reads: _ExecutionReads | None = None, account_policy=None,
        ca_session_id=None,
    ):
        super().__init__(cfg)
        self.risk_reads = risk_reads
        self.account_policy = (risk_reads.account_policy if risk_reads is not None
                               else account_policy)
        self.feed = getattr(risk_reads, "feed", None)
        self.lease = getattr(risk_reads, "lease", None)
        self.network = getattr(risk_reads, "network", None)
        self.market_raw = getattr(risk_reads, "market_raw", None)
        self.ca_session_id = ca_session_id or getattr(risk_reads, "ca_session_id", None)
        if cfg.rdp_sm.normal_entry_mode in {"obi_v1", "ba_v1", "ca_v1", "m1_v1", "m4_v2"} and self.network is None:
            from polymaker.rdp_sm_network import NetworkHealth

            self.network = NetworkHealth(cfg.rdp_sm.obi)

    async def connect_account(self) -> None:
        await super().connect_account()
        gateway = self.gateway
        previous = gateway._client
        gateway._client = ExecutionClobClient(
            host=self.cfg.wallet.clob_host,
            chain_id=self.cfg.wallet.chain_id,
            key=self.cfg.secrets.pk,
            signature_type=self.cfg.wallet.signature_type,
            funder=gateway.funder,
            creds=previous.creds,
            retry_on_error=False,
        )
        gateway._client.known_ids = set()
        gateway._client.sm_config = self.cfg.rdp_sm
        gateway._client.observed_open_rows = {}
        gateway._client.observed_trade_legs = {}
        gateway._client.account_policy = self.account_policy
        gateway._client._pagination_failures = {}
        gateway._client.pagination_revision = 0
        gateway._client.network = self.network
        _require(
            gateway._client.get_address() == gateway.address
            and gateway._client.builder.funder == gateway.funder,
            "ACCOUNT_IDENTITY_MISMATCH",
        )
        if self.risk_reads is not None:
            # Preflight closes its HTTP pool, but its observations remain facts.
            # Carry them across readers/waiting, not a cached READY or balance.
            previous = self.risk_reads.gateway._client
            _execution_identity(self.risk_reads)
            _execution_identity(self)
            _require(
                previous.get_address() == gateway._client.get_address()
                and previous.builder.funder == gateway._client.builder.funder
                and previous.builder.signature_type == gateway._client.builder.signature_type,
                "PREFLIGHT_RISK_ACCOUNT_MISMATCH",
            )
            gateway._client.observed_open_rows = {
                oid: dict(row) for oid, row in previous.observed_open_rows.items()
            }
            gateway._client.observed_trade_legs = dict(previous.observed_trade_legs)
            gateway._client._pagination_failures = dict(previous._pagination_failures)
            gateway._client.pagination_revision = previous.pagination_revision
            # No reader chain or closed credential-bearing clients needed after
            # the identity-bound evidence has been transferred.
            self.risk_reads = None

    async def market(self, slug: str):
        if self.account_policy is None:
            raw = await super().market(slug)
        else:
            from polymaker.rdp_sm_entry import exact_market

            async with GammaClient(self.cfg.wallet.gamma_host) as gamma:
                raw = await exact_market(gamma, slug=slug)
        self.market_raw = raw
        return raw

    async def book(self, token: str):
        _require(self.cfg.rdp_sm.normal_entry_mode not in {"obi_v1", "ba_v1", "ca_v1", "m1_v1", "m4_v2"}, "OBI_REST_BOOK_FORBIDDEN")
        # SDK normally caches tick size indefinitely. SM needs an official read
        # at every submission, rather than a permanently cached M1 snapshot.
        self.gateway._client._ClobClient__tick_sizes.pop(token, None)
        tick = await self.gateway._io(self.gateway._client.get_tick_size, token)
        raw = await self.gateway._io(self.gateway._client.get_order_book, token)
        return raw, tick

    async def ensure_feed(self, meta):
        from polymaker.rdp_sm_marketdata import SessionFeed

        if self.feed is None:
            ca = self.cfg.rdp_sm.ca_observe
            ca_path = (Path(ca.output_dir) / hashlib.sha256(self.ca_session_id.encode()).hexdigest()[:32]
                       / "events.jsonl" if ca.enabled and self.ca_session_id else None)
            self.feed = SessionFeed(meta, self.cfg.rdp_sm.obi, proxy=self.cfg.proxy,
                ba_cfg=self.cfg.rdp_sm.ba if self.cfg.rdp_sm.normal_entry_mode in {"ba_v1", "ca_v1"} else None,
                normal_entry_mode=self.cfg.rdp_sm.normal_entry_mode,
                opposite_ba_cfg=(self.cfg.rdp_sm.repair.opposite_ba
                    if self.cfg.rdp_sm.repair.policy == "opposite_ba_v1" else None),
                opposite_ba_execution=self.cfg.rdp_sm.repair.opposite_ba_execution,
                ca_config=ca if self.ca_session_id else None, ca_log_path=ca_path,
                ca_session_id=self.ca_session_id, ca_pullback_config=self.cfg.rdp_sm.ca_pullback,
                m1_config=self.cfg.rdp_sm.m1,
                m1_log_path=(Path(self.cfg.rdp_sm.m1.output_dir)
                    / hashlib.sha256((self.ca_session_id or "observation").encode()).hexdigest()[:32]
                    / "events.jsonl") if self.ca_session_id else None)
            try:
                await self.refresh_feed_ticks()
            except BaseException:
                if self.feed.ca is not None:
                    await self.feed.close()
                self.feed = None
                raise
            self.feed.task = asyncio.create_task(self.feed.run(), name="rdp-execution-ws")
        _require(
            self.feed.meta.condition_id == meta.condition_id
            and self.feed.meta.tokens == meta.tokens,
            "SESSION_MARKET_CHANGED",
        )
        return self.feed

    async def refresh_feed_ticks(self, *, tokens=None):
        selected = tuple(self.feed.books) if tokens is None else tuple(tokens)
        _require(bool(selected) and all(token in self.feed.books for token in selected),
                 'WS_TICK_TOKEN_IDENTITY')
        ticks = {}
        for token in selected:
            self.gateway._client._ClobClient__tick_sizes.pop(token, None)
            ticks[token] = await self.gateway._io(self.gateway._client.get_tick_size, token)
        try:
            self.feed.verify_ticks(ticks)
        except ValueError as exc:
            raise _Issue(SessionStatus.BLOCKED, str(exc)) from exc

    async def ws_readiness(self, meta):
        feed = await self.ensure_feed(meta)
        deadline = time.monotonic() + self.cfg.risk.ws_stale_halt_s
        while not feed.snapshot().valid:
            if time.monotonic() >= deadline:
                raise _Issue(SessionStatus.BLOCKED, "WS_READINESS_TIMEOUT", context=feed.diagnostics())
            await asyncio.sleep(0.01)
        return feed.snapshot().books()


def _execution_identity(reads: _ExecutionReads) -> str:
    """Verify local SDK/config identity; Safe live permissions remain unverified."""
    from eth_account import Account
    from eth_utils import to_checksum_address

    cfg, gateway = reads.cfg, reads.gateway
    client = gateway._client
    signer = Account.from_key(cfg.secrets.pk).address
    funder = to_checksum_address(cfg.secrets.browser_address)
    mode = cfg.wallet.signature_type
    _require(
        cfg.wallet.chain_id == 137
        and client.get_address() == gateway.address == signer
        and client.builder.funder == gateway.funder == funder
        and client.builder.signature_type == mode,
        "ACCOUNT_IDENTITY_MISMATCH",
    )
    if mode == 0:
        _require(signer == funder, "EOA_FUNDER_IDENTITY_MISMATCH")
        return "LOCAL_EOA_KEY_VERIFIED"
    if mode == 2:
        from py_builder_relayer_client.builder.derive import derive
        from py_builder_relayer_client.config import get_contract_config

        expected = derive(signer, get_contract_config(cfg.wallet.chain_id).safe_factory)
        _require(
            signer != funder and funder == expected,
            "M2_REQUIRES_PROVEN_EOA_OR_DERIVED_SAFE",
        )
        return "NOT_VERIFIED_FOR_LIVE"
    _require(False, "M2_ACCOUNT_SIGNATURE_TYPE_UNSUPPORTED")


class _SMProcessor(UserEventProcessor):
    """Intercept proof invalidation; economic/lifecycle processing stays FL."""

    quantity_tolerance = RDP_TOLERANCE
    price_tolerance = RDP_TOLERANCE
    single_fill_tolerance = RDP_TOLERANCE

    def taker_spec(self, oid):
        spec = getattr(self, 'taker_orders', {}).get(oid)
        owned = self.owned_record(oid)
        if spec is not None and 'buy_amount_usdc' in spec:
            expected = (spec['token_id'], spec['price'], spec['quantity'], spec['buy_amount_usdc'])
            if getattr(self.executor.gateway._client, 'expiry_buy_amounts', {}).get(oid) != expected:
                return None
        if (spec is not None and owned is not None and owned['side'] == 'BUY'
                and spec['market_id'] == owned['condition_id']
                and spec['token_id'] == owned['token_id']
                and spec['price'] == Decimal(str(owned['submitted_price']))
                and spec['quantity'] == Decimal(str(owned['submitted_qty']))):
            return spec
        return None

    def normalize_owned_order(self, row):
        oid = row.get('id', row.get('order_id')) if isinstance(row, dict) else None
        return normalize_order(row, allow_taker_buy_overfill=self.taker_spec(oid) is not None)

    def is_fak_buy(self, oid):
        """Only a registered, actually posted native FAK has killed remainder."""
        if self.taker_spec(oid) is None:
            return False
        return (oid in getattr(self.executor.gateway._client, 'ca_taker_ids', set())
                and any(a.post_started and a.order_id == a.expected_order_id == oid
                        and a.entry_execution == 'taker' and a.order_type == 'FAK'
                        and a.quote.side is Side.BUY for a in self.executor.attempts))

    def fill_quantity_mismatch(self, event, owned, observed_prior):
        spec = self.taker_spec(event.maker_order_id)
        if spec is None:
            return super().fill_quantity_mismatch(event, owned, observed_prior)
        from polymaker.rdp_sm_ca_taker import validate_buy_fills

        if event.liquidity_role != 'TAKER' or event.principal_usdc is None or event.fee_usdc is None:
            return True
        fills = [f for key, f in self.fill_records.items()
                 if key[1] == event.maker_order_id and key[0] != event.trade_id
                 and f.status is not TradeState.FAILED]
        try:
            validate_buy_fills(fills+[event], spec)
            return False
        except (ValueError, TypeError, ArithmeticError):
            return True

    def events_from_trade(self, trade):
        events = super().events_from_trade(trade)
        spec = getattr(self, "taker_orders", {}).get(trade.taker_order_id)
        if spec is not None:
            from polymaker.rdp_sm_ca_taker import taker_event

            events.append(taker_event(trade, spec))
        return events

    def tracked_trade_qty(self, order_id):
        return float(sum((Decimal(str(f.size)) for f in self.economic_fills_for_order(order_id)), Decimal(0)))

    def retire_unaccepted_order(self, order_id, *, source):
        row = self.owned_record(order_id)
        if (row is not None and row["source"] == source
                and not any(key[1] == order_id for key in self.fill_records)
                and order_id not in self.quarantined_orders):
            self._store.retire_unaccepted_order(order_id, source)
            self._owned.discard(order_id)

    def __init__(self, executor: SMWindowExecutor, store: StateStore):
        super().__init__(store)
        self.executor = executor

    def _external_commitment_changed(self, oid):
        observed = self.executor.gateway._client.observed_open_rows
        if oid in observed and self.owned_record(oid) is None:
            # External WS facts cannot be silently discarded while an old
            # no-fill proof is being used to admit a new SM order.
            observed[oid]["_sm_external_event"] = True
            self.executor.invalidate("EXTERNAL_COMMITMENT_EVIDENCE_CHANGED")

    def on_trade(self, ev: TradeEvent, condition_id: str, **kwargs):
        self._external_commitment_changed(ev.maker_order_id)
        e = self.executor
        if ev.maker_order_id in getattr(e, "sl_order_ids", set()) and any(
                (a.order_id or a.expected_order_id) == ev.maker_order_id
                and a.outcome in {"REJECTED", "ABORTED"} for a in e.attempts):
            e.fatal = "REJECTED_ORDER_FILL_CONTRADICTION"
            for a in e.attempts:
                if (a.order_id or a.expected_order_id) == ev.maker_order_id:
                    a.outcome, a.order_id = "UNKNOWN", ev.maker_order_id
                    e.known.add(a.order_id)
                    if self.owned_record(a.order_id) is None:
                        self.register_order(a.order_id, e.meta.condition_id, a.quote.token_id,
                            Side.BUY, a.quote.size, a.quote.price, "rdp_sm_sl", quiet=True)
        key = (ev.trade_id, ev.maker_order_id)
        proven = e.position_reconciled.get(key)
        prior = self.fill_records.get(key)
        confirmation_only = (
            ev.status is TradeState.CONFIRMED
            and proven is not None and prior is not None
            and prior.status in {TradeState.MATCHED, TradeState.MINED}
            and condition_id == proven.market_id
            and e.proofs.get(ev.maker_order_id, {}).get("outcome") == "POSITION_RECONCILED"
            and all(replace(f, status=ev.status, ts=ev.ts) == ev for f in (proven, prior))
        )
        if key not in e.fill_observed_at:
            start = int(e.slug.rsplit("-", 1)[1])
            observed_mono = e.monotonic()
            offset = e.official_time-e.time_anchor-start
            now = start+offset+observed_mono
            native = float(ev.ts or 0)
            if native > 1e12:
                native /= 1000
            lower_bound = e.order_post_elapsed.get(ev.maker_order_id)
            # A future venue stamp cannot anchor a causal price/holding path.
            valid_native = (lower_bound is not None and start+lower_bound <= native <= now)
            e.fill_observed_at[key] = {
                "elapsed": (native if valid_native else now)-start,
                "observed_elapsed": now-start,
                "observed_monotonic": observed_mono,
                "fill_monotonic": native-start-offset if valid_native else observed_mono,
                "source": "VENUE_TRADE_TIME" if valid_native else "LOCAL_FIRST_TRADE_OBSERVATION",
            }
        if ev.status is TradeState.CONFIRMED:
            e.fill_observed_at[key].setdefault("economic_observed_monotonic", e.monotonic())
            e.fill_observed_at[key].setdefault(
                "economic_observed_elapsed", e.official_time+e.monotonic()-e.time_anchor
                - int(e.slug.rsplit("-", 1)[1]))
        economic_before = {(f.trade_id, f.maker_order_id, f.size, f.price)
                           for f in e.economic_fills()}
        before = (
            dict(self.fill_records),
            set(self.applied_fill_keys),
            set(self.quarantined_fill_keys),
        )
        super().on_trade(ev, condition_id, **kwargs)
        after = (self.fill_records, self.applied_fill_keys, self.quarantined_fill_keys)
        if before != after:
            if kwargs.get("source", "WS") == "WS":
                self.executor.external_revision += 1
            confirmation_only = (
                confirmation_only and key in self.applied_fill_keys
                and key not in self.quarantined_fill_keys
                and ev.maker_order_id not in self.quarantined_orders
                and not self.has_unresolved_fill_key(ev.maker_order_id)
                and not self.has_failed_fill(ev.maker_order_id)
            )
            # A proven fill's unchanged CONFIRMED upgrade preserves its queue
            # economics. Invalidate the session revision regardless: admission
            # and final close still require fresh reconciliation of all orders.
            self.executor.invalidate(
                "TRADE_CONFIRMATION_ADVANCED" if confirmation_only else "TRADE_EVIDENCE_CHANGED",
                None if confirmation_only else ev.maker_order_id,
            )
            if e.cfg.rdp_sm.normal_entry_mode in {"ba_v1", "ca_v1"}:
                e.update_repair_episode()
            economic_after = {(f.trade_id, f.maker_order_id, f.size, f.price)
                              for f in e.economic_fills()}
            if economic_before != economic_after:
                e.economic_revision += 1
                e.last_economic_change_monotonic = e.monotonic()
                if e.shared_reads is not None:
                    e.shared_reads.confirmed_inventory_changed()
            with suppress(Exception):
                self.executor.record("sm_trade", asdict(ev))
                if confirmation_only:
                    self.executor.record("sm_trade_confirmation_advanced", {
                        "order_id": ev.maker_order_id, "trade_id": ev.trade_id,
                        "queue_economics_unchanged": True, "close_recheck_required": True,
                    })

    def on_order(self, ev: OrderEvent, condition_id: str, **kwargs):
        self._external_commitment_changed(ev.order_id)
        if self.owned_record(ev.order_id) is not None:
            self.executor.observed_order_events.add(ev.order_id)
            self.executor.external_revision += 1
            previous = self.executor.terminal.get(ev.order_id)
            released = getattr(self.executor, "sl_terminal_quantities", {})
            if ev.order_id in released and (not _terminal(ev, self.executor.matched_remaining_tolerance_shares,
                    taker_buy=self.taker_spec(ev.order_id) is not None,
                    fak_buy=self.is_fak_buy(ev.order_id))
                    or Decimal(str(ev.size_matched)) != released[ev.order_id]):
                released.pop(ev.order_id, None)
                self.executor.fatal = "SL_TERMINAL_EVIDENCE_CONTRADICTION"
            if previous is not None and not _terminal(ev, self.executor.matched_remaining_tolerance_shares,
                    taker_buy=self.taker_spec(ev.order_id) is not None,
                    fak_buy=self.is_fak_buy(ev.order_id)):
                self.executor.terminal_conflict(ev.order_id, source="ORDER_EVENT")
            self.executor.invalidate("ORDER_EVIDENCE_CHANGED", ev.order_id)
            with suppress(Exception):
                self.executor.record("sm_order", asdict(ev))
        super().on_order(ev, condition_id, **kwargs)

    def quarantine_order(self, order_id, reason, **kwargs):
        if self.owned_record(order_id) is not None:
            self.executor.invalidate("ORDER_QUARANTINED", order_id)
        super().quarantine_order(order_id, reason, **kwargs)


def _terminal(event: OrderEvent, tolerance: Decimal = MATCHED_REMAINING_TOLERANCE_SHARES,
              *, taker_buy=False, fak_buy=False) -> bool:
    if event.status in {"CANCELED", "CANCELLED"}:
        return True
    if event.status != "MATCHED":
        return False
    original, matched = Decimal(str(event.original_size)), Decimal(str(event.size_matched))
    if not original.is_finite() or not matched.is_finite():
        return False
    # Inclusive Decimal boundary; inventory still uses actual confirmed fills.
    return (within_tolerance(original, matched, tolerance) or (taker_buy and matched >= original)
            or (taker_buy and fak_buy and event.side is Side.BUY and 0 < matched < original))


def _same_fill_economics(left, right):
    """Keep identity/lifecycle exact; compare only quantity and price with tolerance."""
    return (left[:-2] == right[:-2]
            and within_tolerance(left[-2], right[-2], RDP_TOLERANCE)
            and within_tolerance(left[-1], right[-1], RDP_TOLERANCE))


class SMWindowExecutor:
    """Opt-in production API for M3: open -> execute_window -> close.

    No loop calls execute_window automatically. BLOCKED requires read-only
    verification or manual intervention; UNKNOWN/contradictions never auto-clear.
    The caller must not share this account with another trading writer.
    """

    def __init__(
        self,
        cfg: Config,
        slug: str,
        session_id: str,
        reads: _ExecutionReads,
        journal: RiskJournal,
        *,
        monotonic=time.monotonic,
        sleep=asyncio.sleep,
        display_events=None,
    ):
        self.cfg, self.slug, self.session_id = cfg, slug, session_id
        self.matched_remaining_tolerance_shares = cfg.rdp_sm.matched_remaining_tolerance_shares
        self.reads, self.gateway, self.journal = reads, reads.gateway, journal
        self.monotonic, self.sleep = monotonic, sleep
        self.store = StateStore(":memory:")
        self.processor = _SMProcessor(self, self.store)
        self.attempts: list[Placement] = []
        # Set before a durable OBI candidate write can start, including writes
        # whose caller is cancelled before the completion counter is updated.
        self.preparation_started = False
        self.known: set[str] = set()
        self.terminal: dict[str, OrderEvent] = {}
        self.terminal_conflicts: dict[str, float] = {}
        self.proofs: dict[str, dict[str, Any]] = {}
        # Keep rejected signed IDs observable, without inventing exchange orders
        # or terminal OrderEvents for submissions the venue declared invalid.
        self.canceled_submission_proofs: dict[str, dict[str, Any]] = {}
        self.immediate_rejection_proven_ids: set[str] = set()
        self.observed_order_events: set[str] = set()
        self.pending_confirmations: set[str] = set()
        self.position_reconciled: dict[tuple[str, str], TradeEvent] = {}
        self.position_excluded: set[tuple[str, str]] = set()
        self.starting_collateral: Decimal | None = None
        self.repair_episode: dict | None = None
        from polymaker.rdp_sm_cooldown import Cooldown

        self.cooldown = Cooldown(cfg.rdp_sm.cooldown)
        self.repair_abandonment: dict | None = None
        self._repair_abandon_lock = RLock()
        self.repair_order_started: dict[str, float] = {}
        self.fill_observed_at: dict[tuple[str, str], dict] = {}
        self.ba_opening = None
        self.ba_orders = {}
        self.ba_logged = {}
        self.order_post_elapsed: dict[str, float] = {}
        # Original CA order IDs retain their TP lease through confirmation,
        # cancellation and BA/TP restoration; fresh/reverse lots are independent.
        self.ca_tp_deadlines: dict[str, float] = {}
        self.merged_pairs = Decimal(0)
        self.merged_cost: dict[str, Decimal] = {}
        self.merge_pending = False
        self.inventory_mismatch = None
        self.inventory_observation = None
        self.economic_revision = 0
        self.last_economic_change_monotonic = 0.0
        from polymaker.rdp_sm_inventory_lag import InventoryLagAdmission

        self.inventory_lag = InventoryLagAdmission(self)
        self.revision = 0
        self.external_revision = 0
        self.proof_revision = -1
        self.proof_pagination_revision = -1
        self.fatal: str | None = None
        self.reason: str | None = "NO_CLOSE_EVIDENCE"
        self.used_windows: set[str] = set()
        self.lock = asyncio.Lock()
        self.closed = False
        self.official_time = 0.0
        self.official_time_read_duration_s = 0.0
        self.time_anchor = self.monotonic()
        self.meta = None
        self._cancel_jobs: dict[frozenset[str], asyncio.Task] = {}
        # Only CA TP -> BA handoffs share these per-order tasks. Their result
        # is a positive cancellation ACK time, never a terminal/fill proof.
        self._tp_ba_cancel_jobs: dict[str, asyncio.Task] = {}
        # Optional CLI-only references to already-created Journal payloads.
        # No formatting, I/O, callbacks or queue writes inside execution.
        self.display_events = display_events
        self.shared_reads = None
        from polymaker.rdp_sm_normal_unknown import NormalUnknownRecovery

        self.normal_unknown = NormalUnknownRecovery(self)
        if cfg.rdp_sm.sync_observe.use_for_execution:
            companion = getattr(reads.account_policy, "sync_observer", None)
            _require(cfg.rdp_sm.normal_entry_mode in {"obi_v1", "ba_v1", "ca_v1", "m1_v1"} and companion is not None,
                     "SYNC_CACHE_SERVICE_REQUIRED")
            from polymaker.rdp_sm_cached_reads import CachedExecutionReads

            self.shared_reads = CachedExecutionReads(self, companion)

    @classmethod
    async def open(
        cls,
        cfg: Config,
        slug: str,
        session_id: str,
        *,
        risk_reads: _ExecutionReads | None = None,
        **clock,
    ):
        _require(isinstance(session_id, str) and bool(session_id.strip()), "SESSION_ID_INVALID")
        # M1 READY is a required startup snapshot, never a cached write permit.
        # Preserve even the startup account observation; READY does not prove
        # that a liability omitted by the next replica read has been released.
        startup_reads = _ExecutionReads(cfg, risk_reads=risk_reads, ca_session_id=session_id)
        preflight = await run_preflight(cfg, slug, _reads=startup_reads)
        if preflight.status is not SessionStatus.READY:
            if risk_reads is None:
                if startup_reads.feed is not None:
                    await startup_reads.feed.close()
                if startup_reads.lease is not None:
                    startup_reads.lease.close()
            raise _Issue(SessionStatus.BLOCKED, "M1_SESSION_NOT_READY",
                         context={"preflight": preflight.to_dict()})
        from polymaker.rdp_sm_sizing import validate_market_sizing

        validate_market_sizing(cfg.rdp_sm, Decimal(str(preflight.market["min_order_size"])))
        reads = _ExecutionReads(cfg, risk_reads=startup_reads)
        journal = None
        try:
            await reads.connect_account()
            authority = _execution_identity(reads)
            journal = RiskJournal(cfg.paths.journal_dir, reads.gateway.funder, session_id=session_id)
            if cfg.rdp_sm.normal_entry_mode in {"obi_v1", "ba_v1", "ca_v1", "m1_v1"}:
                from polymaker.rdp_sm_fence import create_fence
                from polymaker.rdp_sm_writer import AsyncRiskWriter

                journal = AsyncRiskWriter(journal)
                await asyncio.to_thread(
                    create_fence,
                    cfg,
                    reads.gateway.funder,
                    reads.feed.meta,
                    session_id,
                    journal.path,
                    reads.lease,
                )
            executor = cls(cfg, slug, session_id, reads, journal, **clock)
            executor.starting_collateral = Decimal(str(preflight.account["official_balance_usd"]))
            executor.record(
                "sm_session",
                {
                    "session_id": session_id,
                    **(
                        {
                            "normal_entry_mode": cfg.rdp_sm.normal_entry_mode,
                            "normal_start_sec": trading_start_sec(cfg),
                            "normal_cutoff_sec": cfg.rdp_sm.normal_cutoff_sec,
                            "sizing_mode": cfg.rdp_sm.sizing_mode,
                            "risk_per_trade_usdc": str(cfg.rdp_sm.risk_per_trade_usdc),
                            "normal_order_policy": "SIGNAL_SIDE_ONLY",
                            "normal_active_order_policy": ("CA_PULLBACK_FIXED_ADVANCE_DEADLINE" if cfg.rdp_sm.ca_pullback.enabled
                                                           else "CA_FIXED_T0_DEADLINE" if cfg.rdp_sm.normal_entry_mode == "ca_v1"
                                                           else "BA_FIXED_T0_DEADLINE" if cfg.rdp_sm.normal_entry_mode == "ba_v1"
                                                           else "FIXED_DEADLINE_CANCEL_ON_VALID_REVERSAL"),
                            "normal_sigma_required": cfg.rdp_sm.normal_entry_mode == "obi_v1",
                            "probability_sigma": sigma_specification(),
                            "tp_execution_policy": (None if cfg.rdp_sm.repair.policy == "opposite_ba_v1"
                                                    else "INDEPENDENT_FIXED_PRICE_TP_WITH_SL_HANDOFF"),
                            "runtime_source_sha256": {
                                name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                                for name in ("rdp_sm.py", "rdp_sm_execution.py", "rdp_sm_normal.py", "rdp_sm_quotes.py",
                                    "rdp_sm_volatility.py", "rdp_sm_repair.py", "rdp_sm_path_repair.py",
                                    "rdp_sm_tp.py", "rdp_sm_sl.py", "rdp_sm_sl_ledger.py", "rdp_sm_cooldown.py", "rdp_sm_abandon.py",
                                    "rdp_sm_lifecycle.py", "rdp_sm_loop.py", "rdp_sm_marketdata.py",
                                    "rdp_sm_unknown_recovery.py", "rdp_sm_inventory_lag.py",
                                    "rdp_sm_cached_reads.py", "rdp_sm_network.py", "rdp_sm_http_trace.py", "rdp_sm_sync.py",
                                    "repair_policy.py", "rdp_sm_ba.py", "rdp_sm_opposite_ba.py", "rdp_sm_signal.py", "config.py",
                                    "rdp_sm_normal_unknown.py",
                                    "rdp_sm_ca.py", "rdp_sm_ca_writer.py", "rdp_sm_ca_entry.py", "rdp_sm_ca_pullback.py", "rdp_sm_ca_taker.py", "rdp_sm_ca_expiry_exit.py", "rdp_sm_repair_ba.py",
                                    "rdp_sm_ca_matched_tp.py", "rdp_sm_restored_tp.py",
                                    "rdp_sm_tp_ba_handoff.py", "rdp_sm_m1.py", "rdp_sm_m1_signal.py")},
                            "ca_matched_tp": cfg.rdp_sm.ca_matched_tp.model_dump(mode="json"),
                            "ca_entry_direction": cfg.rdp_sm.ca_entry_direction,
                            "ca_entry_execution": cfg.rdp_sm.ca_entry_execution,
                            "ca_taker": cfg.rdp_sm.ca_taker.model_dump(mode="json"),
                            "ca_pullback": cfg.rdp_sm.ca_pullback.model_dump(mode="json"),
                            "config_dir": str(cfg.config_dir),
                            "normal_unknown_cache_recovery": {
                                "enabled": cfg.rdp_sm.sync_observe.use_for_execution,
                                "wait_sec": executor.normal_unknown.wait_sec,
                                "risk_retained": True,
                            },
                            "obi": cfg.rdp_sm.obi.model_dump(mode="json"),
                            **({"ba": cfg.rdp_sm.ba.model_dump(mode="json")} if cfg.rdp_sm.normal_entry_mode in {"ba_v1", "ca_v1"} else {}),
                            "ca_observation": {"mode": ("NORMAL_ENTRY" if cfg.rdp_sm.normal_entry_mode == "ca_v1" else "OBSERVATION_ONLY"),
                                "config": cfg.rdp_sm.ca_observe.model_dump(mode="json"),
                                "runtime": reads.feed.ca_diagnostics()},
                            "repair": cfg.rdp_sm.repair.model_dump(mode="json"),
                            "cooldown": cfg.rdp_sm.cooldown.model_dump(mode="json"),
                            "repair_exit_threshold_shares": str(MIN_REPAIR_EXPOSURE),
                        }
                        if cfg.rdp_sm.normal_entry_mode in {"obi_v1", "ba_v1", "ca_v1", "m1_v1"}
                        else {}
                    ),
                    **({"m1": cfg.rdp_sm.m1.model_dump(mode="json"),
                         "m1_signal_spec": "orderbook-toolbox-m1-observation-calibration-v6",
                         "m1_execution_policy": ("OBSERVATION_ONLY_WOULD_POST" if cfg.rdp_sm.m1.observation_only
                                                 else "RF_EX_ONE_POST_ONE_ACTIVE_ORDER")}
                        if cfg.rdp_sm.normal_entry_mode == "m1_v1" else {}),
                    "slug": slug,
                    "signer": reads.gateway.address,
                    "funder": reads.gateway.funder,
                    "signature_type": cfg.wallet.signature_type,
                    "live_authority": authority,
                    "starting_available_capital_usdc": preflight.account["available_balance_usd"],
                    "starting_spendable_usdc": min(
                        preflight.account["available_balance_usd"],
                        preflight.account["applicable_allowance_usd"]
                        - preflight.account["existing_buy_commitment_usd"],
                    ),
                    "starting_official_balance_usdc": preflight.account["official_balance_usd"],
                    "test_capital_limit_usdc": str(executor.capital_limit),
                    # Compatibility fields only; no prior Session evidence.
                    "historical_pending_cost_reserved_usd": 0,
                    "historical_matched": {},
                    **(
                        {
                            "capital_basis": "OFFICIAL_BALANCE_AT_SESSION_START",
                            "historical_pending_cost_observed_usd": 0,
                        }
                        if reads.account_policy is not None
                        and reads.account_policy.use_starting_balance
                        else {}
                    ),
                    "preflight_observed_order_ids": sorted(
                        reads.gateway._client.observed_open_rows
                    ),
                    "preflight_observed_trade_ids": sorted(
                        {tid for tid, _ in reads.gateway._client.observed_trade_legs}
                    ),
                    "preflight_account_incomplete": reads.gateway._client.pagination_failure,
                },
            )
            return executor
        except BaseException:
            reads.close()
            if journal is not None:
                journal.close()
            if risk_reads is None:
                if reads.feed is not None:
                    await reads.feed.close()
                if reads.lease is not None:
                    reads.lease.close()
            raise

    def record(self, kind: str, payload: Any, *, event_wall=None) -> None:
        try:
            self.journal.write(
                kind, {"session_id": self.session_id, "payload": payload},
                time.time() if event_wall is None else event_wall
            )
        except Exception:
            self.fatal = "JOURNAL_WRITE_FAILED"
            self.proof_revision = -1
            raise
        if self.display_events is not None:
            with suppress(Exception):
                self.display_events.append((kind, payload))
        # One-way, in-memory observation only. Never use companion snapshots
        # for trading, network admission, inventory or closing authorization.
        sink = getattr(getattr(getattr(self, "reads", None), "account_policy", None),
                       "sync_observer", None)
        if sink is not None:
            with suppress(Exception):
                sink.capture(kind, payload, monotonic=time.monotonic())

    def drain_network_events(self):
        network = getattr(self.reads, "network", None)
        if network is not None:
            for kind, payload, wall in network.drain_events():
                self.record(kind, payload, event_wall=wall)

    def network_health(self, signal_age_ms=0):
        # The final SDK guard runs in a worker. Only the async coordinator
        # drains diagnostics into the single writer's submission queue.
        return self.reads.network.snapshot(signal_age_ms)

    def invalidate(self, reason: str, order_id: str | None = None) -> None:
        if order_id in self.canceled_submission_proofs:
            self._restore_canceled_submission(order_id)
        self.revision += 1
        self.proof_revision = -1
        self.reason = reason
        if order_id is not None:
            self.proofs.pop(order_id, None)

    def terminal_conflict(self, oid, *, source, observed_after=None):
        """Revoke trading authority until full proof newer than the conflict."""
        stamp = time.monotonic() if observed_after is None else observed_after
        previous = self.terminal_conflicts.get(oid, 0.0)
        if stamp <= previous:
            return
        self.terminal_conflicts[oid] = stamp
        # Do not overwrite unrelated fatal states, e.g. a failed WAL or mutation.
        if self.fatal is None:
            self.fatal = "TERMINAL_LIVE_CONTRADICTION"
        self.invalidate("TERMINAL_LIVE_CONTRADICTION", oid)
        getattr(self, "sl_terminal_quantities", {}).pop(oid, None)
        if self.shared_reads is not None:
            cache = self.shared_reads
            cache.last_write = max(cache.last_write, stamp)
            cache.order_writes[oid] = max(cache.order_writes.get(oid, 0.0), stamp)
            cache.service.track_orders({oid: stamp})
        self.record("sm_terminal_recheck", {"order_id": oid, "source": source,
            "proof_after_monotonic": stamp, "risk_retained": True})

    @property
    def safe_to_continue(self) -> bool:
        return (
            getattr(self.journal, "failure", None) is None
            and not self.closed
            and not self.merge_pending
            and self.fatal is None
            and self.gateway._client.pagination_failure is None
            and self.reason is None
            and self.proof_revision == self.revision
            and self.proof_pagination_revision == self.gateway._client.pagination_revision
            and all(a.outcome in {"ACK", "VERIFIED", "REJECTED", "ABORTED"} for a in self.attempts)
            and set(self.proofs) == self.known
            and set(self.terminal) == self.known
        )

    @property
    def admission_ready(self) -> bool:
        # Cache reconciliation can permit bounded new risk without asserting
        # that an UNKNOWN order is rejected, terminal, or no longer reserved.
        return self.safe_to_continue or bool(self.normal_unknown.ids())

    def interrupted_before_orders(self) -> bool:
        """An abort latch can be cleared only before any OBI candidate risk exists."""
        return (
            self.cfg.rdp_sm.normal_entry_mode in {"obi_v1", "ba_v1", "ca_v1", "m1_v1"}
            and not self.preparation_started
            and not self.attempts
            and not self.known
            and not self.merge_pending
            and self.merged_pairs == 0
            and not self.confirmed()
            and self.fatal in {None, "ABNORMAL_EXIT"}
            and getattr(self.journal, "failure", None) is None
        )

    def confirmed(self) -> list[TradeEvent]:
        return sorted(
            (
                ev
                for ev in self.processor.economic_fills_for_orders(self.known)
                if ev.status is TradeState.CONFIRMED
            ),
            key=lambda ev: (ev.trade_id, ev.maker_order_id),
        )

    def economic_fills(self) -> list[TradeEvent]:
        """One economic leg per identity, independent of a delayed status label."""
        by_key = dict(self.position_reconciled)
        by_key.update({(f.trade_id, f.maker_order_id): f for f in self.confirmed()})
        return [by_key[k] for k in sorted(by_key)]

    @property
    def capital_limit(self):
        from polymaker.rdp_sm import session_capital_limit

        return Decimal(int(session_capital_limit(self.cfg)))

    def economic_inventory(self):
        return tuple(sum((Decimal(str(f.size)) for f in self.economic_fills()
                          if f.token_id == t), Decimal(0))-self.merged_pairs
                     for t in (self.meta.yes.token_id, self.meta.no.token_id))

    def delta_candidate_allowed(self, delta, side, quantity, *, pending_batch=(), normal_entry=False):
        if self.cfg.rdp_sm.normal_entry_mode == "m1_v1":
            return True  # Money, not share Delta, constrains this strategy.
        recovery = getattr(self, "normal_unknown", None)
        if recovery is not None and recovery.unknowns():
            return recovery.candidate_allowed(delta, side, quantity, exclude=pending_batch)
        if (normal_entry and self.cfg.rdp_sm.normal_entry_mode == "ca_v1"
                and self.cfg.rdp_sm.sizing_mode == "fixed_risk"):
            # This explicit opening mode uses a price-dependent principal cap.
            # The quote and native order guards enforce that cap; do not apply
            # the legacy five-share admission limit to a risk-sized opening.
            return 0 < quantity <= self.cfg.rdp_sm.max_order_size
        limit = self.cfg.rdp_sm.repair.delta_limit
        after = delta+(quantity if side == "YES" else -quantity)
        if abs(delta) > limit:
            return abs(after) < abs(delta) and delta*after >= -abs(delta)*limit
        return max(abs(delta), abs(after)) <= limit

    def begin_repair_episode(self, window_id, quotes, *, baseline=None):
        if self.repair_episode is not None:
            raise ValueError("REPAIR_EPISODE_ALREADY_OWNS_SESSION")
        self.cooldown.begin(window_id, self._cooldown_pair_snapshot() if baseline is None else baseline)
        dual = len(quotes) == 2 and {q.outcome for q in quotes} == {"YES", "NO"}
        self.repair_episode = {"opening_window": window_id,
                               "side": None if dual else quotes[0].outcome,
                               "dual_normal": dual,
                               "opening_monotonic": self.monotonic(),
                               "path_clock_offset": self.official_time-self.time_anchor
                               - int(self.slug.rsplit("-", 1)[1]),
                               "original_qty": "0", "submissions": 0,
                               "windows": [window_id]}
        self.record("sm_repair_episode", {"event": "OPENING_SUBMITTED", **self.repair_episode})

    def _cooldown_pair_snapshot(self):
        from polymaker.rdp_sm_cooldown import pair_snapshot

        # economic_fills contains CONFIRMED trades plus actual-position and
        # collateral reconciled trades admitted by the existing close barrier.
        # Both carry real maker leg prices; pending/estimated fills are absent.
        return pair_snapshot(self.economic_fills(), self.fill_observed_at,
                             self.meta.yes.token_id, self.meta.no.token_id)

    @property
    def trading_state(self):
        if self.cfg.rdp_sm.normal_entry_mode == "m1_v1":
            return "M1"
        if self.repair_abandonment is not None:
            return "HOLD_TO_SETTLEMENT"
        if self.cooldown.active:
            return "COOLDOWN"
        return "REPAIR" if (self.repair_episode is not None
                            or repair_required(*self.economic_inventory())) else "NORMAL"

    def check_repair_abandonment(self):
        """Permanent market latch. Also called by the SDK's final POST guard."""
        if self.cfg.rdp_sm.normal_entry_mode == "m1_v1":
            return False
        with self._repair_abandon_lock:
            return self._check_repair_abandonment()

    def _check_repair_abandonment(self):
        from polymaker.rdp_sm_abandon import REASON, eligible

        if self.cfg.rdp_sm.repair.policy == "opposite_ba_v1":
            return False  # Price-based abandonment belongs to the price policy only.
        if self.repair_abandonment is not None:
            return True
        ep, cfg = self.repair_episode, self.cfg.rdp_sm.repair.abandon_high_entry
        elapsed = self.official_time+self.monotonic()-self.time_anchor-int(self.slug.rsplit("-", 1)[1])
        if (ep is None or self.cooldown.active or not cfg.enabled
                or elapsed < cfg.min_elapsed_sec or elapsed >= TRADING_END_S):
            return False
        # Reverse repair legs keep the original NORMAL window through round_id.
        opening = self.cooldown.round_id
        attempts = [a for a in self.attempts if a.quote.window_id == opening
                    and a.outcome not in {"REJECTED", "ABORTED"}]
        if not attempts or any(a.order_id not in self.proofs or a.order_id not in self.terminal
                               for a in attempts):
            return False  # NORMAL has not finished its existing order/fill barrier.
        ids = {a.order_id for a in attempts}
        fills = [f for f in self.economic_fills() if f.maker_order_id in ids]
        sides = {a.quote.outcome for a in attempts}
        side = next(iter(sides)) if len(sides) == 1 else ep.get("normal_entry_side", ep.get("side"))
        token = self.meta.yes.token_id if side == "YES" else self.meta.no.token_id if side == "NO" else None
        fills = [f for f in fills if f.token_id == token]
        qty = sum((Decimal(str(f.size)) for f in fills), Decimal(0))
        cost = sum((fill_cost(f) for f in fills), Decimal(0))
        vwap = cost/qty if qty else None
        if not eligible(cfg, elapsed, vwap):
            return False
        self._cooldown_pair_snapshot()  # Validate actual fill facts before latching.
        self.repair_abandonment = {"event": "TRIGGERED", "state": "HOLD_TO_SETTLEMENT",
            "reason": REASON, "round_id": opening, "entry_side": side,
            "entry_quantity": str(qty), "entry_cost": str(cost), "entry_vwap": str(vwap),
            "elapsed_s": elapsed, "triggered_monotonic": self.monotonic(),
            "parameters": cfg.model_dump(mode="json"),
            "action": "STOP_MAKER_CANCEL_AND_RECONCILE", "inventory_retained": True}
        self.record("sm_repair_abandoned", dict(self.repair_abandonment))
        return True

    def finalize_repair_abandonment(self):
        if self.repair_abandonment is None:
            return
        if not self.safe_to_continue or self.inventory_mismatch is not None:
            return
        from polymaker.rdp_sm_abandon import fifo_attribution

        y, n = self.economic_inventory()
        result = {**self.repair_abandonment, "event": "RECONCILED", "close_barrier": "SAFE_TO_CONTINUE",
            "yes_inventory": str(y), "no_inventory": str(n), "residual_shares": str(abs(y-n)),
            **fifo_attribution(self._cooldown_pair_snapshot(), self.cooldown.baseline,
                               self.meta.yes.token_id)}
        if result != self.repair_abandonment:
            self.repair_abandonment = result
            self.record("sm_repair_abandoned", dict(result))

    def repair_inventory_complete(self):
        y, n = self.economic_inventory()
        return not repair_required(y, n)

    def _complete_repair_episode(self):
        _require(self.safe_to_continue and self.inventory_mismatch is None,
                 "COOLDOWN_CLOSE_BARRIER_REQUIRED")
        y, n = self.economic_inventory()
        _require(self.repair_inventory_complete(), "REPAIR_RESIDUAL_ABOVE_EXIT_THRESHOLD")
        result = self.cooldown.complete(self._cooldown_pair_snapshot(),
                                        self.meta.yes.token_id, self.monotonic())
        ep = self.repair_episode
        opposite_ba = self.cfg.rdp_sm.repair.policy == "opposite_ba_v1"
        if opposite_ba and self.reads.feed is not None:
            # NORMAL must wait for a NEW entry BA after this round, rather than
            # reuse an opportunity observed while the position was still owned.
            with self.reads.feed.lock:
                for detector in self.reads.feed.ba_detectors():
                    detector.invalidate("BA_EXIT_ROUND_COMPLETED")
        self.record("sm_repair_episode", {"event": "COMPLETE_WITH_RESIDUAL_TOLERANCE",
            "opening_window": ep["opening_window"], "direction_leg": ep.get("direction_leg", 0),
            "actual_delta": str(y-n), "residual_shares": str(abs(y-n)),
            "threshold_shares": str(MIN_REPAIR_EXPOSURE), "inventory_flat": y == n,
            "residual_retained_in_ledger": True})
        self.record("sm_cooldown", result)
        self.repair_episode = None

    def resume_cooldown(self, snapshot, health):
        if self.check_repair_abandonment():
            return False
        if not self.cooldown.active:
            return True
        now = self.monotonic()
        elapsed = self.official_time+now-self.time_anchor-int(self.slug.rsplit("-", 1)[1])
        if now < self.cooldown.deadline:
            return False
        checks = (
            ("CLOSE_BARRIER_REQUIRED", self.safe_to_continue),
            ("ACCOUNT_INVENTORY_NOT_EXPLAINED_BY_SM", self.inventory_mismatch is None),
            ("NORMAL_SUBMISSION_CUTOFF", trading_start_sec(self.cfg) <= elapsed < self.cfg.rdp_sm.normal_cutoff_sec),
            ("MARKET_DATA_NOT_READY", snapshot.valid),
            ("NETWORK_ADMISSION_BLOCKED", health.allowed),
            ("ACCOUNT_EVIDENCE_STALE", now-self.time_anchor <= self.cfg.risk.ws_stale_halt_s),
        )
        reason = next((reason for reason, valid in checks if not valid), None)
        if reason is not None:
            if reason != self.cooldown.resume_block_reason:
                self.record("sm_cooldown", {"state": "COOLDOWN", "event": "EXPIRED_WAITING_FOR_ADMISSION",
                    "reason": reason, "round_id": self.cooldown.last_result["round_id"],
                    "deadline_monotonic": self.cooldown.deadline,
                    "observed_monotonic": now, "elapsed_s": elapsed})
                self.cooldown.resume_block_reason = reason
            return False
        self.record("sm_cooldown", {"state": "NORMAL", "event": "EXPIRED",
            "round_id": self.cooldown.last_result["round_id"],
            "deadline_monotonic": self.cooldown.deadline, "resumed_monotonic": now})
        self.cooldown.deadline = None
        self.cooldown.resume_block_reason = None
        return True

    def update_repair_episode(self):
        if self.cfg.rdp_sm.normal_entry_mode == "m1_v1":
            return
        if self.cfg.rdp_sm.normal_entry_mode in {"ba_v1", "ca_v1"}:
            self.record_ba_fills()
        # BA creates REPAIR ownership only for actual inventory. Preserve the
        # pre-POST FIFO baseline, including fills arriving during cancellation.
        if self.ba_opening is not None and self.repair_episode is None:
            opening = self.ba_opening
            if any(f.maker_order_id in opening["ids"] for f in self.economic_fills()):
                self.begin_repair_episode(opening["window_id"], opening["quotes"],
                                          baseline=opening["baseline"])
                self.ba_opening = None
            elif self.safe_to_continue:
                self.ba_opening = None
        if self.check_repair_abandonment():
            return
        ep = self.repair_episode
        if ep is None:
            return
        if (self.safe_to_continue and self.inventory_mismatch is None
                and self.repair_inventory_complete()):
            self._complete_repair_episode()
            return
        if ep.get("dual_normal") and "source_lots" not in ep:
            # Both original BUYs can still fill until the full NORMAL barrier.
            # Neither quote order nor the OBI signal establishes inventory side.
            if not self.safe_to_continue:
                return
            from polymaker.rdp_sm_sl_ledger import fifo_residual

            y, n = self.economic_inventory()
            lots = fifo_residual(self.economic_fills(), self.fill_observed_at, self.meta.yes.token_id)
            qty = sum((Decimal(str(f.size)) for f in lots), Decimal(0))
            if not lots or qty != abs(y-n):
                raise ValueError("NORMAL_RESIDUAL_LOTS_UNPROVEN")
            times = [self.fill_observed_at[(f.trade_id, f.maker_order_id)] for f in lots]
            first = min(times, key=lambda t: t.get("fill_monotonic",
                self.time_anchor+t["elapsed"]+int(self.slug.rsplit("-", 1)[1])-self.official_time))
            side = "YES" if y > n else "NO"
            ca_tp_owned = ep.get("ca_tp_direction_owner") == ep["opening_window"]
            normal_side = ep["normal_entry_side"] if ca_tp_owned else side
            reverse = ca_tp_owned and normal_side != side
            chain = ep["opening_window"]
            ep.update(side=side, source_lots=lots,
                      normal_entry_side=normal_side,
                      original_qty=str(qty), original_cost=str(sum(
                          (fill_cost(f) for f in lots), Decimal(0))),
                      first_fill_elapsed=first["elapsed"], first_fill_time_source=first["source"],
                      direction_leg=1 if reverse else 0, chain_id=chain,
                      chain_windows=list(ep.get("chain_windows", ep["windows"])), windows=[])
            if "fill_monotonic" in first:
                ep["first_fill_monotonic"] = first["fill_monotonic"]
                ep["opening_monotonic"] = first["fill_monotonic"]
            self.record("sm_repair_episode", {"event": "NORMAL_RESIDUAL_ESTABLISHED",
                **{k: v for k, v in ep.items() if k != "source_lots"},
                "cost_basis": "FIFO", "source_lots": [asdict(f) for f in lots]})
            if reverse:
                ep["opening_window"] = f"{chain}:direction:1"
                self.record("sm_repair_reverse", {"chain_id": chain, "direction_leg": 1,
                    "mode": "WAIT_OPPOSITE_BA", "side": side, "original_qty": str(qty),
                    "cost_basis": "FIFO", "first_fill_elapsed": first["elapsed"],
                    "original_cost": ep["original_cost"], "source_lots": [asdict(f) for f in lots]})
            return
        if "source_lots" in ep:
            # FIFO reverse legs already carry actual acquisition quantity/time.
            # Repair fills must not reset their opening inputs or HD policy.
            return
        ids = {a.order_id for a in self.attempts if a.order_id and
               a.quote.window_id == ep["opening_window"]}
        qty = sum((Decimal(str(f.size)) for f in self.economic_fills()
                   if f.maker_order_id in ids), Decimal(0))
        original = [f for f in self.economic_fills() if f.maker_order_id in ids]
        times = [self.fill_observed_at[(f.trade_id, f.maker_order_id)] for f in original
                 if (f.trade_id, f.maker_order_id) in self.fill_observed_at]
        if times:
            first = min(times, key=lambda t: t.get("fill_monotonic",
                self.time_anchor+t["elapsed"]+int(self.slug.rsplit("-", 1)[1])-self.official_time))
            ep["first_fill_elapsed"], ep["first_fill_time_source"] = first["elapsed"], first["source"]
            if "fill_monotonic" in first:
                ep["first_fill_monotonic"] = first["fill_monotonic"]
        ep["original_cost"] = str(sum((fill_cost(f) for f in original), Decimal(0)))
        if ep["original_qty"] != str(qty):
            ep["original_qty"] = str(qty)
            self.record("sm_repair_episode", {"event": "ACTUAL_OPENING_QUANTITY", **ep})

    def record_ba_fills(self):
        for oid, context in self.ba_orders.items():
            fills = [f for f in self.economic_fills() if f.maker_order_id == oid]
            qty = sum((Decimal(str(f.size)) for f in fills), Decimal(0))
            stamps = [self.fill_observed_at[(f.trade_id, oid)] for f in fills
                      if (f.trade_id, oid) in self.fill_observed_at]
            first = min(stamps, key=lambda s: s["fill_monotonic"]) if stamps else None
            evidence = {**context, "order_id": oid, "confirmed_fill_qty": str(qty),
                "first_fill_time": first["fill_monotonic"] if first else None,
                "fill_time_source": first["source"] if first else None,
                "signal_age_at_fill": first["fill_monotonic"]-context["signal_start_mono"] if first else None}
            if evidence != self.ba_logged.get(oid):
                self.record("sm_ca_normal_execution" if context["normal_entry_mode"] == "ca_v1"
                            else "sm_ba_normal_execution", evidence)
                self.ba_logged[oid] = evidence

    def committed(self) -> Decimal:
        """One exposure per placement; terminal proof releases only unfilled size."""
        total = Decimal(0)
        for attempt in self.attempts:
            if attempt.outcome in {"REJECTED", "ABORTED"}:
                continue
            oid = attempt.order_id
            proof = self.proofs.get(oid)
            fills = [f for f in self.economic_fills() if f.maker_order_id == oid] if oid else []
            if proof is not None and oid in self.terminal:
                # Only verified confirmed fills remain after a complete barrier.
                total += sum(
                    (fill_cost(ev) for ev in fills), Decimal(0)
                )
            else:
                # Full limit-price exposure covers fills + remaining + uncertainty
                # once, even if cancellation returned ACK or a WS fill was seen.
                matched = getattr(self, "sl_terminal_quantities", {}).get(oid)
                total += (attempt.maximum if matched is None or attempt.entry_execution == 'taker' else
                          matched*Decimal(str(attempt.quote.price)) + Decimal(attempt.fee_reserve_usdc))
        return max(Decimal(0), total - sum(self.merged_cost.values(), Decimal(0)))

    async def _read_books(self, meta):
        if self.cfg.rdp_sm.normal_entry_mode in {"obi_v1", "ba_v1", "ca_v1", "m1_v1"}:
            feed = await self.reads.ensure_feed(meta)
            snapshot = feed.snapshot()
            _require(snapshot.valid, snapshot.reason)
            return snapshot.books()
        books = {}
        for token in (meta.yes.token_id, meta.no.token_id):
            raw_book, tick = await self.reads.book(token)
            books[token] = _book(raw_book, tick, token, meta)
        now = self.official_time + self.monotonic() - self.time_anchor
        _require(all(-2 <= now - b["timestamp"] <= self.cfg.risk.ws_stale_halt_s
                     for b in books.values()), "EXECUTION_DATA_STALE")
        return books

    async def _recover_pagination(self):
        if self.shared_reads is not None:
            # The cache scope already requires the latest complete collections;
            # partial observations are retained by its single ingest path.
            return
        client = self.gateway._client
        if client.pagination_failure is not None:
            try:
                await self.gateway._io(client.retry_incomplete_reads)
            except Exception as exc:
                if retry_read(exc):
                    raise _Issue(SessionStatus.BLOCKED, reason_code(exc)) from exc
                raise

    async def _snapshot(self, *, include_books=True, allow_repair_inventory_lag=False):
        allow_repair_inventory_lag = (allow_repair_inventory_lag
            and self.cfg.rdp_sm.repair.inventory_lag.enabled)
        if self.shared_reads is not None and self.shared_reads.active is None:
            async with self.shared_reads.scope("account", account=True):
                if self.attempts and not self.admission_ready:
                    _require(await self._verify_close(), self.reason or "CLOSE_EVIDENCE_INVALIDATED")
                result = await self._snapshot(include_books=include_books,
                    allow_repair_inventory_lag=allow_repair_inventory_lag)
                clock = await self.shared_reads.source("time")
                self.official_time_read_duration_s = (
                    clock.completed_monotonic - clock.started_monotonic)
                return result
        began = self.monotonic()
        await self._recover_pagination()
        _require(
            self.gateway._client.pagination_failure is None,
            self.gateway._client.pagination_failure or "ACCOUNT_PAGINATION_UNRESOLVED",
        )
        # Refresh rejected signed IDs without re-reading every closed order.
        # The previous complete barrier remains required; account reads below
        # still check new trades and open orders against its terminal proofs.
        if self.canceled_submission_proofs:
            await self._refresh_canceled_submissions()
        _require(
            all(
                getattr(self.cfg.wallet, key).rstrip("/") == host
                for key, host in OFFICIAL_HOSTS.items()
            )
            and self.cfg.wallet.chain_id == 137,
            "OFFICIAL_ENDPOINT_CONFIGURATION_REQUIRED",
        )
        permitted_unknowns = self.normal_unknown.ids(self.shared_reads.active) if self.shared_reads else set()
        for oid in sorted(self.known - set(self.proofs) - permitted_unknowns):
            _require(
                await reconcile_owned_order(oid, self.gateway, self.processor, retry_delays=())
                is ReconciliationOutcome.COMPLETE,
                "PRE_SUBMIT_FILL_FACTS_UNRESOLVED",
            )
        raw = await self.reads.market(self.slug)
        meta, start = _market(raw, self.slug)
        if self.meta is not None:
            _require(
                meta.condition_id == self.meta.condition_id and meta.tokens == self.meta.tokens,
                "SESSION_MARKET_CHANGED",
            )
        self.meta = meta
        self.gateway._client.session_market_id = meta.condition_id
        tokens = {meta.yes.token_id, meta.no.token_id}
        # Account admission precedes the final book reads. This reference tick
        # is only for _account's descriptive lower bound, never a quote tick.
        market = {side: {"tick": meta.tick_size} for side in ("yes", "no")}
        capital_revision = self.revision
        account = await _account(self.reads, meta, market)
        client = self.gateway._client
        self._check_canceled_submission_observations()
        if self.known:
            # The close proof is reusable, but newly observed trade facts are
            # not. Production account policy already reads complete trade pages.
            if self.reads.account_policy is None:
                await self.gateway.get_trades(market=meta.condition_id)
            if self._official_fills(client.last_trades, self.known) != self._local_fills(self.known):
                self.invalidate("PREVIOUS_WINDOW_TRADE_EVIDENCE_CHANGED")
                _require(False, "PREVIOUS_WINDOW_TRADE_EVIDENCE_CHANGED")
        first_open_rows = list(client.last_open_rows)
        _execution_identity(self.reads)
        # Raw SDK orders already pass F01 identity validation; read full IDs too.
        orders = await self.gateway.open_orders()
        self._check_canceled_submission_observations()
        for order in orders:
            if order.order_id in self.terminal:
                self.terminal_conflict(order.order_id, source="OPEN_ORDERS")
                self.record("sm_terminal_open_order_contradiction", asdict(order))
                _require(False, self.fatal)
            if order.order_id in self.known:
                owned = self.processor.owned_record(order.order_id)
                _require(
                    order.token_id == owned["token_id"]
                    and order.side is Side.BUY
                    and within_tolerance(order.price, owned["submitted_price"], RDP_TOLERANCE)
                    and not exceeds_tolerance(order.size, owned["submitted_qty"], RDP_TOLERANCE),
                    "OPEN_ORDER_ECONOMICS_CONTRADICT_OWNERSHIP",
                )
        _require(
            all(o.order_id in self.known for o in orders if o.token_id in tokens),
            "EXTERNAL_TARGET_ORDER",
        )
        # Balance/allowance are collateral readings, not evidence that a local
        # ACK has already appeared in the open-order replica. Merge both lists
        # and local unresolved placements by identity, reserving each once.
        reservations: dict[str, Decimal] = {}
        identities = {}
        for historical, rows in (
            (True, list(client.observed_open_rows.values())),
            (False, first_open_rows),
            (False, client.last_open_rows),
        ):
            seen = set()
            for row in rows:
                oid = next(
                    row[k].strip()
                    for k in ("id", "orderID", "order_id")
                    if isinstance(row.get(k), str) and row[k].strip()
                )
                if historical and oid in self.known:
                    continue  # Own attempts already have fresh terminal/fill proofs.
                _require(oid not in seen, "OPEN_ORDER_IDENTITY_CONFLICT")
                seen.add(oid)
                price = Decimal(str(strict_number(row["price"])))
                size = Decimal(str(strict_number(row["original_size"])))
                side = Side(row["side"].upper())
                identity = (_token_id(row["asset_id"]), side, price, size)
                _require(
                    oid not in identities or identities[oid] == identity
                    or (oid in self.known and _same_fill_economics(identities[oid], identity)),
                    "OPEN_ORDER_SNAPSHOTS_CONFLICT",
                )
                identities[oid] = identity
                _require(0 < price < 1 and size >= 0, "OPEN_ORDER_COMMITMENT_UNKNOWN")
                _require(identity[0] not in tokens or oid in self.known, "EXTERNAL_TARGET_ORDER")
                if oid in self.known:
                    owned = self.processor.owned_record(oid)
                    _require(
                        _same_fill_economics(identity, (
                            owned["token_id"],
                            Side.BUY,
                            Decimal(str(owned["submitted_price"])),
                            Decimal(str(owned["submitted_qty"])),
                        )),
                        "OPEN_ORDER_ECONOMICS_CONTRADICT_OWNERSHIP",
                    )
                if oid in self.terminal:
                    self.terminal_conflict(oid, source="OPEN_ORDER_SNAPSHOT")
                    _require(False, self.fatal)
                policy = self.reads.account_policy
                if (policy is not None and policy.use_starting_balance
                        and oid in policy.starting_order_ids and oid not in self.known):
                    continue  # The operator manages orders present at session startup.
                if side is Side.BUY:
                    reservations[oid] = max(reservations.get(oid, Decimal(0)), price * size)
        listed = {
            next(
                row[k].strip()
                for k in ("id", "orderID", "order_id")
                if isinstance(row.get(k), str) and row[k].strip()
            )
            for row in first_open_rows + client.last_open_rows
        }
        for oid in sorted(reservations.keys() - self.known - listed):
            if await self._external_no_fill_terminal(oid, identities[oid]):
                reservations[oid] = Decimal(0)
        for attempt in self.attempts:
            if attempt.outcome in {"REJECTED", "ABORTED"}:
                continue
            oid = attempt.order_id
            if oid in self.proofs and oid in self.terminal:
                continue  # Reconciled confirmed inventory is already spent collateral.
            key = oid or f"unknown:{attempt.attempt_id}"
            reservations[key] = max(reservations.get(key, Decimal(0)), attempt.maximum)
        reserved = sum(reservations.values(), Decimal(0))
        account["execution_reserved_usd"] = float(reserved)
        account["basic_spendable_budget_usd"] = float(
            max(
                Decimal(0),
                min(
                    self.capital_limit,
                    Decimal(str(account["official_balance_usd"])) - reserved,
                    Decimal(str(account["applicable_allowance_usd"])) - reserved,
                ),
            )
        )
        inventory = {}
        consistent = True
        inventory_tolerance = (Decimal(".01") if self.cfg.rdp_sm.normal_entry_mode in {"obi_v1", "ba_v1", "ca_v1", "m1_v1"}
                               else INVENTORY_TOLERANCE_SHARES)
        for token, name in ((meta.yes.token_id, "yes"), (meta.no.token_id, "no")):
            owned_qty = sum(
                (Decimal(str(ev.size))
                for ev in self.economic_fills()
                if ev.token_id == token), Decimal(0)
            ) - self.merged_pairs
            actual = Decimal(str(account[f"{name}_inventory"]))
            # A closed, proven session can retain dust omitted by the Data API.
            # Keep its exact ledger quantity/cost; only waive this missing row.
            omitted_known_dust = (
                actual == 0 and 0 < owned_qty < DUST_SHARES and self.safe_to_continue
            )
            consistent = consistent and (
                abs(actual - owned_qty) < inventory_tolerance or omitted_known_dust
            )
            inventory[name.upper()] = {"expected": float(owned_qty), "actual": float(actual),
                                       "difference": float(actual - owned_qty)}
            if omitted_known_dust:
                inventory[name.upper()]["data_api_omitted_known_dust"] = True
        context = {"source": "DATA_API", "tolerance_shares": float(inventory_tolerance),
                   "tolerance_exclusive": True,
                   "inventory": inventory}
        self.inventory_observation = context
        if not consistent:
            self.inventory_mismatch = context
            if not allow_repair_inventory_lag:
                raise InventoryMismatch(context)
        else:
            self.inventory_mismatch = None
        _require(client._ClobClient__resolve_version() == 2, "M2_ORDER_VERSION_UNSUPPORTED")
        clock_read_started = self.monotonic()
        now = await self.reads.server_time()
        self.official_time, self.time_anchor = now, self.monotonic()
        self.official_time_read_duration_s = self.time_anchor - clock_read_started
        # No order reconciliation, account or market requests after these books.
        books = await self._read_books(meta) if include_books else {}
        if books:
            account["minimum_pair_commitment_usd"] = meta.min_order_size * sum(
                b["tick"] for b in books.values())
        _require(
            client.pagination_failure is None,
            client.pagination_failure or "ACCOUNT_PAGINATION_UNRESOLVED",
        )
        _require(self.revision == capital_revision, "CAPITAL_EVIDENCE_CHANGED_DURING_READ")
        account["capital_evidence_revision"] = capital_revision
        age = self.cfg.risk.ws_stale_halt_s
        current_time = now + self.monotonic() - self.time_anchor
        _require(
            self.monotonic() - began <= age
            and all(-2 <= current_time - b["timestamp"] <= age for b in books.values()),
            "EXECUTION_DATA_STALE",
        )
        if consistent:
            self.inventory_lag.matched(account)
        elif not self.inventory_lag.consider(account):
            raise InventoryMismatch(context)
        return meta, start, books, account

    async def _external_no_fill_terminal(self, oid: str, identity) -> bool:
        """Fresh no-fill cancellation proof only; history itself is never erased.

        Recheck on every snapshot. LIVE, failed/ambiguous reads, any fill or
        pending trade restores the full envelope. No external order is owned,
        cancelled or projected into SM inventory by this read-only check.
        """

        observed = self.gateway._client.observed_open_rows[oid]
        if self.gateway._client.pagination_failure is not None:
            return False
        market = observed.get("market")
        if observed.get("_sm_fill_observed") or observed.get("_sm_external_event"):
            return False  # A later zero-fill replica cannot erase a seen fill.

        def no_fill(row):
            ev = self.processor.normalize_owned_order(row)
            if ev is not None and (ev.size_matched > 0 or ev.associate_trades):
                observed["_sm_fill_observed"] = True
            return (
                ev is not None
                and (ev.token_id, ev.side, Decimal(str(ev.price)), Decimal(str(ev.original_size)))
                == identity
                and ev.status in {"CANCELED", "CANCELLED"}
                and ev.size_matched == 0
                and isinstance(row.get("associate_trades"), list)
                and not row["associate_trades"]
                and isinstance(market, str)
                and bool(market)
                and ev.market_id == market
            )

        try:
            row = await self.gateway.get_order(oid)
            if not no_fill(row):
                return False
            trades = await self.gateway.get_trades(market=normalize_order(row).market_id)
            if any(leg.order_id == oid for raw in trades for leg in parse_trade(raw).maker_legs):
                observed["_sm_fill_observed"] = True
                return False
            final = await self.gateway.get_order(oid)
            if (
                not no_fill(final)
                or normalize_order(final).market_id != normalize_order(row).market_id
            ):
                return False
            self.record("sm_external_no_fill_terminal", {"order": final, "trades": trades})
            return True
        except Exception:
            return False  # Absence, errors and incomplete facts cannot release collateral.

    def _validate(self, quote: WindowQuote, window_id: str, books, meta, *, allow_taker=False,
                  normal_entry=False):
        _require(
            quote.market_id == meta.condition_id and quote.window_id == window_id,
            "QUOTE_SESSION_IDENTITY_INVALID",
        )
        token = _token_id(quote.token_id)
        expected = {"YES": meta.yes.token_id, "NO": meta.no.token_id}
        _require(
            quote.outcome in expected
            and expected[quote.outcome] == token
            and quote.side is Side.BUY,
            "QUOTE_BUY_TOKEN_INVALID",
        )
        _require(
            type(quote.price) in (int, float)
            and type(quote.size) in (int, float)
            and math.isfinite(quote.price)
            and math.isfinite(quote.size),
            "QUOTE_NUMBER_INVALID",
        )
        price, size, tick = map(
            lambda x: Decimal(str(x)), (quote.price, quote.size, books[token]["tick"])
        )
        _require(tick <= price <= 1 - tick and price % tick == 0, "QUOTE_PRICE_TICK_INVALID")
        _require(
            size >= max(MIN_MAKER_BUY_SHARES, Decimal(str(meta.min_order_size)))
            and (self.cfg.rdp_sm.normal_entry_mode == "m1_v1"
                 or size <= self.cfg.rdp_sm.max_order_size)
            and size % Decimal(".01") == 0,
            "QUOTE_SIZE_INVALID",
        )
        if allow_taker:
            _require(self.cfg.rdp_sm.normal_entry_mode == "ca_v1"
                     and self.cfg.rdp_sm.ca_entry_execution == "taker"
                     and re.fullmatch(r".+:w\d+", window_id) is not None
                     and Decimal(str(books[token]["best_ask"])) <= price <= self.cfg.rdp_sm.ca_taker.max_price
                     and (not self.cfg.rdp_sm.ca_taker.entry_price_ceiling_enabled
                          or price < self.cfg.rdp_sm.ca_taker.entry_price_ceiling),
                     "CA_TAKER_PRICE_INVALID")
        else:
            _require(price < Decimal(str(books[token]["best_ask"])), "QUOTE_WOULD_TAKE")
        if self.cfg.rdp_sm.normal_entry_mode == "m1_v1":
            _require(price*size <= Decimal(5), "M1_ORDER_BUDGET_EXCEEDED")
        if (normal_entry and self.cfg.rdp_sm.sizing_mode == "fixed_risk"
                and re.fullmatch(r".+:w\d+", window_id) is not None):
            _require(price*size <= self.cfg.rdp_sm.risk_per_trade_usdc,
                     "CA_ENTRY_RISK_EXCEEDED")

    async def _verify_order(self, oid: str) -> None:
        if self.cfg.rdp_sm.normal_entry_mode in {"obi_v1", "ba_v1", "ca_v1", "m1_v1"}:
            row = await self.gateway.get_order(oid)
            ev = self.processor.normalize_owned_order(row)
            owned = self.processor.owned_record(oid)
            _require(
                ev is not None
                and owned is not None
                and ev.market_id == self.meta.condition_id
                and ev.token_id == owned["token_id"]
                and ev.side.value == owned["side"]
                and Decimal(str(ev.original_size)) == Decimal(str(owned["submitted_qty"]))
                and Decimal(str(ev.price)) == Decimal(str(owned["submitted_price"]))
                and (
                    "maker_address" not in row
                    or row["maker_address"].lower() == self.gateway.funder.lower()
                ),
                "SIGNED_ORDER_ECONOMICS_MISMATCH",
            )
        outcome = await reconcile_owned_order(oid, self.gateway, self.processor, retry_delays=())
        trades = list(getattr(self.gateway._client, "last_trades", []))
        row = await self.gateway.get_order(oid)
        with suppress(Exception):
            self.record(
                "sm_official_reconciliation",
                {"order_id": oid, "order": row, "trades": trades, "filltruth": outcome.value},
            )
        ev = self.processor.normalize_owned_order(row)
        owned = self.processor.owned_record(oid)
        identity_tolerance = (
            Decimal(0) if self.cfg.rdp_sm.normal_entry_mode in {"obi_v1", "ba_v1", "ca_v1", "m1_v1"} else RDP_TOLERANCE
        )
        _require(
            ev is not None
            and ev.token_id == owned["token_id"]
            and ev.side.value == owned["side"]
            and ev.market_id == self.meta.condition_id
            and within_tolerance(ev.original_size, owned["submitted_qty"], identity_tolerance)
            and within_tolerance(ev.price, owned["submitted_price"], identity_tolerance),
            "FINAL_ORDER_IDENTITY_UNKNOWN",
        )
        UserEventProcessor.on_order(self.processor, ev, self.meta.condition_id)
        if oid in self.terminal and not _terminal(ev, self.matched_remaining_tolerance_shares,
                taker_buy=self.processor.taker_spec(oid) is not None,
                fak_buy=self.processor.is_fak_buy(oid)):
            self.terminal_conflict(oid, source="ORDER_DETAIL")
        _require(_terminal(ev, self.matched_remaining_tolerance_shares,
                          taker_buy=self.processor.taker_spec(oid) is not None,
                          fak_buy=self.processor.is_fak_buy(oid)), "ORDER_NOT_TERMINAL")
        if self.processor.is_fak_buy(oid) and ev.size_matched < ev.original_size:
            # MATCHED on native FAK proves the unused part was killed, not
            # fully filled. Still reject any contradictory live-order page.
            _require(oid not in {o.order_id for o in await self.gateway.open_orders()},
                     'TERMINAL_LIVE_CONTRADICTION')
        official = self._official_fills(trades, {oid})
        if self.cfg.rdp_sm.normal_entry_mode in {"obi_v1", "ba_v1", "ca_v1", "m1_v1"} and (
            any(v[0] is not TradeState.CONFIRMED for v in official.values())
            or any(k[1] == oid for k in self.position_excluded)
        ) and await self._reconcile_actual_positions():
            self.terminal[oid] = ev
            self.proofs[oid] = {"order": row, "trades": trades, "outcome": "POSITION_RECONCILED"}
            return
        _require(outcome is ReconciliationOutcome.COMPLETE, "FILLTRUTH_NOT_COMPLETE")
        local = self._local_fills({oid})
        _require(
            all(
                key in official
                and _same_fill_economics(official[key][1:],
                                         (leg.asset_id, leg.side, leg.price, leg.matched_amount))
                for key, (_, leg) in self.gateway._client.observed_trade_legs.items()
                if key[1] == oid
            ),
            "OBSERVED_PAGE_FILL_NOT_RECONCILED",
        )
        _require(set(official) == set(local)
                 and all(_same_fill_economics(official[k], local[k]) for k in official),
                 "TRADE_CONFIRMATION_UNRESOLVED")
        if not all(v[0] is TradeState.CONFIRMED for v in official.values()):
            # Only complete, consistent economics on verified terminal orders
            # qualify for waiting. Unknown placement/terminal/fill facts do not.
            if (all(v[0] in {TradeState.MATCHED, TradeState.MINED, TradeState.CONFIRMED}
                    for v in official.values())
                    and "associate_trades" in row
                    and set(ev.associate_trades) == {key[0] for key in official}
                    and within_tolerance(ev.size_matched, self.processor.tracked_trade_qty(oid), RDP_TOLERANCE)
                    and not self.processor.has_unresolved_fill_key(oid)
                    and oid not in self.processor.quarantined_orders):
                self.pending_confirmations.add(oid)
            _require(False, "TRADE_CONFIRMATION_UNRESOLVED")
        _require(
            not self.processor.has_unresolved_fill_key(oid)
            and oid not in self.processor.quarantined_orders
            and all(
                key in self.processor.applied_fill_keys
                for key in self.processor.fill_records
                if key[1] == oid
            ),
            "TRACKER_UNRESOLVED",
        )
        _require(
            "associate_trades" in row
            and set(ev.associate_trades) == {key[0] for key in official}
            and within_tolerance(ev.size_matched, self.processor.tracked_trade_qty(oid), RDP_TOLERANCE),
            "FINAL_FILL_SET_NOT_PROVEN",
        )
        self.terminal[oid] = ev
        self.proofs[oid] = {"order": row, "trades": trades, "outcome": outcome.value}

    def _official_fills(self, trades, ids):
        official = {}
        taker_fills = {}
        for raw_trade in trades:
            parsed = parse_trade(raw_trade)
            for fill in self.processor.events_from_trade(parsed):
                if fill.maker_order_id in ids:
                    _token_id(fill.token_id)
                    _require(
                        parsed.market == self.meta.condition_id, "OFFICIAL_TRADE_MARKET_UNKNOWN"
                    )
                    key = (parsed.trade_id, fill.maker_order_id)
                    value = (parsed.status, fill.token_id, fill.our_side, fill.price, fill.size)
                    _require(
                        key not in official or _same_fill_economics(official[key], value), "OFFICIAL_TRADE_CONFLICT"
                    )
                    official[key] = value
                    if fill.liquidity_role == 'TAKER':
                        taker_fills[key] = fill
        from polymaker.rdp_sm_ca_taker import validate_buy_fills

        for oid in {k[1] for k in taker_fills}:
            validate_buy_fills([f for k, f in taker_fills.items() if k[1] == oid],
                               self.processor.taker_spec(oid))
        return official

    def _local_fills(self, ids):
        result = {
            (f.trade_id, f.maker_order_id): (f.status, f.token_id, f.our_side, f.price, f.size)
            for f in self.processor.economic_fills_for_orders(ids)
        }
        for key in self.position_excluded | self.position_reconciled.keys():
            f = self.processor.fill_records.get(key)
            if key[1] in ids and f is not None:
                result[key] = (f.status, f.token_id, f.our_side, f.price, f.size)
        return result

    async def _reconcile_actual_positions(self):
        """Positive position + exact owned economics + collateral evidence.

        Never timeout-promote MATCHED or turn a missing/failed read into zero.
        Only native FAILED legs may be excluded by a zero-position proof.
        """
        if self.starting_collateral is None:
            return False
        try:
            # Cache account reads must also postdate writes; an order-only
            # scope cannot supply the position fallback.
            if self.shared_reads is not None and self.shared_reads.active is None:
                async with self.shared_reads.scope("repair_position_proof", account=True):
                    return await self._reconcile_actual_positions()
            trades = await self.gateway.get_trades(market=self.meta.condition_id)
            all_legs = self._official_fills(trades, self.known)
            rows = {}
            for oid in sorted(self.known):
                row = await self.gateway.get_order(oid)
                ev = self.processor.normalize_owned_order(row)
                owned = self.processor.owned_record(oid)
                if (ev is None or owned is None or not _terminal(ev, self.matched_remaining_tolerance_shares,
                        taker_buy=self.processor.taker_spec(oid) is not None,
                        fak_buy=self.processor.is_fak_buy(oid))
                    or self.processor.has_unresolved_fill_key(oid)
                    or ev.token_id != owned["token_id"] or ev.side is not Side.BUY
                    or ev.market_id != self.meta.condition_id
                    or Decimal(str(ev.original_size)) != Decimal(str(owned["submitted_qty"]))
                    or Decimal(str(ev.price)) != Decimal(str(owned["submitted_price"]))):
                    return False
                legs = {k: v for k, v in all_legs.items() if k[1] == oid}
                if ("associate_trades" not in row or set(ev.associate_trades) != {k[0] for k in legs}
                    or abs(sum((Decimal(str(v[4])) for v in legs.values()), Decimal(0))-
                           Decimal(str(ev.size_matched))) > Decimal(".000001")):
                    return False
                rows[oid] = row
            targets = {self.meta.yes.token_id, self.meta.no.token_id}
            if any(o.token_id in targets for o in await self.gateway.open_orders()):
                return False
            before = await self.gateway.positions()
            capital = await self.gateway.balance_allowance()
            after = await self.gateway.positions()
            if before != after or not isinstance(before, dict) or not isinstance(capital, dict):
                return False
            balance = Decimal(str(capital["balance"]))/Decimal(1000000)
            if not balance.is_finite() or balance < 0:
                return False
            expected = {t: Decimal(str(before.get(t, (0, 0))[0]))+self.merged_pairs
                        for t in (self.meta.yes.token_id, self.meta.no.token_id)}
            if any(not q.is_finite() or q < 0 for q in expected.values()):
                return False
            fixed, optional = [], []
            actual_events = {(f.trade_id, f.maker_order_id): f
                             for raw in trades for f in self.processor.events_from_trade(parse_trade(raw))}
            for key, (status, token, side, price, qty) in all_legs.items():
                owned = self.processor.owned_record(key[1])
                if (token != owned["token_id"] or side is not Side.BUY
                    or (Decimal(str(price)) > Decimal(str(owned["submitted_price"]))
                        if key[1] in getattr(self.processor, "taker_orders", {}) else
                        Decimal(str(price)) != Decimal(str(owned["submitted_price"])))
                    or not math.isfinite(qty) or qty <= 0):
                    return False
                f = actual_events[key]
                (optional if status is TradeState.FAILED else fixed).append(f)
            if len(optional) > 12:
                return False
            matches = []
            for mask in range(1 << len(optional)):
                selected = fixed+[f for i, f in enumerate(optional) if mask & (1 << i)]
                quantities = {t: sum((Decimal(str(f.size)) for f in selected if f.token_id == t), Decimal(0))
                              for t in expected}
                cost = sum((fill_cost(f) for f in selected), Decimal(0))
                if (all(abs(quantities[t]-expected[t]) <= Decimal(".000001") for t in expected)
                    and abs(self.starting_collateral-cost+self.merged_pairs-balance) <= Decimal(".000001")):
                    matches.append(selected)
            if len(matches) != 1:
                return False
            selected = {(f.trade_id, f.maker_order_id): f for f in matches[0]
                        if f.status is not TradeState.CONFIRMED}
            cost = sum((fill_cost(f) for f in matches[0]), Decimal(0))
            excluded = set(all_legs)-{(f.trade_id, f.maker_order_id) for f in matches[0]}
            changed = selected != self.position_reconciled or excluded != self.position_excluded
            self.position_reconciled, self.position_excluded = selected, excluded
            for key in selected:
                if key in self.fill_observed_at:
                    self.fill_observed_at[key].setdefault("economic_observed_monotonic", self.monotonic())
                    self.fill_observed_at[key].setdefault(
                        "economic_observed_elapsed", self.official_time+self.monotonic()
                        - self.time_anchor-int(self.slug.rsplit("-", 1)[1]))
            if self.shared_reads is not None and self.shared_reads.active is not None:
                self.shared_reads.service.accept_position_proofs(self.shared_reads.active, rows)
            if changed:
                self.record("sm_position_reconciled", {
                    "source": "COMPLETE_DATA_API_POSITIONS_AND_COLLATERAL",
                    "actual": {t: str(q-self.merged_pairs) for t, q in expected.items()},
                    "collateral": str(balance), "cost": str(cost),
                    "included": [asdict(f) for f in selected.values()],
                    "excluded_failed_keys": sorted(excluded), "order_ids": sorted(rows)})
            return True
        except Exception as exc:
            self.record("sm_repair_position_wait", {"reason": type(exc).__name__})
            return False

    async def verify_close(self, *, cancel: bool = False) -> bool:
        """Explicit read/cleanup request; serialize with any active window."""
        async with self.lock:
            if cancel:
                self._cancel_jobs = {}  # An explicit later cleanup may retry cancellation.
            safe = await self._await_owned(self._verify_close(cancel=cancel))
            if self.cfg.rdp_sm.normal_entry_mode in {"obi_v1", "ba_v1", "ca_v1", "m1_v1"}:
                network = self.reads.network
                if safe and network is not None:
                    network.complete_batch(network.snapshot().active_batch, safe=True)
                self.drain_network_events()
                try:
                    await self.journal.flush()
                except Exception:
                    self.fatal = "JOURNAL_WRITE_FAILED"
                    return False
            return safe

    async def _await_owned(self, operation, *, propagate_cancel: bool = True):
        """Drain the same coroutine/SDK reads even when its caller is cancelled.

        Cancelling run_in_executor's awaiter does not stop its SDK thread. Keep
        the operation owned until completion; the independent timer still
        prioritizes known-ID cancellation while we wait.
        """
        task = asyncio.create_task(operation)
        interrupted = False
        while True:
            try:
                result = await asyncio.shield(task)
                break
            except asyncio.CancelledError:
                interrupted = True
                self.fatal = "ABNORMAL_EXIT"
                if task.cancelled():
                    raise RuntimeError("EXECUTION_READ_TASK_CANCELLED") from None
        if interrupted and propagate_cancel:
            raise asyncio.CancelledError()
        return result

    async def _cancel_tp_for_ba_handoff(self, oid, cancel):
        job = self._tp_ba_cancel_jobs.get(oid)
        if job is None:
            job = asyncio.create_task(cancel(), name="ca-tp-ba-cancel")
            self._tp_ba_cancel_jobs[oid] = job

        async def drain():
            return await asyncio.shield(job)

        return await self._await_owned(drain())

    async def _cancel_known(self) -> None:
        """One managed cancellation per known-ID set, shared by timer/closeout.

        The SDK read worker is not cancelled by a timer. Its owner still awaits
        it; this independent known-ID cancellation can run on the existing pool.
        Final reconciliation starts only after that owner regains control.
        """
        scope = frozenset(self.shared_reads.cancel_ids(self.known)
                          if self.shared_reads is not None else self.known)
        for oid in scope & self._tp_ba_cancel_jobs.keys():
            job = self._tp_ba_cancel_jobs[oid]
            began = self.monotonic()

            while True:
                try:
                    ack_at = await asyncio.shield(job)
                    break
                except asyncio.CancelledError:
                    self.fatal = "ABNORMAL_EXIT"
                    if job.cancelled():
                        ack_at = None
                        break
                except Exception:
                    ack_at = None  # Failed/unknown cancellation keeps the original retry path.
                    break
            retry_at = (ack_at + self.cfg.rdp_sm.obi.cancel_retry_sec
                        if ack_at is not None else None)
            if retry_at is not None and self.monotonic() < retry_at:
                scope = scope - {oid}
                self.record("sm_ca_matched_tp_cancel_reused", {
                    "order_id": oid, "ack_monotonic": ack_at,
                    "wait_ms": (self.monotonic()-began)*1000,
                    "retry_after_monotonic": retry_at, "close_proof_required": True})
            else:
                self._tp_ba_cancel_jobs.pop(oid, None)
        if not scope:
            return
        job = self._cancel_jobs.get(scope)
        if job is None:

            async def send():
                began, cancel_ts = self.monotonic(), time.time()
                ack = (
                    asdict(await self.gateway.cancel_detailed(sorted(scope)))
                    if self.cfg.rdp_sm.normal_entry_mode in {"obi_v1", "ba_v1", "ca_v1", "m1_v1"}
                    else await self.gateway.cancel(sorted(scope))
                )
                with suppress(Exception):
                    self.record("sm_cancel_ack", {"ids": sorted(scope), "ack": ack,
                        "request_ts": cancel_ts, "duration_s": self.monotonic() - began})

            job = asyncio.create_task(send())
            self._cancel_jobs[scope] = job
        while True:
            try:
                await asyncio.shield(job)
                if self.cfg.rdp_sm.normal_entry_mode in {"obi_v1", "ba_v1", "ca_v1", "m1_v1"}:
                    self._cancel_jobs.pop(scope, None)
                return
            except asyncio.CancelledError:
                self.fatal = "ABNORMAL_EXIT"
                if job.cancelled():
                    raise RuntimeError("CANCELLATION_TASK_CANCELLED") from None

    async def _verify_close(self, *, cancel: bool = False) -> bool:
        """Fresh official order/trade facts; no old COMPLETE or terminal cache."""
        if self.shared_reads is not None and self.shared_reads.active is None:
            try:
                if cancel:
                    await self._cancel_known()
                # Keep the ordinary detail wait. On timeout, fresh complete
                # collections allow per-ID progress, never a SAFE shortcut:
                # every known ID still needs its original closure proof.
                async with self.shared_reads.scope("close", account=True, partial=True):
                    return await self._verify_close(cancel=False)
            except Exception as exc:
                self.reason = self.fatal or str(exc)
                self.proof_revision = -1
                with suppress(Exception):
                    self.record("sm_close_barrier", {"result": "BLOCKED", "reason": self.reason,
                        "attempts": [asdict(a) for a in self.attempts],
                        "capital_committed": str(self.committed())})
                return False
        self.proof_revision = -1
        self.proofs = {}
        self.pending_confirmations = set()
        try:
            if cancel and self.known:
                await self._cancel_known()
            await self._recover_pagination()
            terminal_rechecks = dict(self.terminal_conflicts)
            if terminal_rechecks:
                # A terminal detail alone cannot explain a still-open order.
                # Cached scopes require all collection reads after the conflict;
                # uncached callers perform this new complete read explicitly.
                opened = await self.gateway.open_orders()
                for order in opened:
                    if order.order_id in self.terminal:
                        self.terminal_conflict(order.order_id, source="CLOSE_OPEN_ORDERS")
                        _require(False, "TERMINAL_LIVE_CONTRADICTION")
            if any(a.outcome in {"UNKNOWN", "INTENT"} for a in self.attempts):
                try:
                    candidates = await self.gateway.open_orders()
                    lookup = {"open_orders": [asdict(order) for order in candidates]}
                except Exception as exc:
                    lookup = {"read_error": type(exc).__name__}
                with suppress(Exception):
                    self.record(
                        "sm_unknown_official_lookup",
                        {
                            **lookup,
                            "resolution": (
                                "PREPARED_SIGNED_IDS; awaiting official per-ID proof"
                                if self.cfg.rdp_sm.normal_entry_mode in {"obi_v1", "ba_v1", "ca_v1", "m1_v1"}
                                else "NO_RELIABLE_REQUEST_TO_ORDER_CORRELATION; risk retained"
                            ),
                        },
                    )
            external_revision = self.external_revision
            errors = []
            candidates = [
                a for a in self.attempts
                if self.cfg.rdp_sm.normal_entry_mode in {"obi_v1", "ba_v1", "ca_v1", "m1_v1"}
                and (
                    a.order_id in self.canceled_submission_proofs
                    or a.outcome in {"UNKNOWN", "REJECTED"}
                    and _canceled_submission_response(a)
                )
            ]
            for attempt in candidates:
                try:
                    await self._verify_canceled_submission(attempt)
                except Exception as exc:
                    errors.append(f"{attempt.order_id}:{exc}")
            for oid in sorted(self.known):
                try:
                    await self._verify_order(oid)
                except Exception as exc:
                    errors.append(f"{oid}:{exc}")
            self._check_canceled_submission_observations()
            _require(not errors, ";".join(errors))
            _require(
                self.external_revision == external_revision, "EVENT_DURING_CLOSE_REQUIRES_RECHECK"
            )
            _require(
                set(self.proofs) == self.known and set(self.terminal) == self.known,
                "CLOSE_EVIDENCE_INVALIDATED",
            )
            # The last complete official trade page set must still explain every
            # earlier order proof; a later read cannot silently introduce a leg.
            _require(
                self._official_fills(getattr(self.gateway._client, "last_trades", []), self.known)
                == self._local_fills(self.known),
                "CROSS_ORDER_TRADE_SET_CHANGED",
            )
            if self.cfg.rdp_sm.normal_entry_mode in {"obi_v1", "ba_v1", "ca_v1", "m1_v1"}:
                for attempt in self.attempts:
                    if (
                        attempt.outcome == "UNKNOWN"
                        and attempt.expected_order_id
                        and attempt.order_id == attempt.expected_order_id
                        and attempt.order_id in self.proofs
                        and attempt.order_id in self.terminal
                    ):
                        attempt.outcome = "VERIFIED"  # official closure, never a synthetic ACK
            _require(
                all(a.outcome in {"ACK", "VERIFIED", "REJECTED", "ABORTED"} for a in self.attempts),
                "UNKNOWN_PLACEMENT_EXPOSURE",
            )
            recovered_terminal = (self.fatal == "TERMINAL_LIVE_CONTRADICTION"
                                  and bool(terminal_rechecks)
                                  and self.terminal_conflicts == terminal_rechecks)
            _require(self.fatal is None or recovered_terminal, self.fatal or "FATAL_EXECUTION_STATE")
            _require(getattr(self.journal, "failure", None) is None, "JOURNAL_WRITE_FAILED")
            _require(not self.merge_pending, "MERGE_PENDING")
            _require(
                self.gateway._client.pagination_failure is None,
                self.gateway._client.pagination_failure or "ACCOUNT_PAGINATION_UNRESOLVED",
            )
            if recovered_terminal:
                self.fatal = None
                self.terminal_conflicts.clear()
                self.record("sm_terminal_recovered", {
                    "order_ids": sorted(terminal_rechecks),
                    "proof_after_monotonic": terminal_rechecks,
                    "source": "FRESH_COMPLETE_ORDER_AND_TRADE_CLOSE_PROOF",
                })
            self.reason = None
            self.proof_revision = self.revision
            self.proof_pagination_revision = self.gateway._client.pagination_revision
            self.record(
                "sm_close_barrier",
                {
                    "result": "SAFE_TO_CONTINUE",
                    "proofs": self.proofs,
                    "confirmed_fills": [asdict(f) for f in self.confirmed()],
                    "economic_fills": [asdict(f) for f in self.economic_fills()],
                    "position_reconciled_keys": sorted(self.position_reconciled),
                },
            )
            return True
        except Exception as exc:
            self.reason = self.fatal or str(exc)
            self.proof_revision = -1
            # Old terminal is diagnostic evidence, never a continuation permit.
            with suppress(Exception):
                self.record(
                    "sm_close_barrier",
                    {
                        "result": "BLOCKED",
                        "reason": self.reason,
                        "attempts": [asdict(a) for a in self.attempts],
                        "capital_committed": str(self.committed()),
                    },
                )
            return False

    def _restore_canceled_submission(self, oid):
        """Any later observation revokes the rejection and reserves full risk."""
        self.canceled_submission_proofs.pop(oid, None)
        self.known.add(oid)
        for attempt in self.attempts:
            if attempt.order_id == oid and attempt.outcome == "REJECTED":
                attempt.outcome = "UNKNOWN"

    def _canceled_submission_observed(self, oid, trades=()):
        client = self.gateway._client
        return (
            oid in client.observed_open_rows
            or oid in self.observed_order_events
            or oid in self.terminal
            or oid in self.store.orders
            or any(key[1] == oid for key in client.observed_trade_legs)
            or any(key[1] == oid for key in self.processor.fill_records)
            or oid in self.processor.quarantined_orders
            or self.processor.has_unresolved_fill_key(oid)
            or bool(self._official_fills(trades, {oid}))
        )

    def _check_canceled_submission_observations(self):
        for oid in list(self.canceled_submission_proofs):
            if self._canceled_submission_observed(oid, self.gateway._client.last_trades):
                self.invalidate("CANCELED_SUBMISSION_EVIDENCE_CHANGED", oid)
                _require(False, "CANCELED_SUBMISSION_EVIDENCE_CHANGED")

    async def _refresh_canceled_submissions(self):
        """Recheck rejection evidence only; never repair an invalid close proof.

        Share fresh full pages across rejected IDs. Replaying the complete
        per-order close here makes snapshot duration grow with session history
        until every otherwise healthy account read exceeds its freshness cap.
        """
        _require(self.admission_ready, self.reason or "PREVIOUS_WINDOW_UNRESOLVED")
        client = self.gateway._client
        ids = set(self.canceled_submission_proofs)
        expected_revision = self.revision + len(ids)
        external_revision = self.external_revision
        pagination_revision = client.pagination_revision
        # Reserve every candidate throughout the reads, including failed reads.
        for oid in ids:
            self.invalidate("CANCELED_SUBMISSION_RECHECK", oid)
        try:
            attempts = [a for a in self.attempts if a.order_id in ids]
            _require(
                len(attempts) == len(ids) and {a.order_id for a in attempts} == ids,
                "CANCELED_SUBMISSION_ID_REUSED",
            )
            orders = await self.gateway.open_orders()
            open_rows = list(client.last_open_rows)
            trades = await self.gateway.get_trades(market=self.meta.condition_id)
            evidence = (orders, open_rows, trades, external_revision)
            for attempt in attempts:
                _require(
                    await self._verify_canceled_submission(attempt, evidence=evidence),
                    "CLOSE_EVIDENCE_INVALIDATED",
                )
            self._check_canceled_submission_observations()
            _require(
                self.revision == expected_revision and self.external_revision == external_revision,
                "EVENT_DURING_CLOSE_REQUIRES_RECHECK",
            )
            _require(client.pagination_revision == pagination_revision, "CLOSE_EVIDENCE_INVALIDATED")
            # Only our rejection-proof refresh advanced the revision. Preserve
            # the existing per-order proofs, never bless unrelated new facts.
            self.reason = None
            self.proof_revision = self.revision
            _require(self.admission_ready, "CLOSE_EVIDENCE_INVALIDATED")
        except Exception as exc:
            self.reason = self.fatal or str(exc)
            self.proof_revision = -1
            raise _Issue(SessionStatus.BLOCKED, self.reason) from exc

    async def _verify_canceled_submission(self, attempt, *, evidence=None):
        oid, client = attempt.order_id, self.gateway._client
        # Revalidation reserves the whole amount until all new reads finish.
        if oid in self.canceled_submission_proofs:
            self._restore_canceled_submission(oid)
            self.invalidate("CANCELED_SUBMISSION_RECHECK", oid)
        _require(_canceled_submission_response(attempt), "CANCELED_SUBMISSION_RESPONSE_CHANGED")
        _require(
            sum(a.expected_order_id == oid for a in self.attempts) == 1,
            "CANCELED_SUBMISSION_ID_REUSED",
        )
        if evidence is None:
            external_revision = self.external_revision
            orders = await self.gateway.open_orders()  # All pages, no absence-only proof.
            open_rows = list(client.last_open_rows)
            trades = await self.gateway.get_trades(market=self.meta.condition_id)
        else:
            orders, open_rows, trades, external_revision = evidence
        _require(client.pagination_failure is None, "CANCELED_SUBMISSION_PAGINATION_UNRESOLVED")
        _require(
            self.external_revision == external_revision,
            "EVENT_DURING_CLOSE_REQUIRES_RECHECK",
        )
        observed = any(order.order_id == oid for order in orders) or self._canceled_submission_observed(oid, trades)
        if observed:
            return False  # The regular order/fill reconciliation owns this ID.
        _require(
            attempt.outcome in {"UNKNOWN", "REJECTED"}
            and _canceled_submission_response(attempt),
            "CANCELED_SUBMISSION_EVIDENCE_CHANGED",
        )
        # The identity-bound POST error is the positive rejection evidence.
        # Empty collections only establish that no conflicting fact was seen.
        if oid not in self.immediate_rejection_proven_ids and _immediate_buy_not_filled_response(attempt):
            self.record("sm_order_rejection_proven", {"order_id": oid,
                "reason": attempt.order_type+"_NOT_FILLED", "order_type": attempt.order_type,
                "source": "IDENTITY_BOUND_POST_RESPONSE_AND_COMPLETE_COLLECTIONS",
                "risk_released": True})
            self.immediate_rejection_proven_ids.add(oid)
        self.canceled_submission_proofs[oid] = {
            "response": attempt.raw_response, "open_orders": open_rows, "trades": trades,
        }
        attempt.outcome = "REJECTED"
        self.known.discard(oid)
        self.proofs.pop(oid, None)
        self.terminal.pop(oid, None)
        if self.shared_reads is not None:
            self.shared_reads.service.retire_rejected_orders({oid})
        return True

    async def _wait_for_confirmation(self) -> bool:
        """After cancellation, keep the barrier closed while settlement catches up.

        No new BUY or repricing here. Stop at this market's trading cutoff plus
        the existing close buffer, or immediately on any other safety failure.
        """
        start = int(self.slug.rsplit("-", 1)[1])
        deadline = self.time_anchor + start + TRADING_END_S + CANCEL_BUFFER_S - self.official_time
        while (
            self.fatal is None
            and self.gateway._client.pagination_failure is None
            and self.pending_confirmations
            and all(a.outcome in {"ACK", "VERIFIED", "REJECTED", "ABORTED"} for a in self.attempts)
            and self.reason
            == ";".join(
                f"{oid}:TRADE_CONFIRMATION_UNRESOLVED" for oid in sorted(self.pending_confirmations)
            )
            and self.monotonic() < deadline
        ):
            if self.shared_reads is not None:
                await self.shared_reads.acquire(newer=True)
            else:
                await self.sleep(min(2.0, deadline - self.monotonic()))
            if self.fatal is not None:
                return False
            if await self._verify_close():
                return True
        return self.safe_to_continue

    def _placement_outcomes(self, raw, quotes):
        """One result per submitted quote; unknown legs never erase known ACKs."""
        if len(quotes) == 1 and isinstance(raw, dict):
            raw = [raw]
        if not isinstance(raw, list) or len(raw) != len(quotes):
            return [("UNKNOWN", None) for _ in quotes]
        outcomes = []
        for item, quote in zip(raw, quotes, strict=True):
            outcome, order = "UNKNOWN", None
            if isinstance(item, dict):
                if _explicit_maker_rejection(item, Decimal(str(quote.price)) * Decimal(str(quote.size))):
                    outcome = "REJECTED"
                elif item.get("success") is False:
                    if not any(item.get(k) not in (None, "") for k in ("orderID", "order_id", "orderId", "id")):
                        outcome = "REJECTED"
                elif item.get("success") is True and item.get("errorMsg") in (None, ""):
                    try:
                        placed = self.gateway._parse_place_response(item, [quote])
                        if len(placed) == 1:
                            outcome, order = "ACK", placed[0]
                    except PlacementUnknown:
                        pass
            outcomes.append((outcome, order))
        ids = [order.order_id for _, order in outcomes if order is not None]
        # A duplicated ID cannot identify both signed orders.
        return [("UNKNOWN", None) if order is not None and ids.count(order.order_id) > 1
                else (outcome, order) for outcome, order in outcomes]

    def _accept_placement(self, attempt, order, meta, *, record=True):
        quote, client = attempt.quote, self.gateway._client
        _require(re.fullmatch(r"[A-Za-z0-9_-]+", order.order_id) is not None, "ORDER_ID_NOT_OPAQUE")
        _require(order.order_id not in self.known and order.order_id not in client.observed_open_rows,
                 "DUPLICATE_ORDER_ID")
        _require(order.token_id == quote.token_id and order.side is Side.BUY
                 and order.price == quote.price and order.size == quote.size,
                 "PLACEMENT_QUOTE_IDENTITY_CHANGED")
        attempt.outcome, attempt.order_id = "ACK", order.order_id
        self.known.add(order.order_id)
        client.known_ids.add(order.order_id)
        self.processor.register_order(order.order_id, meta.condition_id, quote.token_id,
                                      Side.BUY, quote.size, quote.price, "rdp_sm")
        self.store.upsert_order(order)
        if record:
            self.record("sm_placement_ack", asdict(attempt))

    def _configured_quotes(self, meta, books, window_id, *, evidence=None):
        # Local import keeps the existing M3 -> M2 dependency at module load.
        from polymaker.rdp_sm_quotes import make_quotes

        fills = self.confirmed()
        yes, no = (sum((Decimal(str(f.size)) for f in fills if f.token_id == token), Decimal(0))
                   for token in (meta.yes.token_id, meta.no.token_id))
        return make_quotes(meta, books, yes, no, window_id, self.cfg.rdp_sm,
            now=self.official_time + self.monotonic() - self.time_anchor,
            official_time=self.official_time,
            official_time_read_duration_s=self.official_time_read_duration_s,
            max_age=self.cfg.risk.ws_stale_halt_s, evidence=evidence)[1]

    async def execute_window(
        self,
        window_id: str,
        quotes: list[WindowQuote],
        *,
        fill_window_sec: float = 3.0,
        quote_revision: int | None = None,
        quote_factory=None,
    ) -> WindowResult:
        _require(self.cfg.rdp_sm.normal_entry_mode == "legacy", "OBI_CONTROLLER_REQUIRED")
        async with self.lock:
            result = WindowResult(
                self.session_id,
                window_id,
                quotes[0].market_id if quotes else "",
                time.time(),
                requested_quotes=list(quotes),
            )
            first_attempt = len(self.attempts)
            previous_ids = frozenset(self.known)
            deadline_handle = None
            deadline_job = None
            skipped_quote_reason = None
            self._cancel_jobs = {}

            def close_at_deadline():
                nonlocal deadline_job
                deadline_job = asyncio.create_task(self._cancel_known())

            def refresh_quotes(meta, books):
                nonlocal quotes
                if quote_factory is None:
                    return False
                _require(quote_revision is not None, "STRATEGY_QUOTE_REVISION_REQUIRED")
                fresh = quote_factory(meta, books, window_id)
                # Keep the revision-bound side/inventory decision. Any resized
                # leg must equal the same configured sizing decision used by M3.
                def identity(q):
                    return q.market_id, q.token_id, q.outcome, q.side, q.window_id
                _require([identity(q) for q in fresh] == [identity(q) for q in quotes],
                         "STRATEGY_QUOTE_IDENTITY_CHANGED")
                expected = self._configured_quotes(meta, books, window_id)
                _require([(q.outcome, q.size) for q in fresh]
                         == [(q.outcome, q.size) for q in expected],
                         "STRATEGY_QUOTE_SIZE_CHANGED")
                changed = quotes != fresh
                quotes = list(fresh)
                result.requested_quotes = list(quotes)
                return changed

            try:
                _require(not self.closed and self.fatal is None, self.fatal or "SESSION_CLOSED")
                _require(window_id not in self.used_windows, "WINDOW_ID_ALREADY_USED")
                _require(
                    isinstance(window_id, str) and bool(window_id.strip()), "WINDOW_ID_INVALID"
                )
                _require(
                    1 <= len(quotes) <= 2 and len({q.outcome for q in quotes}) == len(quotes),
                    "WINDOW_QUOTE_COUNT_INVALID",
                )
                _require(
                    math.isfinite(fill_window_sec) and fill_window_sec >= 3, "FILL_WINDOW_INVALID"
                )
                if self.attempts:
                    _require(self.safe_to_continue, "PREVIOUS_WINDOW_UNRESOLVED")
                self.used_windows.add(window_id)
                # One fresh admission for the entire request. There is no
                # account/market roundtrip between YES and NO submissions.
                meta, start, books, account = await self._await_owned(self._snapshot())
                if self.attempts:
                    _require(self.safe_to_continue, "PREVIOUS_WINDOW_EVIDENCE_CHANGED")
                refresh_quotes(meta, books)
                for quote in quotes:
                    self._validate(quote, window_id, books, meta)
                new = sum((Decimal(str(q.price)) * Decimal(str(q.size)) for q in quotes), Decimal(0))
                _require(self.committed() + new <= self.capital_limit, "TEST_CAPITAL_LIMIT_EXCEEDED")
                _require(new <= Decimal(str(account["basic_spendable_budget_usd"])),
                         "OFFICIAL_CAPITAL_INSUFFICIENT")
                elapsed = self.official_time + self.monotonic() - self.time_anchor - start
                delta = inventory_delta(*(
                    sum((Decimal(str(f.size)) for f in self.confirmed() if f.token_id == token),
                        Decimal(0)) - self.merged_pairs
                    for token in (meta.yes.token_id, meta.no.token_id)))
                normal_cutoff = self.cfg.rdp_sm.normal_cutoff_sec
                cutoff = TRADING_END_S if inventory_trading_state(delta) == "REBALANCE" else normal_cutoff
                _require(trading_start_sec(self.cfg) <= elapsed < cutoff,
                         "MARKET_SUBMISSION_CUTOFF")
                if elapsed >= normal_cutoff:
                    _require(len(quotes) == 1
                             and quotes[0].outcome == ("NO" if delta > 0 else "YES")
                             and [(q.outcome, q.size) for q in quotes]
                             == [(q.outcome, q.size) for q in self._configured_quotes(meta, books, window_id)],
                             "LATE_REBALANCE_QUOTE_INVALID")
                _require(account["applicable_spenders"], "SPENDER_UNKNOWN")
                _require(self.revision == account["capital_evidence_revision"],
                         "CAPITAL_EVIDENCE_CHANGED_BEFORE_SUBMIT")
                _require(self.gateway._client.pagination_failure is None,
                         self.gateway._client.pagination_failure or "ACCOUNT_PAGINATION_UNRESOLVED")
                _require(quote_revision is None or self.revision == quote_revision,
                         "STRATEGY_QUOTE_EVIDENCE_CHANGED")
                if quote_factory is not None:
                    sizing_evidence = {}
                    self._configured_quotes(meta, books, window_id, evidence=sizing_evidence)
                    self.record("sm_quote_refreshed", {
                        "window_id": window_id, "quotes": [asdict(q) for q in quotes],
                        "books": {"YES": books[meta.yes.token_id], "NO": books[meta.no.token_id]},
                        "sizing": sizing_evidence})
                batch = [Placement(uuid.uuid4().hex, quote) for quote in quotes]
                self.attempts.extend(batch)
                self.invalidate("PLACEMENT_INTENT")
                # Both full commitments and both durable intents precede POST.
                for attempt in batch:
                    self.record("sm_placement_intent", {**asdict(attempt),
                        "capital_committed_usd": str(self.committed())})
                self.processor.arm_pending_many([(q.token_id, Side.BUY) for q in quotes])
                result.t0_monotonic = self.monotonic()
                deadline = result.t0_monotonic + fill_window_sec
                result.fill_deadline_monotonic = deadline
                revision = self.revision

                def guard():
                    elapsed = self.official_time + self.monotonic() - self.time_anchor - start
                    return (
                        self.fatal is None and self.revision == revision
                        and previous_ids <= self.proofs.keys()
                        and self.monotonic() < deadline
                        and self.monotonic() - self.time_anchor <= self.cfg.risk.ws_stale_halt_s
                        and trading_start_sec(self.cfg) <= elapsed < cutoff
                    )

                client = self.gateway._client
                client.attempt, client.batch_attempts, client.submit_guard = batch[0], batch, guard
                gateway_quotes = [Quote(q.token_id, Side.BUY, q.price, q.size) for q in quotes]

                def parse_response(raw, submitted):
                    return [order for _, order in self._placement_outcomes(raw, submitted)
                            if order is not None]

                task = asyncio.create_task(self.gateway.place(
                    gateway_quotes, replace(meta, tick_size=books[quotes[0].token_id]["tick"]),
                    submit_guard=guard, force_post_only=True, strict_placement=True,
                    tick_sizes={q.token_id: books[q.token_id]["tick"] for q in quotes},
                    placement_parser=parse_response,
                ))
                try:
                    while True:
                        try:
                            await asyncio.shield(task)
                            break
                        except asyncio.CancelledError:
                            # Drain the same request, then close every known leg.
                            self.fatal = "ABNORMAL_EXIT"
                            if task.cancelled():
                                raise RuntimeError("SUBMISSION_TASK_CANCELLED") from None
                except Exception:
                    # The actual transport observer determines whether a POST
                    # began. A timeout after POST retains every unknown leg.
                    pass
                finally:
                    client.attempt, client.batch_attempts = None, None
                outcomes = (self._placement_outcomes(batch[0].raw_response, gateway_quotes)
                            if batch[0].post_started else [("ABORTED", None) for _ in batch])
                for attempt, (outcome, order) in zip(batch, outcomes, strict=True):
                    try:
                        attempt.outcome = outcome
                        if outcome == "ACK":
                            self._accept_placement(attempt, order, meta)
                        elif outcome == "UNKNOWN":
                            self.fatal = self.fatal or "UNKNOWN_PLACEMENT_EXPOSURE"
                            self.record("sm_placement_unknown", asdict(attempt))
                        self.record("sm_placement_result", asdict(attempt))
                    except Exception:
                        if attempt.outcome != "ACK" or attempt.order_id is None:
                            attempt.outcome = "UNKNOWN"
                        self.fatal = self.fatal or "UNKNOWN_PLACEMENT_EXPOSURE"
                        with suppress(Exception):
                            self.record("sm_placement_unknown" if attempt.outcome == "UNKNOWN"
                                        else "sm_placement_record_failed", asdict(attempt))
                if all(a.outcome in {"ACK", "REJECTED", "ABORTED"} for a in batch):
                    self.processor.discard_pending()
                if self.known:
                    deadline_handle = asyncio.get_running_loop().call_later(
                        max(0, deadline - self.monotonic()), close_at_deadline)
                while (self.monotonic() < deadline and self.fatal is None
                       and all(a.outcome == "ACK" for a in batch)):
                    await self.sleep(min(0.25, max(0, deadline - self.monotonic())))
                safe = await self._await_owned(self._verify_close(cancel=True))
                if not safe and self.pending_confirmations:
                    await self._await_owned(self._wait_for_confirmation())
            except asyncio.CancelledError:
                self.fatal = "ABNORMAL_EXIT"
                await self._await_owned(self._verify_close(cancel=True), propagate_cancel=False)
            except Exception as exc:
                self.reason = self.fatal or str(exc)
                if self.display_events is not None and getattr(exc, "context", None):
                    self.display_events.append(("sm_diagnostic", exc.context))
                if any(a.outcome == "INTENT" for a in self.attempts):
                    self.fatal = self.fatal or "UNRESOLVED_PLACEMENT_INTENT"
                pre_submit = (
                    quote_revision is not None
                    and len(self.attempts) == first_attempt
                    and self.fatal is None)
                # Reuse the loop's read classification before any placement
                # intent exists. Never retry a POST or mask a hard failure.
                read_retry = pre_submit and not pause_reason(self.reason) and retry_read(exc)
                if read_retry:
                    self.reason = reason_code(exc)
                quote_moved = pre_submit and (self.reason in SAFE_QUOTE_SKIP_REASONS or read_retry)
                inventory_wait = (isinstance(exc, InventoryMismatch)
                                  and len(self.attempts) == first_attempt and self.fatal is None)
                if self.known or quote_moved or inventory_wait:
                    # Close known exposure even when second pre-submit validation fails.
                    original_reason = self.reason
                    safe = await self._await_owned(self._verify_close(cancel=True), propagate_cancel=False)
                    if inventory_wait and safe and self.safe_to_continue:
                        skipped_quote_reason = original_reason
                    elif quote_moved and safe and self.safe_to_continue:
                        skipped_quote_reason = original_reason
                        self.record("sm_quote_skipped", {
                            "window_id": window_id,
                            "elapsed_s": self.official_time - int(self.slug.rsplit("-", 1)[1]),
                            "reason": original_reason})
                    elif not quote_moved:
                        self.reason = original_reason
            finally:
                if deadline_handle is not None:
                    deadline_handle.cancel()
                if deadline_job is not None:
                    # No timer cancellation job may outlive this result/store.
                    while True:
                        try:
                            await asyncio.shield(deadline_job)
                            break
                        except asyncio.CancelledError:
                            self.fatal = "ABNORMAL_EXIT"
                            if deadline_job.cancelled():
                                break
            result.end_time = time.time()
            result.close_monotonic = self.monotonic()
            result.placement_results = [asdict(a) for a in self.attempts[first_attempt:]]
            result.known_order_ids = sorted(self.known)
            result.confirmed_fills = [asdict(f) for f in self.confirmed()]
            if self.meta is not None:
                result.yes_inventory = sum(
                    f.size for f in self.confirmed() if f.token_id == self.meta.yes.token_id
                ) - float(self.merged_pairs)
                result.no_inventory = sum(
                    f.size for f in self.confirmed() if f.token_id == self.meta.no.token_id
                ) - float(self.merged_pairs)
            result.delta = result.yes_inventory - result.no_inventory
            result.capital_committed = float(self.committed())
            result.close_barrier = "SAFE_TO_CONTINUE" if self.safe_to_continue else "BLOCKED"
            result.block_reason = (skipped_quote_reason if self.safe_to_continue
                                   else self.reason or self.fatal)
            result.journal_references = [f"{self.journal.path}#{self.journal.sequence}"]
            try:
                self.record("sm_window_result", result.to_dict())
            except Exception:
                result.close_barrier, result.block_reason = "BLOCKED", "JOURNAL_WRITE_FAILED"
            return result

    def close(self) -> None:
        """Release resources; never automatically trade or erase the risk ledger."""
        if self.lock.locked():
            raise RuntimeError("EXECUTION_ACTIVE; cancel and await the window before teardown")
        verified = self.safe_to_continue
        self.closed = True
        lease = getattr(self.reads, "lease", None)
        try:
            self.reads.close()
            self.store.close()
            self.journal.close()
            if lease is not None and not lease.file.closed and verified and getattr(self.journal, "failure", None) is None:
                lease.verified_closed()
        finally:
            if lease is not None:
                lease.close()

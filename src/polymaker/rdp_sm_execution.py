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
from decimal import ROUND_CEILING, Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from py_clob_client_v2.client import ClobClient
from py_clob_client_v2.constants import INITIAL_CURSOR

from polymaker.catalog.gamma import GammaClient
from polymaker.config import Config, RDPSMConfig
from polymaker.domain import Quote, Side, TradeState
from polymaker.execution.filltruth import ReconciliationOutcome, reconcile_owned_order
from polymaker.execution.gateway import PlacementUnknown
from polymaker.journal import Journal
from polymaker.rdp_sm import (
    NORMAL_END_S,
    OFFICIAL_HOSTS,
    TEST_CAPITAL_LIMIT_USDC,
    TRADING_END_S,
    TRADING_START_S,
    ReadOnlyClobClient,
    SessionStatus,
    _account,
    _book,
    _Issue,
    _market,
    _Reads,
    _require,
    _token_id,
    inventory_trading_state,
    run_preflight,
)
from polymaker.rdp_sm_recovery import pause_reason, reason_code, retry_read
from polymaker.state.store import StateStore
from polymaker.state.tracker import (
    OrderEvent,
    TradeEvent,
    UserEventProcessor,
    exceeds_tolerance,
    within_tolerance,
)
from polymaker.userstream.parse import normalize_order, parse_trade, strict_number

CAPITAL_LIMIT = Decimal(int(TEST_CAPITAL_LIMIT_USDC))
MIN_MAKER_BUY_NOTIONAL = Decimal("1")
MIN_MAKER_BUY_SHARES = Decimal("5")
RDP_TOLERANCE = Decimal(".01")
MATCHED_REMAINING_TOLERANCE_SHARES = RDPSMConfig().matched_remaining_tolerance_shares
INVENTORY_TOLERANCE_SHARES = RDP_TOLERANCE
SAFE_QUOTE_SKIP_REASONS = frozenset({
    "QUOTE_WOULD_TAKE", "QUOTE_PRICE_TICK_INVALID", "QUOTE_PRICE_TICK_OR_SIZE_INVALID",
    "NORMAL_LEG_NOTIONAL_BELOW_MINIMUM", "MAKER_BUY_NOTIONAL_BELOW_MINIMUM",
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
    """Round up the gap, share minimum and BUY notional to the SDK quantity grid."""
    return max(inventory_gap, MIN_MAKER_BUY_SHARES, min_order_size,
               MIN_MAKER_BUY_NOTIONAL / price).quantize(Decimal(".01"), rounding=ROUND_CEILING)


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
        and 0 < notional < MIN_MAKER_BUY_NOTIONAL)
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

    @property
    def maximum(self) -> Decimal:
        return Decimal(str(self.quote.price)) * Decimal(str(self.quote.size))


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

    def create_order(self, order_args, options=None):
        # The SDK floors size*100. Binary float 5.02*100 is below 502 and
        # otherwise signs 5.01 shares. Preserve the validated 0.01-share size;
        # the existing signed-quote equality check remains unchanged.
        return super().create_order(
            replace(order_args, size=Decimal(str(order_args.size))), options=options)

    def _get(self, endpoint, headers=None, params=None):
        parsed = urlsplit(endpoint)
        extra = parsed.path == "/data/trades" or parsed.path in {
            f"/data/order/{oid}"
            for oid in getattr(self, "known_ids", set())
            | getattr(self, "observed_open_rows", {}).keys()
        }
        if not extra:
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
        if parsed.path == "/data/trades":
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
        attempt = getattr(self, "attempt", None)
        attempts = getattr(self, "batch_attempts", None) or ([attempt] if attempt is not None else [])
        _require(
            endpoint == f"{OFFICIAL_HOSTS['clob_host']}/orders"
            and bool(attempts)
            and all(not a.post_started for a in attempts)
            and self.pagination_failure is None
            and self.submit_guard(),
            "SM_POST_NOT_PERMITTED",
        )
        rows = json.loads(data)
        _require(
            len(rows) == len(attempts) and 1 <= len(rows) <= 2
            and all(r.get("postOnly") is True and r.get("orderType") == "GTC" for r in rows),
            "SM_MAKER_ONLY",
        )
        for row, current in zip(rows, attempts, strict=True):
            signed, quote = row["order"], current.quote
            _require(
                signed["side"] == "BUY"
                and signed["signatureType"] == self.builder.signature_type
                and signed["tokenId"] == quote.token_id
                and signed["maker"].lower() == self.builder.funder.lower()
                and signed["signer"].lower() == self.get_address().lower()
                and Decimal(str(signed["takerAmount"])) == Decimal(str(quote.size)) * 1_000_000
                and Decimal(str(signed["makerAmount"])) == current.maximum * 1_000_000,
                "SIGNED_QUOTE_ECONOMICS_CHANGED",
            )
        for current in attempts:
            current.post_started = True
            current.request_hash = hashlib.sha256(data.encode()).hexdigest()
        raw = ClobClient._post(self, endpoint, headers=headers, data=data, params=params)
        for current in attempts:
            current.raw_response = raw
        return raw

    def _delete(self, endpoint, headers=None, data=None, params=None):
        ids = json.loads(data)
        _require(
            endpoint == f"{OFFICIAL_HOSTS['clob_host']}/orders"
            and isinstance(ids, list)
            and bool(ids)
            and set(ids) <= self.known_ids,
            "SM_CANCEL_SCOPE_INVALID",
        )
        return ClobClient._delete(self, endpoint, headers=headers, data=data, params=params)


class _ExecutionReads(_Reads):
    def __init__(self, cfg: Config, *, risk_reads: _ExecutionReads | None = None,
                 account_policy=None):
        super().__init__(cfg)
        self.risk_reads = risk_reads
        self.account_policy = (risk_reads.account_policy if risk_reads is not None
                               else account_policy)

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
        gateway._client.observed_open_rows = {}
        gateway._client.observed_trade_legs = {}
        gateway._client.account_policy = self.account_policy
        gateway._client._pagination_failures = {}
        gateway._client.pagination_revision = 0
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
            return await super().market(slug)
        from polymaker.rdp_sm_entry import exact_market

        async with GammaClient(self.cfg.wallet.gamma_host) as gamma:
            return await exact_market(gamma, slug=slug)

    async def book(self, token: str):
        # SDK normally caches tick size indefinitely. SM needs an official read
        # at every submission, rather than a permanently cached M1 snapshot.
        self.gateway._client._ClobClient__tick_sizes.pop(token, None)
        tick = await self.gateway._io(self.gateway._client.get_tick_size, token)
        raw = await self.gateway._io(self.gateway._client.get_order_book, token)
        return raw, tick


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

    def tracked_trade_qty(self, order_id):
        return float(sum((Decimal(str(f.size)) for f in self.economic_fills_for_order(order_id)), Decimal(0)))

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
            self.executor.invalidate("TRADE_EVIDENCE_CHANGED", ev.maker_order_id)
            with suppress(Exception):
                self.executor.record("sm_trade", asdict(ev))

    def on_order(self, ev: OrderEvent, condition_id: str, **kwargs):
        self._external_commitment_changed(ev.order_id)
        if self.owned_record(ev.order_id) is not None:
            self.executor.external_revision += 1
            previous = self.executor.terminal.get(ev.order_id)
            if previous is not None and not _terminal(ev, self.executor.matched_remaining_tolerance_shares):
                self.executor.fatal = "TERMINAL_LIVE_CONTRADICTION"
            self.executor.invalidate("ORDER_EVIDENCE_CHANGED", ev.order_id)
            with suppress(Exception):
                self.executor.record("sm_order", asdict(ev))
        super().on_order(ev, condition_id, **kwargs)

    def quarantine_order(self, order_id, reason, **kwargs):
        if self.owned_record(order_id) is not None:
            self.executor.invalidate("ORDER_QUARANTINED", order_id)
        super().quarantine_order(order_id, reason, **kwargs)


def _terminal(event: OrderEvent, tolerance: Decimal = MATCHED_REMAINING_TOLERANCE_SHARES) -> bool:
    if event.status in {"CANCELED", "CANCELLED"}:
        return True
    if event.status != "MATCHED":
        return False
    original, matched = Decimal(str(event.original_size)), Decimal(str(event.size_matched))
    if not original.is_finite() or not matched.is_finite():
        return False
    # Inclusive Decimal boundary; inventory still uses actual confirmed fills.
    return within_tolerance(original, matched, tolerance)


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
        self.known: set[str] = set()
        self.terminal: dict[str, OrderEvent] = {}
        self.proofs: dict[str, dict[str, Any]] = {}
        self.pending_confirmations: set[str] = set()
        self.merged_pairs = Decimal(0)
        self.merged_cost: dict[str, Decimal] = {}
        self.merge_pending = False
        self.inventory_mismatch = None
        self.inventory_observation = None
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
        # Optional CLI-only references to already-created Journal payloads.
        # No formatting, I/O, callbacks or queue writes inside execution.
        self.display_events = display_events

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
        startup_reads = _ExecutionReads(cfg, risk_reads=risk_reads)
        preflight = await run_preflight(cfg, slug, _reads=startup_reads)
        if preflight.status is not SessionStatus.READY:
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
            executor = cls(cfg, slug, session_id, reads, journal, **clock)
            executor.record(
                "sm_session",
                {
                    "session_id": session_id,
                    "slug": slug,
                    "signer": reads.gateway.address,
                    "funder": reads.gateway.funder,
                    "signature_type": cfg.wallet.signature_type,
                    "live_authority": authority,
                    "starting_spendable_usdc": min(preflight.account['available_balance_usd'],
                        preflight.account['applicable_allowance_usd'] -
                        preflight.account['existing_buy_commitment_usd']),
                    "starting_official_balance_usdc": preflight.account['official_balance_usd'],
                    "test_capital_limit_usdc": str(CAPITAL_LIMIT),
                    # Compatibility fields only; no prior Session evidence.
                    "historical_pending_cost_reserved_usd": 0,
                    "historical_matched": {},
                    **({"capital_basis": "OFFICIAL_BALANCE_AT_SESSION_START",
                        "historical_pending_cost_observed_usd": 0}
                       if reads.account_policy is not None and reads.account_policy.use_starting_balance else {}),
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
            raise

    def record(self, kind: str, payload: Any) -> None:
        try:
            self.journal.write(
                kind, {"session_id": self.session_id, "payload": payload}, time.time()
            )
        except Exception:
            self.fatal = "JOURNAL_WRITE_FAILED"
            self.proof_revision = -1
            raise
        if self.display_events is not None:
            with suppress(Exception):
                self.display_events.append((kind, payload))

    def invalidate(self, reason: str, order_id: str | None = None) -> None:
        self.revision += 1
        self.proof_revision = -1
        self.reason = reason
        if order_id is not None:
            self.proofs.pop(order_id, None)

    @property
    def safe_to_continue(self) -> bool:
        return (
            not self.closed
            and not self.merge_pending
            and self.fatal is None
            and self.gateway._client.pagination_failure is None
            and self.reason is None
            and self.proof_revision == self.revision
            and self.proof_pagination_revision == self.gateway._client.pagination_revision
            and all(a.outcome in {"ACK", "REJECTED", "ABORTED"} for a in self.attempts)
            and set(self.proofs) == self.known
            and set(self.terminal) == self.known
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

    def committed(self) -> Decimal:
        """One exposure per placement; terminal proof releases only unfilled size."""
        total = Decimal(0)
        for attempt in self.attempts:
            if attempt.outcome in {"REJECTED", "ABORTED"}:
                continue
            oid = attempt.order_id
            proof = self.proofs.get(oid)
            fills = self.processor.economic_fills_for_order(oid) if oid else []
            if proof is not None and oid in self.terminal:
                # Only verified confirmed fills remain after a complete barrier.
                total += sum(
                    (Decimal(str(ev.price)) * Decimal(str(ev.size)) for ev in fills), Decimal(0)
                )
            else:
                # Full limit-price exposure covers fills + remaining + uncertainty
                # once, even if cancellation returned ACK or a WS fill was seen.
                total += attempt.maximum
        return max(Decimal(0), total - sum(self.merged_cost.values(), Decimal(0)))

    async def _read_books(self, meta):
        books = {}
        for token in (meta.yes.token_id, meta.no.token_id):
            raw_book, tick = await self.reads.book(token)
            books[token] = _book(raw_book, tick, token, meta)
        now = self.official_time + self.monotonic() - self.time_anchor
        _require(all(-2 <= now - b["timestamp"] <= self.cfg.risk.ws_stale_halt_s
                     for b in books.values()), "EXECUTION_DATA_STALE")
        return books

    async def _recover_pagination(self):
        client = self.gateway._client
        if client.pagination_failure is not None:
            try:
                await self.gateway._io(client.retry_incomplete_reads)
            except Exception as exc:
                if retry_read(exc):
                    raise _Issue(SessionStatus.BLOCKED, reason_code(exc)) from exc
                raise

    async def _snapshot(self, *, include_books=True):
        began = self.monotonic()
        await self._recover_pagination()
        _require(
            self.gateway._client.pagination_failure is None,
            self.gateway._client.pagination_failure or "ACCOUNT_PAGINATION_UNRESOLVED",
        )
        _require(
            all(
                getattr(self.cfg.wallet, key).rstrip("/") == host
                for key, host in OFFICIAL_HOSTS.items()
            )
            and self.cfg.wallet.chain_id == 137,
            "OFFICIAL_ENDPOINT_CONFIGURATION_REQUIRED",
        )
        for oid in sorted(self.known - set(self.proofs)):
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
        tokens = {meta.yes.token_id, meta.no.token_id}
        # Account admission precedes the final book reads. This reference tick
        # is only for _account's descriptive lower bound, never a quote tick.
        market = {side: {"tick": meta.tick_size} for side in ("yes", "no")}
        capital_revision = self.revision
        account = await _account(self.reads, meta, market)
        client = self.gateway._client
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
        for order in orders:
            if order.order_id in self.terminal:
                self.fatal = "TERMINAL_LIVE_CONTRADICTION"
                self.invalidate("OFFICIAL_OPEN_ORDER_CONTRADICTS_TERMINAL", order.order_id)
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
                    self.fatal = "TERMINAL_LIVE_CONTRADICTION"
                    self.invalidate("OFFICIAL_OPEN_ORDER_CONTRADICTS_TERMINAL", oid)
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
                    CAPITAL_LIMIT,
                    Decimal(str(account["official_balance_usd"])) - reserved,
                    Decimal(str(account["applicable_allowance_usd"])) - reserved,
                ),
            )
        )
        inventory = {}
        consistent = True
        for token, name in ((meta.yes.token_id, "yes"), (meta.no.token_id, "no")):
            owned_qty = sum(
                (Decimal(str(ev.size))
                for ev in self.processor.economic_fills_for_orders(self.known)
                if ev.token_id == token), Decimal(0)
            ) - self.merged_pairs
            actual = Decimal(str(account[f"{name}_inventory"]))
            consistent = consistent and abs(actual - owned_qty) <= INVENTORY_TOLERANCE_SHARES
            inventory[name.upper()] = {"expected": float(owned_qty), "actual": float(actual),
                                       "difference": float(actual - owned_qty)}
        context = {"source": "DATA_API", "tolerance_shares": float(INVENTORY_TOLERANCE_SHARES),
                   "inventory": inventory}
        self.inventory_observation = context
        if not consistent:
            self.inventory_mismatch = context
            raise InventoryMismatch(context)
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
            ev = normalize_order(row)
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

    def _validate(self, quote: WindowQuote, window_id: str, books, meta):
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
            and size <= self.cfg.rdp_sm.max_order_size
            and size % Decimal(".01") == 0,
            "QUOTE_SIZE_INVALID",
        )
        _require(price < Decimal(str(books[token]["best_ask"])), "QUOTE_WOULD_TAKE")

    async def _verify_order(self, oid: str) -> None:
        outcome = await reconcile_owned_order(oid, self.gateway, self.processor, retry_delays=())
        trades = list(getattr(self.gateway._client, "last_trades", []))
        row = await self.gateway.get_order(oid)
        with suppress(Exception):
            self.record(
                "sm_official_reconciliation",
                {"order_id": oid, "order": row, "trades": trades, "filltruth": outcome.value},
            )
        ev = normalize_order(row)
        owned = self.processor.owned_record(oid)
        _require(
            ev is not None
            and ev.token_id == owned["token_id"]
            and ev.side.value == owned["side"]
            and ev.market_id == self.meta.condition_id
            and within_tolerance(ev.original_size, owned["submitted_qty"], RDP_TOLERANCE)
            and within_tolerance(ev.price, owned["submitted_price"], RDP_TOLERANCE),
            "FINAL_ORDER_IDENTITY_UNKNOWN",
        )
        UserEventProcessor.on_order(self.processor, ev, self.meta.condition_id)
        if oid in self.terminal and not _terminal(ev, self.matched_remaining_tolerance_shares):
            self.fatal = "TERMINAL_LIVE_CONTRADICTION"
        _require(_terminal(ev, self.matched_remaining_tolerance_shares), "ORDER_NOT_TERMINAL")
        _require(outcome is ReconciliationOutcome.COMPLETE, "FILLTRUTH_NOT_COMPLETE")
        official = self._official_fills(trades, {oid})
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
        for raw_trade in trades:
            parsed = parse_trade(raw_trade)
            for leg in parsed.maker_legs:
                if leg.order_id in ids:
                    _token_id(leg.asset_id)
                    _require(
                        parsed.market == self.meta.condition_id, "OFFICIAL_TRADE_MARKET_UNKNOWN"
                    )
                    key = (parsed.trade_id, leg.order_id)
                    value = (parsed.status, leg.asset_id, leg.side, leg.price, leg.matched_amount)
                    _require(
                        key not in official or _same_fill_economics(official[key], value), "OFFICIAL_TRADE_CONFLICT"
                    )
                    official[key] = value
        return official

    def _local_fills(self, ids):
        return {
            (f.trade_id, f.maker_order_id): (f.status, f.token_id, f.our_side, f.price, f.size)
            for f in self.processor.economic_fills_for_orders(ids)
        }

    async def verify_close(self, *, cancel: bool = False) -> bool:
        """Explicit read/cleanup request; serialize with any active window."""
        async with self.lock:
            if cancel:
                self._cancel_jobs = {}  # An explicit later cleanup may retry cancellation.
            return await self._await_owned(self._verify_close(cancel=cancel))

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

    async def _cancel_known(self) -> None:
        """One managed cancellation per known-ID set, shared by timer/closeout.

        The SDK read worker is not cancelled by a timer. Its owner still awaits
        it; this independent known-ID cancellation can run on the existing pool.
        Final reconciliation starts only after that owner regains control.
        """
        scope = frozenset(self.known)
        if not scope:
            return
        job = self._cancel_jobs.get(scope)
        if job is None:

            async def send():
                began, cancel_ts = self.monotonic(), time.time()
                ack = await self.gateway.cancel(sorted(scope))
                with suppress(Exception):
                    self.record("sm_cancel_ack", {"ids": sorted(scope), "ack": ack,
                        "request_ts": cancel_ts, "duration_s": self.monotonic() - began})

            job = asyncio.create_task(send())
            self._cancel_jobs[scope] = job
        while True:
            try:
                await asyncio.shield(job)
                return
            except asyncio.CancelledError:
                self.fatal = "ABNORMAL_EXIT"
                if job.cancelled():
                    raise RuntimeError("CANCELLATION_TASK_CANCELLED") from None

    async def _verify_close(self, *, cancel: bool = False) -> bool:
        """Fresh official order/trade facts; no old COMPLETE or terminal cache."""
        self.proof_revision = -1
        self.proofs = {}
        self.pending_confirmations = set()
        try:
            if cancel and self.known:
                await self._cancel_known()
            await self._recover_pagination()
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
                            "resolution": "NO_RELIABLE_REQUEST_TO_ORDER_CORRELATION; risk retained",
                        },
                    )
            external_revision = self.external_revision
            errors = []
            for oid in sorted(self.known):
                try:
                    await self._verify_order(oid)
                except Exception as exc:
                    errors.append(f"{oid}:{exc}")
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
            _require(
                all(a.outcome in {"ACK", "REJECTED", "ABORTED"} for a in self.attempts),
                "UNKNOWN_PLACEMENT_EXPOSURE",
            )
            _require(self.fatal is None, self.fatal or "FATAL_EXECUTION_STATE")
            _require(not self.merge_pending, "MERGE_PENDING")
            _require(
                self.gateway._client.pagination_failure is None,
                self.gateway._client.pagination_failure or "ACCOUNT_PAGINATION_UNRESOLVED",
            )
            self.reason = None
            self.proof_revision = self.revision
            self.proof_pagination_revision = self.gateway._client.pagination_revision
            self.record(
                "sm_close_barrier",
                {
                    "result": "SAFE_TO_CONTINUE",
                    "proofs": self.proofs,
                    "confirmed_fills": [asdict(f) for f in self.confirmed()],
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

    async def _wait_for_confirmation(self) -> bool:
        """After cancellation, keep the barrier closed while settlement catches up.

        No new BUY or repricing here. Stop at this market's trading cutoff plus
        the existing close buffer, or immediately on any other safety failure.
        """
        start = int(self.slug.rsplit("-", 1)[1])
        deadline = self.time_anchor + start + TRADING_END_S + CANCEL_BUFFER_S - self.official_time
        while (self.fatal is None and self.gateway._client.pagination_failure is None
               and self.pending_confirmations
               and all(a.outcome in {"ACK", "REJECTED", "ABORTED"} for a in self.attempts)
               and self.reason == ";".join(
                   f"{oid}:TRADE_CONFIRMATION_UNRESOLVED" for oid in sorted(self.pending_confirmations))
               and self.monotonic() < deadline):
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

    def _accept_placement(self, attempt, order, meta):
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
        self.record("sm_placement_ack", asdict(attempt))

    def _configured_quotes(self, meta, books, window_id, *, evidence=None):
        # Local import keeps the existing M3 -> M2 dependency at module load.
        from polymaker.rdp_sm_loop import make_quotes

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
                if quote_revision is not None:
                    _require(all(Decimal(str(q.price)) * Decimal(str(q.size)) >= MIN_MAKER_BUY_NOTIONAL
                                 for q in quotes), "MAKER_BUY_NOTIONAL_BELOW_MINIMUM")
                new = sum((Decimal(str(q.price)) * Decimal(str(q.size)) for q in quotes), Decimal(0))
                _require(self.committed() + new <= CAPITAL_LIMIT, "TEST_CAPITAL_LIMIT_EXCEEDED")
                _require(new <= Decimal(str(account["basic_spendable_budget_usd"])),
                         "OFFICIAL_CAPITAL_INSUFFICIENT")
                elapsed = self.official_time + self.monotonic() - self.time_anchor - start
                delta = sum((Decimal(str(f.size)) * (1 if f.token_id == meta.yes.token_id else -1)
                             for f in self.confirmed()), Decimal(0))
                cutoff = TRADING_END_S if inventory_trading_state(delta) == "REBALANCE" else NORMAL_END_S
                _require(TRADING_START_S <= elapsed < cutoff,
                         "MARKET_SUBMISSION_CUTOFF")
                if elapsed >= NORMAL_END_S:
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
                        and TRADING_START_S <= elapsed < cutoff
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
        self.closed = True
        self.reads.close()
        self.store.close()
        self.journal.close()

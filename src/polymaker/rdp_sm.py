"""One-shot RDP-SM read-only preflight. No Engine, execution or persistent state.

Reuse FL metadata/book/account readers; isolate their unsafe initialization paths.
READY is a snapshot, never trading authorization or an execution-window lease.
"""

from __future__ import annotations

import json
import math
import re
import time
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from decimal import Decimal
from enum import Enum
from time import monotonic
from typing import Any
from urllib.parse import urlsplit

from eth_account import Account
from eth_utils import is_address, to_checksum_address
from py_clob_client_v2.client import ClobClient

from polymaker.btc5m import PERIOD_S, slug_for_epoch
from polymaker.catalog.gamma import GammaClient, parse_market
from polymaker.config import Config
from polymaker.domain import MarketMeta, Side
from polymaker.execution.gateway import ExecutionGateway
from polymaker.favorite_live import collateral_readiness
from polymaker.marketdata.orderbook import OrderBook
from polymaker.marketdata.parse import parse_book
from polymaker.userstream.parse import strict_number

TEST_CAPITAL_LIMIT_USDC = 30.0
TRADING_START_S = 15
TRADING_END_S = 240
OFFICIAL_HOSTS = {
    "clob_host": "https://clob.polymarket.com",
    "gamma_host": "https://gamma-api.polymarket.com",
    "data_api_host": "https://data-api.polymarket.com",
}


class SessionStatus(str, Enum):
    READY = "SESSION_READY"
    REJECTED = "SESSION_REJECTED"
    BLOCKED = "SESSION_BLOCKED"


@dataclass(frozen=True)
class Check:
    name: str
    outcome: str
    detail: str


@dataclass
class PreflightResult:
    status: SessionStatus = SessionStatus.BLOCKED
    checked_at: float = field(default_factory=time.time)
    time_source: str = "LOCAL_UNVERIFIED"
    read_only: bool = True
    trading_authorized: bool = False
    test_capital_limit_usdc: float = TEST_CAPITAL_LIMIT_USDC
    market: dict[str, Any] | None = None
    account: dict[str, Any] | None = None
    checks: list[Check] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class _Issue(ValueError):
    def __init__(self, status: SessionStatus, code: str):
        super().__init__(code)
        self.status = status


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise _Issue(SessionStatus.BLOCKED, code)


def _token_id(value: Any) -> str:
    """Require one canonical positive uint256 identity; never discard aliases."""
    _require(
        isinstance(value, str)
        and re.fullmatch(r"[1-9][0-9]{0,77}", value) is not None
        and int(value) < 2**256,
        "TOKEN_IDENTITY_INVALID",
    )
    return value


class ReadOnlyClobClient(ClobClient):
    """Same installed FL SDK, with an explicit read-only transport boundary.

    No write methods/allowance-update GET can reach its transport. This is not
    paper mode and does not rely on Gateway.connect or a boolean trading flag.
    """

    _READ_PATHS = {
        "/auth/derive-api-key",
        "/balance-allowance",
        "/data/orders",
        "/book",
        "/tick-size",
        "/version",
        "/time",
    }

    def _get(self, endpoint, headers=None, params=None):
        parsed = urlsplit(endpoint)
        _require(
            f"{parsed.scheme}://{parsed.netloc}" == OFFICIAL_HOSTS["clob_host"]
            and parsed.path in self._READ_PATHS
            and not parsed.fragment,
            "UNAPPROVED_CLOB_READ",
        )
        if parsed.path == "/data/orders":
            cursor = (params or {}).get("next_cursor")
            seen = getattr(self, "_sm_order_cursors", set())
            _require(
                isinstance(cursor, str) and cursor not in seen, "OPEN_ORDER_PAGINATION_UNRESOLVED"
            )
            self._sm_order_cursors = seen | {cursor}
        raw = super()._get(endpoint, headers=headers, params=params)
        if parsed.path == "/data/orders":
            _require(
                isinstance(raw, dict)
                and isinstance(raw.get("data"), list)
                and isinstance(raw.get("next_cursor"), str)
                and bool(raw["next_cursor"]),
                "OPEN_ORDER_PAGE_UNKNOWN",
            )
            # Gateway defaults absent cumulative sizes to zero. SM cannot
            # silently discard another market's collateral commitment.
            _require(
                all(
                    isinstance(row, dict) and "original_size" in row and "size_matched" in row
                    for row in raw["data"]
                ),
                "OPEN_ORDER_SIZE_UNKNOWN",
            )
            # Validate before Gateway strips asset_id whitespace or compares it.
            for row in raw["data"]:
                _token_id(row.get("asset_id"))
        return raw

    def _post(self, *_args, **_kwargs):
        raise RuntimeError("SM_READ_ONLY: POST prohibited")

    def _delete(self, *_args, **_kwargs):
        raise RuntimeError("SM_READ_ONLY: DELETE prohibited")

    def get_version(self) -> int:
        # SDK get_version silently defaults to 2 on failed/malformed reads.
        # Preserve the existing spender resolver but make this read authoritative.
        raw = self._get(f"{self.host}/version")
        _require(
            isinstance(raw, dict) and type(raw.get("version")) is int and raw["version"] in (1, 2),
            "VERSION_UNKNOWN",
        )
        return raw["version"]


class _Reads:
    """Ephemeral Gateway read composition; never opens SQLite or live runtime."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.gateway = ExecutionGateway(cfg, paper=False)

    async def connect_account(self) -> None:
        cfg = self.cfg
        _require(
            cfg.secrets.has_wallet and cfg.wallet.signature_type in (0, 1, 2, 3),
            "ACCOUNT_CONFIGURATION_UNKNOWN",
        )
        _require(is_address(cfg.secrets.browser_address), "FUNDER_IDENTITY_INVALID")
        funder = to_checksum_address(cfg.secrets.browser_address)
        _require(int(funder, 16) > 0, "FUNDER_IDENTITY_INVALID")
        signer = Account.from_key(cfg.secrets.pk).address
        _require(cfg.wallet.signature_type != 0 or signer == funder, "EOA_FUNDER_IDENTITY_MISMATCH")

        def build():
            client = ReadOnlyClobClient(
                host=cfg.wallet.clob_host,
                chain_id=cfg.wallet.chain_id,
                key=cfg.secrets.pk,
                signature_type=cfg.wallet.signature_type,
                funder=funder,
                use_server_time=False,
            )
            # Existing API key only; no create/create_or_derive fallback.
            creds = client.derive_api_key()
            _require(
                all(
                    isinstance(value, str) and value.strip()
                    for value in (creds.api_key, creds.api_secret, creds.api_passphrase)
                ),
                "AUTH_IDENTITY_UNKNOWN",
            )
            client.set_api_creds(creds)
            _require(
                client.get_address() == signer and client.builder.funder == funder,
                "ACCOUNT_IDENTITY_MISMATCH",
            )
            return client, creds

        client, creds = await self.gateway._io(build)
        self.gateway._client, self.gateway._creds = client, creds
        self.gateway._address, self.gateway._funder = signer, funder

    async def market(self, slug: str):
        async with GammaClient(self.cfg.wallet.gamma_host) as gamma:
            return await gamma.market_by_slug(slug)

    async def book(self, token: str) -> tuple[dict[str, Any], Any]:
        client = self.gateway._client
        raw = await self.gateway._io(client.get_order_book, token)
        tick = await self.gateway._io(client.get_tick_size, token)
        return raw, tick

    async def server_time(self) -> float:
        raw = await self.gateway._io(self.gateway._client.get_server_time)
        stamp = strict_number(raw)
        _require(stamp > 0, "OFFICIAL_TIME_INVALID")
        return stamp

    def close(self) -> None:
        self.gateway.close()


def _market(raw: Any, slug: str) -> tuple[MarketMeta, int]:
    match = re.fullmatch(r"btc-updown-5m-([0-9]+)", slug)
    _require(match is not None, "BTC5M_SLUG_INVALID")
    start = int(match[1])
    _require(
        start > 0 and start % PERIOD_S == 0 and slug_for_epoch(start) == slug,
        "BTC5M_INTERVAL_INVALID",
    )
    _require(isinstance(raw, dict) and raw.get("slug") == slug, "MARKET_IDENTITY_UNKNOWN")
    cid = raw.get("conditionId")
    _require(
        isinstance(cid, str) and re.fullmatch(r"0x[0-9a-fA-F]{64}", cid) is not None,
        "CONDITION_ID_INVALID",
    )
    for key in ("active", "closed", "acceptingOrders", "enableOrderBook", "negRisk"):
        _require(type(raw.get(key)) is bool, f"MARKET_FLAG_UNKNOWN:{key}")
    if (
        not raw["active"]
        or raw["closed"]
        or not raw["acceptingOrders"]
        or not raw["enableOrderBook"]
    ):
        raise _Issue(SessionStatus.REJECTED, "MARKET_NOT_TRADABLE")
    _require(isinstance(raw.get("endDate"), str), "MARKET_END_UNKNOWN")
    end = datetime.fromisoformat(raw["endDate"].replace("Z", "+00:00"))
    _require(
        end.tzinfo is not None and end.timestamp() == start + PERIOD_S, "MARKET_DURATION_NOT_300S"
    )
    # Gamma startDate may be publication time; start is the exact official slug
    # epoch, independently checked against official endDate and 300s duration.
    if "eventStartTime" in raw:
        event_start = datetime.fromisoformat(raw["eventStartTime"].replace("Z", "+00:00"))
        _require(
            event_start.tzinfo is not None and event_start.timestamp() == start,
            "MARKET_START_CONTRADICTION",
        )
    for key in ("clobTokenIds", "outcomes"):
        values = json.loads(raw[key]) if isinstance(raw.get(key), str) else raw.get(key)
        _require(
            isinstance(values, list)
            and len(values) == 2
            and all(isinstance(v, str) and v.strip() == v and v for v in values),
            "TOKEN_MAPPING_UNKNOWN",
        )
    meta = parse_market(raw)
    _require(meta is not None, "MARKET_METADATA_INVALID")
    tokens = {t.outcome.casefold(): t for t in meta.tokens}
    _require(set(tokens) == {"up", "down"}, "UP_DOWN_MAPPING_UNKNOWN")
    yes, no = tokens["up"], tokens["down"]
    yes_id, no_id = _token_id(yes.token_id), _token_id(no.token_id)
    _require(
        yes_id != no_id,
        "TOKEN_IDS_INVALID",
    )
    minimum = strict_number(raw.get("orderMinSize"))
    _require(minimum > 0, "VENUE_MINIMUM_UNKNOWN")
    return replace(meta, tokens=(yes, no), min_order_size=minimum), start


def _book(raw: Any, tick_raw: Any, token: str, meta: MarketMeta) -> dict[str, Any]:
    from py_clob_client_v2.order_builder.builder import ROUNDING_CONFIG

    _require(
        isinstance(raw, dict)
        and _token_id(raw.get("asset_id")) == token
        and raw.get("market") == meta.condition_id,
        "BOOK_IDENTITY_INVALID",
    )
    tick = strict_number(tick_raw)
    _require(
        f"{tick:g}" in ROUNDING_CONFIG and strict_number(raw.get("tick_size")) == tick,
        "DYNAMIC_TICK_UNKNOWN_OR_CHANGED",
    )
    # FL parser intentionally skips bad levels; validate first so SM cannot
    # report READY from an incomplete/malformed official snapshot.
    for side in ("bids", "asks"):
        _require(isinstance(raw.get(side), list) and bool(raw[side]), "BOOK_SIDE_MISSING")
        for level in raw[side]:
            _require(isinstance(level, dict), "BOOK_LEVEL_INVALID")
            price, size = strict_number(level.get("price")), strict_number(level.get("size"))
            _require(0 < price < 1 and size > 0, "BOOK_LEVEL_INVALID")
    strict_number(raw.get("timestamp"))
    update = parse_book(raw)
    _require(update is not None and update.ts > 0, "BOOK_TIMESTAMP_UNKNOWN")
    book = OrderBook(tick_size=tick)
    book.apply_snapshot(update.bids, update.asks, update.ts, update.book_hash)
    view = book.view()
    _require(
        view.best_bid is not None and view.best_ask is not None and view.best_bid < view.best_ask,
        "BOOK_INVALID_OR_CROSSED",
    )
    return {
        "token_id": token,
        "best_bid": view.best_bid,
        "best_ask": view.best_ask,
        "tick": tick,
        "timestamp": update.ts,
    }


async def _account(reads: _Reads, meta: MarketMeta, market: dict[str, Any]) -> dict[str, Any]:
    gateway = reads.gateway
    positions = await gateway.positions()
    # Missing target keys mean zero only after validating returned identities.
    # Gateway preserves positive-position keys exactly, including whitespace.
    for token in positions:
        _token_id(token)
    orders = await gateway.open_orders()  # entire account, not just this market
    for order in orders:
        _token_id(order.token_id)
    spenders = await gateway.required_collateral_spenders(meta)
    _require(bool(spenders), "APPLICABLE_SPENDER_UNKNOWN")
    raw = await gateway.balance_allowance()
    _require(
        isinstance(raw, dict) and isinstance(raw.get("allowances"), dict), "CAPITAL_READ_UNKNOWN"
    )
    amounts = [raw.get("balance")]
    allowance_keys = [str(k).lower() for k in raw["allowances"]]
    _require(len(set(allowance_keys)) == len(allowance_keys), "ALLOWANCE_ALIASES_CONFLICT")
    allowances = {str(k).lower(): v for k, v in raw["allowances"].items()}
    amounts.extend(allowances.get(spender.lower()) for spender in spenders)
    for value in amounts:
        number = strict_number(value)
        _require(number >= 0 and number.is_integer(), "CAPITAL_MICRO_UNITS_INVALID")
    balance, allowance = collateral_readiness(raw, spenders)
    _require(balance is not None and allowance is not None, "CAPITAL_READ_UNKNOWN")
    # Reject duplicate identities; they would make reserved commitment unreliable.
    _require(len({o.order_id for o in orders}) == len(orders), "OPEN_ORDER_IDENTITY_CONFLICT")
    for order in orders:
        _require(
            math.isfinite(order.price)
            and 0 < order.price < 1
            and math.isfinite(order.size)
            and order.size >= 0,
            "OPEN_ORDER_COMMITMENT_UNKNOWN",
        )
    historical_reserved = Decimal(0)
    policy = getattr(reads, 'account_policy', None)
    if policy is not None:
        historical_reserved = await policy.inspect(reads, meta, positions, orders)
    reserved = sum(o.notional for o in orders if o.side is Side.BUY) + float(historical_reserved)
    _require(math.isfinite(reserved), "OPEN_ORDER_COMMITMENT_UNKNOWN")
    available = max(0.0, balance - reserved)
    spendable = max(0.0, allowance - reserved)
    # Necessary lower bound for two venue-minimum Maker BUYs at the smallest
    # legal prices, not quotes, a strategy price choice or a CapitalGate.
    minimum = meta.min_order_size * (market["yes"]["tick"] + market["no"]["tick"])
    _require(math.isfinite(minimum) and minimum > 0, "VENUE_MINIMUM_INVALID")
    return {
        "signer": gateway.address,
        "funder": gateway.funder,
        "signature_type": reads.cfg.wallet.signature_type,
        "identity_source": "locally derived signer; configured SDK funder; authenticated CLOB reads",
        "yes_inventory": positions.get(meta.yes.token_id, (0.0, 0.0))[0],
        "no_inventory": positions.get(meta.no.token_id, (0.0, 0.0))[0],
        "target_open_orders": sum(
            o.token_id in {meta.yes.token_id, meta.no.token_id} for o in orders
        ),
        "account_open_orders": len(orders),
        "official_balance_usd": balance,
        "applicable_allowance_usd": allowance,
        "applicable_spenders": list(spenders),
        "existing_buy_commitment_usd": reserved,
        "historical_pending_cost_reserved_usd": float(historical_reserved),
        "available_balance_usd": available,
        "available_balance_source": "official CLOB balance minus official open BUY commitment",
        "basic_spendable_budget_usd": min(TEST_CAPITAL_LIMIT_USDC, available, spendable),
        "minimum_pair_commitment_usd": minimum,
    }


async def run_preflight(cfg: Config, slug: str, *, _reads: _Reads | None = None) -> PreflightResult:
    """Read one selected market/account once; never submit, cancel or persist."""
    result = PreflightResult()
    reads = _reads if _reads is not None else _Reads(cfg)
    began = monotonic()
    stage = "configuration"
    try:
        _require(
            all(
                getattr(cfg.wallet, key).rstrip("/") == host for key, host in OFFICIAL_HOSTS.items()
            )
            and cfg.wallet.chain_id == 137,
            "OFFICIAL_ENDPOINT_CONFIGURATION_REQUIRED",
        )
        max_age = cfg.risk.ws_stale_halt_s
        _require(math.isfinite(max_age) and max_age > 0, "FRESHNESS_POLICY_INVALID")
        stage = "market"
        raw = await reads.market(slug)
        meta, start = _market(raw, slug)
        stage = "account_identity"
        await reads.connect_account()
        stage = "books"
        yes_raw, yes_tick = await reads.book(meta.yes.token_id)
        no_raw, no_tick = await reads.book(meta.no.token_id)
        result.market = {
            "slug": meta.slug,
            "condition_id": meta.condition_id,
            "start": start,
            "end": start + PERIOD_S,
            "duration_s": PERIOD_S,
            "tradable": True,
            "mapping_source": "Gamma outcomes Up=YES / Down=NO",
            "yes": _book(yes_raw, yes_tick, meta.yes.token_id, meta),
            "no": _book(no_raw, no_tick, meta.no.token_id, meta),
        }
        result.checks.append(Check("market", "PASS", "official identity, mapping, books and ticks"))
        stage = "account"
        result.account = await _account(reads, meta, result.market)
        result.checks.append(
            Check("account_reads", "PASS", "official authenticated reads complete")
        )
        account = result.account
        clean = (
            account["yes_inventory"] == 0
            and account["no_inventory"] == 0
            and account["target_open_orders"] == 0
        )
        result.checks.append(
            Check(
                "clean_market",
                "PASS" if clean else "REJECTED",
                "zero target inventory and no pre-existing target orders",
            )
        )
        eligible = account["basic_spendable_budget_usd"] >= account["minimum_pair_commitment_usd"]
        result.checks.append(
            Check(
                "capital",
                "PASS" if eligible else "REJECTED",
                "basic venue minimum within available balance, allowance and $30 cap",
            )
        )
        stage = "freshness"
        stamp = await reads.server_time()
        result.checked_at, result.time_source = stamp, "OFFICIAL_CLOB_TIME"
        _require(monotonic() - began <= max_age, "PREFLIGHT_SNAPSHOT_EXPIRED")
        for side in ("yes", "no"):
            _require(
                -2 <= stamp - result.market[side]["timestamp"] <= max_age, "BOOK_STALE_OR_FUTURE"
            )
        result.checks.append(
            Check("freshness", "PASS", "fresh snapshots against official CLOB time")
        )
        elapsed = stamp - start
        window = TRADING_START_S <= elapsed < TRADING_END_S
        result.market.update(
            elapsed_s=elapsed,
            trading_eligible=window,
            no_trading_final_seconds=PERIOD_S - TRADING_END_S,
        )
        result.checks.append(
            Check(
                "trading_window",
                "PASS" if window else "REJECTED",
                "15 <= elapsed < 240; M2 must recheck before execution",
            )
        )
        result.status = (
            SessionStatus.READY
            if all(c.outcome == "PASS" for c in result.checks)
            else SessionStatus.REJECTED
        )
    except _Issue as exc:
        result.status = exc.status
        result.checks.append(
            Check(stage, "BLOCKED" if exc.status is SessionStatus.BLOCKED else "REJECTED", str(exc))
        )
    except Exception as exc:  # malformed/failed reads are unknown, never clean zero
        result.status = SessionStatus.BLOCKED
        result.checks.append(Check(stage, "BLOCKED", f"READ_OR_PARSE_FAILED:{type(exc).__name__}"))
    finally:
        reads.close()
    return result

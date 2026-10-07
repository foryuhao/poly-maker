"""Read-only authenticated account evidence for the RDP preflight."""

from __future__ import annotations

import asyncio
import copy
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit

from polymaker.config import Config
from polymaker.domain import MarketMeta, Side
from polymaker.execution.gateway import ExecutionGateway
from polymaker.rdp_live import RDPBootstrap, RDPBootstrapError


class RDPAccountError(RDPBootstrapError):
    """A sanitized, categorized account-read failure."""


@dataclass(frozen=True, slots=True)
class RDPPositionEvidence:
    outcome: str
    token_id: str
    quantity: Decimal
    average_price: Decimal


@dataclass(frozen=True, slots=True)
class RDPOrderEvidence:
    order_id: str
    token_id: str
    side: str
    price: Decimal
    remaining_size: Decimal


@dataclass(frozen=True, slots=True)
class RDPAccountSnapshot:
    signer: str
    funder: str
    balance_usd: Decimal
    allowance_usd: Decimal
    required_spenders: tuple[str, ...]
    selected_positions: tuple[RDPPositionEvidence, RDPPositionEvidence]
    selected_open_orders: tuple[RDPOrderEvidence, ...]
    total_open_order_count: int
    orders_receipt_ts: float
    positions_receipt_ts: float
    collateral_receipt_ts: float


_OFFICIAL = {
    "clob": "https://clob.polymarket.com",
    "data": "https://data-api.polymarket.com",
}


def _origin(value: Any, official: str, label: str) -> str:
    if not isinstance(value, str):
        raise RDPAccountError("UNAPPROVED_ACCOUNT_ORIGIN", f"{label} API origin is invalid")
    try:
        parsed = urlsplit(value)
        expected = urlsplit(official)
        valid = (parsed.scheme == "https" and parsed.hostname == expected.hostname
                 and parsed.port is None and parsed.username is None and parsed.password is None
                 and parsed.path in ("", "/") and not parsed.query and not parsed.fragment)
    except ValueError:
        valid = False
    if not valid:
        raise RDPAccountError("UNAPPROVED_ACCOUNT_ORIGIN", f"{label} API origin is not approved")
    return official


def _identity(cfg: Config) -> tuple[str, str]:
    secrets = cfg.secrets
    if not secrets.pk or not secrets.browser_address:
        raise RDPAccountError("ACCOUNT_IDENTITY_MISSING", "configured signer and funder are required")
    if cfg.wallet.signature_type not in (0, 1, 2, 3):
        raise RDPAccountError("UNSUPPORTED_SIGNATURE_TYPE", "configured signature type is unsupported")
    try:
        from eth_account import Account
        from web3 import Web3

        signer = Account.from_key(secrets.pk).address
        if not Web3.is_address(secrets.browser_address):
            raise ValueError("invalid funder")
        funder = Web3.to_checksum_address(secrets.browser_address)
    except (ValueError, TypeError) as exc:
        raise RDPAccountError("ACCOUNT_IDENTITY_INVALID", "configured wallet identity is invalid") from exc
    if cfg.wallet.signature_type == 0 and signer.lower() != funder.lower():
        raise RDPAccountError("ACCOUNT_IDENTITY_MISMATCH", "signature type zero requires signer and funder agreement")
    return signer, funder


def _decimal(value: Any, *, field: str, nonnegative: bool = True) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise RDPAccountError("INVALID_ACCOUNT_EVIDENCE", f"account {field} is not numeric")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise RDPAccountError("INVALID_ACCOUNT_EVIDENCE", f"account {field} is malformed") from exc
    if not result.is_finite() or (nonnegative and result < 0):
        raise RDPAccountError("INVALID_ACCOUNT_EVIDENCE", f"account {field} is outside its valid range")
    return result


def _micro_usd(value: Any, *, field: str) -> Decimal:
    amount = _decimal(value, field=field)
    sign, digits, exponent = amount.as_tuple()
    # Shift the exponent directly so Decimal context precision cannot round a
    # large but valid raw six-decimal amount during division.
    return Decimal((sign, digits, exponent - 6))


def _selected_positions(raw: Any, market: MarketMeta) -> tuple[RDPPositionEvidence, RDPPositionEvidence]:
    if not isinstance(raw, Mapping):
        raise RDPAccountError("POSITIONS_UNAVAILABLE", "official positions read was unavailable")
    rows = []
    for token in market.tokens:
        value = raw.get(token.token_id, (0, 0))
        if not isinstance(value, (tuple, list)) or len(value) != 2:
            raise RDPAccountError("INVALID_POSITION_EVIDENCE", "position row is malformed")
        quantity = _decimal(value[0], field="position quantity")
        average_price = _decimal(value[1], field="position average price")
        if average_price > 1:
            raise RDPAccountError("INVALID_POSITION_EVIDENCE", "position average price is outside [0,1]")
        rows.append(RDPPositionEvidence(token.outcome, token.token_id, quantity, average_price))
    return rows[0], rows[1]


def _collateral(raw: Any, spenders: tuple[str, ...]) -> tuple[Decimal, Decimal]:
    if not isinstance(raw, Mapping) or "balance" not in raw:
        raise RDPAccountError("COLLATERAL_UNAVAILABLE", "collateral balance evidence is missing")
    balance = _micro_usd(raw["balance"], field="collateral balance")
    allowances = raw.get("allowances")
    if not isinstance(allowances, Mapping) or not spenders:
        raise RDPAccountError("ALLOWANCE_UNAVAILABLE", "required collateral allowance evidence is missing")

    normalized: dict[str, Decimal] = {}
    for key, value in allowances.items():
        if not isinstance(key, str) or not key.strip():
            raise RDPAccountError("INVALID_ALLOWANCE_EVIDENCE", "allowance spender identity is malformed")
        lowered = key.strip().lower()
        amount = _micro_usd(value, field="collateral allowance")
        if lowered in normalized and normalized[lowered] != amount:
            raise RDPAccountError("CONTRADICTORY_ALLOWANCE_EVIDENCE", "duplicate normalized spender entries disagree")
        normalized[lowered] = amount

    required = [normalized.get(spender.lower()) for spender in spenders]
    if any(value is None for value in required):
        raise RDPAccountError("ALLOWANCE_UNAVAILABLE", "a required collateral spender allowance is missing")
    return balance, min(value for value in required if value is not None)


def _credentials_complete(creds: Any) -> bool:
    values = tuple(getattr(creds, key, None) for key in ("api_key", "api_secret", "api_passphrase"))
    return all(isinstance(value, str) and bool(value.strip()) for value in values)


async def account_snapshot(
    cfg: Config,
    bootstrap: RDPBootstrap,
    *,
    now: Callable[[], float] = time.time,
    gateway_factory: Callable[..., ExecutionGateway] = ExecutionGateway,
) -> RDPAccountSnapshot:
    """Read immutable selected-market account evidence without account mutation."""
    # Freeze all runtime inputs before the first await. The gateway operates on
    # this copy even if the caller changes its configuration during a read.
    try:
        config = copy.deepcopy(cfg)
    except Exception as exc:  # noqa: BLE001
        raise RDPAccountError("ACCOUNT_CONFIGURATION_INVALID", "account configuration could not be captured") from exc
    clob_origin = _origin(config.wallet.clob_host, _OFFICIAL["clob"], "CLOB")
    data_origin = _origin(config.wallet.data_api_host, _OFFICIAL["data"], "Data API")
    config.wallet.clob_host = clob_origin
    config.wallet.data_api_host = data_origin
    signer, funder = _identity(config)
    if now() >= bootstrap.end_epoch:
        raise RDPAccountError("MARKET_INTERVAL_ELAPSED", "selected BTC5m interval ended before account reads")

    def ensure_market_open() -> None:
        if now() >= bootstrap.end_epoch:
            raise RDPAccountError("MARKET_INTERVAL_ELAPSED", "selected BTC5m interval ended during account reads")

    gateway = gateway_factory(config, paper=False)
    try:
        await gateway.connect(derive_only=True)
        if not _credentials_complete(gateway.creds):
            raise RDPAccountError("DERIVED_CREDENTIALS_INCOMPLETE", "read-only API credentials are incomplete")
        if gateway.address.lower() != signer.lower() or gateway.funder.lower() != funder.lower():
            raise RDPAccountError("ACCOUNT_IDENTITY_MISMATCH", "connected gateway identity differs from configuration")

        orders = await gateway.open_orders(strict_account=True)
        orders_ts = now()
        if orders_ts >= bootstrap.end_epoch:
            raise RDPAccountError("MARKET_INTERVAL_ELAPSED", "selected BTC5m interval ended during account reads")
        if not isinstance(orders, list):
            raise RDPAccountError("OPEN_ORDERS_UNAVAILABLE", "open order read was unavailable")
        token_ids = {token.token_id for token in bootstrap.market.tokens}
        selected_orders = []
        for order in orders:
            if not all((isinstance(getattr(order, "order_id", None), str)
                        and order.order_id.strip(),
                        isinstance(getattr(order, "token_id", None), str)
                        and order.token_id.strip(),
                        isinstance(getattr(order, "side", None), Side))):
                raise RDPAccountError("INVALID_OPEN_ORDER_EVIDENCE", "open order row is malformed")
            price = _decimal(order.price, field="open order price")
            size = _decimal(order.size, field="open order remaining size")
            if price > 1:
                raise RDPAccountError("INVALID_OPEN_ORDER_EVIDENCE", "open order price is outside [0,1]")
            if order.token_id in token_ids:
                selected_orders.append(RDPOrderEvidence(order.order_id, order.token_id, order.side.value,
                                                        price, size))

        positions_raw = await gateway.positions(strict_account=True)
        positions_ts = now()
        if positions_ts >= bootstrap.end_epoch:
            raise RDPAccountError("MARKET_INTERVAL_ELAPSED", "selected BTC5m interval ended during account reads")
        positions = _selected_positions(positions_raw, bootstrap.market)

        spenders_raw = await gateway.required_collateral_spenders(bootstrap.market)
        if (not isinstance(spenders_raw, tuple) or not spenders_raw
                or any(not isinstance(item, str) or not item.strip() for item in spenders_raw)
                or len({item.lower() for item in spenders_raw}) != len(spenders_raw)):
            raise RDPAccountError("REQUIRED_SPENDERS_UNRESOLVED", "required collateral spenders are unresolved")
        from web3 import Web3
        if any(not Web3.is_address(item) for item in spenders_raw):
            raise RDPAccountError("REQUIRED_SPENDERS_UNRESOLVED", "required collateral spender identity is invalid")
        spenders = tuple(item.strip() for item in spenders_raw)
        collateral_raw = await gateway.balance_allowance()
        collateral_ts = now()
        if collateral_ts >= bootstrap.end_epoch:
            raise RDPAccountError("MARKET_INTERVAL_ELAPSED", "selected BTC5m interval ended during account reads")
        balance, allowance = _collateral(collateral_raw, spenders)
        ensure_market_open()
        return RDPAccountSnapshot(signer, funder, balance, allowance, spenders, positions,
                                  tuple(selected_orders), len(orders), orders_ts, positions_ts,
                                  collateral_ts)
    except RDPAccountError:
        raise
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - never persist SDK/network exception text
        raise RDPAccountError("ACCOUNT_READ_FAILED", f"read-only account check failed ({type(exc).__name__})") from exc
    finally:
        drain = getattr(gateway, "aclose", None)
        if callable(drain):
            await drain()
        else:
            gateway.close()

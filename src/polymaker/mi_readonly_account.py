"""Official Polymarket account reads for MI observation; no order write methods.

The CLOB SDK object is private to this adapter. It is used only for GET auth,
open-order and collateral reads. MI runtime receives only AccountSnapshot.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx

from polymaker.config import Config
from polymaker.domain import MarketMeta


class AccountReadError(RuntimeError):
    """An official account read was unavailable or malformed."""


@dataclass(frozen=True, slots=True)
class AccountSnapshot:
    positions: dict[str, Decimal]
    open_order_tokens: tuple[str, ...]
    cash_available: Decimal


class OfficialReadOnlyAccount:
    def __init__(self, config: Config) -> None:
        self._config = config
        self._client: Any = None
        self._funder = config.secrets.browser_address

    async def connect(self) -> None:
        if not self._config.secrets.has_wallet:
            raise AccountReadError("official account credentials are unavailable")

        def build() -> Any:
            from py_clob_client_v2.client import ClobClient

            client = ClobClient(
                host=self._config.wallet.clob_host,
                chain_id=self._config.wallet.chain_id,
                key=self._config.secrets.pk,
                signature_type=self._config.wallet.signature_type,
                funder=self._funder,
            )
            # GET-only derivation. create_or_derive_api_key can issue a write.
            client.set_api_creds(client.derive_api_key())
            return client

        try:
            self._client = await asyncio.to_thread(build)
        except Exception as exc:
            raise AccountReadError("official CLOB read authentication failed") from exc

    async def snapshot(self, meta: MarketMeta) -> AccountSnapshot:
        if self._client is None:
            raise AccountReadError("official CLOB account reader is disconnected")

        def clob_reads() -> tuple[tuple[str, ...], Decimal]:
            from py_clob_client_v2.clob_types import AssetType, BalanceAllowanceParams
            from py_clob_client_v2.config import get_contract_config

            raw_orders = self._client.get_open_orders()
            rows = raw_orders if isinstance(raw_orders, list) else (
                raw_orders.get("data", raw_orders.get("orders"))
                if isinstance(raw_orders, dict) else None
            )
            if not isinstance(rows, list):
                raise AccountReadError("official open-order response is invalid")
            tokens: list[str] = []
            for row in rows:
                if not isinstance(row, dict) or not isinstance(row.get("asset_id"), str):
                    raise AccountReadError("official open-order row is invalid")
                tokens.append(row["asset_id"])

            raw_balance = self._client.get_balance_allowance(
                BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            )
            if not isinstance(raw_balance, dict):
                raise AccountReadError("official collateral response is invalid")
            try:
                balance = Decimal(str(raw_balance["balance"])) / Decimal("1000000")
                allowances = raw_balance["allowances"]
                if not isinstance(allowances, dict):
                    raise ValueError("allowances missing")
                contracts = get_contract_config(self._config.wallet.chain_id)
                version = self._client._ClobClient__resolve_version()  # noqa: SLF001
                if version == 2:
                    spender = contracts.neg_risk_exchange_v2 if meta.neg_risk else contracts.exchange_v2
                elif version == 1:
                    spender = contracts.neg_risk_exchange if meta.neg_risk else contracts.exchange
                else:
                    raise ValueError("unknown exchange version")
                normalized = {str(k).lower(): v for k, v in allowances.items()}
                allowance = Decimal(str(normalized[spender.lower()])) / Decimal("1000000")
            except (KeyError, ValueError, TypeError) as exc:
                raise AccountReadError("official collateral balance/allowance is invalid") from exc
            if not balance.is_finite() or not allowance.is_finite() or min(balance, allowance) < 0:
                raise AccountReadError("official collateral balance/allowance is invalid")
            return tuple(tokens), min(balance, allowance)

        async def data_positions() -> dict[str, Decimal]:
            if not self._funder or not self._funder.startswith("0x"):
                raise AccountReadError("funder identity unavailable")
            positions: dict[str, Decimal] = {}
            cursor: str | None = None
            seen: set[str] = set()
            async with httpx.AsyncClient(timeout=15.0) as http:
                while True:
                    params = {"user": self._funder}
                    if cursor is not None:
                        params["cursor"] = cursor
                    response = await http.get(
                        f"{self._config.wallet.data_api_host.rstrip('/')}/v2/positions",
                        params=params,
                    )
                    response.raise_for_status()
                    payload = response.json()
                    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
                        raise AccountReadError("official positions response is invalid")
                    page = payload.get("pagination")
                    if not isinstance(page, dict) or "next_cursor" not in page:
                        raise AccountReadError("official positions pagination is invalid")
                    for row in payload["data"]:
                        if not isinstance(row, dict) or not isinstance(row.get("token_id"), str):
                            raise AccountReadError("official position row is invalid")
                        try:
                            size = Decimal(str(row["current_size"]))
                        except (KeyError, ValueError, TypeError) as exc:
                            raise AccountReadError("official position size is invalid") from exc
                        if not size.is_finite() or size < 0:
                            raise AccountReadError("official position size is invalid")
                        if size > 0:
                            positions[row["token_id"]] = size
                    cursor = page["next_cursor"]
                    if cursor is None:
                        return positions
                    if not isinstance(cursor, str) or not cursor or cursor in seen:
                        raise AccountReadError("official positions pagination is invalid")
                    seen.add(cursor)

        try:
            (tokens, cash), positions = await asyncio.gather(
                asyncio.to_thread(clob_reads), data_positions()
            )
            return AccountSnapshot(positions, tokens, cash)
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise AccountReadError("official account snapshot failed") from exc

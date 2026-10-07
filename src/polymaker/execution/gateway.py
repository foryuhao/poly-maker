"""ExecutionGateway: the only component that sends actions to the CLOB.

Wraps the synchronous py-clob-client-v2 (which owns the hard V2 EIP-712 signing,
pUSD balance adjustment, and tick/fee caching) and offloads its blocking network
calls to a thread pool so the asyncio hot path never stalls. Every quote goes out
**post-only** (the maker-only mandate, enforced at the exchange).

A `paper=True` gateway shares the same path but fabricates order ids instead of
posting — so paper mode exercises the full pipeline.
"""

from __future__ import annotations

import asyncio
import itertools
import math
import time
import uuid
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from decimal import Decimal, InvalidOperation
from typing import Any, TypeVar, get_args

import httpx

from polymaker.config import Config
from polymaker.domain import MarketMeta, OpenOrder, OrderState, Quote, Side
from polymaker.execution.ratelimit import TokenBucket
from polymaker.journal import Journal
from polymaker.logging import get_logger
from polymaker.research.capture import CausalCapture

log = get_logger("execution.gateway")

_T = TypeVar("_T")


class GatewayReadError(RuntimeError):
    """An authoritative Gateway snapshot could not be fetched or parsed."""


class PlacementUnknown(RuntimeError):
    """A POST may have reached the exchange, but no usable order result arrived."""

    def __init__(self, message: str, *, acknowledged_orders: tuple[OpenOrder, ...] = (),
                 observed_order_ids: tuple[str, ...] = (), reason: str | None = None) -> None:
        super().__init__(message)
        self.acknowledged_orders = tuple(acknowledged_orders)
        self.observed_order_ids = tuple(observed_order_ids)
        self.reason = reason


class BatchPlacementIncomplete(PlacementUnknown):
    """A two-order placement had a known one-leg acceptance."""


def _normalize_place_response(raw: Any) -> dict[str, Any] | None:
    """Unwrap only the two supported placement envelope shapes."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, list) and len(raw) == 1 and isinstance(raw[0], dict):
        return raw[0]
    return None


_ORDER_ID_FIELDS = ("orderID", "order_id", "orderId", "id")


def _usable_order_ids(row: Mapping[str, Any]) -> tuple[str, ...]:
    found: list[str] = []
    for key in _ORDER_ID_FIELDS:
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            normalized = value.strip()
            if normalized not in found:
                found.append(normalized)
    return tuple(found)


def _observed_order_ids(raw: Any) -> tuple[str, ...]:
    """Preserve IDs visible in a response without claiming request attribution."""
    rows = raw if isinstance(raw, list) else [raw]
    found: list[str] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        for order_id in _usable_order_ids(row):
            if order_id not in found:
                found.append(order_id)
    return tuple(found)


def _tick_str(tick: float) -> str:
    return f"{tick:g}"


class ExecutionGateway:
    def __init__(
        self,
        cfg: Config,
        journal: Journal | None = None,
        *,
        paper: bool = False,
        research: CausalCapture | None = None,
    ) -> None:
        self._cfg = cfg
        self._paper = paper
        self._journal = journal
        self._research = research
        self._client: Any = None  # py_clob_client_v2.ClobClient
        self._creds: Any = None
        self._address: str = ""  # signer EOA
        self._funder: str = ""  # funds/positions live here (proxy/deposit wallet)
        self._data_host = cfg.wallet.data_api_host
        # rate budgets: fraction of documented POST/DELETE ceilings (per second)
        f = cfg.execution.rate_budget_fraction
        self._order_bucket = TokenBucket(rate_per_s=200.0 * f, burst=500.0 * f)
        self._cancel_bucket = TokenBucket(rate_per_s=200.0 * f, burst=500.0 * f)
        self._paper_ids = itertools.count(1)
        self._hb_id: str = ""  # heartbeat chain
        self._hb_failures: int = 0
        # dedicated, bounded pool for blocking order/HTTP calls so a burst of
        # requotes across many markets can't starve the default executor
        self._pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="clob-io")

    @property
    def paper(self) -> bool:
        return self._paper

    @property
    def order_pressure(self) -> float:
        """0 = plenty of order-post budget, 1 = about to queue (shed load)."""
        return self._order_bucket.pressure

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    async def _io(self, fn: Callable[..., _T], *args: Any) -> _T:
        """Run a blocking client call on the dedicated pool."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._pool, fn, *args)

    @property
    def creds(self) -> Any:
        return self._creds

    @property
    def address(self) -> str:
        """The signing EOA address."""
        return self._address

    @property
    def funder(self) -> str:
        """The address holding funds/positions (proxy/deposit wallet, or the EOA)."""
        return self._funder or self._address

    # ── lifecycle ───────────────────────────────────────────────────────
    async def connect(self) -> None:
        """Build the client and derive L2 API creds (network). No-op fields in paper."""
        sec = self._cfg.secrets
        if self._paper and not sec.has_wallet:
            # paper mode runs the full pipeline without a wallet (no orders posted)
            self._address = sec.browser_address or "0xPAPER"
            self._funder = sec.browser_address or self._address
            log.info("gateway_connected", address=self._address[:10], paper=True)
            return
        if not sec.has_wallet:
            raise RuntimeError("no wallet configured (set PK and BROWSER_ADDRESS in .env)")

        def _build() -> tuple[Any, Any, str]:
            from py_clob_client_v2.client import ClobClient

            client = ClobClient(
                host=self._cfg.wallet.clob_host,
                chain_id=self._cfg.wallet.chain_id,
                key=sec.pk,
                # use_server_time=False: fetching /time before EVERY signed order
                # adds a full round-trip per op (latency killer through a proxy).
                # We check clock drift once below and rely on the local (NTP) clock.
                signature_type=self._cfg.wallet.signature_type,
                funder=sec.browser_address,
            )
            creds = client.create_or_derive_api_key()
            client.set_api_creds(creds)
            return client, creds, client.get_address()

        self._client, self._creds, self._address = await self._io(_build)
        await self._check_clock_drift()
        # funds/positions live on the funder (proxy/deposit wallet); fall back to EOA
        self._funder = sec.browser_address or self._address
        log.info("gateway_connected", signer=self._address[:10], funder=self._funder[:10],
                 paper=self._paper)

    async def _check_clock_drift(self) -> None:
        """Warn once if the local clock is skewed vs the exchange (affects L2 auth)."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as c:
                r = await c.get(f"{self._cfg.wallet.clob_host}/time")
                server = float(r.text.strip().strip('"'))
            drift = abs(time.time() - server)
            if drift > 5.0:
                log.warning("clock_drift", drift_s=round(drift, 1),
                            note="sync system clock (NTP) — large skew can fail order auth")
            else:
                log.info("clock_ok", drift_s=round(drift, 1))
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("clock_check_failed", err=str(exc))

    # ── placement ───────────────────────────────────────────────────────
    async def place(
        self, quotes: list[Quote], meta: MarketMeta, *,
        submit_guard: Callable[[], bool] | None = None,
        force_post_only: bool = False,
        strict_placement: bool = False,
        allow_two_order_rdp_pair: bool = False,
        token_ticks: Mapping[str, int | float | Decimal] | None = None,
    ) -> list[OpenOrder]:
        signing_ticks: dict[str, str] | None = None
        if token_ticks is not None:
            quotes, signing_ticks = self._snapshot_token_tick_request(
                quotes, meta, token_ticks,
                force_post_only=force_post_only,
                strict_placement=strict_placement,
                allow_two_order_rdp_pair=allow_two_order_rdp_pair,
                submit_guard=submit_guard,
            )
        if allow_two_order_rdp_pair:
            self._validate_two_order_rdp_pair(
                quotes, meta, force_post_only=force_post_only,
                strict_placement=strict_placement)
        if not quotes:
            return []
        if any(q.size + 1e-9 < meta.min_order_size for q in quotes):
            log.warning("order_below_venue_minimum", cid=meta.condition_id[:8],
                        minimum=meta.min_order_size,
                        sizes=[q.size for q in quotes])
            return []
        await self._order_bucket.acquire(len(quotes))
        if submit_guard is not None and not submit_guard():
            log.info("order_place_aborted_stale_connectivity", cid=meta.condition_id[:8])
            return []
        ts = time.time()
        self._journal_write("orders_out", [asdict(q) for q in quotes], ts)

        if self._paper:
            placed = [self._paper_order(q) for q in quotes]
            if self._research is not None:
                paper_request_id = uuid.uuid4().hex
                self._order_observe("ORDER_SUBMIT", meta.condition_id, {"request_id": paper_request_id, "quotes": [self._research_quote(q) for q in quotes], "paper": True})
                self._order_observe("ORDER_ACK", meta.condition_id, {"request_id": paper_request_id, "order_ids": [o.order_id for o in placed], "outcome": "ACK", "paper": True})
            return placed

        attempted = False
        request_id = uuid.uuid4().hex if self._research is not None else None
        if request_id is not None:
            self._order_observe("ORDER_SUBMIT", meta.condition_id,
                {"request_id": request_id, "quotes": [self._research_quote(q) for q in quotes],
                 "paper": False})

        def _place() -> list[OpenOrder]:
            nonlocal attempted
            from py_clob_client_v2.clob_types import (
                OrderArgsV2,
                OrderType,
                PartialCreateOrderOptions,
                PostOrdersV2Args,
            )

            legacy_opts = (PartialCreateOrderOptions(
                tick_size=_tick_str(meta.tick_size), neg_risk=meta.neg_risk)
                if signing_ticks is None else None)
            args = []
            for q in quotes:
                opts = (legacy_opts if signing_ticks is None else PartialCreateOrderOptions(
                    tick_size=signing_ticks[q.token_id], neg_risk=meta.neg_risk))
                signed = self._client.create_order(
                    OrderArgsV2(token_id=q.token_id, price=q.price, size=q.size, side=q.side.value),
                    options=opts,
                )
                args.append(PostOrdersV2Args(order=signed, orderType=OrderType.GTC))
            if submit_guard is not None and not submit_guard():
                log.info("order_place_aborted_stale_connectivity", cid=meta.condition_id[:8])
                return []
            attempted = True
            try:
                resp = self._client.post_orders(
                    args, post_only=(True if force_post_only else self._cfg.execution.post_only)
                )
            except Exception as exc:
                reason = "BATCH_POST_EXCEPTION" if allow_two_order_rdp_pair else None
                raise PlacementUnknown("order POST outcome is unknown", reason=reason) from exc
            try:
                if allow_two_order_rdp_pair:
                    return self._parse_two_order_rdp_pair_response(resp, quotes)
                return self._parse_place_response(resp, quotes)
            except PlacementUnknown:
                raise
            except Exception as exc:
                raise PlacementUnknown("order POST response could not be parsed") from exc

        try:
            placed = await self._io(_place)
            if request_id is not None:
                self._order_observe("ORDER_ACK", meta.condition_id,
                    {"request_id": request_id, "order_ids": [o.order_id for o in placed],
                     "outcome": "ACK" if placed else "REJECTED" if attempted else "ABORTED"})
            return placed
        except PlacementUnknown as exc:
            payload = {"request_id": request_id, "outcome": "UNKNOWN"}
            if exc.reason is not None or exc.acknowledged_orders or exc.observed_order_ids:
                payload.update({
                    "reason": exc.reason,
                    "acknowledged_order_ids": tuple(o.order_id for o in exc.acknowledged_orders),
                    "observed_order_ids": exc.observed_order_ids,
                })
            self._order_observe("ORDER_ACK", meta.condition_id, payload)
            if strict_placement:
                raise
            log.error("place_outcome_unknown", n=len(quotes))
            return []
        except Exception as exc:  # noqa: BLE001 - surface + continue; engine handles error rate
            self._order_observe("ORDER_ACK", meta.condition_id, {"request_id": request_id, "outcome": "FAILED", "error_type": type(exc).__name__})
            log.error("place_failed", err=str(exc), n=len(quotes))
            return []

    def _paper_order(self, q: Quote) -> OpenOrder:
        oid = f"paper-{next(self._paper_ids)}"
        return OpenOrder(oid, q.token_id, q.side, q.price, q.size, OrderState.LIVE)

    @staticmethod
    def _snapshot_token_tick_request(
        quotes: list[Quote], meta: MarketMeta,
        token_ticks: Mapping[str, int | float | Decimal], *,
        force_post_only: bool, strict_placement: bool,
        allow_two_order_rdp_pair: bool,
        submit_guard: Callable[[], bool] | None,
    ) -> tuple[list[Quote], dict[str, str]]:
        """Copy and validate opt-in per-token signing inputs before any await."""
        if (force_post_only is not True or strict_placement is not True
                or not callable(submit_guard)):
            raise ValueError("per-token tick placement requires forced post-only, strict mode, and a submit guard")
        if not isinstance(meta, MarketMeta) or not isinstance(quotes, list):
            raise ValueError("per-token tick placement requires market metadata and a quote list")
        quote_snapshot = list(quotes)
        if len(quote_snapshot) == 1:
            quote = quote_snapshot[0]
            if (not isinstance(quote, Quote) or quote.side is not Side.BUY
                    or quote.token_id not in (meta.yes.token_id, meta.no.token_id)):
                raise ValueError("per-token tick single placement requires one market BUY quote")
        elif len(quote_snapshot) == 2 and allow_two_order_rdp_pair is True:
            ExecutionGateway._validate_two_order_rdp_pair(
                quote_snapshot, meta, force_post_only=force_post_only,
                strict_placement=strict_placement,
            )
        else:
            raise ValueError("per-token tick placement requires one BUY or an opted-in market pair")

        if not isinstance(token_ticks, Mapping):
            raise ValueError("per-token ticks must be a token-to-tick mapping")
        try:
            tick_snapshot = dict(token_ticks)
        except (RuntimeError, TypeError, ValueError) as exc:
            raise ValueError("per-token ticks could not be copied") from exc
        quote_tokens = [quote.token_id for quote in quote_snapshot]
        if (any(not isinstance(token, str) for token in quote_tokens)
                or len(set(quote_tokens)) != len(quote_tokens)):
            raise ValueError("per-token tick quotes must have unique string token ids")
        if set(tick_snapshot) != set(quote_tokens):
            raise ValueError("per-token tick keys must exactly match submitted quote tokens")

        from py_clob_client_v2.clob_types import TickSize

        supported_ticks = get_args(TickSize)
        validated_ticks: dict[str, str] = {}
        for token, value in tick_snapshot.items():
            if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
                raise ValueError(f"invalid tick value for token {token!r}")
            try:
                tick = Decimal(str(value))
            except (InvalidOperation, ValueError) as exc:
                raise ValueError(f"invalid tick value for token {token!r}") from exc
            if not tick.is_finite() or tick <= 0:
                raise ValueError(f"unsupported tick value for token {token!r}")
            sdk_tick = next((candidate for candidate in supported_ticks
                             if tick == Decimal(candidate)), None)
            if sdk_tick is None:
                raise ValueError(f"unsupported tick value for token {token!r}")
            validated_ticks[token] = sdk_tick

        for quote in quote_snapshot:
            if (isinstance(quote.price, bool)
                    or not isinstance(quote.price, (int, float, Decimal))):
                raise ValueError(f"invalid quote price for token {quote.token_id!r}")
            try:
                price = Decimal(str(quote.price))
            except (InvalidOperation, ValueError) as exc:
                raise ValueError(f"invalid quote price for token {quote.token_id!r}") from exc
            tick = Decimal(validated_ticks[quote.token_id])
            if (not price.is_finite() or price <= 0 or price >= 1
                    or price % tick != 0):
                raise ValueError(f"quote price is invalid or off-grid for token {quote.token_id!r}")
        return quote_snapshot, validated_ticks

    def _parse_place_response(self, resp: Any, quotes: list[Quote]) -> list[OpenOrder]:
        """Map a post_orders response to OpenOrders without guessing on shape."""
        normalized = _normalize_place_response(resp)
        # This client call submits one quote; a multi-result response cannot be
        # deterministically attributed to that placement.
        if normalized is None or len(quotes) != 1:
            log.warning(
                "place_response_invalid_shape",
                response_type=type(resp).__name__,
                list_length=len(resp) if isinstance(resp, list) else None,
                dict_keys=sorted(str(k) for k in resp) if isinstance(resp, dict) else None,
            )
            raise PlacementUnknown("order POST response omitted order results")
        out: list[OpenOrder] = []
        q, item = quotes[0], normalized
        if item.get("success") is False:
            return []  # explicit exchange rejection
        # orderID is the placement API field. Check other familiar spellings
        # only for contradictions; they are not accepted as placement aliases.
        usable_ids = {str(item[k]).strip() for k in ("orderID", "order_id", "orderId", "id")
                      if isinstance(item.get(k), str) and item[k].strip()}
        if len(usable_ids) > 1:
            raise PlacementUnknown("order POST response contained conflicting order ids")
        oid = item.get("orderID")
        if not isinstance(oid, str) or not oid.strip():
            log.warning(
                "place_response_missing_id",
                response_type=type(resp).__name__,
                list_length=len(resp) if isinstance(resp, list) else None,
                dict_keys=sorted(str(k) for k in item),
                success=item.get("success"),
                success_type=type(item.get("success")).__name__,
                status=item.get("status"),
                order_id_type=type(oid).__name__,
                order_id_length=len(oid) if isinstance(oid, str) else None,
            )
            raise PlacementUnknown("order POST response had no usable order id")
        out.append(OpenOrder(oid.strip(), q.token_id, q.side, q.price, q.size, OrderState.LIVE))
        return out

    @staticmethod
    def _validate_two_order_rdp_pair(
        quotes: list[Quote], meta: MarketMeta, *, force_post_only: bool,
        strict_placement: bool,
    ) -> None:
        if (not isinstance(quotes, list) or len(quotes) != 2
                or not isinstance(meta, MarketMeta)
                or force_post_only is not True or strict_placement is not True):
            raise ValueError("RDP pair placement requires two quotes, strict mode, and forced post-only")
        if any(not isinstance(quote, Quote) or quote.side is not Side.BUY for quote in quotes):
            raise ValueError("RDP pair placement requires BUY quotes")
        if (not isinstance(meta.yes.token_id, str) or not isinstance(meta.no.token_id, str)
                or any(not isinstance(quote.token_id, str) for quote in quotes)):
            raise ValueError("RDP pair placement requires string outcome token ids")
        tokens = {quote.token_id for quote in quotes}
        expected = {meta.yes.token_id, meta.no.token_id}
        if (len(expected) != 2 or tokens != expected):
            raise ValueError("RDP pair placement requires one quote per market outcome token")

    @staticmethod
    def _parse_two_order_rdp_pair_response(
        resp: Any, quotes: list[Quote],
    ) -> list[OpenOrder]:
        """Parse only the official positional two-result batch envelope."""
        observed = _observed_order_ids(resp)
        if not isinstance(resp, list) or len(resp) != 2:
            raise PlacementUnknown(
                "two-order POST response did not contain two positional results",
                observed_order_ids=observed, reason="BATCH_CARDINALITY_UNKNOWN")

        classifications: list[tuple[str, str | None, tuple[str, ...]]] = []
        for row in resp:
            if not isinstance(row, Mapping):
                classifications.append(("unknown", None, ()))
                continue
            ids = _usable_order_ids(row)
            canonical = row.get("orderID")
            canonical_id = canonical.strip() if isinstance(canonical, str) and canonical.strip() else None
            if row.get("success") is True and canonical_id is not None and len(ids) <= 1:
                classifications.append(("accepted", canonical_id, ids))
            elif row.get("success") is False and not ids:
                classifications.append(("rejected", None, ()))
            else:
                classifications.append(("unknown", None, ids))

        accepted_ids = [order_id for state, order_id, _ids in classifications
                        if state == "accepted" and order_id is not None]
        duplicate_ids = {order_id for order_id in accepted_ids if accepted_ids.count(order_id) > 1}
        unsafe_ids = set(duplicate_ids)
        for state, _order_id, ids in classifications:
            if state == "unknown":
                unsafe_ids.update(ids)

        acknowledged: list[OpenOrder] = []
        for index, (state, order_id, _ids) in enumerate(classifications):
            if state == "accepted" and order_id is not None and order_id not in unsafe_ids:
                quote = quotes[index]
                acknowledged.append(OpenOrder(order_id, quote.token_id, quote.side,
                                              quote.price, quote.size, OrderState.LIVE))

        if duplicate_ids:
            raise PlacementUnknown(
                "two-order POST response reused an accepted order id",
                acknowledged_orders=tuple(acknowledged), observed_order_ids=observed,
                reason="DUPLICATE_ACCEPTED_ID")
        if any(state == "unknown" for state, _order_id, _ids in classifications):
            raise PlacementUnknown(
                "two-order POST response contains an unresolved result",
                acknowledged_orders=tuple(acknowledged), observed_order_ids=observed,
                reason="UNRESOLVED_BATCH_RESULT")
        accepted_count = sum(state == "accepted" for state, _order_id, _ids in classifications)
        rejected_count = sum(state == "rejected" for state, _order_id, _ids in classifications)
        if accepted_count == 2:
            return acknowledged
        if rejected_count == 2:
            return []
        raise BatchPlacementIncomplete(
            "two-order POST was partially accepted",
            acknowledged_orders=tuple(acknowledged), observed_order_ids=observed,
            reason="PARTIAL_ACCEPTANCE")


    # ── cancellation ────────────────────────────────────────────────────
    async def cancel(self, order_ids: list[str]) -> bool:
        """Cancel by id. Returns True on success — callers must NOT drop the
        orders from local state on failure (they may still be live)."""
        if not order_ids or self._paper:
            return True
        await self._cancel_bucket.acquire(1)
        request_id = uuid.uuid4().hex if self._research is not None else None
        self._order_observe("CANCEL_REQUEST", "", {"request_id": request_id, "order_ids": tuple(order_ids), "scope": "IDS"})

        def _cancel() -> None:
            self._client.cancel_orders(order_ids)

        try:
            await self._io(_cancel)
            self._order_observe("CANCEL_ACK", "", {"request_id": request_id, "order_ids": tuple(order_ids), "outcome": "ACK"})
            return True
        except Exception as exc:  # noqa: BLE001
            self._order_observe("CANCEL_ACK", "", {"request_id": request_id, "order_ids": tuple(order_ids), "outcome": "UNKNOWN", "error_type": type(exc).__name__})
            log.error("cancel_failed", err=str(exc), n=len(order_ids))
            return False

    async def cancel_asset(self, asset_id: str) -> bool:
        """Cancel every order on one token (idempotent quarantine primitive)."""
        if self._paper:
            return True
        request_id = uuid.uuid4().hex if self._research is not None else None
        self._order_observe("CANCEL_REQUEST", "", {"request_id": request_id, "token_id": asset_id, "scope": "ASSET"})

        def _cancel() -> None:
            from py_clob_client_v2.clob_types import OrderMarketCancelParams

            self._client.cancel_market_orders(OrderMarketCancelParams(asset_id=asset_id))

        try:
            await self._io(_cancel)
            self._order_observe("CANCEL_ACK", "", {"request_id": request_id, "token_id": asset_id, "outcome": "ACK"})
            return True
        except Exception as exc:  # noqa: BLE001
            self._order_observe("CANCEL_ACK", "", {"request_id": request_id, "token_id": asset_id, "outcome": "UNKNOWN", "error_type": type(exc).__name__})
            log.error("cancel_asset_failed", err=str(exc), token=asset_id[:12])
            return False

    async def cancel_market_asset(self, market: str, asset_id: str) -> bool:
        """Cancel orders scoped to one market and asset, for uncertain placement cleanup."""
        if self._paper:
            return True
        request_id = uuid.uuid4().hex if self._research is not None else None
        self._order_observe("CANCEL_REQUEST", market, {"request_id": request_id, "token_id": asset_id, "scope": "MARKET_ASSET"})

        def _cancel() -> None:
            from py_clob_client_v2.clob_types import OrderMarketCancelParams

            self._client.cancel_market_orders(
                OrderMarketCancelParams(market=market, asset_id=asset_id)
            )

        try:
            await self._io(_cancel)
            self._order_observe("CANCEL_ACK", market, {"request_id": request_id, "token_id": asset_id, "outcome": "ACK"})
            return True
        except Exception as exc:  # noqa: BLE001
            self._order_observe("CANCEL_ACK", market, {"request_id": request_id, "token_id": asset_id, "outcome": "UNKNOWN", "error_type": type(exc).__name__})
            log.error("cancel_market_asset_failed", err=str(exc), market=market[:12],
                      token=asset_id[:12])
            return False

    async def cancel_all(self) -> None:
        if self._paper or self._client is None:
            return
        request_id = uuid.uuid4().hex if self._research is not None else None
        self._order_observe("CANCEL_REQUEST", "", {"request_id": request_id, "scope": "ALL"})
        try:
            await self._io(self._client.cancel_all)
        except BaseException:
            self._order_observe("CANCEL_ACK", "", {"request_id": request_id, "scope": "ALL", "outcome": "UNKNOWN"})
            raise
        self._order_observe("CANCEL_ACK", "", {"request_id": request_id, "scope": "ALL", "outcome": "ACK"})
        log.info("cancel_all_sent")

    def _order_observe(self, kind: str, cid: str, payload: dict[str, Any]) -> None:
        if self._research is not None:
            self._research.emit(kind, cid, payload)

    @staticmethod
    def _research_quote(quote: Quote) -> dict[str, Any]:
        return {"token_id": quote.token_id, "side": quote.side.value,
                "price": quote.price, "size": quote.size}

    # ── market (taker) orders — used by moneydoctor, NOT the maker strategy ──
    async def market_order(
        self, token_id: str, side: Side, amount: float, meta: MarketMeta,
        *, fak: bool = True,
    ) -> dict[str, Any]:
        """Place a marketable order. amount = USD for BUY, shares for SELL.

        This is a TAKER order (crosses the spread) — only the moneydoctor live
        self-test uses it; the maker strategy never does.
        """
        if self._paper or self._client is None:
            return {"paper": True}

        def _do() -> dict[str, Any]:
            from py_clob_client_v2.clob_types import (
                MarketOrderArgsV2,
                OrderType,
                PartialCreateOrderOptions,
            )

            ot = OrderType.FAK if fak else OrderType.FOK
            args = MarketOrderArgsV2(token_id=token_id, amount=amount,
                                     side=side.value, order_type=ot)
            opts = PartialCreateOrderOptions(tick_size=_tick_str(meta.tick_size),
                                             neg_risk=meta.neg_risk)
            try:
                resp = self._client.create_and_post_market_order(args, opts, order_type=ot)
                return resp if isinstance(resp, dict) else {"resp": resp}
            except Exception as exc:  # noqa: BLE001 - surface as data, never crash the caller
                return {"status": "failed", "error": str(exc)}

        return await self._io(_do)

    async def get_book(self, token_id: str) -> dict[str, float]:
        """Live best bid/ask + touch depth for one token (public REST)."""
        try:
            async with httpx.AsyncClient(timeout=15.0) as c:
                r = await c.get(f"{self._cfg.wallet.clob_host}/book",
                                params={"token_id": token_id})
                r.raise_for_status()
                b = r.json()
                bids = [(float(x["price"]), float(x["size"])) for x in b.get("bids", [])]
                asks = [(float(x["price"]), float(x["size"])) for x in b.get("asks", [])]
                best_bid = max(bids)[0] if bids else 0.0
                best_ask = min(asks)[0] if asks else 1.0
                ask_depth = sum(s for p, s in asks if p <= best_ask + 1e-9)
                bid_depth = sum(s for p, s in bids if p >= best_bid - 1e-9)
                return {"best_bid": best_bid, "best_ask": best_ask,
                        "ask_depth": ask_depth, "bid_depth": bid_depth}
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            log.warning("get_book_failed", err=str(exc))
            return {}

    async def get_full_book(
        self, token_id: str
    ) -> tuple[list[tuple[float, float]], list[tuple[float, float]], str | None] | None:
        """Full L2 book (bids, asks, hash) via public REST — for periodic
        integrity refresh against the WS book."""
        try:
            async with httpx.AsyncClient(timeout=15.0) as c:
                r = await c.get(f"{self._cfg.wallet.clob_host}/book",
                                params={"token_id": token_id})
                r.raise_for_status()
                b = r.json()
                bids = [(float(x["price"]), float(x["size"])) for x in b.get("bids", [])]
                asks = [(float(x["price"]), float(x["size"])) for x in b.get("asks", [])]
                return bids, asks, b.get("hash")
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            log.warning("get_full_book_failed", err=str(exc))
            return None

    async def token_balance(self, token_id: str) -> float:
        """Exact on-chain conditional-token balance (shares) held by the funder.

        Returns None on total RPC failure so callers can distinguish "0 shares"
        from "couldn't read".
        """
        bal = await self._token_balance_opt(token_id)
        return bal if bal is not None else 0.0

    async def _token_balance_opt(self, token_id: str) -> float | None:
        def _read() -> float | None:
            from web3 import Web3
            from web3.middleware import ExtraDataToPOAMiddleware

            configured = self._cfg.secrets.polygon_rpc or self._cfg.wallet.polygon_rpc
            rpcs = [configured, "https://polygon-bor-rpc.publicnode.com",
                    "https://polygon.llamarpc.com", "https://rpc.ankr.com/polygon"]
            abi = [{"name": "balanceOf", "type": "function", "stateMutability": "view",
                    "inputs": [{"name": "a", "type": "address"}, {"name": "id", "type": "uint256"}],
                    "outputs": [{"name": "", "type": "uint256"}]}]
            for rpc in dict.fromkeys(rpcs):  # dedupe, keep order
                try:
                    w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 15}))
                    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
                    ctf = w3.eth.contract(
                        address=Web3.to_checksum_address("0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"),
                        abi=abi,
                    )
                    raw = ctf.functions.balanceOf(
                        Web3.to_checksum_address(self.funder), int(token_id)
                    ).call()
                    return float(raw) / 1e6
                except Exception:  # noqa: BLE001, PERF203 - try next RPC
                    continue
            return None

        try:
            return await self._io(_read)
        except Exception as exc:  # noqa: BLE001
            log.warning("token_balance_failed", err=str(exc))
            return None

    async def token_balances(self, token_ids: list[str]) -> dict[str, float] | None:
        """Batch on-chain balances for several tokens in one RPC session.

        Used by the position-divergence monitor. Returns None on RPC failure.
        """
        if not token_ids:
            return {}

        def _read() -> dict[str, float] | None:
            from web3 import Web3
            from web3.middleware import ExtraDataToPOAMiddleware

            configured = self._cfg.secrets.polygon_rpc or self._cfg.wallet.polygon_rpc
            rpcs = [configured, "https://polygon-bor-rpc.publicnode.com",
                    "https://polygon.llamarpc.com", "https://rpc.ankr.com/polygon"]
            abi = [{"name": "balanceOf", "type": "function", "stateMutability": "view",
                    "inputs": [{"name": "a", "type": "address"}, {"name": "id", "type": "uint256"}],
                    "outputs": [{"name": "", "type": "uint256"}]}]
            funder = None
            for rpc in dict.fromkeys(rpcs):
                try:
                    w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 20}))
                    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
                    ctf = w3.eth.contract(
                        address=Web3.to_checksum_address("0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"),
                        abi=abi,
                    )
                    funder = Web3.to_checksum_address(self.funder)
                    out: dict[str, float] = {}
                    for tid in token_ids:
                        raw = ctf.functions.balanceOf(funder, int(tid)).call()
                        out[tid] = float(raw) / 1e6
                    return out
                except Exception:  # noqa: BLE001, PERF203
                    continue
            return None

        try:
            return await self._io(_read)
        except Exception as exc:  # noqa: BLE001
            log.warning("token_balances_failed", err=str(exc))
            return None

    async def collateral_balance(self) -> float:
        """pUSD balance (float) on the funder."""
        ba = await self.balance_allowance()
        for k in ("balance", "collateral", "amount"):
            if isinstance(ba, dict) and k in ba:
                try:
                    v = float(ba[k])
                    return v / 1e6 if v > 1e6 else v
                except (ValueError, TypeError):
                    return 0.0
        return 0.0

    # ── heartbeat (dead-man switch) ─────────────────────────────────────
    async def heartbeat(self) -> bool:
        """Send one chained heartbeat. Returns True on success.

        The exchange expects each heartbeat to carry the previous heartbeat_id.
        Consecutive failures are tracked in `heartbeat_failures`: after enough
        misses the exchange auto-cancels ALL our orders, so the engine must
        stop quoting and resync once the heartbeat recovers.
        """
        if self._paper or self._client is None:
            return True

        def _beat() -> Any:
            return self._client.post_heartbeat(self._hb_id)

        try:
            resp = await self._io(_beat)
            new_id = _first(resp, "heartbeat_id", "heartbeatId", "id")
            self._hb_id = str(new_id) if new_id else ""
            if self._hb_failures:
                log.info("heartbeat_recovered", after_failures=self._hb_failures)
            self._hb_failures = 0
            return True
        except Exception as exc:  # noqa: BLE001
            self._hb_failures += 1
            self._hb_id = ""  # broken chain — restart it
            log.warning("heartbeat_failed", err=str(exc), consecutive=self._hb_failures)
            return False

    @property
    def heartbeat_failures(self) -> int:
        return self._hb_failures

    # ── reads ───────────────────────────────────────────────────────────
    async def open_orders(self) -> list[OpenOrder]:
        if self._paper:
            return []
        if self._client is None:
            raise GatewayReadError("open orders unavailable: CLOB client is not connected")

        def _get() -> list[OpenOrder]:
            from polymaker.userstream.parse import strict_number

            raw = self._client.get_open_orders()
            if isinstance(raw, list):
                rows = raw
            elif isinstance(raw, dict):
                rows = raw.get("data", raw.get("orders"))
            else:
                raise GatewayReadError("open orders returned an invalid response")
            if not isinstance(rows, list):
                raise GatewayReadError("open orders response omitted its order list")
            out = []
            for r in rows:
                try:
                    if not isinstance(r, dict):
                        raise TypeError("order row is not an object")
                    order_id = _first(r, "id", "orderID", "order_id")
                    if not isinstance(order_id, str) or not order_id.strip():
                        raise ValueError("order row has no valid exchange order id")
                    asset_id = r["asset_id"]
                    if not isinstance(asset_id, str) or not asset_id.strip():
                        raise ValueError("order row has no valid asset id")
                    side = Side(str(r["side"]).upper())
                    original_size = strict_number(r.get("original_size", r.get("size", 0)))
                    size_matched = strict_number(r.get("size_matched", 0))
                    if original_size < 0 or size_matched < 0 or size_matched > original_size:
                        raise ValueError("order row has invalid cumulative size")
                    remaining = original_size - size_matched
                    price = strict_number(r["price"])
                    out.append(
                        OpenOrder(
                            order_id.strip(),
                            asset_id.strip(),
                            side,
                            price,
                            remaining,
                            OrderState.LIVE,
                        )
                    )
                except (KeyError, ValueError, TypeError) as exc:
                    raise GatewayReadError("open orders contained an invalid order row") from exc
            return out

        try:
            return await self._io(_get)
        except Exception as exc:  # noqa: BLE001
            log.warning("open_orders_failed", err=str(exc))
            if isinstance(exc, GatewayReadError):
                raise
            raise GatewayReadError("open orders snapshot failed") from exc

    async def positions(self) -> dict[str, tuple[float, float]]:
        """{token_id: (size, avg_price)} from the data API (reconcile use).

        Queries the FUNDER (where positions live), not the signer EOA.
        """
        if self._paper:
            return {}
        user = self.funder
        if not user or not user.startswith("0x"):
            raise GatewayReadError("positions unavailable: funder address is unknown")
        try:
            from polymaker.userstream.parse import strict_number

            async with httpx.AsyncClient(timeout=15.0) as c:
                url = f"{self._data_host.rstrip('/')}/v2/positions"
                positions: dict[str, tuple[float, float]] = {}
                cursor: str | None = None
                seen_cursors: set[str] = set()
                while True:
                    params: dict[str, str] = {"user": user}
                    if cursor is not None:
                        params["cursor"] = cursor
                    r = await c.get(url, params=params)
                    r.raise_for_status()
                    payload = r.json()
                    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
                        raise GatewayReadError("positions response omitted its v2 data list")
                    pagination = payload.get("pagination")
                    if not isinstance(pagination, dict) or "next_cursor" not in pagination:
                        raise GatewayReadError("positions response omitted its v2 pagination cursor")
                    for row in payload["data"]:
                        if not isinstance(row, dict):
                            raise GatewayReadError("positions response contained an invalid row")
                        token_id = row.get("token_id")
                        if not isinstance(token_id, str) or not token_id.strip():
                            raise GatewayReadError("positions row omitted its token_id")
                        try:
                            size = strict_number(row["current_size"])
                            avg_price = strict_number(row["avg_price"])
                        except (KeyError, TypeError, ValueError) as exc:
                            raise GatewayReadError("positions row omitted valid current_size/avg_price") from exc
                        if not math.isfinite(size) or not math.isfinite(avg_price) or size < 0:
                            raise GatewayReadError("positions row contained invalid position values")
                        if size > 0:
                            positions[token_id] = (size, avg_price)
                    next_cursor = pagination["next_cursor"]
                    if next_cursor is None:
                        return positions
                    if not isinstance(next_cursor, str) or not next_cursor or next_cursor in seen_cursors:
                        raise GatewayReadError("positions response contained an invalid pagination cursor")
                    seen_cursors.add(next_cursor)
                    cursor = next_cursor
        except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
            log.warning("positions_failed", err=str(exc))
            raise GatewayReadError("positions snapshot failed") from exc
        except GatewayReadError:
            raise

    async def get_order(self, order_id: str) -> dict[str, Any]:
        """Strict authenticated CLOB order read; malformed/failed is UNKNOWN."""
        if self._paper or self._client is None:
            raise GatewayReadError("order read unavailable")
        def _get() -> dict[str, Any]:
            row = self._client.get_order(order_id)
            if not isinstance(row, dict) or not row:
                raise GatewayReadError("order read returned malformed data")
            oid = row.get("id", row.get("orderID", row.get("order_id")))
            if not isinstance(oid, str) or oid.strip() != order_id:
                raise GatewayReadError("order read returned a different order")
            from polymaker.userstream.parse import normalize_order
            if normalize_order(row) is None:
                raise GatewayReadError("order read omitted valid cumulative order fields")
            return row
        try:
            return await self._io(_get)
        except Exception as exc:
            if isinstance(exc, GatewayReadError):
                raise
            raise GatewayReadError("order read failed") from exc

    async def get_trades(self, *, market: str | None = None, asset_id: str | None = None) -> list[dict[str, Any]]:
        """Strict authenticated trade history read using supported V2 filters."""
        if self._paper or self._client is None:
            raise GatewayReadError("trade read unavailable")
        def _get() -> list[dict[str, Any]]:
            from py_clob_client_v2.clob_types import TradeParams
            params = TradeParams(market=market, asset_id=asset_id)
            rows = self._client.get_trades(params)
            if not isinstance(rows, list) or any(not isinstance(x, dict) for x in rows):
                raise GatewayReadError("trade history returned malformed data")
            return rows
        try:
            return await self._io(_get)
        except Exception as exc:
            if isinstance(exc, GatewayReadError):
                raise
            raise GatewayReadError("trade history read failed") from exc

    async def balance_allowance(self) -> dict[str, Any]:
        """Collateral balance/allowance snapshot (for `doctor`)."""
        if self._client is None:
            return {}

        def _get() -> dict[str, Any]:
            from py_clob_client_v2.clob_types import AssetType, BalanceAllowanceParams

            result: dict[str, Any] = self._client.get_balance_allowance(
                BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            )
            return result

        try:
            return await self._io(_get)
        except Exception as exc:  # noqa: BLE001
            log.warning("balance_allowance_failed", err=str(exc))
            return {}

    async def required_collateral_spenders(self, meta: MarketMeta) -> tuple[str, ...]:
        """Resolve the exchange used by this client's signed BUY order path."""
        if self._client is None:
            return ()
        from py_clob_client_v2.config import get_contract_config

        def _resolve() -> tuple[str, ...]:
            # create_order resolves the same cached version and uses neg_risk
            # from the market-specific options passed by place().
            version = self._client._ClobClient__resolve_version()  # noqa: SLF001
            contracts = get_contract_config(self._client.chain_id)
            if version == 2:
                return (contracts.neg_risk_exchange_v2 if meta.neg_risk else contracts.exchange_v2,)
            if version == 1:
                return (contracts.neg_risk_exchange if meta.neg_risk else contracts.exchange,)
            return ()

        try:
            return await self._io(_resolve)
        except Exception:  # noqa: BLE001 - unknown spender fails preflight closed
            return ()

    def _journal_write(self, kind: str, payload: Any, ts: float) -> None:
        if self._journal is not None:
            self._journal.write(kind, payload, ts)


def _first(d: Any, *keys: str) -> Any:
    if not isinstance(d, dict):
        return None
    for k in keys:
        if k in d and d[k]:
            return d[k]
    return None

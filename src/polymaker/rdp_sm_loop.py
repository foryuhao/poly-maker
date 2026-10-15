"""Single BTC5m strategy composition; dedicated production entry, offline default."""

from __future__ import annotations

import asyncio
import math
import time
from dataclasses import asdict, dataclass, field
from decimal import ROUND_CEILING, Decimal

import httpx
from py_clob_client_v2.http_helpers import helpers

from polymaker.config import Config
from polymaker.domain import Side
from polymaker.rdp_sm import (
    NORMAL_END_S,
    TRADING_END_S,
    TRADING_START_S,
    SessionStatus,
    _Issue,
    _require,
    inventory_trading_state,
    run_preflight,
)
from polymaker.rdp_sm_execution import (
    MIN_MAKER_BUY_NOTIONAL,
    MIN_MAKER_BUY_SHARES,
    SAFE_QUOTE_SKIP_REASONS,
    InventoryMismatch,
    SMWindowExecutor,
    WindowQuote,
    _execution_identity,
    _ExecutionReads,
    maker_buy_size,
)
from polymaker.rdp_sm_recovery import (
    UNCERTAIN_MUTATIONS,
    pause_reason,
    preflight_pause,
    reason_code,
    retry_read,
)


class QuoteUnavailable(ValueError):
    """Known quote illegality; no placement or exposure was created."""


def make_quotes(meta, books, yes: Decimal, no: Decimal, window_id: str):
    delta = yes - no
    state = inventory_trading_state(delta)
    if state == "NORMAL":
        sizes = {"YES": Decimal(5) + max(-delta, 0), "NO": Decimal(5) + max(delta, 0)}
        # Confirmed fills may carry finer precision (e.g. 4.98410 shares).
        # Preserve inventory exactly; round only submitted sizes to the SDK grid.
        sizes = {side: size.quantize(Decimal(".01"), rounding=ROUND_CEILING)
                 for side, size in sizes.items()}
    else:
        sizes = {"NO" if delta > 0 else "YES": abs(delta)}
    quotes = []
    for outcome, size in sizes.items():
        token = meta.yes.token_id if outcome == "YES" else meta.no.token_id
        book = books[token]
        tick, bid, ask = (Decimal(str(book[k])) for k in ("tick", "best_bid", "best_ask"))
        price = bid - tick if state == "NORMAL" else bid
        if (
            not all(n.is_finite() for n in (tick, bid, ask, price, size))
            or tick <= 0
            or not tick <= price <= 1 - tick
            or price % tick != 0
            or price >= ask
        ):
            raise QuoteUnavailable("QUOTE_PRICE_TICK_OR_SIZE_INVALID")
        if state == "REBALANCE":
            size = maker_buy_size(price, size, Decimal(str(meta.min_order_size)))
        if (size < max(MIN_MAKER_BUY_SHARES, Decimal(str(meta.min_order_size)))
                or size % Decimal(".01") != 0):
            raise QuoteUnavailable("QUOTE_PRICE_TICK_OR_SIZE_INVALID")
        if state == "NORMAL" and price * size < MIN_MAKER_BUY_NOTIONAL:
            raise QuoteUnavailable("NORMAL_LEG_NOTIONAL_BELOW_MINIMUM")
        quotes.append(
            WindowQuote(
                meta.condition_id, token, outcome, Side.BUY, float(price), float(size), window_id
            )
        )
    return state, quotes


@dataclass
class RunResult:
    market_id: str | None
    session_id: str
    start_time: float = field(default_factory=time.time)
    stop_time: float | None = None
    stop_reason: str | None = None
    total_windows: int = 0
    normal_windows: int = 0
    rebalance_windows: int = 0
    active_cycles: int = 0
    confirmed_fills: int = 0
    merged_pairs: float = 0
    recovered_collateral_usdc: float = 0
    blocked_windows: int = 0
    confirmed_yes: float = 0
    confirmed_no: float = 0
    delta: float = 0
    completed_pairs: float = 0
    residual_shares: float = 0
    average_yes_cost: float | None = None
    average_no_cost: float | None = None
    theoretical_pair_pnl: float | str = 0
    final_close_barrier: str = "BLOCKED"
    execution_status: str = "BLOCKED"
    live_authority: str = "NOT_VERIFIED_FOR_LIVE"
    windows: list[dict] = field(default_factory=list)
    skipped_quotes: list[dict] = field(default_factory=list)
    stop_elapsed_s: float | None = None

    def to_dict(self):
        return asdict(self)


class SMTradingLoop:
    """One market, one run; M1 opens the real M2 executor, M2 owns all orders.

    run() defaults to a blocked result and preserves its offline MockTransport
    boundary. The dedicated CLI calls run_production once for the discovered
    current market. No authorization check is added inside a fill window.
    """

    def __init__(
        self,
        cfg: Config,
        slug: str,
        session_id: str,
        *,
        sleep=asyncio.sleep,
        monotonic=time.monotonic,
        observer=None,
    ):
        self.cfg, self.slug, self.session_id = cfg, slug, session_id
        self.sleep, self.monotonic = sleep, monotonic
        self.executor = None
        self.started = False
        self.production = False
        self.expected_market = None
        self.observer = observer
        self.market_deadline = None
        self.pause_reason = None
        self.pause_stage = None
        self.merge_disabled_reason = None

    def _estimated_elapsed(self):
        return 300 - (self.market_deadline - self.monotonic()) if self.market_deadline is not None else None

    async def _pause(self, result, reason, stage, *, elapsed=None, context=None):
        elapsed = self._estimated_elapsed() if elapsed is None else elapsed
        _require(elapsed is not None, "RECOVERY_TIME_UNAVAILABLE")
        self.pause_reason, self.pause_stage = reason, stage
        result.stop_elapsed_s = elapsed
        if self.executor is not None and self.executor.meta is not None:
            self._inventory(result)
        if self.observer is not None:
            self.observer.emit("recovery", {"status": "PAUSED", "reason": reason,
                                           "elapsed_s": elapsed, "stage": stage,
                                           "context": context})
        if elapsed < TRADING_END_S:
            await self.sleep(min(2, TRADING_END_S - elapsed))

    def _resume(self, stage):
        if self.pause_reason is not None and self.pause_stage == stage:
            if self.observer is not None:
                self.observer.emit("recovery", {"status": "RESUMED", "reason": self.pause_reason,
                    "elapsed_s": self._estimated_elapsed(), "stage": stage})
            self.pause_reason = self.pause_stage = None

    async def _merge(self, result, elapsed=None):
        if not self.production or self.merge_disabled_reason is not None:
            return False
        from polymaker.rdp_sm_merge import merge_confirmed_pairs

        try:
            merged = await merge_confirmed_pairs(self.executor, elapsed_s=elapsed,
                                                on_progress=self._display_events)
        except Exception as exc:
            reason = str(exc)
            if (self.executor.fatal in UNCERTAIN_MUTATIONS or pause_reason(reason)
                    or not self.executor.merge_pending and retry_read(exc)):
                await self._pause(result, self.executor.fatal or reason_code(exc), "close")
                return False
            if reason.startswith("MERGE_PREPARE_FAILED:") or reason in {"MERGE_FAILED", "MERGE_NOT_SUBMITTED"}:
                # No accepted unknown mutation. Keep the actual inventory cost,
                # disable settlement for this run, and let safe REBALANCE proceed.
                self.merge_disabled_reason = reason
                if self.observer is not None:
                    self.observer.emit("recovery", {"status": "MERGE_DISABLED", "reason": reason,
                                                   "elapsed_s": self._estimated_elapsed(), "stage": "merge"})
                return False
            raise
        if merged:
            self._resume("capital")
        return merged

    def _display_events(self, decision=None, window=None):
        if self.observer is not None and self.executor is not None:
            events = self.executor.display_events
            self.executor.display_events = []
            if decision is not None:
                events.insert(0, ("sm_decision_book", decision))
            if window is not None:
                # A failed final Journal write can change the returned barrier.
                # Display the same WindowResult as JSON, even if no event saved.
                events = [(kind, data) for kind, data in events if kind != "sm_window_result"]
                events.append(("sm_window_result", window))
            self.observer.emit("events", events)

    async def _offline_boundary(self):
        _require(
            isinstance(helpers._http_client._transport, httpx.MockTransport),
            "M3_LIVE_EXECUTION_NOT_AUTHORIZED",
        )
        async with httpx.AsyncClient() as client:
            _require(
                isinstance(client._transport, httpx.MockTransport),
                "M3_LIVE_EXECUTION_NOT_AUTHORIZED",
            )

    def _inventory(self, result):
        executor = self.executor
        fills = executor.confirmed()
        yes = sum(
            (Decimal(str(f.size)) for f in fills if f.token_id == executor.meta.yes.token_id),
            Decimal(0),
        )
        no = sum(
            (Decimal(str(f.size)) for f in fills if f.token_id == executor.meta.no.token_id),
            Decimal(0),
        )
        yes -= executor.merged_pairs
        no -= executor.merged_pairs
        result.merged_pairs = result.recovered_collateral_usdc = float(executor.merged_pairs)
        result.confirmed_yes, result.confirmed_no = float(yes), float(no)
        result.delta, result.completed_pairs = float(yes - no), float(min(yes, no) + executor.merged_pairs)
        result.residual_shares, result.confirmed_fills = float(abs(yes - no)), len(fills)
        owners = {a.order_id: a.quote.window_id for a in executor.attempts if a.order_id}
        result.active_cycles = len({owners[f.maker_order_id] for f in fills})
        costs = []
        for token, qty in ((executor.meta.yes.token_id, yes), (executor.meta.no.token_id, no)):
            legs = [f for f in fills if f.token_id == token]
            if not all(math.isfinite(f.price) and f.price > 0 and f.size > 0 for f in legs):
                costs.append(None)
            else:
                cost = sum((Decimal(str(f.price)) * Decimal(str(f.size)) for f in legs), Decimal(0))
                cost -= executor.merged_cost.get(token, Decimal(0))
                costs.append(cost / qty if qty else None)
        result.average_yes_cost, result.average_no_cost = (
            float(cost) if cost is not None else None for cost in costs
        )
        result.theoretical_pair_pnl = (
            0
            if min(yes, no) == 0
            else float(min(yes, no) * (1 - sum(costs)))
            if all(c is not None for c in costs)
            else "NOT_VERIFIED"
        )
        return yes, no

    async def _run_session(self, result, fill_window_sec):
        executor = self.executor
        failed = False
        market_start = int(self.slug.rsplit("-", 1)[1])
        waiting = False
        while True:
            if not self.production:
                await self._offline_boundary()
            try:
                now = await executor._await_owned(executor.reads.server_time())
            except Exception as exc:
                if not retry_read(exc):
                    raise
                if self._estimated_elapsed() is not None and self._estimated_elapsed() >= TRADING_END_S:
                    failed, result.stop_reason = True, reason_code(exc)
                    result.stop_elapsed_s = self._estimated_elapsed()
                    break
                await self._pause(result, reason_code(exc), "read")
                continue
            result.stop_elapsed_s = now - market_start
            if now - market_start >= TRADING_END_S:
                failed = executor.inventory_mismatch is not None or self.pause_reason is not None
                result.stop_reason = ("ACCOUNT_INVENTORY_NOT_EXPLAINED_BY_SM" if executor.inventory_mismatch
                                      else self.pause_reason or "MARKET_CUTOFF")
                break
            if executor.fatal is not None and executor.fatal not in UNCERTAIN_MUTATIONS:
                _require(False, executor.fatal)
            if (executor.attempts or self.pause_stage == "close") and not executor.safe_to_continue:
                safe = await executor.verify_close(cancel=True)
                if not safe:
                    if executor.fatal not in UNCERTAIN_MUTATIONS and not pause_reason(executor.reason):
                        _require(False, executor.fatal or executor.reason or "M2_PREVIOUS_WINDOW_BLOCKED")
                    await self._pause(result, executor.fatal or executor.reason, "close")
                    continue
                self._resume("close")
            # Reuse M2's official market/account/freshness snapshot and managed SDK reads.
            inventory_waiting = executor.inventory_mismatch is not None
            try:
                async with executor.lock:
                    meta, start, books, _account = await executor._await_owned(
                        executor._snapshot(include_books=False))
            except InventoryMismatch:
                await self._wait_for_inventory(result)
                continue
            except Exception as exc:
                if not retry_read(exc):
                    raise
                await self._pause(result, reason_code(exc), "close" if not executor.safe_to_continue else "read")
                continue
            self._resume("read")
            if self.expected_market is not None:
                _require(meta.condition_id == self.expected_market.condition_id
                         and meta.tokens == self.expected_market.tokens, "DISCOVERED_MARKET_CHANGED")
            if executor.attempts and not executor.safe_to_continue:
                if executor.fatal is not None and executor.fatal not in UNCERTAIN_MUTATIONS:
                    _require(False, executor.fatal)
                await self._pause(result, executor.reason or "M2_PREVIOUS_EVIDENCE_CHANGED", "close")
                continue
            elapsed = executor.official_time + executor.monotonic() - executor.time_anchor - start
            result.stop_elapsed_s = elapsed
            if inventory_waiting and self.observer is not None:
                self.observer.emit("inventory_check", {**executor.inventory_observation,
                    "status": "RESUMED", "elapsed_s": elapsed})
            yes, no = self._inventory(result)
            normal = inventory_trading_state(yes - no) == "NORMAL"
            if elapsed >= TRADING_END_S:
                result.stop_reason = "MARKET_CUTOFF"
                break
            if await self._merge(result, elapsed):
                self._inventory(result)
                self._display_events()
                # Reread the Data API after a burn before planning another BUY.
                continue
            if executor.fatal in UNCERTAIN_MUTATIONS or self.pause_stage == "close":
                continue
            if elapsed < TRADING_START_S or (normal and elapsed >= NORMAL_END_S):
                # Hold balanced inventory until market end without requiring a
                # quotable late-market book or creating any planned order.
                if not waiting and self.observer is not None:
                    self.observer.emit("phase", {"elapsed_s": elapsed,
                                                 "state": "WAIT_FOR_MARKET_END_NORMAL"})
                waiting = True
                await self.sleep(1)
                continue
            waiting = False
            try:
                async with executor.lock:
                    books = await executor._await_owned(executor._read_books(meta))
            except Exception as exc:
                if not retry_read(exc):
                    raise
                await self._pause(result, reason_code(exc), "books", context=getattr(exc, "context", None))
                continue
            self._resume("books")
            window_id = f"{self.session_id}:w{result.total_windows + 1}"
            try:
                state, quotes = make_quotes(meta, books, yes, no, window_id)
                for quote in quotes:
                    executor._validate(quote, window_id, books, meta)
            except QuoteUnavailable as exc:
                result.skipped_quotes.append({"elapsed_s": elapsed, "reason": str(exc)})
                executor.record("sm_quote_skipped", result.skipped_quotes[-1])
                self._display_events()
                await self.sleep(1)
                continue
            _require((state == "NORMAL" and {q.outcome for q in quotes} == {"YES", "NO"}
                      and len(quotes) == 2)
                     or (state == "REBALANCE" and len(quotes) == 1),
                     "STRATEGY_ORDER_SIDES_INVALID")
            executor.record(
                "sm_strategy_window",
                {
                    "window_id": window_id,
                    "state": state,
                    "delta": str(yes - no),
                    "elapsed_s": elapsed,
                    "quotes": [asdict(q) for q in quotes],
                },
            )
            def fresh_quotes(fresh_meta, fresh_books, fresh_window_id, *, yes=yes, no=no, state=state):
                fresh_state, fresh = make_quotes(fresh_meta, fresh_books, yes, no, fresh_window_id)
                _require(fresh_state == state, "STRATEGY_STATE_CHANGED")
                return fresh

            window = await executor.execute_window(
                window_id,
                quotes,
                fill_window_sec=fill_window_sec,
                quote_revision=executor.revision,
                quote_factory=fresh_quotes,
            )
            result.total_windows += 1
            result.normal_windows += state == "NORMAL"
            result.rebalance_windows += state == "REBALANCE"
            result.windows.append(
                {"state": state, "elapsed_s": elapsed, "result": window.to_dict()}
            )
            self._inventory(result)
            # Preserve the existing Journal bytes. Only after M2 has closed
            # do we hand off the exact books used to make these planned quotes.
            if self.observer is not None:
                self._display_events({"inventory_yes": yes, "inventory_no": no,
                                      "books": {"YES": books[meta.yes.token_id],
                                                "NO": books[meta.no.token_id]}},
                                     result.windows[-1]["result"])
            if window.close_barrier != "SAFE_TO_CONTINUE":
                result.blocked_windows += 1
                reason = window.block_reason or "M2_WINDOW_BLOCKED"
                if executor.fatal not in UNCERTAIN_MUTATIONS and not pause_reason(reason):
                    result.stop_reason, failed = reason, True
                    break
                stage = "capital" if reason in {"OFFICIAL_CAPITAL_INSUFFICIENT", "TEST_CAPITAL_LIMIT_EXCEEDED"} else "close"
                await self._pause(result, executor.fatal or reason, stage)
                continue
            if (not window.placement_results and executor.inventory_mismatch is not None
                    and window.block_reason == "ACCOUNT_INVENTORY_NOT_EXPLAINED_BY_SM"):
                await self._wait_for_inventory(result)
                continue
            if window.placement_results:
                self._resume("capital")
            if (not window.placement_results
                    and window.block_reason in SAFE_QUOTE_SKIP_REASONS):
                result.skipped_quotes.append({"elapsed_s": elapsed, "reason": window.block_reason})
                # No HTTP submission occurred. M2 freshly proved the old orders
                # closed; the next iteration takes a new decision snapshot.
                await self.sleep(1)
        safe = await executor.verify_close(cancel=True)
        if safe and self.production and executor.inventory_mismatch is None:
            await self._merge(result)
            safe = executor.safe_to_continue
        result.final_close_barrier = "SAFE_TO_CONTINUE" if safe else "BLOCKED"
        if not safe:
            result.stop_reason = executor.reason or executor.fatal or "M2_CLOSE_BARRIER_BLOCKED"
        elif self.merge_disabled_reason is not None:
            failed, result.stop_reason = True, self.merge_disabled_reason
        self._inventory(result)
        result.execution_status = "INCOMPLETE" if not safe else "BLOCKED" if failed else "COMPLETED"

    async def _wait_for_inventory(self, result):
        """No BUY or Merge while Data API inventory is unexplained; retry until T=300."""
        executor = self.executor
        now = await executor._await_owned(executor.reads.server_time())
        elapsed = now - int(self.slug.rsplit("-", 1)[1])
        result.stop_elapsed_s = elapsed
        self._inventory(result)
        if self.observer is not None:
            self.observer.emit("inventory_check", {**executor.inventory_mismatch,
                "status": "BLOCKED_WAIT_UNTIL_300", "elapsed_s": elapsed})
        if elapsed < TRADING_END_S:
            await self.sleep(min(1, TRADING_END_S - elapsed))

    async def run(self, *, offline: bool = False, fill_window_sec: float = 3.0) -> RunResult:
        return await self._run(offline=offline, fill_window_sec=fill_window_sec)

    async def run_production(self, *, expected_market, account_policy) -> RunResult:
        # This method is invoked only by the dedicated, explicit one-market entry.
        return await self._run(production=True, expected_market=expected_market,
                               account_policy=account_policy, fill_window_sec=3.0)

    async def _run(self, *, offline=False, production=False, expected_market=None,
                   account_policy=None, fill_window_sec=3.0) -> RunResult:
        result = RunResult(None, self.session_id)
        if self.started:
            result.stop_reason = "M3_RUN_ALREADY_USED"
            result.stop_time = time.time()
            return result
        self.started = True
        try:
            self.production, self.expected_market = production, expected_market
            if not production:
                _require(offline is True, "M3_LIVE_EXECUTION_NOT_AUTHORIZED")
                await self._offline_boundary()
            _require(math.isfinite(fill_window_sec) and fill_window_sec >= 3, "FILL_WINDOW_INVALID")
            # Before T+15 M1 reports a clean but time-ineligible session. Wait
            # without opening an executor or creating any order state.
            startup_reads = None
            while True:
                startup_reads = _ExecutionReads(self.cfg, risk_reads=startup_reads,
                                                account_policy=account_policy)
                preflight = await run_preflight(self.cfg, self.slug, _reads=startup_reads)
                if self.observer is not None:
                    self.observer.emit("preflight", preflight.to_dict())
                if preflight.account is not None:
                    result.live_authority = _execution_identity(startup_reads)
                if preflight.market and "elapsed_s" in preflight.market:
                    self.market_deadline = self.monotonic() + TRADING_END_S - preflight.market["elapsed_s"]
                if preflight.market:
                    result.market_id = preflight.market["condition_id"]
                    if expected_market is not None:
                        _require(result.market_id == expected_market.condition_id
                                 and preflight.market['yes']['token_id'] == expected_market.yes.token_id
                                 and preflight.market['no']['token_id'] == expected_market.no.token_id,
                                 'DISCOVERED_MARKET_CHANGED')
                if preflight.status is SessionStatus.READY:
                    self._resume("startup")
                    break
                time_only = (
                    preflight.market is not None
                    and preflight.time_source == "OFFICIAL_CLOB_TIME"
                    and all(
                        c.outcome == "PASS" or c.name == "trading_window" for c in preflight.checks
                    )
                )
                if not time_only:
                    if (preflight_pause(preflight) and self.market_deadline is not None
                            and self._estimated_elapsed() < TRADING_END_S):
                        await self._pause(result, "M1_SESSION_NOT_READY", "startup")
                        continue
                    result.stop_elapsed_s = self._estimated_elapsed()
                    raise _Issue(SessionStatus.BLOCKED, "M1_SESSION_NOT_READY",
                                 context={"preflight": preflight.to_dict()})
                elapsed = preflight.market["elapsed_s"]
                result.stop_elapsed_s = elapsed
                if elapsed >= TRADING_END_S:
                    client = startup_reads.gateway._client
                    _require(
                        not client.observed_open_rows
                        and not client.observed_trade_legs
                        and client.pagination_failure is None,
                        "PREFLIGHT_RISK_UNRESOLVED_AT_CUTOFF",
                    )
                    result.stop_reason = "MARKET_CUTOFF_NO_EXECUTION"
                    result.execution_status = "COMPLETED"
                    result.final_close_barrier = "SAFE_TO_CONTINUE"
                    return result
                await self.sleep(min(1, max(0.01, 15 - elapsed)))
            while True:
                try:
                    self.executor = await SMWindowExecutor.open(
                        self.cfg, self.slug, self.session_id, risk_reads=startup_reads,
                        sleep=self.sleep, monotonic=self.monotonic,
                        display_events=[] if self.observer is not None else None)
                    self._resume("startup")
                    break
                except Exception as exc:
                    preflight_detail = (getattr(exc, "context", None) or {}).get("preflight")
                    retry = (str(exc) == "M1_SESSION_NOT_READY" and preflight_detail
                             and preflight_pause(preflight_detail)) or retry_read(exc)
                    if not retry or self.market_deadline is None or self._estimated_elapsed() >= TRADING_END_S:
                        raise
                    await self._pause(result, reason_code(exc), "startup")
            result.live_authority = (
                "NOT_VERIFIED_FOR_LIVE"
                if self.cfg.wallet.signature_type == 2
                else "LOCAL_EOA_KEY_VERIFIED"
            )
            await self._run_session(result, fill_window_sec)
        except (Exception, asyncio.CancelledError) as exc:
            result.stop_reason = str(exc) or type(exc).__name__
            if self.observer is not None:
                self.observer.emit("diagnostic", {"reason": result.stop_reason,
                                                  "context": getattr(exc, "context", None)})
            if self.executor is not None:
                if isinstance(exc, asyncio.CancelledError):
                    self.executor.fatal = "ABNORMAL_EXIT"
                safe = await self.executor._await_owned(
                    self.executor.verify_close(cancel=True), propagate_cancel=False
                )
                result.final_close_barrier = "SAFE_TO_CONTINUE" if safe else "BLOCKED"
                if self.executor.meta is not None:
                    self._inventory(result)
                result.execution_status = "BLOCKED" if safe else "INCOMPLETE"
        finally:
            result.stop_time = time.time()
            if self.executor is not None:
                try:
                    self.executor.record("sm_run_result", result.to_dict())
                except Exception:
                    result.execution_status = "INCOMPLETE"
                    result.final_close_barrier = "BLOCKED"
                    result.stop_reason = "JOURNAL_WRITE_FAILED"
                self.executor.close()
                self._display_events()
        return result

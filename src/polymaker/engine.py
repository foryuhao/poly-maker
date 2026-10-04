"""Engine: wires every component into a single async event loop.

Data flow per market:
  market WS -> OrderBook -> (wake) -> Quoter task -> strategy (pure) -> reconcile
  -> ExecutionGateway ; user WS -> StateStore ; periodic REST reconcile + heartbeat.

One lightweight quoter task per market, woken by book/fill events and debounced.
The strategy layer is pure; the engine owns all the state and I/O around it.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from dataclasses import asdict, replace
from datetime import datetime
from enum import Enum
from typing import Any

from polymaker.alerts import Alerter
from polymaker.catalog.gamma import GammaClient, fetch_reward_rates, parse_market
from polymaker.catalog.store import CatalogStore
from polymaker.config import Config, StrategyProfile
from polymaker.domain import Fill, MarketMeta, Regime, Side, TargetQuotes
from polymaker.execution.filltruth import ReconciliationOutcome, reconcile_owned_order
from polymaker.execution.gateway import ExecutionGateway
from polymaker.execution.reconciler import reconcile
from polymaker.journal import Journal
from polymaker.logging import get_logger
from polymaker.marketdata.parse import TradePrint
from polymaker.marketdata.service import MarketDataService
from polymaker.merge import Merger
from polymaker.research.capture import CausalCapture, clock
from polymaker.research.identity import (
    ResearchConfigIdentity,
    research_config_identity,
    validate_research_config_identity,
)
from polymaker.research.lifecycle import EpisodeLifecycle
from polymaker.research.observer import ResearchObserver
from polymaker.risk.manager import RiskManager
from polymaker.state.store import StateStore
from polymaker.state.tracker import UserEventProcessor
from polymaker.strategy.estimators import (
    FlowEstimator,
    MarketEstimators,
    MarkoutTracker,
    VolEstimator,
)
from polymaker.strategy.favorite_first import (
    FavoriteFirstConfig,
    StrategyMemory,
    StrategySnapshot,
    StrategyState,
)
from polymaker.strategy.favorite_first import (
    evaluate as evaluate_favorite,
)
from polymaker.strategy.quoting import QuoteInputs, compute_fair_value, construct_quotes
from polymaker.strategy.regime import RegimeInputs, RegimeMachine
from polymaker.userstream.client import UserStream

log = get_logger("engine")


class Engine:
    def __init__(self, cfg: Config, *, paper: bool = False,
                 favorite_first: FavoriteFirstConfig | None = None,
                 market_start_ts: float | None = None,
                 research_observer: ResearchObserver | None = None,
                 research_session_id: str | None = None,
                 research_config_identity: ResearchConfigIdentity | None = None) -> None:
        if favorite_first is not None and market_start_ts is None:
            raise ValueError("Favorite-First requires the exact market start timestamp")
        self.cfg = cfg
        self.paper = paper
        self._favorite_bootstrap = (favorite_first, market_start_ts)
        self._favorite_armed = favorite_first is None
        self._running = False
        self.research = (CausalCapture(research_observer, research_session_id or uuid.uuid4().hex)
                         if research_observer is not None and research_observer.config.enabled else None)
        self.research_config_identity = research_config_identity
        self.research_lifecycle: EpisodeLifecycle | None = None

        self.journal = Journal(cfg.paths.journal_dir, enabled=cfg.engine.journal,
                               day="paper" if paper else "live")
        self.state = StateStore(cfg.paths.db)
        self.catalog = CatalogStore(cfg.paths.db)
        self.gateway = ExecutionGateway(cfg, self.journal, paper=paper, research=self.research)
        self.risk = RiskManager(cfg.risk, self.state)
        self.merger = Merger(cfg)
        self.alerter = Alerter(cfg.secrets.alert_webhook_url, proxy=cfg.proxy)

        self.md = MarketDataService(on_dirty=self._on_dirty, on_trade=self._on_trade,
                                    on_connection=self._on_market_ws_connection,
                                    journal=self.journal, proxy=cfg.proxy, research=self.research)
        self._fill_recovery_pending: set[str] = set()
        self._fill_recovery_orders: dict[str, set[str]] = {}
        self._fill_truth_generation: dict[str, int] = {}
        self.user_proc = UserEventProcessor(self.state, on_change=self._wake_cid,
            on_fill=self._on_fill, on_reconcile=self._schedule_order_reconcile,
            research=self.research,
            fill_truth_ready=self._research_fill_truth_ready if self.research is not None else None)
        self.user: UserStream | None = None

        # per-market state
        self.metas: dict[str, MarketMeta] = {}
        self.profiles: dict[str, StrategyProfile] = {}
        self.est: dict[str, MarketEstimators] = {}
        self.regime_m: dict[str, RegimeMachine] = {}
        self._dirty: dict[str, asyncio.Event] = {}
        self._sweep: dict[str, bool] = {}
        self._merging: set[str] = set()
        self._token_cid: dict[str, str] = {}
        self._locks: dict[str, asyncio.Lock] = {}  # per-market: serialize recompute vs reconcile
        self._halted: set[str] = set()  # markets closed/resolved/not-accepting
        self._favorite_halt_reasons: dict[str, str] = {}
        self._last_quote_fv: dict[str, float] = {}  # requote suppression
        self._favorite: dict[str, tuple[float, FavoriteFirstConfig, StrategyMemory]] = {}
        self._favorite_last_reprice: dict[str, float] = {}
        self._favorite_last_record: dict[str, tuple[Any, ...]] = {}
        self._favorite_cutoff_seen: set[str] = set()
        self._favorite_first_orders: dict[str, set[str]] = {}
        self._favorite_pair_orders: dict[str, set[str]] = {}
        self._favorite_first_token: dict[str, str] = {}
        self._favorite_first_finalized: set[str] = set()
        self._favorite_pair_terminal: set[str] = set()
        self._favorite_mismatch_pending: set[str] = set()
        self._favorite_mismatch_reads: dict[str, int] = {}
        self._favorite_mismatch_confirmed: set[str] = set()
        self._research_rest_replacements: dict[str, int] = {}
        # supervised tasks: name -> (factory, task) so a dead task restarts
        self._task_specs: dict[str, Any] = {}
        self._tasks: dict[str, asyncio.Task[Any]] = {}
        self._aux_tasks: list[asyncio.Task[Any]] = []  # fire-and-forget (merges)
        # health / recovery signals
        self._reconcile_now = asyncio.Event()
        self._market_ws_was_connected = False
        self._market_recovery_pending = False
        self._market_connectivity_generation = 0
        self._market_recovery_generation: int | None = None
        self._order_state_uncertain: set[str] = set()
        self._user_started = False  # user WS task launched (live mode)
        self._hb_was_down = False
        self._chain_lock = asyncio.Lock()  # serialize on-chain txs (nonce safety)

    # ── lifecycle ───────────────────────────────────────────────────────
    async def start(self, *, arm_favorite: bool = True) -> None:
        # The controlled-live wrapper starts all feeds disarmed. The quoter and
        # final submit guard both enforce this boundary.
        if self._favorite_bootstrap[0] is not None:
            self._favorite_armed = arm_favorite
        self._running = True
        await self.gateway.connect()
        await self._resolve_markets()
        if not self.metas:
            log.warning("no_markets_selected", hint="add markets to config/markets.toml, run `polymaker scan`")
        # freshen reward/fee/end-date params from live Gamma BEFORE quoting so a
        # stale catalog (e.g. old reward min-size) can't mis-size our orders
        await self.refresh_market_metadata()
        await self._startup_reconcile()
        favorite_config, market_start_ts = self._favorite_bootstrap
        if favorite_config is not None:
            assert market_start_ts is not None
            self.attach_favorite_first(next(iter(self.metas)), market_start_ts,
                                       favorite_config)

        # subscribe feeds
        self.md.set_markets([(cid, [m.yes.token_id, m.no.token_id]) for cid, m in self.metas.items()])
        self.user = UserStream(
            self.gateway.creds, self.gateway.address, self.user_proc,
            other_token=self._other_token, condition_of_token=self._cid_of_token,
            journal=self.journal, proxy=self.cfg.proxy,
            on_reconnect=self._on_user_reconnect,
            research=self.research,
        )
        self.user.set_markets(list(self.metas))

        # launch supervised tasks (a dead task is restarted, never silently gone)
        self._spawn("market_ws", self.md.run)
        if not self.paper:
            assert self.user is not None
            self._spawn("user_ws", self.user.run)
            # register the dead-man switch BEFORE any quoter can place an order,
            # so a crash between placing and the first heartbeat still auto-cancels
            with contextlib.suppress(Exception):
                await self.gateway.heartbeat()
            self._spawn("heartbeat", self._heartbeat_loop)
            self._user_started = True
        self._spawn("reconcile", self._reconcile_loop)
        self._spawn("metadata", self._metadata_refresh_loop)
        self._spawn("maintenance", self._maintenance_loop)
        for cid in self.metas:
            self._spawn(f"quote:{cid[:8]}", lambda c=cid: self._quoter(c))
        if self.research_lifecycle is not None:
            try:
                self.research_lifecycle.start()
            except Exception:
                assert self.research is not None
                self.research.failures += 1
        self._spawn("supervisor", self._supervise)
        self.risk.reset_day()
        log.info("engine_started", markets=len(self.metas), paper=self.paper)

    def _spawn(self, name: str, factory: Any) -> None:
        self._task_specs[name] = factory
        self._tasks[name] = asyncio.create_task(factory(), name=name)

    _supervise_interval_s: float = 5.0

    async def _supervise(self) -> None:
        """Restart any engine task that exits while we're running. Never down."""
        while self._running:
            await asyncio.sleep(self._supervise_interval_s)
            for name, task in list(self._tasks.items()):
                if name == "supervisor" or not task.done():
                    continue
                if not self._running:
                    return
                exc = None
                with contextlib.suppress(asyncio.CancelledError, asyncio.InvalidStateError):
                    exc = task.exception()
                log.critical("task_died_restarting", task=name, err=str(exc) if exc else "exited")
                self.alerter.alert("task_died", f"{name} died: {exc}", critical=True)
                self._tasks[name] = asyncio.create_task(self._task_specs[name](), name=name)

    async def run_forever(self) -> None:
        await self.start()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.gather(*self._tasks.values(), *self._aux_tasks)

    async def shutdown(self) -> None:
        self._running = False
        if self.research_lifecycle is not None:
            try:
                await self.research_lifecycle.close()
            except Exception:
                assert self.research is not None
                self.research.failures += 1
        log.info("engine_shutdown")
        self.md.stop()
        if self.user:
            self.user.stop()
        tasks = [*self._tasks.values(), *self._aux_tasks]
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        with contextlib.suppress(Exception):
            if self._favorite_bootstrap[0] is not None:
                for meta in self.metas.values():
                    for token in (meta.yes.token_id, meta.no.token_id):
                        await self.gateway.cancel_asset(token)
            else:
                await self.gateway.cancel_all()
        self.gateway.close()
        self.journal.close()
        self.state.close()
        self.catalog.close()

    # ── market resolution ───────────────────────────────────────────────
    async def _resolve_markets(self) -> None:
        reward_rates: dict[str, float] | None = None
        async with GammaClient(self.cfg.wallet.gamma_host) as gamma:
            for entry in self.cfg.enabled_markets:
                meta = self.catalog.get_by_slug(entry.slug) if entry.slug else None
                if meta is None and entry.condition_id:
                    meta = self.catalog.get(entry.condition_id)
                if meta is None:  # fall back to a live Gamma fetch
                    if reward_rates is None:
                        reward_rates = await fetch_reward_rates(self.cfg.wallet.clob_host)
                    meta = await self._fetch_meta(gamma, entry.slug, entry.condition_id, reward_rates)
                if meta is None:
                    log.warning("market_unresolved", ref=entry.ref)
                    continue
                self.metas[meta.condition_id] = meta
                self.profiles[meta.condition_id] = self.cfg.profile_for(entry)
                self.est[meta.condition_id] = self._make_estimators(self.profiles[meta.condition_id])
                self.regime_m[meta.condition_id] = RegimeMachine()
                self._dirty[meta.condition_id] = asyncio.Event()
                self._locks[meta.condition_id] = asyncio.Lock()
                for tok in (meta.yes.token_id, meta.no.token_id):
                    self._token_cid[tok] = meta.condition_id
        favorite_config, _ = self._favorite_bootstrap
        if favorite_config is not None and len(self.metas) != 1:
            raise ValueError("Favorite-First requires exactly one resolved market")

    async def _fetch_meta(
        self, gamma: GammaClient, slug: str | None, condition_id: str | None,
        reward_rates: dict[str, float],
    ) -> MarketMeta | None:
        tag_id = self.catalog.cached_tag("politics")
        if tag_id is None:  # cold start: resolve + cache so the sweep is scoped
            tag_id = await gamma.resolve_tag_id("politics")
            if tag_id:
                self.catalog.cache_tag("politics", tag_id)
        async for raw in gamma.iter_markets(tag_id=tag_id, max_pages=25):
            if (slug and raw.get("slug") == slug) or (condition_id and raw.get("conditionId") == condition_id):
                m = parse_market(raw, reward_rates)
                if m:
                    self.catalog.upsert_market(m)
                return m
        return None

    @staticmethod
    def _make_estimators(p: StrategyProfile) -> MarketEstimators:
        return MarketEstimators(
            vol=VolEstimator(p.vol_short_halflife_s, p.vol_long_halflife_s),
            flow=FlowEstimator(p.flow_ewma_halflife_s),
            markout=MarkoutTracker(),
        )

    async def _startup_reconcile(self) -> None:
        # Capture exchange-live IDs before cancel-all so persisted ownership can
        # be reconciled even when the process died between POST and local state.
        pre_cancel = await self.gateway.open_orders()
        selected_tokens = set(self._token_cid)
        favorite_scoped = getattr(self, "_favorite_bootstrap", (None, None))[0] is not None
        if favorite_scoped:
            pre_cancel = [o for o in pre_cancel if o.token_id in selected_tokens]
        active_ids = {o.order_id for o in pre_cancel}
        with contextlib.suppress(Exception):
            if favorite_scoped:
                if active_ids:
                    await self.gateway.cancel(sorted(active_ids))
            else:
                await self.gateway.cancel_all()  # normal engine clean slate
        # cancel-all may have partially failed — verify no orders remain, and
        # cancel/adopt any stragglers so we never quote on top of an unknown order
        leftover = await self.gateway.open_orders()
        if favorite_scoped:
            leftover = [o for o in leftover if o.token_id in selected_tokens]
        if leftover:
            log.warning("startup_orders_remain", n=len(leftover))
            for tok in {o.token_id for o in leftover}:
                await self.gateway.cancel_asset(tok)
            still = await self.gateway.open_orders()
            if favorite_scoped:
                still = [o for o in still if o.token_id in selected_tokens]
            for tok in self._token_cid:
                self.state.replace_open_orders(
                    tok, [o for o in still if o.token_id == tok], grace_s=0.0
                )
            if still:
                log.error("startup_orders_stuck", n=len(still))
                self.alerter.alert("startup_orders_stuck",
                                   f"{len(still)} orders survived cancel-all", critical=True)
                raise RuntimeError("startup cannot establish a clean order state")
        elif favorite_scoped:
            # A prior process may have persisted an order that the exchange
            # now confirms absent. Clear that local working view only after
            # the authoritative selected-market open-order read.
            for tok in selected_tokens:
                self.state.replace_open_orders(tok, [], grace_s=0.0)
        relevant = self.state.owned_orders_for_recovery(
            since_ts=time.time() - 24 * 60 * 60,
            active_ids=active_ids,
            condition_ids=set(self.metas),
        )
        for row in relevant:
            order_id = str(row["order_id"])
            cid = str(row["condition_id"] or "")
            if cid:
                self._invalidate_fill_truth(cid)
                self._fill_recovery_pending.add(cid)
                self._fill_recovery_orders.setdefault(cid, set()).add(order_id)
            if await reconcile_owned_order(order_id, self.gateway, self.user_proc) \
                    is not ReconciliationOutcome.COMPLETE:
                raise RuntimeError(f"startup fill reconciliation unresolved for {order_id}")
            if cid:
                pending = self._fill_recovery_orders.get(cid, set())
                pending.discard(order_id)
                if not pending:
                    self._fill_recovery_orders.pop(cid, None)
                    self._fill_recovery_pending.discard(cid)
        # purge positions that leaked in for markets we don't trade (manual UI
        # bets etc.) so they can't distort exposure caps or PnL
        self.state.drop_untracked_positions(set(self._token_cid))
        positions = self._only_traded(await self.gateway.positions())
        self.state.reconcile_positions(positions)
        if positions:
            log.info("startup_positions", n=len(positions))

    def _only_traded(self, positions: dict[str, tuple[float, float]]) -> dict[str, tuple[float, float]]:
        """Scope account positions to tokens WE trade. Manual/UI positions in
        other markets are the operator's business — they must not enter our
        state, exposure caps, or PnL."""
        return {t: v for t, v in positions.items() if t in self._token_cid}

    # ── callbacks ───────────────────────────────────────────────────────
    def _on_dirty(self, condition_id: str, token_id: str) -> None:
        ev = self._dirty.get(condition_id)
        if ev is not None:
            ev.set()
        if (self._market_recovery_pending and self.md.connected
                and self._market_books_fresh(condition_id)):
            self._reconcile_now.set()

    def _market_books_fresh(self, cid: str) -> bool:
        meta = self.metas.get(cid)
        return meta is not None and self.md.books_fresh(meta)

    def _invalidate_fill_truth(self, condition_id: str) -> int:
        generations = getattr(self, "_fill_truth_generation", None)
        if generations is None:
            generations = self._fill_truth_generation = {}
        generation = generations.get(condition_id, 0) + 1
        generations[condition_id] = generation
        self._research_emit("SAFETY", condition_id,
            {"transition": "FILL_TRUTH_INVALIDATED", "fill_truth_generation": generation})
        return generation

    def _can_submit_market(self, expected_generation: int, condition_id: str | None = None,
                           expected_fill_truth_generation: int | None = None) -> bool:
        return (
            self._running
            and expected_generation == self._market_connectivity_generation
            and (condition_id is None or expected_fill_truth_generation is None
                 or expected_fill_truth_generation == getattr(self, "_fill_truth_generation", {}).get(condition_id, 0))
            and self.md.connected
            and not self._market_recovery_pending
            and (condition_id is None or condition_id not in self._fill_recovery_pending)
            and (condition_id is None or not self.user_proc.has_quarantine_for_condition(condition_id))
        )

    def _research_fill_truth_ready(self, cid: str) -> bool:
        """Copy the current live recovery/quarantine guards at this revision."""
        return (cid not in self._fill_recovery_pending
                and cid not in self._order_state_uncertain
                and not self.user_proc.has_quarantine_for_condition(cid))

    def favorite_live_user_ready(self) -> bool:
        """Current authenticated execution feed state for a new live order."""
        return bool(self._user_started and self.user is not None and self.user.connected)

    def favorite_halt_reason(self, cid: str) -> str | None:
        """Sticky safety halt for the selected market, including Engine halts."""
        return self._favorite_halt_reasons.get(cid) or (
            "MARKET_METADATA_HALT" if cid in self._halted else None
        )

    def _on_market_ws_connection(self, connected: bool) -> None:
        if connected:
            self._market_ws_was_connected = True
            log.info("market_ws_connected", recovery=self._market_recovery_pending)
            self._wake_all()
            return
        if self._market_ws_was_connected:
            self._market_connectivity_generation += 1
            self._market_recovery_pending = True
            self._market_recovery_generation = self._market_connectivity_generation
            log.warning("market_ws_blind", markets=len(self.metas))
        self._market_ws_was_connected = False
        self._wake_all()

    async def _reconcile_empty(self, cid: str, meta: MarketMeta, profile: StrategyProfile) -> None:
        live = self.state.orders_for(meta.yes.token_id) + self.state.orders_for(meta.no.token_id)
        target = TargetQuotes(cid, Regime.HALTED, ())
        plan = reconcile(target, live, tick=meta.tick_size,
                         reprice_ticks=profile.reprice_ticks, resize_frac=profile.resize_frac)
        if not plan.to_cancel:
            return
        ok = await self.gateway.cancel(plan.to_cancel)
        if ok:
            for oid in plan.to_cancel:
                self.state.remove_order(oid)
            log.info("market_ws_orders_cancelled", cid=cid[:8], count=len(plan.to_cancel))
        else:
            await self._refresh_token_orders(meta, grace_s=0.0)

    def _wake_cid(self, condition_id: str) -> None:
        ev = self._dirty.get(condition_id)
        if ev is not None:
            ev.set()

    def _wake_all(self) -> None:
        for ev in self._dirty.values():
            ev.set()

    def _on_user_reconnect(self) -> None:
        """User WS reconnected: events during the gap were lost — force an
        immediate REST reconcile before trusting our state again."""
        log.warning("user_ws_reconnected_forcing_reconcile")
        self._reconcile_now.set()
        rows = self.state.owned_orders_for_recovery(
            since_ts=time.time() - 24 * 60 * 60,
            active_ids=set(self.state.orders),
            condition_ids=set(self.metas),
        )
        for row in rows:
            self._schedule_order_reconcile(str(row["order_id"]))

    def _schedule_order_reconcile(self, order_id: str) -> None:
        row = self.state.owned_order(order_id)
        if row is None:
            return
        cid = str(row["condition_id"] or "")
        if cid:
            pending = self._fill_recovery_orders.setdefault(cid, set())
            if order_id not in pending:
                self._invalidate_fill_truth(cid)
            pending.add(order_id)
            self._fill_recovery_pending.add(cid)
        try:
            asyncio.get_running_loop().create_task(self._reconcile_owned_order(order_id, cid))
        except RuntimeError:
            return

    async def _reconcile_owned_order(self, order_id: str, cid: str) -> None:
        outcome = await reconcile_owned_order(order_id, self.gateway, self.user_proc)
        self._research_emit("SAFETY", cid, {"transition": "FILL_TRUTH_RECONCILE",
            "order_id": order_id, "outcome": outcome.value})
        if outcome is ReconciliationOutcome.COMPLETE:
            pending = self._fill_recovery_orders.get(cid, set())
            pending.discard(order_id)
            if not pending:
                self._fill_recovery_orders.pop(cid, None)
                self._fill_recovery_pending.discard(cid)
            self._wake_cid(cid)

    def _on_trade(self, tp: TradePrint) -> None:
        cid = self._token_cid.get(tp.asset_id)
        if cid is None:
            return
        p = self.profiles[cid]
        self.est[cid].flow.update(tp.aggressor, tp.size, tp.ts)
        # A trade only flags a SWEEP (-> pull quotes) if it's genuinely toxic:
        # large in absolute terms AND large relative to the resting depth it
        # consumed (i.e. it actually ate through the book). A big trade absorbed
        # by a deep book doesn't move the price and isn't toxic — for a liquid
        # market the FV-jump detector is the real event signal. event_sweep_mult
        # sets how many order-sizes big the print must be to even be considered.
        base = p.base_size_usdc / max(tp.price, 0.01)
        if tp.size < p.event_sweep_mult * base:
            return
        book = self.md.book(tp.asset_id)
        if book is None:
            return
        bb, ba = book.best_bid(), book.best_ask()
        if bb is None or ba is None:
            return
        # aggressor BUY lifts asks; SELL hits bids — measure the side it consumed
        if tp.aggressor is Side.BUY:
            consumed = book.depth_within(Side.SELL, ba.price, ba.price + 3 * book.tick_size)
        else:
            consumed = book.depth_within(Side.BUY, bb.price - 3 * book.tick_size, bb.price)
        if consumed > 0 and tp.size >= p.event_sweep_frac * consumed:
            self._sweep[cid] = True

    def _on_fill(self, fill: Fill) -> None:
        self.risk.note_fill(fill)
        cid = self._token_cid.get(fill.token_id)
        if cid is None:
            return
        self._invalidate_fill_truth(cid)
        if cid in self._favorite:
            self._wake_cid(cid)
        est = self.est[cid]
        fv = est.last_fv if est.last_fv is not None else fill.price
        token_fv = fv if fill.token_id == self.metas[cid].yes.token_id else (1.0 - fv)
        est.markout.record_fill(fill.side, token_fv, fill.ts)

    # ── quoter ──────────────────────────────────────────────────────────
    def attach_favorite_first(self, condition_id: str, market_start_ts: float,
                              config: FavoriteFirstConfig) -> None:
        """Opt in one already resolved market; caller supplies all risk parameters."""
        if self._favorite or len(self.metas) != 1 or condition_id not in self.metas:
            raise ValueError("Favorite-First requires exactly one resolved market")
        meta = self.metas[condition_id]
        if (self.state.position(meta.yes.token_id).size > 0
                or self.state.position(meta.no.token_id).size > 0
                or self.state.orders_for(meta.yes.token_id)
                or self.state.orders_for(meta.no.token_id)):
            raise ValueError("Favorite-First requires a flat market at attachment")
        if meta.min_order_size > config.first_leg_qty + 1e-9:
            raise ValueError("Favorite-First first leg is below the venue minimum")
        self._favorite[condition_id] = (market_start_ts, config, StrategyMemory())
        self._favorite_first_orders[condition_id] = set()
        self._favorite_pair_orders[condition_id] = set()
        if self.research is not None:
            try:
                identity = self.research_config_identity
                if identity is None:
                    identity = research_config_identity(config, dataset_id="m4a",
                                                        session_id=self.research.session_id)
                    self.research_config_identity = identity
                validate_research_config_identity(identity, config, dataset_id="m4a",
                                                  session_id=self.research.session_id)
                rcfg = self.research.observer.config
                self.research_lifecycle = EpisodeLifecycle(self.research,
                    dataset_id=identity.dataset_id, market_slug=meta.slug, condition_id=condition_id,
                    yes_token_id=meta.yes.token_id, no_token_id=meta.no.token_id,
                    market_start_us=int(market_start_ts * 1_000_000),
                    market_end_us=int((market_start_ts + 300) * 1_000_000),
                    strategy_version="FavoriteFirst-v0.3", strategy_config_id=identity.config_id,
                    venue_min_order_qty=meta.min_order_size,
                    forced_pair_s=config.forced_pair_start_s,
                    order_cutoff_s=config.hard_order_cutoff_s,
                    interval_s=rcfg.snapshot_interval_s,
                    prefill_s=rcfg.prefill_ring_duration_s)
            except Exception:
                self.research.failures += 1
        self._wake_cid(condition_id)

    def arm_favorite_first(self, cid: str) -> None:
        """Cross the controlled-live arm boundary after external preflight."""
        if (cid not in self._favorite or not self._running or self._favorite_armed
                or not self._market_books_fresh(cid) or self._market_recovery_pending
                or not self._user_started or self.user is None or not self.user.connected
                or self._fill_recovery_pending or self._order_state_uncertain
                or cid in self._halted):
            raise RuntimeError("Favorite-First feeds or reconciliation are not ready")
        meta = self.metas[cid]
        economics = self._favorite_economics(cid)
        if (economics["first_qty"] or economics["pair_qty"] or economics["mismatch"]
                or self.state.orders_for(meta.yes.token_id)
                or self.state.orders_for(meta.no.token_id)
                or self._favorite[cid][2] != StrategyMemory()):
            raise RuntimeError("Favorite-First is not at a flat OBSERVE baseline")
        self._favorite_armed = True
        self._wake_cid(cid)

    def disarm_favorite_first(self, cid: str) -> None:
        self._favorite_armed = False
        self._wake_cid(cid)

    async def finalize_favorite_market(self, cid: str) -> dict[str, Any]:
        """Scoped end-of-market cleanup using existing order and fill truth."""
        self.disarm_favorite_first(cid)
        meta = self.metas[cid]
        async with self._locks[cid]:
            for token in (meta.yes.token_id, meta.no.token_id):
                await self.gateway.cancel_asset(token)
            await self._refresh_token_orders(meta, grace_s=0.0)
            live = self.state.orders_for(meta.yes.token_id) + self.state.orders_for(meta.no.token_id)
            outcomes = {}
            for oid in self._favorite_first_orders[cid] | self._favorite_pair_orders[cid]:
                outcomes[oid] = (await reconcile_owned_order(
                    oid, self.gateway, self.user_proc)).value
            positions = await self.gateway.positions()
            economics = self._favorite_economics(cid)
            first_side = economics["first_side"]
            first_qty, pair_qty = economics["first_qty"], economics["pair_qty"]
            expected_yes = first_qty if first_side == "YES" else pair_qty if first_side else 0.0
            expected_no = first_qty if first_side == "NO" else pair_qty if first_side else 0.0
            yes = positions.get(meta.yes.token_id, (0.0, 0.0))[0]
            no = positions.get(meta.no.token_id, (0.0, 0.0))[0]
            return {
                "owned_open_orders": len(live),
                "fill_truth_complete": all(v == ReconciliationOutcome.COMPLETE.value
                                           for v in outcomes.values()),
                "order_reconciliation": outcomes,
                "position_reconciled": (abs(yes - expected_yes) <= 1e-6
                                        and abs(no - expected_no) <= 1e-6),
                "yes_qty": yes, "no_qty": no, "economics": economics,
            }

    def _favorite_economics(self, cid: str) -> dict[str, Any]:
        """Read accepted economics from the existing owned-fill tracker."""
        first_fills = self.user_proc.economic_fills_for_orders(self._favorite_first_orders[cid])
        pair_fills = self.user_proc.economic_fills_for_orders(self._favorite_pair_orders[cid])

        def totals(fills: list[Any]) -> tuple[float, float]:
            qty = sum(f.size for f in fills)
            return qty, sum(f.size * f.price for f in fills) / qty if qty > 0 else 0.0

        first_qty, first_vwap = totals(first_fills)
        pair_qty, pair_vwap = totals(pair_fills)
        first_token = self._favorite_first_token.get(cid)
        meta = self.metas[cid]
        first_side = ("YES" if first_token == meta.yes.token_id else "NO"
                      if first_token == meta.no.token_id else None) if first_fills else None
        first_ts = min((f.ts for f in first_fills), default=None)
        first_active = [o for o in self.state.orders.values()
                        if o.order_id in self._favorite_first_orders[cid]]
        pair_active = [o for o in self.state.orders.values()
                       if o.order_id in self._favorite_pair_orders[cid]]
        first_order = first_active[0] if len(first_active) == 1 else None
        pair_order = pair_active[0] if len(pair_active) == 1 else None
        first_remaining = min(first_order.size, max(0.0,
            self._favorite[cid][1].first_leg_qty - first_qty)) if first_order else 0.0
        pair_remaining = min(pair_order.size, max(0.0, first_qty - pair_qty)) \
            if pair_order else 0.0
        expected_yes = first_qty if first_side == "YES" else pair_qty if first_side else 0.0
        expected_no = first_qty if first_side == "NO" else pair_qty if first_side else 0.0
        mismatch = (abs(self.state.position(meta.yes.token_id).size - expected_yes) > 1e-6
                    or abs(self.state.position(meta.no.token_id).size - expected_no) > 1e-6
                    or len(first_active) > 1 or len(pair_active) > 1
                    or any(f.token_id != first_token for f in first_fills)
                    or any(f.token_id == first_token for f in pair_fills))
        return dict(first_qty=first_qty, first_vwap=first_vwap,
                    pair_qty=pair_qty, pair_vwap=pair_vwap,
                    first_side=first_side, first_ts=first_ts,
                    first_order=first_order, pair_order=pair_order,
                    first_remaining=first_remaining, pair_remaining=pair_remaining,
                    mismatch=mismatch)

    async def _favorite_finalize_orders(self, cid: str, *, first: bool) -> None:
        """Verify terminal order state and accepted fills with existing REST paths."""
        ids = (self._favorite_first_orders[cid] if first
               else self._favorite_pair_orders[cid])
        terminal = self._favorite_first_finalized if first else self._favorite_pair_terminal
        if cid in terminal:
            return
        meta = self.metas[cid]
        if not self.paper:
            await self._refresh_token_orders(meta, grace_s=0.0)
        if any(oid in self.state.orders for oid in ids):
            return
        if not self.paper:
            for oid in ids:
                outcome = await reconcile_owned_order(oid, self.gateway, self.user_proc)
                if outcome is not ReconciliationOutcome.COMPLETE:
                    self._fill_recovery_pending.add(cid)
                    self._fill_recovery_orders.setdefault(cid, set()).add(oid)
                    self._invalidate_fill_truth(cid)
                    return
        terminal.add(cid)
        self._research_emit("SAFETY", cid, {"transition": "FIRST_FINALIZED" if first else "PAIR_TERMINAL",
            "order_ids": tuple(ids)})
        self._wake_cid(cid)

    async def _quoter(self, cid: str) -> None:
        debounce = self.cfg.engine.debounce_ms / 1000.0
        base_tick = self.cfg.engine.quoter_tick_s
        ev = self._dirty[cid]
        while self._running:
            try:
                # Book/fill events wake us instantly. Otherwise we refresh on a
                # slow baseline tick, EXCEPT: if an EVENT cool-off is active,
                # wake precisely when it ends (re-enter promptly, not up to a
                # minute late); if we're holding inventory, tick faster to walk
                # exit urgency.
                timeout = self._next_wake_s(cid, base_tick)
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(ev.wait(), timeout=timeout)
                if ev.is_set():
                    await asyncio.sleep(debounce)  # coalesce a burst of updates
                ev.clear()
                await self._recompute(cid)
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001
                log.error("quoter_error", cid=cid[:8], err=str(exc))
                await asyncio.sleep(0.5)

    def _next_wake_s(self, cid: str, base_tick: float) -> float:
        now = time.time()
        wake = base_tick
        if cid in self._favorite:
            wake = min(wake, 1.0)
        rm = self.regime_m.get(cid)
        if rm is not None:
            cd = rm.cooloff_remaining(now)
            if cd > 0:
                wake = min(wake, cd + 0.5)  # re-enter right when cool-off ends
        meta = self.metas.get(cid)
        if meta is not None:  # holding inventory -> tick faster to manage exits
            held = self.state.position(meta.yes.token_id).size + self.state.position(meta.no.token_id).size
            if held >= meta.min_order_size:
                wake = min(wake, 10.0)
        return max(1.0, wake)

    async def _recompute(self, cid: str) -> None:
        lock = self._locks.get(cid)
        if lock is None:
            return
        async with lock:  # serialize vs the reconcile loop mutating this market
            await self._recompute_locked(cid)

    async def _recompute_locked(self, cid: str) -> None:
        meta = self.metas[cid]
        p = self.profiles[cid]
        if self._favorite_bootstrap[0] is not None and not self._favorite_armed:
            await self._reconcile_empty(cid, meta, p)
            return
        expected_market_generation = self._market_connectivity_generation
        expected_fill_truth_generation = self._fill_truth_generation.get(cid, 0)
        if cid in self._order_state_uncertain:
            await self._quarantine(meta, reason="order_state_uncertain")
            return
        if (cid in self._fill_recovery_pending
                or self.user_proc.has_quarantine_for_condition(cid)):
            await self._reconcile_empty(cid, meta, p)
            return
        if (not self.md.connected or self._market_recovery_pending
                or not self._market_books_fresh(cid)):
            await self._reconcile_empty(cid, meta, p)
            return
        yes_book = self.md.book(meta.yes.token_id)
        no_book = self.md.book(meta.no.token_id)
        if yes_book is None or yes_book.is_empty:
            return

        # crossed/locked or one-sided book -> FV is unreliable; skip this tick
        bb, ba = yes_book.best_bid(), yes_book.best_ask()
        if bb is None or ba is None or bb.price >= ba.price:
            return

        now = time.time()
        micro = yes_book.microprice(p.micro_levels)
        if micro is None:
            return
        est = self.est[cid]
        est.flow.decay_to(now)
        fv = compute_fair_value(micro, est.flow.z, meta.tick_size)
        prev_fv = est.last_fv
        est.on_fair_value(fv, now)

        self.risk.update_mark(meta.yes.token_id, fv)
        self.risk.update_mark(meta.no.token_id, 1.0 - fv)

        pos_yes = self.state.position(meta.yes.token_id)
        pos_no = self.state.position(meta.no.token_id)
        q_max = p.q_max_usdc
        inv_util = abs(pos_yes.size - pos_no.size) * fv / q_max if q_max > 0 else 0.0
        hours_to_end = _hours_to_end(meta.end_date_iso, now)

        # ── blind/stale conditions ──────────────────────────────────────────
        # A QUIET market with a live WS link is NOT stale — the CLOB WS pings
        # every 5s (pong-timeout 10s), so a dead link flips `connected` within
        # ~15s. Gating on the connection (not book-mutation recency) stops a
        # legitimately-quiet thin market from false-halting into zero rewards.
        market_stale = (
            not self.md.connected
            and self.md.disconnected_since > 0.0
            and (now - self.md.disconnected_since) > self.cfg.risk.ws_stale_halt_s
        )
        user_blind = (
            self._user_started
            and self.user is not None
            and not self.user.connected
            and (now - self.user.disconnected_since) > self.cfg.risk.user_ws_blind_halt_s
        )
        hb_blind = (
            not self.paper
            and self.cfg.engine.heartbeat
            and (self.gateway.heartbeat_failures >= self.cfg.risk.heartbeat_halt_failures
                 or self._hb_was_down)
        )
        halted = cid in self._halted
        blind = market_stale or user_blind or hb_blind or halted
        if cid in self._favorite:
            if halted:
                self._favorite_halt_reasons.setdefault(cid, "MARKET_METADATA_HALT")
            elif user_blind:
                self._favorite_halt_reasons.setdefault(cid, "USER_WS_BLIND_HALT")
            elif market_stale:
                self._favorite_halt_reasons.setdefault(cid, "MARKET_WS_STALE_HALT")
            elif hb_blind:
                self._favorite_halt_reasons.setdefault(cid, "HEARTBEAT_HALT")
        if blind:
            log.warning("market_blind", cid=cid[:8], market_stale=market_stale,
                        user_blind=user_blind, hb_blind=hb_blind, halted=halted)
            self.alerter.alert(
                f"blind:{cid[:8]}",
                f"{meta.question[:40]} blind (stale={market_stale} user={user_blind} "
                f"hb={hb_blind} halted={halted})",
                critical=hb_blind,
            )

        rd = self.risk.evaluate(meta, ws_stale=blind,
                                event_group_cost=self._event_group_cost(meta))
        if rd.halt and rd.reason not in ("ws_stale",) and cid in self._favorite:
            self._favorite_halt_reasons.setdefault(cid, f"RISK_HALT:{rd.reason}")
        if rd.halt and rd.reason not in ("ws_stale",):
            self.alerter.alert(
                f"risk_halt:{rd.reason}", f"risk halt: {rd.reason}",
                critical=any(k in rd.reason for k in ("daily_loss", "kill", "error_rate")),
            )
        ws_stale = blind
        regime = self.regime_m[cid].decide(
            RegimeInputs(
                now=now, tick=meta.tick_size, fv=fv, prev_fv=prev_fv,
                vol_ratio=est.vol.ratio, flow_z=est.flow.z, inventory_util=inv_util,
                hours_to_end=hours_to_end, sweep_flagged=self._sweep.pop(cid, False),
                ws_stale=ws_stale, risk_halt=rd.halt, risk_reduce_only=rd.reduce_only,
            ),
            p,
        )

        tq = construct_quotes(QuoteInputs(
            meta=meta, regime=regime, fv=fv, vol_short=est.vol.short,
            toxicity=est.markout.toxicity, yes_view=yes_book.view(),
            no_view=(no_book.view() if no_book else _empty_view()),
            pos_yes=pos_yes, pos_no=pos_no, profile=p, now=now,
            risk_size_scale=rd.size_scale,
        ))

        favorite_config = None
        favorite_decision = None
        if cid in self._favorite:
            start, favorite_config, memory = self._favorite[cid]
            economics = self._favorite_economics(cid)
            mismatch = economics["mismatch"]
            if mismatch and cid not in self._favorite_mismatch_pending:
                self._favorite_mismatch_pending.add(cid)
                self._reconcile_now.set()
            elif not mismatch:
                self._favorite_mismatch_pending.discard(cid)
                self._favorite_mismatch_confirmed.discard(cid)
                self._favorite_mismatch_reads.pop(cid, None)
            first_side = economics["first_side"]
            first_qty = economics["first_qty"]
            pair_qty = economics["pair_qty"]
            first_order = economics["first_order"]
            pair_order = economics["pair_order"]
            strategy_yes = first_qty if first_side == "YES" else pair_qty if first_side else 0.0
            strategy_no = first_qty if first_side == "NO" else pair_qty if first_side else 0.0
            strategy_yes_vwap = (economics["first_vwap"] if first_side == "YES"
                                 else economics["pair_vwap"])
            strategy_no_vwap = (economics["first_vwap"] if first_side == "NO"
                                else economics["pair_vwap"])
            strategy_snapshot = StrategySnapshot(
                now=now, market_start_ts=start, yes=yes_book.view(), no=no_book.view(),
                yes_qty=strategy_yes, no_qty=strategy_no,
                yes_vwap=strategy_yes_vwap, no_vwap=strategy_no_vwap,
                yes_token=meta.yes.token_id, no_token=meta.no.token_id,
                tick=meta.tick_size, first_fill_ts=economics["first_ts"],
                first_leg_side=first_side,
                first_leg_working_remaining=economics["first_remaining"],
                first_leg_working_price=first_order.price if first_order else None,
                first_leg_finalized=cid in self._favorite_first_finalized,
                pair_working_remaining=economics["pair_remaining"],
                pair_working_price=pair_order.price if pair_order else None,
                pair_terminal_authoritative=cid in self._favorite_pair_terminal,
                venue_min_order_size=meta.min_order_size,
                account_position_mismatch=cid in self._favorite_mismatch_confirmed,
            )
            decision = evaluate_favorite(strategy_snapshot, memory, favorite_config)
            favorite_decision = decision
            if self.research is not None:
                try:
                    self._research_emit("BASELINE_DECISION", cid, {
                        "snapshot": self._research_primitive(asdict(strategy_snapshot)),
                        "memory_before": self._research_primitive(asdict(memory)),
                        "config": asdict(favorite_config),
                        "strategy_state": decision.memory.state.value,
                        "first_leg_side": decision.memory.first_leg_side,
                        "first_leg_qty": decision.first_leg_qty,
                        "paired_qty": decision.paired_qty,
                        "pair_trigger": (decision.memory.pair_trigger.value
                                         if decision.memory.pair_trigger else None),
                        "pure_quote": self._research_quote(decision.quote),
                        "yes": {"bid": yes_book.view().best_bid, "ask": yes_book.view().best_ask},
                        "no": {"bid": no_book.view().best_bid if no_book else None,
                               "ask": no_book.view().best_ask if no_book else None},
                        "first_fill_exchange_ts": economics["first_ts"],
                    })
                except Exception:
                    self.research.failures += 1
            self._favorite[cid] = (start, favorite_config, decision.memory)
            if (now - start >= favorite_config.hard_order_cutoff_s
                    and cid not in self._favorite_cutoff_seen):
                self._favorite_cutoff_seen.add(cid)
                self._reconcile_now.set()
                for row in self.state.owned_orders():
                    if row["condition_id"] == cid:
                        self._schedule_order_reconcile(str(row["order_id"]))
            desired = (decision.quote if not (blind or rd.halt or rd.reduce_only or mismatch
                         or (decision.memory.first_leg_side is None
                             and rd.size_scale < 1.0)) else None)
            live_now = self.state.orders_for(meta.yes.token_id) + self.state.orders_for(meta.no.token_id)
            if desired is not None and len(live_now) == 1:
                active = live_now[0]
                interval = (favorite_config.urgent_reprice_min_s
                            if decision.pair_quote_mode is not None
                            and decision.pair_quote_mode.value in ("AGGRESSIVE", "FORCED")
                            else favorite_config.normal_reprice_min_s)
                if (not decision.submin_working_remainder
                        and active.token_id == desired.token_id and active.side is desired.side
                        and abs(active.size - desired.size) <= 1e-8
                        and now - self._favorite_last_reprice.get(cid, -float("inf")) < interval):
                    desired = type(desired)(desired.token_id, desired.side, active.price, desired.size)
            tq = TargetQuotes(cid, Regime.HALTED if desired is None else regime,
                              (desired,) if desired is not None else ())
            if self.research is not None:
                self._research_emit("SAFETY", cid, {
                    "halt_reason": self.favorite_halt_reason(cid), "blind": blind,
                    "fill_truth_ready": cid not in self._fill_recovery_pending,
                    "market_recovery_pending": self._market_recovery_pending,
                    "first_finalized": cid in self._favorite_first_finalized,
                    "pair_terminal": cid in self._favorite_pair_terminal,
                    "cutoff_seen": cid in self._favorite_cutoff_seen,
                })
            record = (decision.memory.state, desired, decision.memory.pair_trigger,
                      decision.pair_quote_mode, decision.first_leg_qty,
                      decision.paired_qty, decision.submin_working_remainder,
                      decision.memory.residual_reason,
                      round(decision.peak_locked_pnl, 2)
                      if decision.peak_locked_pnl is not None else None)
            if record != self._favorite_last_record.get(cid):
                self._favorite_last_record[cid] = record
                active_price = live_now[0].price if len(live_now) == 1 else None
                fields = dict(market_elapsed=now-start, strategy_state=decision.memory.state.value,
                              favorite_side=decision.favorite_side,
                              favorite_probability=decision.favorite_probability,
                              favorite_confirm_age=decision.favorite_confirm_age,
                              first_leg_side=decision.memory.first_leg_side,
                              first_leg_qty=decision.first_leg_qty,
                              first_leg_vwap=decision.first_leg_vwap,
                              time_since_first_fill=(now-decision.memory.first_fill_ts
                                                     if decision.memory.first_fill_ts else None),
                              paired_qty=decision.paired_qty, unpaired_qty=decision.unpaired_qty,
                              current_locked_pnl=decision.current_locked_pnl,
                              peak_locked_pnl=decision.peak_locked_pnl,
                              trailing_floor=decision.trailing_floor,
                              pair_quote_mode=(decision.pair_quote_mode.value
                                               if decision.pair_quote_mode else None),
                              desired_price=desired.price if desired else None,
                              active_order_price=active_price,
                              aggressive_opposite_price=decision.aggressive_opposite_price,
                              pair_trigger=(decision.memory.pair_trigger.value
                                            if decision.memory.pair_trigger else None),
                              yes_bid=bb.price, yes_ask=ba.price, yes_mid=yes_book.view().mid,
                              no_bid=no_book.view().best_bid, no_ask=no_book.view().best_ask,
                              no_mid=no_book.view().mid)
                fields.update(first_fill_time=decision.memory.first_fill_ts,
                              risk_window_deadline=(decision.memory.first_fill_ts
                                                    + favorite_config.probation_max_s
                                                    if decision.memory.first_fill_ts else None),
                              risk_window_elapsed=(now-decision.memory.first_fill_ts
                                                   if decision.memory.first_fill_ts else None),
                              risk_window_remaining=(max(0.0, favorite_config.probation_max_s
                                                        -(now-decision.memory.first_fill_ts))
                                                     if decision.memory.first_fill_ts else None),
                              strategy_first_leg_cumulative_qty=decision.first_leg_qty,
                              first_leg_target_qty=favorite_config.first_leg_qty,
                              first_leg_working_remaining=economics["first_remaining"],
                              venue_min_order_size=meta.min_order_size,
                              paired_locked_pnl=decision.paired_locked_pnl,
                              residual_lockable_pnl=decision.residual_lockable_pnl,
                              total_lockable_pnl=decision.current_locked_pnl,
                              peak_total_lockable_pnl=decision.peak_locked_pnl,
                              submin_working_remainder=decision.submin_working_remainder,
                              unpairable_residual_reason=decision.memory.residual_reason,
                              strategy_account_position_mismatch=mismatch)
                log.info("favorite_first_decision", cid=cid[:8], **fields)
                self.journal.write("favorite_first_decision", {"cid": cid, **fields}, now)

        live = self.state.orders_for(meta.yes.token_id) + self.state.orders_for(meta.no.token_id)
        if favorite_config:
            # A maker fill can precede its cumulative order WS event. Compare
            # targets with the remaining quantity implied by accepted owned
            # fills, without changing StateStore or inventing an order.
            adjusted = []
            scoped_ids = self._favorite_first_orders[cid] | self._favorite_pair_orders[cid]
            for order in live:
                owned = self.state.owned_order(order.order_id)
                if owned is not None and order.order_id in scoped_ids:
                    remaining = max(0.0, float(owned["submitted_qty"])
                                    - self.user_proc.tracked_trade_qty(order.order_id))
                    adjusted.append(replace(order, size=min(order.size, remaining)))
                else:
                    adjusted.append(order)
            live = adjusted
        plan = reconcile(tq, live, tick=meta.tick_size,
                         reprice_ticks=0 if favorite_config else p.reprice_ticks,
                         resize_frac=0 if favorite_config else p.resize_frac)
        if favorite_config and self.research is not None:
            try:
                self._research_emit("BASELINE_DECISION", cid, {
                    "phase": "ORDER_PLAN", "stage": "RECONCILED_PLAN",
                    "target_quote": self._research_quote(desired),
                    "quote": self._research_quote(plan.to_place[0]) if plan.to_place else None,
                    "to_place": [self._research_quote(q) for q in plan.to_place],
                    "to_cancel": tuple(plan.to_cancel), "is_noop": plan.is_noop,
                    "suppressed": desired is None and favorite_decision is not None
                        and favorite_decision.quote is not None,
                    "blind": blind, "risk_halt": rd.halt,
                    "reduce_only": rd.reduce_only, "mismatch": mismatch,
                })
            except Exception:
                self.research.failures += 1
        if plan.is_noop:
            if favorite_config and self.research is not None:
                self._research_emit("BASELINE_DECISION", cid, {
                    "phase": "EFFECTIVE_QUOTE", "stage": "NOOP", "quote": None,
                    "is_noop": True,
                })
            if favorite_decision and favorite_decision.first_leg_finalize_needed:
                await self._favorite_finalize_orders(cid, first=True)
            if favorite_decision and favorite_decision.pair_finalize_needed:
                await self._favorite_finalize_orders(cid, first=False)
            if favorite_config is None:
                self._maybe_merge(cid, meta, p, pos_yes.size, pos_no.size)
            return

        if plan.to_cancel:
            ok = await self.gateway.cancel(plan.to_cancel)
            if ok:
                for oid in plan.to_cancel:
                    self.state.remove_order(oid)
            else:
                # cancel MAY have partially applied server-side — keep our view,
                # resync from REST, and skip placing this cycle (avoid doubles)
                if favorite_config and self.research is not None:
                    self._research_emit("BASELINE_DECISION", cid, {
                        "phase": "EFFECTIVE_QUOTE", "stage": "CANCEL_UNCERTAIN",
                        "quote": None, "to_cancel": tuple(plan.to_cancel),
                    })
                await self._refresh_token_orders(meta, grace_s=10.0)
                self._dirty[cid].set()
                return
            if favorite_config and plan.to_place:
                # A cancel acknowledgement alone does not establish the final
                # maker-fill quantity. Verify these exact owned IDs before a
                # replacement can be evaluated against the new inventory.
                if self.research is not None:
                    self._research_emit("BASELINE_DECISION", cid, {
                        "phase": "EFFECTIVE_QUOTE", "stage": "PLACEMENT_DEFERRED",
                        "reason": "CANCEL_REPLACE_VERIFICATION", "quote": None,
                        "to_place": [self._research_quote(q) for q in plan.to_place],
                    })
                if not self.paper:
                    await self._refresh_token_orders(meta, grace_s=0.0)
                    if any(oid in self.state.orders for oid in plan.to_cancel):
                        self._dirty[cid].set()
                        return
                    for oid in plan.to_cancel:
                        if self.state.owned_order(oid) is None:
                            continue
                        if await reconcile_owned_order(oid, self.gateway, self.user_proc) \
                                is not ReconciliationOutcome.COMPLETE:
                            self._fill_recovery_pending.add(cid)
                            self._fill_recovery_orders.setdefault(cid, set()).add(oid)
                            self._invalidate_fill_truth(cid)
                            return
                if (favorite_decision and favorite_decision.memory.first_leg_side is not None
                        and plan.to_place[0].token_id == self._favorite_first_token.get(cid)):
                    start, config, memory = self._favorite[cid]
                    self._favorite[cid] = (start, config,
                        replace(memory, first_leg_replacement_pending=True))
                self._dirty[cid].set()
                return
        if favorite_config and self.research is not None and not plan.to_place:
            self._research_emit("BASELINE_DECISION", cid, {
                "phase": "EFFECTIVE_QUOTE", "stage": "CANCEL_ONLY",
                "quote": None, "to_cancel": tuple(plan.to_cancel),
            })
        if favorite_decision and favorite_decision.first_leg_finalize_needed:
            await self._favorite_finalize_orders(cid, first=True)
        if favorite_decision and favorite_decision.pair_finalize_needed:
            await self._favorite_finalize_orders(cid, first=False)
        placed_n = 0
        if plan.to_place:
            # The WS may have dropped while cancellation/reconciliation awaited
            # I/O above. Recheck immediately before submitting any new order.
            if (not self.md.connected or self._market_recovery_pending
                    or not self._market_books_fresh(cid)):
                if favorite_config and self.research is not None:
                    self._research_emit("BASELINE_DECISION", cid, {
                        "phase": "EFFECTIVE_QUOTE", "stage": "SUBMISSION_SUPPRESSED",
                        "reason": "MARKET_NOT_READY", "quote": None,
                        "to_place": [self._research_quote(q) for q in plan.to_place],
                    })
                self._dirty[cid].set()
                return
            # LOAD SHED: under rate-budget pressure, skip *new* quotes in calm
            # regimes (cancels/exits above already ran) so we don't inject latency
            # right when the book is busy. Risk regimes always place.
            shed = (
                not self.paper
                and self.gateway.order_pressure > 0.85
                and regime in (Regime.QUIET, Regime.TRENDING)
            )
            if shed:
                if favorite_config and self.research is not None:
                    self._research_emit("BASELINE_DECISION", cid, {
                        "phase": "EFFECTIVE_QUOTE", "stage": "SUBMISSION_SUPPRESSED",
                        "reason": "LOAD_SHED", "quote": None,
                        "to_place": [self._research_quote(q) for q in plan.to_place],
                    })
                log.warning("shed_load", cid=cid[:8], pressure=round(self.gateway.order_pressure, 2))
                self._dirty[cid].set()  # retry soon
            else:
                self.user_proc.arm_pending_many([(q.token_id, q.side) for q in plan.to_place])
                try:
                    placed = await self.gateway.place(
                        plan.to_place, meta,
                        force_post_only=favorite_config is not None,
                        submit_guard=lambda: self._can_submit_market(
                            expected_market_generation, cid, expected_fill_truth_generation)
                            and (favorite_config is None or self._favorite_submit_ok(
                                cid, plan.to_place, meta)),
                    )
                except BaseException:
                    self.user_proc.discard_pending()
                    raise
                placed_n = len(placed)
                if favorite_config and self.research is not None:
                    self._research_emit("BASELINE_DECISION", cid, {
                        "phase": "EFFECTIVE_QUOTE", "stage": "PLACEMENT_RESULT",
                        "quote": (self._research_quote(plan.to_place[0])
                                  if placed_n else None),
                        "planned_count": len(plan.to_place), "acknowledged_count": placed_n,
                    })
                if favorite_config and placed_n:
                    self._favorite_last_reprice[cid] = time.time()
                self.risk.note_order_result(len(placed) == len(plan.to_place))
                for o in placed:
                    self.state.upsert_order(o)
                    if favorite_config:
                        if (favorite_decision and
                                (favorite_decision.memory.first_leg_side is None
                                 or o.token_id == self._favorite_first_token.get(cid))):
                            self._favorite_first_orders[cid].add(o.order_id)
                            self._favorite_first_token[cid] = o.token_id
                            start, config, memory = self._favorite[cid]
                            self._favorite[cid] = (start, config,
                                replace(memory, first_leg_replacement_pending=False))
                            source = "favorite_first_leg"
                        elif favorite_decision and favorite_decision.memory.first_leg_side is not None:
                            self._favorite_pair_orders[cid].add(o.order_id)
                            self._favorite_pair_terminal.discard(cid)
                            source = "favorite_pair_leg"
                        else:
                            source = "engine"
                    else:
                        source = "engine"
                    self.user_proc.register_order(o.order_id, meta.condition_id, o.token_id,
                                                  o.side, o.size, o.price, source)
                self.user_proc.discard_pending()
                if len(placed) < len(plan.to_place):
                    # QUARANTINE: a failed/partial batch may still have posted
                    # orders we don't have ids for. Cancel everything on these
                    # tokens (idempotent) and resync — never risk an untracked order.
                    await self._quarantine(meta, reason="place_incomplete")
        self._last_quote_fv[cid] = fv
        log.info("requote", cid=cid[:8], regime=regime.value, fv=round(fv, 4),
                 place=placed_n, cancel=len(plan.to_cancel),
                 pos_yes=round(pos_yes.size, 1), pos_no=round(pos_no.size, 1),
                 tox=round(est.markout.toxicity, 3), flowz=round(est.flow.z, 2))
        if favorite_config is None:
            self._maybe_merge(cid, meta, p, pos_yes.size, pos_no.size)

    def _favorite_submit_ok(self, cid: str, quotes: list[Any], meta: MarketMeta) -> bool:
        if not self._favorite_armed:
            return False
        if not self.paper and not self.favorite_live_user_ready():
            return False
        if self.favorite_halt_reason(cid) is not None:
            return False
        start, config, memory = self._favorite[cid]
        elapsed = time.time() - start
        if elapsed >= config.hard_order_cutoff_s or len(quotes) != 1:
            return False
        if not self._market_books_fresh(cid):
            return False
        q = quotes[0]
        if (q.side is not Side.BUY or q.size + 1e-9 < meta.min_order_size
                or q.size > config.first_leg_qty + 1e-8):
            return False
        book = self.md.book(q.token_id)
        ask = book.best_ask() if book is not None else None
        if ask is None or q.price >= ask.price:
            return False
        economics = self._favorite_economics(cid)
        if economics["mismatch"]:
            return False
        first_qty = economics["first_qty"]
        pair_qty = economics["pair_qty"]
        if first_qty <= 1e-8:
            return (elapsed >= config.entry_start_s and elapsed <= config.last_new_entry_s
                    and q.size <= config.first_leg_qty + 1e-8)
        first_token = self._favorite_first_token.get(cid)
        if q.token_id == first_token:
            return (memory.state is StrategyState.FIRST_LEG_RISK_WINDOW
                    and cid not in self._favorite_first_finalized
                    and memory.first_fill_ts is not None
                    and time.time() < memory.first_fill_ts + config.probation_max_s
                    and q.size <= config.first_leg_qty - first_qty + 1e-8)
        opposite_token = (meta.no.token_id if memory.first_leg_side == "YES"
                          else meta.yes.token_id)
        unpaired = first_qty - pair_qty
        return (memory.state in (StrategyState.RUNNER, StrategyState.PAIR_STOP,
                                 StrategyState.FORCED_PAIR)
                and q.token_id == opposite_token and q.size <= unpaired + 1e-8)

    async def _quarantine(self, meta: MarketMeta, reason: str) -> None:
        """Cancel all orders on a market's tokens and resync state from REST."""
        cid = meta.condition_id
        self._order_state_uncertain.add(cid)
        log.warning("quarantine", cid=meta.condition_id[:8], reason=reason)
        for tok in (meta.yes.token_id, meta.no.token_id):
            if await self.gateway.cancel_asset(tok):
                for o in self.state.orders_for(tok):
                    self.state.remove_order(o.order_id)
        await self._refresh_token_orders(meta, grace_s=0.0)
        self._order_state_uncertain.discard(cid)
        self._wake_cid(cid)

    async def _refresh_token_orders(self, meta: MarketMeta, grace_s: float = 0.0) -> None:
        """Open-orders resync for one market's tokens (grace_s=0 = authoritative)."""
        live = await self.gateway.open_orders()
        for tok in (meta.yes.token_id, meta.no.token_id):
            self.state.replace_open_orders(
                tok, [o for o in live if o.token_id == tok], grace_s=grace_s
            )
            self._research_order_snapshot(meta.condition_id, tok, "TARGETED_REST", grace_s)

    def _maybe_merge(self, cid: str, meta: MarketMeta, p: StrategyProfile,
                     yes_size: float, no_size: float) -> None:
        """Live automatic merge is disabled pending an approved RPC-free design."""
        return

    async def _merge_task(self, cid: str, meta: MarketMeta, amount: float) -> None:
        try:
            # serialize all on-chain txs so concurrent merges can't reuse a nonce;
            # read on-chain balances as source of truth for the mergeable amount
            async with self._chain_lock:
                bals = await self.gateway.token_balances([meta.yes.token_id, meta.no.token_id])
                if bals:
                    amount = min(amount, bals.get(meta.yes.token_id, 0.0),
                                 bals.get(meta.no.token_id, 0.0))
                raw = int(amount * 1e6)
                if raw <= 0:
                    return
                await asyncio.to_thread(self.merger.merge, meta.condition_id, raw, meta.neg_risk)
        finally:
            self._merging.discard(cid)

    # ── background loops ────────────────────────────────────────────────
    async def _heartbeat_loop(self) -> None:
        if not self.cfg.engine.heartbeat:
            return
        halt_after = self.cfg.risk.heartbeat_halt_failures
        while self._running:
            ok = await self.gateway.heartbeat()
            if not ok and self.gateway.heartbeat_failures >= halt_after and not self._hb_was_down:
                # exchange is (or soon will be) auto-cancelling everything we
                # have live; recompute will see hb_blind and pull quotes
                self._hb_was_down = True
                log.critical("heartbeat_down_halting", failures=self.gateway.heartbeat_failures)
                self._wake_all()
            elif ok and self._hb_was_down:
                # Only resume after every authoritative order snapshot succeeds.
                log.warning("heartbeat_recovered_resyncing")
                try:
                    for meta in self.metas.values():
                        await self._refresh_token_orders(meta, grace_s=0.0)
                except Exception as exc:  # noqa: BLE001
                    log.warning("heartbeat_recovery_resync_failed", err=str(exc))
                else:
                    self._hb_was_down = False
                    self._wake_all()
            await asyncio.sleep(self.cfg.engine.heartbeat_interval_s)

    async def _reconcile_loop(self) -> None:
        rounds = 0
        while self._running:
            # periodic cadence, but wake immediately when a reconnect/recovery
            # demands an urgent resync
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(
                    self._reconcile_now.wait(),
                    timeout=self.cfg.engine.reconcile_interval_s,
                )
            forced = self._reconcile_now.is_set()
            self._reconcile_now.clear()
            recovery_generation = (
                self._market_recovery_generation
                if self._market_recovery_pending and self.md.connected
                and all(self._market_books_fresh(cid) for cid in self.metas)
                else None
            )
            rounds += 1
            try:
                # a MATCHED whose settlement event was lost would block a token's
                # reconciliation forever — expire stale in-flight guards first
                expired = self.state.expire_inflight(self.cfg.engine.reconcile_interval_s * 2)
                if expired:
                    self.alerter.alert("inflight_expired",
                                       f"{len(expired)} stuck in-flight guards cleared")

                positions = self._only_traded(await self.gateway.positions())
                if positions:
                    self.state.reconcile_positions(positions)
                live = await self.gateway.open_orders()
                by_token: dict[str, list[Any]] = {}
                for o in live:
                    by_token.setdefault(o.token_id, []).append(o)
                # iterate ALL our tokens, not just those in the REST response — a
                # token whose orders vanished server-side must be cleaned up too.
                # Hold the market lock so we don't race the quoter mid-flight.
                for cid, meta in self.metas.items():
                    lock = self._locks.get(cid)
                    if lock is None:
                        continue
                    async with lock:
                        market_orders_reconciled = all(
                            self.state.inflight(tok) == 0
                            for tok in (meta.yes.token_id, meta.no.token_id)
                        )
                        for tok in (meta.yes.token_id, meta.no.token_id):
                            if self.state.inflight(tok) == 0:
                                self.state.replace_open_orders(
                                    tok, by_token.get(tok, []),
                                    grace_s=0.0 if cid in self._order_state_uncertain else 10.0,
                                )
                                self._research_order_snapshot(cid, tok, "PERIODIC_REST",
                                    0.0 if cid in self._order_state_uncertain else 10.0)
                        if cid in self._favorite_mismatch_pending:
                            tokens = (meta.yes.token_id, meta.no.token_id)
                            recent = any(self.state.has_recent_fill(tok) for tok in tokens)
                            if not recent and all(self.state.inflight(tok) == 0 for tok in tokens):
                                if self._favorite_economics(cid)["mismatch"]:
                                    reads = self._favorite_mismatch_reads.get(cid, 0) + 1
                                    self._favorite_mismatch_reads[cid] = reads
                                    if reads >= 2:
                                        self._favorite_mismatch_confirmed.add(cid)
                                    else:
                                        self._reconcile_now.set()
                                else:
                                    self._favorite_mismatch_pending.discard(cid)
                                    self._favorite_mismatch_reads.pop(cid, None)
                                self._wake_cid(cid)
                        if market_orders_reconciled:
                            self._order_state_uncertain.discard(cid)
                if forced:
                    log.info("forced_reconcile_done", positions=len(positions),
                             open_orders=len(live))
                    self._wake_all()
                if (self._running and recovery_generation is not None
                        and recovery_generation == self._market_recovery_generation
                        and self._market_recovery_pending and self.md.connected
                        and not self._order_state_uncertain
                        and all(self._market_books_fresh(cid) for cid in self.metas)):
                    self._market_recovery_pending = False
                    self._market_recovery_generation = None
                    log.info("market_reconcile_after_ws_recovery", markets=len(self.metas))
                    log.info("market_ws_trading_resumed", markets=len(self.metas))
                    self._wake_all()
            except Exception as exc:  # noqa: BLE001
                log.warning("reconcile_error", err=str(exc))

            # Slower local bookkeeping; position truth remains on the existing
            # Polymarket Data API and Fill Truth reconciliation paths.
            self.state.record_pnl(self.risk.equity, self.risk.net_cash,
                                  self.risk.inventory_value, self.risk.daily_pnl)
            if rounds % 20 == 0:
                self.state.checkpoint_wal()

    async def refresh_market_metadata(self) -> None:
        """Pull fresh metadata from Gamma for all traded markets: halt on
        closed/not-accepting, and freshen reward/fee/end-date params so we quote
        at the CURRENT reward minimum, band, and fees (these change over time —
        e.g. the reward min-size jumping 50->100 shares). Called at startup and
        periodically. Safe to await."""
        if not self.metas:
            return
        try:
            async with GammaClient(self.cfg.wallet.gamma_host) as gamma:
                raws = await gamma.markets_by_condition(list(self.metas))
        except Exception as exc:  # noqa: BLE001
            log.warning("metadata_refresh_error", err=str(exc))
            return
        for cid, raw in raws.items():
            if cid not in self.metas:
                continue
            accepting = bool(raw.get("acceptingOrders", True))
            closed = bool(raw.get("closed", False))
            if closed or not accepting:
                if cid in self._favorite:
                    self._favorite_halt_reasons.setdefault(cid, "MARKET_METADATA_HALT")
                if cid not in self._halted:
                    self._halted.add(cid)
                    log.critical("market_halted_by_meta", cid=cid[:8], closed=closed,
                                 accepting=accepting)
                    self.alerter.alert(f"halted:{cid[:8]}",
                                       f"{self.metas[cid].question[:40]} closed/not-accepting",
                                       critical=True)
                    meta = self.metas[cid]
                    for tok in (meta.yes.token_id, meta.no.token_id):
                        with contextlib.suppress(Exception):
                            await self.gateway.cancel_asset(tok)
                    self._wake_cid(cid)
                continue
            self._halted.discard(cid)
            self._apply_meta_refresh(cid, raw)

    def _apply_meta_refresh(self, cid: str, raw: dict[str, Any]) -> None:
        import dataclasses

        old = self.metas[cid]
        fee = raw.get("feeSchedule") or {}
        rate = _fnum(fee.get("rate"))
        candidates: dict[str, Any] = {
            "rewards_min_size": _fnum(raw.get("rewardsMinSize")),
            "rewards_max_spread": _fnum(raw.get("rewardsMaxSpread")),
            "taker_fee_bps": int(round(rate * 10000)) if rate is not None else None,
            "rebate_rate": _fnum(fee.get("rebateRate")),
            "end_date_iso": raw.get("endDate"),
            "min_order_size": _fnum(raw.get("orderMinSize")),
        }
        updates = {k: v for k, v in candidates.items()
                   if v is not None and getattr(old, k) != v}
        if updates:
            self.metas[cid] = dataclasses.replace(old, **updates)
            log.info("meta_refreshed", cid=cid[:8], **updates)
            self._wake_cid(cid)

    async def _metadata_refresh_loop(self) -> None:
        while self._running:
            await asyncio.sleep(self.cfg.engine.catalog_refresh_s)
            await self.refresh_market_metadata()

    async def _maintenance_loop(self) -> None:
        """Periodic REST book refresh to catch any silently-missed WS deltas."""
        while self._running:
            await asyncio.sleep(120.0)
            for meta in list(self.metas.values()):
                for tok in (meta.yes.token_id, meta.no.token_id):
                    with contextlib.suppress(Exception):
                        await self._refresh_book(tok)

    async def _refresh_book(self, token_id: str) -> None:
        levels = await self.gateway.get_full_book(token_id)
        rest_receipt = clock() if self.research is not None else (None, None)
        if levels is None:
            return
        bids, asks, book_hash = levels
        book = self.md.book(token_id)
        if book is None:
            return
        # drift check: only overwrite if the REST top-of-book disagrees with ours
        cur_bb = book.best_bid()
        cur_ba = book.best_ask()
        rest_bb = max((p for p, _ in bids), default=None)
        rest_ba = min((p for p, _ in asks), default=None)
        drift = (
            (cur_bb is None) != (rest_bb is None)
            or (cur_ba is None) != (rest_ba is None)
            or (cur_bb and rest_bb and abs(cur_bb.price - rest_bb) > book.tick_size)
            or (cur_ba and rest_ba and abs(cur_ba.price - rest_ba) > book.tick_size)
        )
        if drift:
            log.warning("book_drift_corrected", token=token_id[:12])
            book.apply_snapshot(bids, asks, time.time(), book_hash)
            cid = self._token_cid.get(token_id)
            if self.research is not None:
                replacement_seq = self._research_rest_replacements.get(token_id, 0) + 1
                self._research_rest_replacements[token_id] = replacement_seq
                self._research_emit("REST_BOOK", cid or "", {
                    "token_id": token_id, "bids": bids, "asks": asks,
                    "book_hash": book_hash, "tick_size": book.tick_size,
                    "connection_generation": self.md.connection_generation,
                    "rest_replacement_seq": replacement_seq,
                }, source="REST", received_at_us=rest_receipt[0],
                    received_monotonic_ns=rest_receipt[1],
                    connection_generation=self.md.connection_generation)
            if cid:
                self._wake_cid(cid)

    # ── helpers ─────────────────────────────────────────────────────────
    @staticmethod
    def _research_quote(quote: Any) -> dict[str, Any] | None:
        if quote is None:
            return None
        return {"token_id": quote.token_id, "side": quote.side.value,
                "price": quote.price, "size": quote.size}

    def _research_order_snapshot(self, cid: str, token_id: str, reason: str,
                                 grace_s: float) -> None:
        capture = getattr(self, "research", None)
        if capture is None:
            return
        try:
            orders = []
            for order in self.state.orders_for(token_id):
                owned = self.state.owned_order(order.order_id)
                role = ({"favorite_first_leg": "FIRST", "favorite_pair_leg": "PAIR"}.get(str(owned["source"]))
                        if owned is not None else None)
                orders.append({"order_id": order.order_id, "side": order.side.value,
                    "price": order.price, "remaining_qty": order.size, "state": order.state.value,
                    "role": role})
            self._research_emit("ORDER_STATE", cid, {"token_id": token_id,
                "reason": reason, "grace_s": grace_s, "orders": orders}, source="REST")
        except Exception:
            # Additional Research reads must not interrupt authoritative resync.
            capture.failures += 1

    @staticmethod
    def _research_primitive(value: Any) -> Any:
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, dict):
            return {key: Engine._research_primitive(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [Engine._research_primitive(item) for item in value]
        return value

    def _research_emit(self, kind: str, cid: str, payload: dict[str, Any], *,
                       source: str = "ENGINE", received_at_us: int | None = None,
                       received_monotonic_ns: int | None = None,
                       connection_generation: int | None = None) -> None:
        capture = getattr(self, "research", None)
        if capture is not None:
            capture.emit(kind, cid, payload, source=source,
                received_at_us=received_at_us,
                received_monotonic_ns=received_monotonic_ns,
                connection_generation=connection_generation)

    def _other_token(self, token_id: str) -> str | None:
        cid = self._token_cid.get(token_id)
        return self.metas[cid].other_token(token_id) if cid else None

    def _cid_of_token(self, token_id: str) -> str | None:
        return self._token_cid.get(token_id)

    def _event_group_cost(self, meta: MarketMeta) -> float:
        if not meta.event_id:
            return 0.0
        cost = 0.0
        for m in self.metas.values():
            if m.event_id == meta.event_id:
                for tok in (m.yes.token_id, m.no.token_id):
                    pos = self.state.position(tok)
                    cost += pos.size * pos.avg_price
        return cost


def _fnum(v: object) -> float | None:
    if v is None:
        return None
    try:
        return float(v)  # type: ignore[arg-type]
    except (ValueError, TypeError):
        return None


def _hours_to_end(end_date_iso: str | None, now: float) -> float | None:
    if not end_date_iso:
        return None
    try:
        dt = datetime.fromisoformat(end_date_iso.replace("Z", "+00:00"))
        hrs = (dt.timestamp() - now) / 3600.0
        # A past end date on a still-trading market is a stale/placeholder date
        # (common for "next X" appointment markets) — treat as unknown so we
        # don't wrongly HALT. The true end is signalled by acceptingOrders=False,
        # which the metadata refresh already halts on.
        return hrs if hrs > 0.0 else None
    except (ValueError, TypeError):
        return None


def _empty_view() -> Any:
    from polymaker.marketdata.orderbook import BookView

    return BookView(None, 0.0, None, 0.0, None, None, 0.0, 0.0)

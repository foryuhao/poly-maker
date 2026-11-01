"""M1 single-engine policy; signing, WAL, writes and close proofs stay shared."""

from dataclasses import asdict
from decimal import Decimal

from polymaker.domain import Quote, Side
from polymaker.rdp_sm import _require
from polymaker.rdp_sm_execution import WindowQuote
from polymaker.rdp_sm_lifecycle import Candidate
from polymaker.rdp_sm_m1_policy import budget_quantity as _budget_quantity
from polymaker.rdp_sm_normal import BANormalController
from polymaker.rdp_sm_quotes import QuoteUnavailable


def budget_quantity(price, minimum, budget=Decimal(5)):
    try:
        return _budget_quantity(price, minimum, budget)
    except ValueError as exc:
        raise QuoteUnavailable(str(exc)) from exc


class M1Controller(BANormalController):
    phase = "M1"
    m1_mode = True

    def __init__(self, executor, lifecycle):
        super().__init__(executor, lifecycle)
        self.marked_rf, self.marked_ex = set(), set()

    @property
    def episodes(self):
        return self.lifecycle.feed.m1

    @property
    def cutoff(self):
        return self.executor.cfg.rdp_sm.m1.cutoff_sec

    def select(self, snapshot):
        signal = self.episodes.evaluate(snapshot)
        self.signal_block_reason = "M1_NO_OPPORTUNITY" if not signal.direction else None
        return signal.direction, signal

    def snapshot(self):
        feed = self.lifecycle.feed
        with feed.lock:
            detector = self.episodes
            if detector.claimed is not None:
                ep = next((v for v in detector.opportunities.values()
                           if v.episode_id == detector.claimed[0]), None)
            else:
                detector.evaluate(feed.snapshot(ba_cfg=self.executor.cfg.rdp_sm.ba))
                ep = detector.selected
            # M1 evidence is token-local. An unrelated quiet counterpart book
            # must not veto an otherwise valid signal or choose its direction.
            return feed.snapshot(repair_token=ep.token_id if ep else None,
                                 ba_cfg=self.executor.cfg.rdp_sm.ba)

    def deadline(self, snapshot):
        with self.lifecycle.feed.lock:
            self.select(snapshot)
            ep = self.episodes.selected
            if ep is None:
                return 0.
            e = self.executor
            market_deadline = e.time_anchor + int(e.slug.rsplit("-", 1)[1]) + 240 - e.official_time
            return min(ep.order_deadline, market_deadline)

    def candidates(self, snapshot, yes, no, window_id):
        direction, _ = self.select(snapshot)
        ep = self.episodes.selected
        if not direction or ep is None:
            return {}
        book = snapshot.yes if direction == "YES" else snapshot.no
        if (not any(p == ep.anchor_bid_price and q > 0 for p, q in book.bids)
                if self.episodes.cfg.track_visible_anchor else book.best_bid != ep.anchor_bid_price):
            return {}
        q = budget_quantity(ep.anchor_bid_price, self.executor.meta.min_order_size)
        quote = WindowQuote(self.executor.meta.condition_id, ep.token_id, direction, Side.BUY,
                            float(ep.anchor_bid_price), float(q), window_id)
        evidence = {"normal_entry_mode": "m1_v1", "order_policy": "M1_SIGNAL_SIDE_ONLY",
            "order_budget_usdc": "5", "session_budget_usdc": "20",
            "signed_target_delta": "0", "target_sizing_applied": False,
            "favorite_data_validity": "NOT_USED", "actual_delta_before": str(yes-no),
            "opportunity": {k: str(v) if isinstance(v, Decimal) else v for k, v in asdict(ep).items()}}
        return {direction: ((quote,), evidence)}

    def admission(self, deadline):
        e, cfg = self.executor, self.executor.cfg.rdp_sm.m1
        writes = e.reads.network.recent_writes()
        post = max(cfg.post_budget_floor_ms, writes.post.max_ms or 0.)
        remaining = (deadline-e.monotonic())*1000
        reason = ("M1_CANCEL_LATENCY_HIGH" if writes.delete.max_ms is not None
                  and writes.delete.max_ms > cfg.max_cancel_rtt_ms else
                  "M1_EXECUTION_BUDGET_INSUFFICIENT" if remaining < post+cfg.min_live_budget_ms else None)
        return {"reason": reason, "remaining_ms": remaining, "post_budget_ms": post,
                "min_live_budget_ms": cfg.min_live_budget_ms,
                "post_observed": writes.post.max_ms is not None,
                "bootstrap_basis": "native POST 307.334ms; historical 20261031 write trace; 400ms floor"}

    async def prepare(self, yes, no, window_id):
        e, life = self.executor, self.lifecycle
        _require(not life.busy, "BATCH_ALREADY_ACTIVE")
        _require(not e.attempts or e.safe_to_continue, "PREVIOUS_WINDOW_UNRESOLVED")
        snapshot = life.snapshot(self, yes, no)
        if life.feed.m1_pending_received or life.feed.m1_processing_received is not None:
            life.wait("M1_INPUT_PENDING", snapshot)
            return None
        elapsed = e.official_time+e.monotonic()-e.time_anchor-int(e.slug.rsplit("-", 1)[1])
        if not e.cfg.rdp_sm.m1.entry_start_sec <= elapsed < 240 or not snapshot.valid or e.inventory_mismatch is not None:
            life.wait(snapshot.reason or "M1_TIME_OR_ACCOUNT_UNAVAILABLE", snapshot)
            return None
        direction, _ = self.select(snapshot)
        if not direction:
            life.wait("M1_NO_OPPORTUNITY", snapshot)
            return None
        admission = self.admission(self.deadline(snapshot))
        if admission["reason"]:
            life.wait(admission["reason"], snapshot)
            return None
        if not e.network_health().allowed or not e.journal.ready:
            return None
        if e.committed()+5 > e.capital_limit or Decimal(str(life.account["basic_spendable_budget_usd"])) < 5:
            life.wait("M1_FULL_ORDER_BUDGET_UNAVAILABLE", snapshot)
            return None
        if e.cfg.rdp_sm.m1.observation_only:
            ep = self.episodes.selected
            if ep.rf_id in self.marked_rf or ep.ex_id in self.marked_ex:
                return None
            try:
                plans = self.candidates(snapshot, yes, no, window_id)
                if not plans:
                    return None
                quotes, sizing = plans[direction]
                for q in quotes:
                    e._validate(q, window_id, snapshot.books(), e.meta)
            except QuoteUnavailable as exc:
                life.wait(str(exc), snapshot)
                return None
            marker = {"marker": "WOULD_POST", "observation_only": True, "post_started": False,
                      "trading_window": {"start_sec": e.cfg.rdp_sm.m1.entry_start_sec, "cutoff_sec": 240},
                      "signal_parameters": self.episodes.parameters,
                      "parameter_hash": self.episodes.parameter_hash,
                      "elapsed_s": elapsed, "quotes": [asdict(q) for q in quotes],
                      "opportunity": sizing["opportunity"], "admission": admission,
                      "note": "No signature, native POST, reservation or simulated fill"}
            e.record("sm_m1_would_post", marker)
            self.episodes.emit("WOULD_POST", **marker)
            self.marked_rf.add(ep.rf_id)
            self.marked_ex.add(ep.ex_id)
            return None
        ticks = {}
        client = e.gateway._client
        for token, book in life.feed.books.items():
            cached = client._ClobClient__tick_sizes.get(token)
            ticks[token] = (float(cached) if cached is not None and Decimal(str(cached)) == book.tick else
                            float(await life._read_preparation_constraint(client.get_tick_size, token)))
        life.feed.verify_ticks(ticks)
        snapshot = life.snapshot(self, yes, no)
        try:
            plans = self.candidates(snapshot, yes, no, window_id)
        except QuoteUnavailable as exc:
            life.wait(str(exc), snapshot)
            return None
        if not plans:
            return None
        candidates = []
        for direction, (quotes, sizing) in plans.items():
            for q in quotes:
                e._validate(q, window_id, snapshot.books(), e.meta)
            signed = await e.gateway.prepare([Quote(q.token_id, q.side, q.price, q.size) for q in quotes], e.meta, ticks)
            candidates.append(Candidate(direction, quotes, signed, sizing))
        life.first_eligible.setdefault(self.episodes.episode_id, self.episodes.episode_start)
        return await life.persist_slot(self, snapshot, yes, no, window_id, candidates)

    def guard(self, intent, revision, deadline, *, pending_batch=()):
        e, life = self.executor, self.lifecycle
        if e.cfg.rdp_sm.m1.observation_only:
            life.guard_reason = "M1_OBSERVATION_ONLY"
            return False
        now = e.monotonic()
        current = life.snapshot(self, intent.yes, intent.no)
        health = e.network_health()
        life.final_evidence = current, health
        elapsed = e.official_time+now-e.time_anchor-int(e.slug.rsplit("-", 1)[1])
        admission = self.admission(deadline[0])
        life.ba_admission = admission
        checks = [
            ("SESSION_CLOSED", not e.closed),
            (e.fatal or "FATAL_EXECUTION_STATE", e.fatal is None),
            ("ASYNC_JOURNAL_NOT_READY", e.journal.ready),
            ("CAPITAL_EVIDENCE_CHANGED_BEFORE_SUBMIT", e.revision == revision),
            ("ACCOUNT_INVENTORY_NOT_EXPLAINED_BY_SM", e.inventory_mismatch is None),
            ("ACCOUNT_PAGINATION_UNRESOLVED", e.gateway._client.pagination_failure is None),
            ("ACCOUNT_EVIDENCE_STALE", now-e.time_anchor <= e.cfg.risk.ws_stale_halt_s),
            ("MARKET_SUBMISSION_CUTOFF", e.cfg.rdp_sm.m1.entry_start_sec <= elapsed < 240),
            (current.reason or "WS_INVALID", current.valid),
            ("M1_INPUT_PENDING", not life.feed.m1_pending_received
             and life.feed.m1_processing_received is None),
            ("WS_GENERATION_CHANGED", current.generation == intent.snapshot.generation),
            ("INTENT_EXPIRED", now < deadline[0]),
            ("M1_EPISODE_CHANGED", self.episodes.current(intent.claim)),
            (health.reason, health.allowed),
            (admission["reason"], admission["reason"] is None),
            ("M1_BUDGET_EXCEEDED", e.committed() <= 20),
            ("M1_ORDER_BUDGET_EXCEEDED", all(Decimal(str(q.price))*Decimal(str(q.size)) <= 5 for q in intent.quotes)),
            ("FINAL_QUOTES_OR_DIRECTION_CHANGED", self.matches(intent, current)),
        ]
        if e.shared_reads is not None:
            checks.append(("SYNC_CACHE_EVIDENCE_CHANGED", e.shared_reads.admission_current(pending_batch=pending_batch)))
        life.guard_reason = next((r for r, ok in checks if not ok), None)
        return life.guard_reason is None


class M1Engine:
    """Inventory-independent state machine using the existing session coordinator."""

    def __init__(self, executor, lifecycle):
        self.executor = executor
        self.controller = M1Controller(executor, lifecycle)
        self.state = None
        self.transition("PREPARING")

    def transition(self, state):
        if state != self.state:
            previous, self.state = self.state, state
            self.executor.record("sm_m1_state", {"previous": previous, "state": state})

    def advance(self, elapsed):
        if elapsed < self.executor.cfg.rdp_sm.m1.entry_start_sec:
            self.transition("PREPARING")
        elif elapsed < 240:
            self.transition("TRADING")
        elif self.state not in {"MERGING", "HOLD_SETTLEMENT"}:
            self.transition("DRAINING")

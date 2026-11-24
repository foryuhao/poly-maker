"""Single-sided NORMAL entry, with configurable confirmations in each time band."""

from dataclasses import asdict, replace
from decimal import Decimal

from polymaker.rdp_sm_quotes import QuoteUnavailable, make_quotes
from polymaker.rdp_sm_signal import evaluate


def normal_quotes(meta, snapshot, yes, no, window_id, cfg, direction, *, sigma=None, **clock):
    if not snapshot.valid or direction not in {"YES", "NO"}:
        raise QuoteUnavailable(snapshot.reason or "OBI_SIGNAL_MISSING")
    state, quotes = make_quotes(
        meta, snapshot.books(), yes, no, window_id, cfg,
        signal_side=direction, normal_sigma=sigma, **clock
    )
    if state != "NORMAL" or len(quotes) != 1 or quotes[0].outcome != direction:
        raise QuoteUnavailable("NORMAL_INVENTORY_CHANGED")
    return quotes


class NormalController:
    phase = "NORMAL"

    @property
    def cutoff(self):
        return self.executor.cfg.rdp_sm.normal_cutoff_sec

    @property
    def episodes(self):
        return self.lifecycle.feed.episodes

    def __init__(self, executor, lifecycle):
        self.executor, self.lifecycle = executor, lifecycle
        self.sigma_ready = None
        self.quote_block_reason = None
        self.signal_block_reason = None
        self.signal_confirmation = None

    def volatility(self, snapshot):
        from polymaker.rdp_sm_volatility import estimate_sigma

        feed = self.lifecycle.feed
        # Bind the volatility input to this immutable book, including guards
        # running in SDK workers while the feed can apply a later frame.
        last = max((snapshot.yes, snapshot.no), key=lambda b: b.applied_mono)
        wall = last.received_wall+last.applied_mono-last.received_mono
        return estimate_sigma(feed, monotonic=last.applied_mono,
                              wall=wall, generation=snapshot.generation)

    def candidates(self, snapshot, yes, no, window_id):
        result = {}
        self.quote_block_reason = None
        estimate = self.volatility(snapshot)
        key = (estimate.generation, estimate.sigma is not None)
        if key != self.sigma_ready:
            self.sigma_ready = key
            self.executor.record("sm_sigma_readiness", {
                "phase": "NORMAL", "ready": estimate.sigma is not None,
                **asdict(estimate)})
        if estimate.sigma is None:
            self.quote_block_reason = "NORMAL_VOLATILITY_UNKNOWN"
            return result
        for direction in ("YES", "NO"):
            evidence = {}
            try:
                quotes = normal_quotes(
                    self.executor.meta,
                    snapshot,
                    yes,
                    no,
                    window_id,
                    self.executor.cfg.rdp_sm,
                    direction,
                    sigma=estimate.sigma,
                    evidence=evidence,
                    **self.lifecycle.clock(),
                )
                result[direction] = (tuple(quotes), evidence)
            except QuoteUnavailable as exc:
                self.quote_block_reason = str(exc)
                continue
        return result

    def select(self, snapshot):
        signal = evaluate(snapshot, self.executor.cfg.rdp_sm.obi.threshold)
        self.signal_block_reason = None
        self.signal_confirmation = None
        e = self.executor
        start = int(e.slug.rsplit("-", 1)[1])
        elapsed = e.official_time+e.monotonic()-e.time_anchor-start
        if 0 <= elapsed < self.cutoff:
            cfg = e.cfg.rdp_sm.obi
            band_start = 0 if elapsed < 60 else 60
            required = (cfg.normal_confirmations_before_60 if elapsed < 60
                        else cfg.normal_confirmations_from_60)
            since = e.time_anchor+start+band_start-e.official_time
            samples = self.lifecycle.feed.normal_confirmations(snapshot, since)[-required:]
            self.signal_confirmation = {"required": required, "elapsed_s": elapsed,
                "band_start_s": band_start,
                "samples": [asdict(s) for s in samples]}
            if signal.direction and len(samples) < required:
                self.signal_block_reason = "NORMAL_SIGNAL_CONFIRMATION_PENDING"
                return None, signal
        return signal.direction, signal

    def deadline(self, snapshot):
        return snapshot.signal_event + self.executor.cfg.rdp_sm.obi.signal_horizon_sec

    def matches(self, intent, snapshot):
        direction, _ = self.select(snapshot)
        if direction != intent.direction:
            return False
        try:
            quotes = normal_quotes(
                self.executor.meta,
                snapshot,
                intent.yes,
                intent.no,
                intent.window_id,
                self.executor.cfg.rdp_sm,
                direction,
                sigma=self.volatility(snapshot).sigma,
                **self.lifecycle.clock(),
            )
            return tuple(quotes) == intent.quotes
        except (ValueError, ArithmeticError, TypeError):
            return False

    async def enter(self, yes: Decimal, no: Decimal, window_id):
        slot = await self.lifecycle.prepare(self, yes, no, window_id)
        if slot:
            return await self.lifecycle.activate(self, slot)
        await self.lifecycle.feed.wait_after(self.lifecycle.feed.sequence, .1)
        return None


class BANormalController(NormalController):
    """BA direction and lifetime, sharing only NORMAL quote sizing/execution."""

    immediate_activation = True

    @property
    def episodes(self):
        return self.lifecycle.feed.ba

    def candidates(self, snapshot, yes, no, window_id):
        result = {}
        self.quote_block_reason = None
        for direction in ("YES", "NO"):
            evidence = {}
            try:
                # Sigma does not enter the existing sizing formula. BA has no
                # volatility readiness gate; 0 uses the same legal bid/sizing.
                quotes = normal_quotes(self.executor.meta, snapshot, yes, no, window_id,
                    self.executor.cfg.rdp_sm, direction, sigma=0, evidence=evidence,
                    normal_taker=getattr(self, "taker", False),
                    **self.lifecycle.clock(normal=True))
                evidence.pop("normal_sigma", None)
                evidence.pop("sigma_window_seconds", None)
                evidence["normal_entry_mode"] = self.executor.cfg.rdp_sm.normal_entry_mode
                result[direction] = (tuple(quotes), evidence)
            except QuoteUnavailable as exc:
                self.quote_block_reason = str(exc)
        return result

    def select(self, snapshot):
        signal = self.episodes.evaluate(snapshot)
        self.signal_block_reason = "BA_NO_SIGNAL" if signal.direction is None else None
        return signal.direction, signal

    def deadline(self, snapshot):
        with self.lifecycle.feed.lock:
            self.select(snapshot)
            return self.episodes.selected.order_deadline if self.episodes.selected else 0

    def matches(self, intent, snapshot):
        with self.lifecycle.feed.lock:
            direction, _ = self.select(snapshot)
            if (direction != intent.direction or self.episodes.selected is None
                    or intent.claim is not None and self.episodes.selected.episode_id != intent.claim[0]):
                return False
            return self.candidates(snapshot, intent.yes, intent.no, intent.window_id).get(
                direction, (None,))[0] == intent.quotes

    def active_close_reason(self, intent, snapshot):
        return self.episodes.active_reason(intent.claim, snapshot)

    async def enter(self, yes, no, window_id):
        slot = await self.lifecycle.prepare(self, yes, no, window_id)
        if slot:
            result = await self.lifecycle.activate(self, slot)
            if result is not None:
                return result
        await self.lifecycle.feed.wait_after(self.lifecycle.feed.sequence, .1)
        return None


class CANormalController(BANormalController):
    """CA trigger/first-SELL lease, with an independently selected opening side."""

    @property
    def episodes(self):
        return self.lifecycle.feed.ca_entry

    @property
    def taker(self):
        return self.executor.cfg.rdp_sm.ca_entry_execution == "taker"

    @property
    def quote_after_constraints(self):
        return self.taker or self.executor.cfg.rdp_sm.ca_matched_tp.taker_exit_on_expiry

    @property
    def ba_config(self):
        return self.executor.cfg.rdp_sm.ba

    async def prepare_execution_constraints(self):
        if (not (self.taker or self.executor.cfg.rdp_sm.ca_matched_tp.taker_exit_on_expiry)
                or getattr(self, "taker_fees", None) is not None):
            return
        from polymaker.rdp_sm_ca_taker import fee_parameters

        e = self.executor
        raw = await self.lifecycle._read_preparation_constraint(
            e.gateway._client.get_clob_market_info, e.meta.condition_id)
        self.taker_fees = fee_parameters(raw, (e.meta.yes.token_id, e.meta.no.token_id))
        e.record("sm_ca_taker_fee_parameters", {"market_id": e.meta.condition_id,
                 **self.taker_fees, "source": "OFFICIAL_CLOB_MARKET_INFO"})

    def candidates(self, snapshot, yes, no, window_id):
        if not self.executor.cfg.rdp_sm.ca_pullback.enabled:
            result = super().candidates(snapshot, yes, no, window_id)
            if self.taker:
                if getattr(self, "taker_fees", None) is None:
                    self.quote_block_reason = "CA_TAKER_FEE_PARAMETERS_UNAVAILABLE"
                    return {}
                for direction, (quotes, evidence) in list(result.items()):
                    if any(Decimal(str(q.price))*Decimal(str(q.size)) < 1 for q in quotes):
                        self.quote_block_reason = "CA_TAKER_MIN_NOTIONAL"
                        del result[direction]
                        continue
                    evidence.update(ca_entry_execution="taker", ca_taker_fees=self.taker_fees,
                                    ca_taker=self.executor.cfg.rdp_sm.ca_taker.model_dump(mode="json"))
            return result
        self.quote_block_reason = None
        direction, _ = self.select(snapshot)
        ep = self.episodes.selected
        if direction is None or ep is None:
            return {}
        evidence = {}
        try:
            quotes = normal_quotes(
                self.executor.meta, snapshot, yes, no, window_id,
                self.executor.cfg.rdp_sm, direction, sigma=0, evidence=evidence,
                normal_entry_price=ep.entry_price, **self.lifecycle.clock(normal=True))
        except QuoteUnavailable as exc:
            self.quote_block_reason = str(exc)
            return {}
        evidence.pop("normal_sigma", None)
        evidence.pop("sigma_window_seconds", None)
        evidence.update(normal_entry_mode="ca_v1", ca_pullback=self.episodes.diagnostics())
        return {direction: (tuple(quotes), evidence)}

    def matches(self, intent, snapshot):
        if not self.taker:
            return super().matches(intent, snapshot)
        with self.lifecycle.feed.lock:
            direction, _ = self.select(snapshot)
            if (direction != intent.direction or self.episodes.selected is None
                    or intent.claim is not None and self.episodes.selected.episode_id != intent.claim[0]):
                return False
            # Keep the signed cap and size; a better Ask is allowed, never chase.
            return all(Decimal(str(q.price)) >= (snapshot.yes if q.outcome == "YES" else snapshot.no).best_ask
                       and Decimal(str(q.price)) <= self.executor.cfg.rdp_sm.ca_taker.max_price
                       and (not self.executor.cfg.rdp_sm.ca_taker.entry_price_ceiling_enabled
                            or Decimal(str(q.price)) < self.executor.cfg.rdp_sm.ca_taker.entry_price_ceiling)
                       for q in intent.quotes)

    def select(self, snapshot):
        signal = self.episodes.evaluate(snapshot)
        self.signal_block_reason = (self.episodes.block_reason or "CA_NO_SIGNAL"
                                    if signal.direction is None else None)
        if signal.direction and self.executor.cfg.rdp_sm.ca_entry_direction == "higher_bid":
            # Preserve the selected CA episode, its anchor, claim and t0 lease.
            # Only the executable order side changes. matches() repeats this
            # choice under the native POST guard, against the current books.
            if snapshot.yes.best_bid == snapshot.no.best_bid:
                self.signal_block_reason = "CA_ENTRY_PRICE_TIE"
                signal = replace(signal, label="NONE")
            else:
                direction = "YES" if snapshot.yes.best_bid > snapshot.no.best_bid else "NO"
                signal = replace(signal, label="VALID_" + direction)
        return signal.direction, signal

"""Single-sided NORMAL entry, with configurable confirmations in each time band."""

from dataclasses import asdict
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
    """CA direction/first-SELL lease, with the unchanged anchored Bid order path."""

    @property
    def episodes(self):
        return self.lifecycle.feed.ca_entry

    def select(self, snapshot):
        signal = self.episodes.evaluate(snapshot)
        self.signal_block_reason = "CA_NO_SIGNAL" if signal.direction is None else None
        return signal.direction, signal

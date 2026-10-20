"""NORMAL controller: one causal signal event, one maker order on its side."""

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
    cutoff = 120

    def __init__(self, executor, lifecycle):
        self.executor, self.lifecycle = executor, lifecycle
        self.sigma_ready = None
        self.quote_block_reason = None

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

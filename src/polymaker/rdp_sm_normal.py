"""NORMAL controller: one causal signal event, one maker order on its side."""

from decimal import Decimal

from polymaker.rdp_sm_quotes import QuoteUnavailable, make_quotes
from polymaker.rdp_sm_signal import evaluate


def normal_quotes(meta, snapshot, yes, no, window_id, cfg, direction, **clock):
    if not snapshot.valid or direction not in {"YES", "NO"}:
        raise QuoteUnavailable(snapshot.reason or "OBI_SIGNAL_MISSING")
    state, quotes = make_quotes(
        meta, snapshot.books(), yes, no, window_id, cfg, signal_side=direction, **clock
    )
    if state != "NORMAL" or len(quotes) != 1 or quotes[0].outcome != direction:
        raise QuoteUnavailable("NORMAL_INVENTORY_CHANGED")
    return quotes


class NormalController:
    phase = "NORMAL"
    cutoff = 120

    def __init__(self, executor, lifecycle):
        self.executor, self.lifecycle = executor, lifecycle

    def candidates(self, snapshot, yes, no, window_id):
        result = {}
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
                    evidence=evidence,
                    **self.lifecycle.clock(),
                )
                result[direction] = (tuple(quotes), evidence)
            except QuoteUnavailable:
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
                **self.lifecycle.clock(),
            )
            return tuple(quotes) == intent.quotes
        except (ValueError, ArithmeticError, TypeError):
            return False

    async def enter(self, yes: Decimal, no: Decimal, window_id):
        slot = await self.lifecycle.prepare(self, yes, no, window_id)
        return await self.lifecycle.activate(self, slot) if slot else None

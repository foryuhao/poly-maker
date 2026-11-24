"""REBALANCE controller: confirmed deficit, independent maker order lease."""

from polymaker.rdp_sm_quotes import QuoteUnavailable, make_quotes


def rebalance_quotes(meta, snapshot, yes, no, window_id, cfg, **clock):
    if not snapshot.valid:
        raise QuoteUnavailable(snapshot.reason)
    books = snapshot.books()
    # A legal empty/old opposite book cannot manufacture a Favorite.
    for token, book in books.items():
        if snapshot.signal_event - book["received_mono"] > cfg.obi.max_book_age_ms / 1000:
            books[token] = {**book, "best_bid": None, "best_ask": None}
    state, quotes = make_quotes(meta, books, yes, no, window_id, cfg, **clock)
    if state != "REBALANCE" or len(quotes) != 1:
        raise QuoteUnavailable("REBALANCE_INVENTORY_CHANGED")
    return quotes


class RebalanceController:
    phase = "REBALANCE"
    cutoff = 300

    def __init__(self, executor, lifecycle):
        self.executor, self.lifecycle = executor, lifecycle

    def candidates(self, snapshot, yes, no, window_id):
        evidence = {}
        quotes = rebalance_quotes(
            self.executor.meta,
            snapshot,
            yes,
            no,
            window_id,
            self.executor.cfg.rdp_sm,
            evidence=evidence,
            **self.lifecycle.clock(),
        )
        return {"REPAIR": (tuple(quotes), evidence)}

    def select(self, snapshot):
        return "REPAIR", None

    def matches(self, intent, snapshot):
        try:
            quotes = rebalance_quotes(
                self.executor.meta,
                snapshot,
                intent.yes,
                intent.no,
                intent.window_id,
                self.executor.cfg.rdp_sm,
                **self.lifecycle.clock(),
            )
            return tuple(quotes) == intent.quotes
        except (ValueError, ArithmeticError, TypeError):
            return False

    async def repair(self, yes, no, window_id):
        slot = await self.lifecycle.prepare(self, yes, no, window_id)
        return await self.lifecycle.activate(self, slot) if slot else None

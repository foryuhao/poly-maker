"""Public-book quality and fixed-queue arithmetic, independent of a strategy."""
from dataclasses import dataclass
from decimal import Decimal

ZERO = Decimal(0)


@dataclass
class Round:
    id: str
    start: float
    anchor: Decimal
    initial: Decimal
    low: Decimal
    hit: Decimal = ZERO
    hold_start: float | None = None
    held: bool = False


def legal(self, book, now):
    return (self.feed.connected and book.snapshot_generation == self.feed.generation
            and book.best_bid is not None and book.best_ask is not None
            and 0 < book.best_bid < book.best_ask < 1 and book.tick > 0
            and self.feed.tick_verified.get(book.token_id) == book.tick
            and 0 <= now-book.received_mono <= .200 + 1e-9
            and -.002-1e-9 <= self.feed.wall()-book.source_time <= .500 + 1e-9
            and (book.reported_bid is None or book.reported_bid == book.best_bid)
            and (book.reported_ask is None or book.reported_ask == book.best_ask))


def recovery(r, quantity):
    loss = r.initial-r.low
    return min(Decimal(1), max(ZERO, quantity-r.low)/loss) if loss > 0 else ZERO


def hold(self, r, quantity, now):
    if not self.qualified(r):
        return
    recovered = self.recovery(r, quantity)
    if recovered < Decimal('.2'):
        r.held = False
    if recovered < Decimal('.4'):
        r.hold_start = None
    elif r.hold_start is None and recovered >= Decimal('.5'):
        r.hold_start = now
    if r.hold_start is not None and min(now, self.feed.m1_watermark())-r.hold_start >= .100-1e-9:
        r.held = True

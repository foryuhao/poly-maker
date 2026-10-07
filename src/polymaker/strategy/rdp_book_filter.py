"""Read-only NORMAL Book Filter v0 evidence for one RDP market.

The Inactive predicate uses the approved cumulative absolute consecutive
Q_eff-change definition in ITER-0006/03_HUMAN_DECISION.md. This component
snapshots the existing official OrderBook and TradePrint inputs; it performs
no I/O and has no execution authority.
"""

from __future__ import annotations

import math
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal, DecimalException, Inexact, localcontext
from numbers import Real

from polymaker.domain import MarketMeta, Side
from polymaker.marketdata.orderbook import OrderBook
from polymaker.marketdata.parse import TradePrint

_ZERO = Decimal("0")
_ONE = Decimal("1")
_INACTIVE_LIMIT = Decimal("5")
_DEPTH_FLOOR = Decimal("5")
_DEPTH_RATIO = Decimal("4")
_DEPTH_MIN_HIGH = Decimal("50")
_TWO_SECONDS = Decimal("2")
_ONE_SECOND = Decimal("1")


@dataclass(frozen=True, slots=True)
class RDPBookSideEvidence:
    token_id: str
    tick_size: Decimal | None
    q_eff: Decimal | None
    depth_change_2s: Decimal | None
    real_trade_in_2s: bool
    bid_excursion_1s: Decimal | None
    inactive: bool | None
    fast_move: bool | None
    window_coverage_seconds: float
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class RDPBookFilterResult:
    ready: bool
    rejected: bool
    inactive: bool | None
    depth_extreme: bool | None
    depth_ratio: Decimal | None
    fast_move: bool | None
    reasons: tuple[str, ...]
    generation: int | None
    evaluated_at: float
    sides: tuple[RDPBookSideEvidence, RDPBookSideEvidence]


@dataclass(frozen=True, slots=True)
class _BookSample:
    at: Decimal
    bid: Decimal
    q_eff: Decimal
    tick: Decimal


@dataclass(slots=True)
class _SideHistory:
    samples: deque[_BookSample]
    trades: deque[Decimal]
    started_at: Decimal | None = None
    last_event_at: Decimal | None = None
    tick: Decimal | None = None
    invalid_reason: str | None = None

    @classmethod
    def create(cls) -> _SideHistory:
        return cls(deque(), deque())

    def reset(self, reason: str | None = None) -> None:
        self.samples.clear()
        self.trades.clear()
        self.started_at = None
        self.last_event_at = None
        self.tick = None
        self.invalid_reason = reason


class RDPBookFilter:
    """Rolling NORMAL book evidence for the YES and NO tokens of one market."""

    def __init__(self, market: MarketMeta) -> None:
        if not isinstance(market, MarketMeta) or not market.condition_id:
            raise ValueError("RDP book filter requires a market identity")
        if (not market.yes.token_id or not market.no.token_id
                or market.yes.token_id == market.no.token_id):
            raise ValueError("RDP book filter requires distinct outcome tokens")
        self.market = market
        self._tokens = (market.yes.token_id, market.no.token_id)
        self._history = {token: _SideHistory.create() for token in self._tokens}
        self._generation: int | None = None
        self._last_evaluated_at: Decimal | None = None

    def observe_book(self, token_id: str, book: OrderBook, received_at: float,
                     generation: int, *, condition_id: str) -> bool:
        """Snapshot one valid current book; foreign token events are ignored."""
        if token_id not in self._history:
            return False
        if not self._accept_generation(generation):
            self._reset_histories("INVALID_GENERATION")
            return False
        at = _finite_time(received_at)
        if (at is None or not isinstance(book, OrderBook)
                or condition_id != self.market.condition_id):
            self._invalidate(token_id, "INVALID_BOOK_EVIDENCE")
            return False
        history = self._history[token_id]
        if not self._accept_event_time(history, at):
            return False
        try:
            tick = _decimal(book.tick_size)
            best = book.best_bid()
            if tick is None or not _ZERO < tick < _ONE or best is None:
                raise ValueError("invalid tick or missing best bid")
            bid = _decimal(best.price)
            best_size = _decimal(best.size)
            if (bid is None or not _ZERO <= bid < _ONE or best_size is None
                    or best_size < _ZERO or bid % tick != _ZERO):
                raise ValueError("invalid best bid economics")
            neighbor_raw = next((size for price, size in book.bids.items()
                                 if _decimal(price) == bid - tick), 0.0)
            neighbor_size = _decimal(neighbor_raw)
            if neighbor_size is None or neighbor_size < _ZERO:
                raise ValueError("invalid tick-neighbor depth")
            if neighbor_size == _ZERO:
                q_eff = best_size
            else:
                with _exact_context():
                    q_eff = best_size + neighbor_size
        except DecimalException:
            self._invalidate(token_id, "UNSUPPORTED_PRECISION")
            return False
        except (TypeError, ValueError, OverflowError):
            self._invalidate(token_id, "INVALID_BOOK_EVIDENCE")
            return False

        if history.tick is not None and tick != history.tick:
            history.reset("TICK_CHANGED")
        history.tick = tick
        if history.started_at is None:
            history.started_at = at
        history.last_event_at = at
        history.invalid_reason = None
        history.samples.append(_BookSample(at, bid, q_eff, tick))
        return True

    def observe_trade(self, trade: TradePrint, received_at: float,
                      generation: int) -> bool:
        """Record a valid positive-size real trade for its outcome token."""
        if not isinstance(trade, TradePrint) or trade.asset_id not in self._history:
            return False
        token_id = trade.asset_id
        if not self._accept_generation(generation):
            self._reset_histories("INVALID_GENERATION")
            return False
        at = _finite_time(received_at)
        price = _decimal(trade.price)
        size = _decimal(trade.size)
        if (at is None or trade.condition_id != self.market.condition_id
                or price is None or not _ZERO < price < _ONE
                or size is None or size <= _ZERO or not isinstance(trade.aggressor, Side)):
            self._invalidate(token_id, "INVALID_TRADE_EVIDENCE")
            return False
        history = self._history[token_id]
        if not self._accept_event_time(history, at):
            return False
        history.last_event_at = at
        history.trades.append(at)
        return True

    def evaluate(self, now: float, *, generation: int,
                 ticks: dict[str, float], state_reliable: bool) -> RDPBookFilterResult:
        """Evaluate rolling predicates against explicit current authority."""
        at = _finite_time(now)
        if at is None:
            self._reset_histories("INVALID_EVALUATION_TIME")
            self._last_evaluated_at = None
            return self._not_ready("INVALID_EVALUATION_TIME", 0.0)
        if self._last_evaluated_at is not None and at < self._last_evaluated_at:
            self._reset_histories("EVALUATION_TIME_REVERSED")
            self._last_evaluated_at = at
            return self._not_ready("EVALUATION_TIME_REVERSED", at)
        self._last_evaluated_at = at
        if not self._valid_generation(generation):
            self._reset_generation(generation if isinstance(generation, int) else None)
            return self._not_ready("INVALID_GENERATION", at)
        if self._generation is None:
            self._generation = generation
        elif generation != self._generation:
            self._reset_generation(generation)
            return self._not_ready("GENERATION_CHANGED", at)
        if state_reliable is not True:
            self._reset_histories("UNRELIABLE_STATE")
            return self._not_ready("UNRELIABLE_STATE", at)
        if not isinstance(ticks, dict) or set(ticks) != set(self._tokens):
            self._reset_histories("INVALID_TICKS")
            return self._not_ready("INVALID_TICKS", at)

        parsed_ticks: dict[str, Decimal] = {}
        for token in self._tokens:
            tick = _decimal(ticks[token])
            if tick is None or not _ZERO < tick < _ONE:
                self._reset_histories("INVALID_TICK")
                return self._not_ready("INVALID_TICK", at)
            parsed_ticks[token] = tick
            history = self._history[token]
            if history.tick is not None and history.tick != tick:
                history.reset("TICK_CHANGED")
                history.tick = tick

        latest = [h.last_event_at for h in self._history.values() if h.last_event_at is not None]
        if latest and at < max(latest):
            self._reset_histories("EVALUATION_PRECEDES_EVIDENCE")
            return self._not_ready("EVALUATION_PRECEDES_EVIDENCE", at)

        evidence: list[RDPBookSideEvidence] = []
        for token in self._tokens:
            side = self._evaluate_side(token, parsed_ticks[token], at)
            evidence.append(side)
        not_ready = [side.reason for side in evidence if side.reason is not None]
        if not_ready:
            result = self._result(False, at, tuple(f"NOT_READY:{r}" for r in dict.fromkeys(not_ready)),
                                  None, None, None, None, tuple(evidence))
            return result

        yes, no = evidence
        assert yes.q_eff is not None and no.q_eff is not None
        with localcontext() as context:
            context.prec = 64
            high, low = max(yes.q_eff, no.q_eff), min(yes.q_eff, no.q_eff)
            denominator = max(low, _DEPTH_FLOOR)
            depth_ratio = high / denominator
        try:
            with _exact_context():
                # Compare the rational values exactly; division above is only
                # explanatory output and cannot round the strict decision.
                ratio_threshold = denominator * _DEPTH_RATIO
                depth_extreme = high > _DEPTH_MIN_HIGH and high > ratio_threshold
        except DecimalException:
            self._reset_histories("UNSUPPORTED_PRECISION")
            return self._not_ready("UNSUPPORTED_PRECISION", at)
        inactive = bool(yes.inactive or no.inactive)
        fast_move = bool(yes.fast_move or no.fast_move)
        reasons: list[str] = []
        if yes.inactive:
            reasons.append("INACTIVE_YES")
        if no.inactive:
            reasons.append("INACTIVE_NO")
        if depth_extreme:
            reasons.append("DEPTH_EXTREME")
        if yes.fast_move:
            reasons.append("FAST_MOVE_YES")
        if no.fast_move:
            reasons.append("FAST_MOVE_NO")
        return self._result(True, at, tuple(reasons), inactive, depth_extreme,
                            depth_ratio, fast_move, tuple(evidence))

    def _evaluate_side(self, token: str, tick: Decimal, now: float) -> RDPBookSideEvidence:
        history = self._history[token]
        try:
            coverage_decimal = (_ZERO if history.started_at is None else
                                _exact_subtract(now, history.started_at))
        except DecimalException:
            self._invalidate(token, "UNSUPPORTED_PRECISION")
            return _side_not_ready(token, tick, 0.0, "UNSUPPORTED_PRECISION")
        coverage = float(max(_ZERO, coverage_decimal))
        if history.invalid_reason is not None:
            return _side_not_ready(token, tick, coverage, history.invalid_reason)
        if not history.samples:
            return _side_not_ready(token, tick, coverage, "NO_BOOK_BASELINE")
        if history.tick != tick:
            return _side_not_ready(token, tick, coverage, "TICK_BASELINE_REQUIRED")
        if now < history.samples[-1].at:
            return _side_not_ready(token, tick, coverage, "EVALUATION_PRECEDES_EVIDENCE")
        if coverage < _TWO_SECONDS:
            return _side_not_ready(token, tick, coverage, "WARMUP")

        try:
            cutoff2 = _exact_subtract(now, _TWO_SECONDS)
            cutoff1 = _exact_subtract(now, _ONE_SECOND)
        except DecimalException:
            self._invalidate(token, "UNSUPPORTED_PRECISION")
            return _side_not_ready(token, tick, coverage, "UNSUPPORTED_PRECISION")
        _prune_samples(history.samples, cutoff2)
        while history.trades and history.trades[0] <= cutoff2:
            history.trades.popleft()
        if not history.samples or history.samples[0].at > cutoff2:
            return _side_not_ready(token, tick, coverage, "MISSING_WINDOW_ANCHOR")
        try:
            with _exact_context():
                variation = sum((abs(current.q_eff - prior.q_eff)
                                 for prior, current in zip(history.samples, list(history.samples)[1:],
                                                          strict=False)
                                 if current.at > cutoff2), _ZERO)
                recent_trade = any(cutoff2 < trade_at <= now for trade_at in history.trades)
                one_second = [sample for sample in history.samples if sample.at >= cutoff1]
                anchor = [sample for sample in history.samples if sample.at <= cutoff1]
                if anchor:
                    bids = [anchor[-1].bid] + [sample.bid for sample in one_second
                                               if sample.at > cutoff1]
                else:
                    return _side_not_ready(token, tick, coverage, "MISSING_FAST_MOVE_ANCHOR")
                excursion = max(bids) - min(bids)
                inactive = not recent_trade and variation < _INACTIVE_LIMIT
                fast_move = excursion >= Decimal("2") * tick
        except DecimalException:
            self._invalidate(token, "UNSUPPORTED_PRECISION")
            return _side_not_ready(token, tick, coverage, "UNSUPPORTED_PRECISION")
        return RDPBookSideEvidence(token, tick, history.samples[-1].q_eff,
            variation, recent_trade, excursion, inactive, fast_move, coverage, None)

    def _accept_event_time(self, history: _SideHistory, at: float) -> bool:
        if history.last_event_at is not None and at < history.last_event_at:
            history.reset("NON_MONOTONIC_EVIDENCE_TIME")
            return False
        return True

    def _accept_generation(self, generation: int) -> bool:
        if not self._valid_generation(generation):
            return False
        if self._generation is None:
            self._generation = generation
        elif generation != self._generation:
            self._reset_generation(generation)
        return True

    @staticmethod
    def _valid_generation(generation: object) -> bool:
        return isinstance(generation, int) and not isinstance(generation, bool) and generation >= 0

    def _reset_generation(self, generation: int | None) -> None:
        self._generation = generation
        for history in self._history.values():
            history.reset("GENERATION_CHANGED")

    def _reset_histories(self, reason: str) -> None:
        for history in self._history.values():
            history.reset(reason)

    def _invalidate(self, token: str, reason: str) -> None:
        self._history[token].reset(reason)

    def _not_ready(self, reason: str, at: float | Decimal) -> RDPBookFilterResult:
        evidence = tuple(_side_not_ready(token, self._history[token].tick,
            _coverage(self._history[token], at), reason) for token in self._tokens)
        return self._result(False, at, (reason,), None, None, None, None, evidence)

    def _result(self, ready: bool, at: float | Decimal, reasons: tuple[str, ...],
                inactive: bool | None, depth_extreme: bool | None,
                depth_ratio: Decimal | None,
                fast_move: bool | None,
                sides: tuple[RDPBookSideEvidence, ...]) -> RDPBookFilterResult:
        return RDPBookFilterResult(ready, bool(reasons), inactive,
            depth_extreme, depth_ratio, fast_move, reasons, self._generation, float(at),
            sides)  # type: ignore[arg-type]


def _prune_samples(samples: deque[_BookSample], cutoff: Decimal) -> None:
    # Retain exactly the latest observation at/before the cutoff as the anchor.
    while len(samples) > 1 and samples[1].at <= cutoff:
        samples.popleft()


def _coverage(history: _SideHistory, now: float | Decimal) -> float:
    at = _finite_time(now)
    if history.started_at is None or at is None:
        return 0.0
    try:
        return float(max(_ZERO, _exact_subtract(at, history.started_at)))
    except DecimalException:
        return 0.0


def _side_not_ready(token: str, tick: Decimal | None, coverage: float,
                    reason: str) -> RDPBookSideEvidence:
    return RDPBookSideEvidence(token, tick, None, None, False, None,
                               None, None, coverage, reason)


def _finite_time(value: object) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (Real, Decimal)):
        return None
    result = _decimal(value)
    if result is None or result < _ZERO:
        return None
    try:
        return result if math.isfinite(float(result)) else None
    except (OverflowError, ValueError):
        return None


def _decimal(value: object) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (Real, Decimal, str)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (DecimalException, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


@contextmanager
def _exact_context():
    with localcontext() as context:
        context.prec = 64
        context.traps[Inexact] = True
        yield context


def _exact_subtract(left: Decimal, right: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = 64
        context.traps[Inexact] = True
        return left - right

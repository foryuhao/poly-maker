"""Pure NORMAL and REBALANCE quote-candidate construction for RDP.

These builders only create candidate BUY quotes. Their mode-specific gates,
market/order reliability, cycle reconciliation, and live authorization remain
mandatory before an execution caller may submit them. A candidate is not a
ready-to-send decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, DecimalException, InvalidOperation
import math
from numbers import Real

from polymaker.domain import MarketMeta, Quote, Side
from polymaker.marketdata.orderbook import BookView
from polymaker.strategy.rdp_accounting import RDPInventorySnapshot

_BASE_CLIP = Decimal("5")
_NORMAL_LIMIT = Decimal("5")
_BINARY_ONE = Decimal("1")
_REQUIRED_GATES = (
    "Book Filter",
    "CapitalGate",
    "market-data and order-state reliability",
    "current-cycle reconciliation",
    "live authorization",
)
_REBALANCE_GATES = (
    "CanSafelyContinue: time, capital, market tradability and reliable state",
    "CapitalGate",
    "cancel and reconcile existing orders before replacement",
    "live authorization",
)


@dataclass(frozen=True, slots=True)
class RDPQuoteDecision:
    """Candidate quote(s) or an explicit fail-closed reason.

    `post_only_required` describes the required execution mode. `required_gates`
    lists independent controls that remain outstanding. Neither field grants
    permission to place an order.
    """

    quotes: tuple[Quote, ...]
    reason: str | None
    post_only_required: bool = True
    required_gates: tuple[str, ...] = _REQUIRED_GATES

    @property
    def has_candidate(self) -> bool:
        return bool(self.quotes)


def build_normal_quotes(
    market: MarketMeta,
    inventory: RDPInventorySnapshot,
    yes_book: BookView,
    no_book: BookView,
    yes_tick: float,
    no_tick: float,
) -> RDPQuoteDecision:
    """Build a candidate or reject unsupported decimal precision explicitly."""
    try:
        return _build_normal_quotes(market, inventory, yes_book, no_book, yes_tick, no_tick)
    except DecimalException:
        return _reject("INVALID_PRECISION")


def _build_normal_quotes(
    market: MarketMeta,
    inventory: RDPInventorySnapshot,
    yes_book: BookView,
    no_book: BookView,
    yes_tick: float,
    no_tick: float,
) -> RDPQuoteDecision:
    """Build exact Frozen NORMAL candidates without I/O or state mutation."""
    if not isinstance(market, MarketMeta) or not isinstance(inventory, RDPInventorySnapshot):
        return _reject("INVALID_INPUT")
    if (inventory.condition_id != market.condition_id
            or inventory.yes_token_id != market.yes.token_id
            or inventory.no_token_id != market.no.token_id
            or market.yes.token_id == market.no.token_id):
        return _reject("MARKET_INVENTORY_MISMATCH")

    inventory_values = _inventory_values(inventory)
    if inventory_values is None:
        return _reject("INVALID_INVENTORY")
    qy, qn, delta = inventory_values
    try:
        qy_float = float(inventory.qy)
        qn_float = float(inventory.qn)
        delta_float = float(inventory.delta)
    except (OverflowError, TypeError, ValueError):
        return _reject("INVALID_INVENTORY")
    if (not all(math.isfinite(value) for value in (qy_float, qn_float, delta_float))
            or qy < 0 or qn < 0 or delta_float != qy_float - qn_float):
        return _reject("INVALID_INVENTORY")
    if _decimal(inventory.inventory_exposure) != abs(delta):
        return _reject("INVALID_INVENTORY")
    if abs(delta) >= _NORMAL_LIMIT:
        return _reject("NON_NORMAL_INVENTORY")

    ticks = (_decimal(yes_tick), _decimal(no_tick))
    if any(tick is None or tick <= 0 for tick in ticks):
        return _reject("INVALID_TICK")
    yes_tick_d, no_tick_d = ticks

    min_size = _decimal(market.min_order_size)
    if min_size is None or min_size <= 0:
        return _reject("INVALID_MIN_ORDER_SIZE")

    books = (_book_prices(yes_book), _book_prices(no_book))
    if any(prices is None for prices in books):
        return _reject("INVALID_BOOK")
    (yes_bid, yes_ask), (no_bid, no_ask) = books
    assert yes_bid is not None and yes_ask is not None
    assert no_bid is not None and no_ask is not None

    if yes_bid + no_bid > _BINARY_ONE:
        return _reject("PRICE_GATE")

    if yes_bid % yes_tick_d != 0 or no_bid % no_tick_d != 0:
        return _reject("OFF_TICK")
    yes_price = yes_bid - yes_tick_d
    no_price = no_bid - no_tick_d
    if yes_price % yes_tick_d != 0 or no_price % no_tick_d != 0:
        return _reject("OFF_TICK")
    if (not _inside_binary(yes_price) or not _inside_binary(no_price)
            or yes_price >= yes_ask or no_price >= no_ask):
        return _reject("ILLEGAL_QUOTE_PRICE")

    yes_size = _BASE_CLIP + max(-delta, Decimal("0"))
    no_size = _BASE_CLIP + max(delta, Decimal("0"))
    if yes_size < min_size or no_size < min_size:
        return _reject("MIN_ORDER_SIZE")

    try:
        yes_price_float = float(yes_price)
        no_price_float = float(no_price)
        yes_size_float = float(yes_size)
        no_size_float = float(no_size)
    except (OverflowError, ValueError):
        return _reject("INVALID_INPUT")
    # A valid Decimal price is not enough if converting it for the existing
    # float-based Quote type erases its required one-tick offset or grid.
    yes_returned_price = _decimal(yes_price_float)
    no_returned_price = _decimal(no_price_float)
    if (yes_returned_price != yes_price or no_returned_price != no_price
            or yes_returned_price % yes_tick_d != 0
            or no_returned_price % no_tick_d != 0):
        return _reject("UNREPRESENTABLE_PRICE")
    try:
        quotes = (
            Quote(market.yes.token_id, Side.BUY, yes_price_float, yes_size_float),
            Quote(market.no.token_id, Side.BUY, no_price_float, no_size_float),
        )
    except (OverflowError, ValueError):
        return _reject("INVALID_INPUT")
    if any(not math.isfinite(q.price) or not math.isfinite(q.size) for q in quotes):
        return _reject("INVALID_INPUT")
    return RDPQuoteDecision(quotes=quotes, reason=None)


def build_rebalance_quote(
    market: MarketMeta,
    inventory: RDPInventorySnapshot,
    yes_book: BookView,
    no_book: BookView,
    yes_tick: float,
    no_tick: float,
) -> RDPQuoteDecision:
    """Build the single deficient-side Frozen REBALANCE candidate.

    The surplus-side book and tick are accepted for a common caller interface
    but are not inspected. A candidate still requires CanSafelyContinue,
    CapitalGate, order cancellation/reconciliation, reliable state and live
    authorization before any execution is considered.
    """
    try:
        return _build_rebalance_quote(market, inventory, yes_book, no_book, yes_tick, no_tick)
    except DecimalException:
        return _reject("INVALID_PRECISION", _REBALANCE_GATES)


def _build_rebalance_quote(
    market: MarketMeta,
    inventory: RDPInventorySnapshot,
    yes_book: BookView,
    no_book: BookView,
    yes_tick: float,
    no_tick: float,
) -> RDPQuoteDecision:
    if not isinstance(market, MarketMeta) or not isinstance(inventory, RDPInventorySnapshot):
        return _reject("INVALID_INPUT", _REBALANCE_GATES)
    if (inventory.condition_id != market.condition_id
            or inventory.yes_token_id != market.yes.token_id
            or inventory.no_token_id != market.no.token_id
            or market.yes.token_id == market.no.token_id):
        return _reject("MARKET_INVENTORY_MISMATCH", _REBALANCE_GATES)

    inventory_values = _inventory_values(inventory)
    if inventory_values is None:
        return _reject("INVALID_INVENTORY", _REBALANCE_GATES)
    qy, qn, delta = inventory_values
    try:
        qy_float = float(inventory.qy)
        qn_float = float(inventory.qn)
        delta_float = float(inventory.delta)
    except (OverflowError, TypeError, ValueError):
        return _reject("INVALID_INVENTORY", _REBALANCE_GATES)
    if (not all(math.isfinite(value) for value in (qy_float, qn_float, delta_float))
            or qy < 0 or qn < 0 or delta_float != qy_float - qn_float
            or _decimal(inventory.inventory_exposure) != abs(delta)):
        return _reject("INVALID_INVENTORY", _REBALANCE_GATES)
    if abs(delta) < _NORMAL_LIMIT:
        return _reject("NON_REBALANCE_INVENTORY", _REBALANCE_GATES)

    if delta > 0:
        token_id, selected_book, selected_tick = market.no.token_id, no_book, no_tick
    else:
        token_id, selected_book, selected_tick = market.yes.token_id, yes_book, yes_tick

    tick = _decimal(selected_tick)
    if tick is None or tick <= 0:
        return _reject("INVALID_TICK", _REBALANCE_GATES)
    minimum = _decimal(market.min_order_size)
    if minimum is None or minimum <= 0:
        return _reject("INVALID_MIN_ORDER_SIZE", _REBALANCE_GATES)
    if _BASE_CLIP < minimum:
        return _reject("MIN_ORDER_SIZE", _REBALANCE_GATES)

    selected_prices = _book_prices(selected_book)
    if selected_prices is None:
        return _reject("INVALID_BOOK", _REBALANCE_GATES)
    _best_bid, best_ask = selected_prices
    if best_ask % tick != 0:
        return _reject("OFF_TICK", _REBALANCE_GATES)
    price = best_ask - tick
    if price % tick != 0:
        return _reject("OFF_TICK", _REBALANCE_GATES)
    if not _inside_binary(price) or price >= best_ask:
        return _reject("ILLEGAL_QUOTE_PRICE", _REBALANCE_GATES)

    try:
        price_float = float(price)
    except (OverflowError, ValueError):
        return _reject("INVALID_INPUT", _REBALANCE_GATES)
    returned_price = _decimal(price_float)
    if (returned_price != price or returned_price % tick != 0
            or best_ask - returned_price != tick):
        return _reject("UNREPRESENTABLE_PRICE", _REBALANCE_GATES)
    if not math.isfinite(price_float):
        return _reject("INVALID_INPUT", _REBALANCE_GATES)
    quote = Quote(token_id, Side.BUY, price_float, float(_BASE_CLIP))
    return RDPQuoteDecision((quote,), None, required_gates=_REBALANCE_GATES)


def _reject(reason: str, required_gates: tuple[str, ...] = _REQUIRED_GATES) -> RDPQuoteDecision:
    return RDPQuoteDecision(quotes=(), reason=reason, required_gates=required_gates)


def _decimal(value: object) -> Decimal | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if not isinstance(value, (Real, Decimal, str)):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return result if result.is_finite() else None


def _inventory_values(
    inventory: RDPInventorySnapshot,
) -> tuple[Decimal, Decimal, Decimal] | None:
    qy = _decimal(inventory.qy)
    qn = _decimal(inventory.qn)
    delta = _decimal(inventory.delta)
    if qy is None or qn is None or delta is None:
        return None
    return qy, qn, delta


def _book_prices(view: BookView) -> tuple[Decimal, Decimal] | None:
    if not isinstance(view, BookView):
        return None
    bid = _decimal(view.best_bid)
    ask = _decimal(view.best_ask)
    if bid is None or ask is None:
        return None
    if not _inside_binary(bid) or not _inside_binary(ask) or bid >= ask:
        return None
    return bid, ask


def _inside_binary(price: Decimal) -> bool:
    return Decimal("0") < price < _BINARY_ONE

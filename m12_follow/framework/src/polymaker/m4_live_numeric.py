"""Exact live-Taker arithmetic and clock boundaries; baseline is unaffected."""

from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal, getcontext
from functools import lru_cache
from types import MethodType

from polymaker.domain import Side
from polymaker.execution.taker_fees import buy_amount_fee_reserve
from polymaker.m4_live_config import all_in_cost
from polymaker.m4_state import decimal
from polymaker.rdp_sm_ca_taker import validate_buy_fills
from polymaker.state.tracker import TradeEvent

MICRO = Decimal(".000001")
FEE_UNIT = Decimal(".00001")
STEP = Decimal(".1")
ZERO = Decimal(0)


def units(value):
    return decimal(value).quantize(MICRO, rounding=ROUND_HALF_UP)


def _microseconds(value):
    return int((decimal(value) * 1_000_000).to_integral_value(rounding=ROUND_HALF_UP))


_recent_float_clocks_us = lru_cache(maxsize=8192)(_microseconds)


def age_us(now, then):
    # Several detectors compare the same frame and book clocks. Reuse the
    # conversion only where multiplying a float's decimal spelling is exact
    # in this context. Convert the two clocks independently before subtraction.
    ctx = getcontext()
    if ctx.prec >= 24 and ctx.Emin <= -324 and ctx.Emax >= 18 and not ctx.clamp:
        left = _recent_float_clocks_us if type(now) is float and -1e12 < now < 1e12 else _microseconds
        right = _recent_float_clocks_us if type(then) is float and -1e12 < then < 1e12 else _microseconds
        return left(now) - right(then)
    return _microseconds(now) - _microseconds(then)


def fak_amounts_compatible(price, quantity):
    # Immediate BUY validates maker USDC at cents and taker shares at 4 places.
    # The user retains the stricter .1-share strategy step. Do not round either
    # signed leg independently: that would change the effective limit price.
    return quantity >= 0 and quantity % STEP == 0 and price * quantity % Decimal('.01') == 0


def budget_quantity(price, budget, fees, *, executable=True):
    price, budget = decimal(price), max(ZERO, decimal(budget))
    unit = price + decimal(fees["rate"]) * (price * (1 - price)) ** fees["exponent"]
    quantity = (budget / unit / STEP).to_integral_value(rounding=ROUND_FLOOR) * STEP
    while quantity > 0 and (all_in_cost(price, quantity, fees) > budget
                            or executable and not fak_amounts_compatible(price, quantity)):
        quantity -= STEP
    return quantity


def live_book_legal(self, book, now):
    return (
        self.feed.connected
        and book.snapshot_generation == self.feed.generation
        and book.best_bid is not None
        and book.best_ask is not None
        and 0 < book.best_bid < book.best_ask < 1
        and book.tick > 0
        and self.feed.tick_verified.get(book.token_id) == book.tick
        and 0 <= age_us(now, book.received_mono) <= 200_000
        and -2_000 <= age_us(self.feed.wall(), book.source_time) <= 500_000
        and (book.reported_bid is None or book.reported_bid == book.best_bid)
        and (book.reported_ask is None or book.reported_ask == book.best_ask)
    )


def live_before_event(self, kind, row, book, received, wall, *, books=None):
    self.decision_reason = None
    if self.failure:
        return False
    try:
        stamp = decimal(row["timestamp"]) / 1000
        if not -2_000 <= age_us(self.feed.wall(), stamp) <= 500_000:
            raise ValueError("M4_SOURCE_AGE")
        previous = max(self.source.get(book.token_id, ZERO), decimal(book.source_time))
        if stamp < previous:
            if age_us(previous, stamp) <= 20_000:
                self.decision_reason = "M4_EVENT_OUT_OF_ORDER_IGNORED"
                return False
            raise ValueError("M4_SOURCE_REGRESSION")
        self.source[book.token_id] = stamp
        if not 0 <= age_us(self.feed.monotonic(), received) <= 200_000:
            raise ValueError("M4_PROCESSING_GAP")
        return True
    except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
        self.decision_reason = str(exc)
        self.invalidate(self.decision_reason, book.token_id)
        return False


def live_round_before(self, kind, row, book, received, wall):
    """Same round extraction as M4Signals.before, with the live clock units."""
    token, now = book.token_id, self.feed.monotonic()
    try:
        if kind == "tick_size_change":
            raise ValueError("TICK_CHANGED")
        stamp = decimal(row["timestamp"]) / 1000
        if not -2_000 <= age_us(wall, stamp) <= 500_000:
            raise ValueError("SOURCE_AGE")
        floor = max(self.sources.get(token, ZERO), decimal(book.source_time))
        if stamp < floor:
            raise ValueError("SOURCE_REGRESSION")
        self.sources[token] = stamp
        if not 0 <= age_us(now, received) <= 200_000 or not live_book_legal(self, book, now):
            raise ValueError("BOOK_UNAVAILABLE")
        if kind == "last_trade_price":
            side, price, size = row["side"], decimal(row["price"]), decimal(row["size"])
            if side not in {"BUY", "SELL"} or size <= 0 or not 0 < price < 1 or price % book.tick:
                raise ValueError("TRADE_INVALID")
            key = (token, stamp, side, price, size, row.get("transaction_hash"))
            if key in self.key_set:
                raise ValueError("DUPLICATE_AMBIGUITY")
            self.keys.append(key)
            self.key_set.add(key)
            if len(self.keys) > 8192:
                self.key_set.remove(self.keys.popleft())
            self.pair(token)["BID" if side == "SELL" else "ASK"].hit(price, size, book, received)
        return True
    except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
        self.invalidate(str(exc), token)
        self.emit("M4", "INPUT_UNAVAILABLE", {"token": token, "reason": str(exc)})
        return False


def live_round_tick(self):
    # The live M4 frequency detector consumes original round features only.
    if not self.features_only:
        raise ValueError("M4_LIVE_ROUND_MODE_CHANGED")
    now = self.feed.monotonic()
    for token, pair in list(self.queues.items()):
        book = self.feed.books[token]
        if not live_book_legal(self, book, now):
            self.invalidate("BOOK_UNAVAILABLE", token)
            continue
        for queue in pair.values():
            queue.tick(book)


def live_frequency_tick(self):
    """Frequency candidates use the same clock predicate as live admission."""
    from polymaker.m4_frequency_signal import PARAMETERS, STRATEGY

    now = self.feed.monotonic()
    for state in self.states.values():
        self.expire(state, now)
    tokens = {token for token, _ in self.states} | set(self.candidates)
    for token in tokens:
        bid, ask = self.states.get((token, "BID")), self.states.get((token, "ASK"))
        support, df = (bid["support"] if bid else None), (ask["df"] if ask else None)
        book = self.feed.books[token]
        if (
            not support or not df or not live_book_legal(self, book, now)
            or not any(p == support["anchor"] and q > 0 for p, q in book.bids)
        ):
            self.end_candidate(token, "COMPONENT_UNAVAILABLE")
            continue
        key = support["id"] + ":" + df["id"]
        if self.candidates.get(token, {}).get("id") == key:
            continue
        candidate = {
            "id": key, "root": df["id"], "token": token, "price": support["anchor"],
            "confirmed": now, "deadline": min(support["deadline"], df["deadline"]),
            "support_id": support["id"], "df_id": df["id"],
            "definition": PARAMETERS["definition"], "generation": self.feed.generation,
        }
        self.candidates[token] = candidate
        self.emit(STRATEGY, "CANDIDATE", candidate)


def install_live_clock_boundaries(feed):
    # Only this live instance is changed. Signal parameters and thresholds stay fixed.
    feed.m1.legal = MethodType(live_book_legal, feed.m1)
    feed.m1.before_event = MethodType(live_before_event, feed.m1)
    feed.m1.rounds.before = MethodType(live_round_before, feed.m1.rounds)
    feed.m1.rounds.tick = MethodType(live_round_tick, feed.m1.rounds)
    feed.m1.frequency.tick = MethodType(live_frequency_tick, feed.m1.frequency)


def fill_event(trade, spec):
    """Keep raw decimal strings through economics; never sum binary floats."""
    raw = trade.raw
    if (
        trade.market != spec["market_id"]
        or trade.taker_asset_id != spec["token_id"]
        or trade.taker_side != "BUY"
        or raw.get("trader_side") != "TAKER"
        or not trade.maker_legs
    ):
        raise ValueError("M4_TRADE_IDENTITY_INVALID")
    price, quantity = decimal(raw["price"]), units(raw["size"])
    if not 0 < price <= spec["price"] or quantity <= 0:
        raise ValueError("M4_FILL_PRICE_OR_QUANTITY_INVALID")
    principal, fee, legs_qty = ZERO, ZERO, ZERO
    for leg in raw["maker_orders"]:
        px, qty = decimal(leg["price"]), units(leg["matched_amount"])
        if leg["asset_id"] == spec["token_id"] and leg["side"].upper() == "SELL":
            actual = px
        elif leg["asset_id"] == spec["opposite_token"] and leg["side"].upper() == "BUY":
            actual = 1 - px
        else:
            raise ValueError("M4_MAKER_LEG_IDENTITY_CONFLICT")
        if not 0 < actual <= spec["price"] or qty < 0:
            raise ValueError("M4_FILL_PRICE_OR_QUANTITY_INVALID")
        legs_qty += qty
        principal += qty * actual
        fee += (
            qty
            * decimal(spec["fees"]["rate"])
            * (actual * (1 - actual)) ** spec["fees"]["exponent"]
        ).quantize(FEE_UNIT, rounding=ROUND_HALF_UP)
    if abs(legs_qty - quantity) > MICRO:
        raise ValueError("M4_TRADE_QUANTITY_CONFLICT")
    tolerance = len(raw["maker_orders"]) * FEE_UNIT
    fee_ceiling = fee + tolerance
    if raw.get("fee_usdc") is not None:
        observed = units(raw["fee_usdc"])
        if observed < 0 or abs(observed - fee) > tolerance:
            raise ValueError("M4_TRADE_FEE_CONFLICT")
        fee = observed
    event = TradeEvent(
        spec["token_id"],
        Side.BUY,
        float(units(principal) / quantity),
        float(quantity),
        trade.trade_id,
        trade.status,
        trade.timestamp,
        trade.taker_order_id,
        trade.market,
        "TAKER",
        str(units(principal)),
        str(units(fee)),
    )
    validate_cash_fills([event], spec)
    return event, tolerance, fee_ceiling


def maker_fill_event(trade, spec, oid, funder):
    """Only this signed resting BUY leg belongs to the follower."""
    from polymaker.userstream.parse import maker_order_id

    legs = [leg for leg in trade.raw["maker_orders"] if maker_order_id(leg) == oid]
    if (trade.market != spec["market_id"] or trade.raw.get("trader_side") != "MAKER"
            or len(legs) != 1 or trade.taker_order_id == oid):
        raise ValueError("M12_MAKER_TRADE_IDENTITY_INVALID")
    leg = legs[0]
    px, qty = decimal(leg["price"]), units(leg["matched_amount"])
    signed_price = spec["price"]
    if (leg["asset_id"] != spec["token_id"] or leg["side"].upper() != "BUY"
            or str(leg.get("maker_address", funder)).lower() != funder.lower()
            or not 0 < qty <= spec["quantity"]):
        raise ValueError("M12_MAKER_LEG_CONFLICT")
    # The venue can express its rounded cash amount as an implied unit price.
    # Accept only a sub-tick representation with exactly the same cash units.
    principal = units(qty * signed_price)
    if abs(px - signed_price) > MICRO or units(qty * px) != principal:
        raise ValueError("M12_MAKER_LEG_CONFLICT")
    fee = units(leg.get("fee_usdc", 0))
    if fee != ZERO:
        raise ValueError("M12_MAKER_FEE_CONFLICT")
    event = TradeEvent(spec["token_id"], Side.BUY, float(signed_price), float(qty), trade.trade_id,
                       trade.status, trade.timestamp, oid, trade.market, "MAKER",
                       str(principal), str(fee))
    validate_cash_fills([event], spec)
    return event, ZERO, ZERO


def validate_cash_fills(fills, spec):
    validate_buy_fills(fills, spec)
    # Evidence comparison tolerances are not permission to exceed signed money
    # or the saved gross reservation. Use the ledger's same canonical cash unit.
    principal = units(sum((decimal(f.principal_usdc) for f in fills), ZERO))
    cost = units(sum((decimal(f.principal_usdc) + decimal(f.fee_usdc) for f in fills), ZERO))
    signed_principal = spec.get("buy_amount_usdc", spec["price"] * spec["quantity"])
    if principal > signed_principal or cost > signed_principal + buy_amount_fee_reserve(
        signed_principal, spec["fees"]
    ):
        raise ValueError("M4_FILL_EXCEEDS_RESERVED_CASH")


def compatible_economics(previous, current, fee_tolerance):
    identity = ("token_id", "our_side", "trade_id", "maker_order_id", "market_id", "liquidity_role")
    if any(previous.get(k) != current.get(k) for k in identity):
        return False
    return (
        abs(decimal(previous["size"]) - decimal(current["size"])) <= MICRO
        and abs(decimal(previous["principal_usdc"]) - decimal(current["principal_usdc"])) <= MICRO
        and abs(decimal(previous["fee_usdc"]) - decimal(current["fee_usdc"])) <= fee_tolerance
    )

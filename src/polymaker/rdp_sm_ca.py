"""CounterAttack v1.1 evidence, independent of BA and shared book watermarks.

Uses pre-event books in socket order. The separate CA entry adapter owns
selection/consumption when normal_entry_mode=ca_v1; this detector never trades.
"""

from collections import Counter, deque
from dataclasses import asdict, dataclass
from decimal import Decimal

from polymaker.rdp_sm_signal import number


def plain(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [plain(v) for v in value]
    return value


@dataclass
class CAEpisode:
    episode_id: str
    market_id: str
    token_id: str
    outcome: str
    generation: int
    anchor_bid_price: Decimal
    t0: float
    observation_deadline: float
    deadline: float
    start_ordinal: int
    first_sell: dict
    state: str = "COLLECT_SELL"
    sell_shares: Decimal = Decimal(0)
    buy_shares: Decimal = Decimal(0)
    tS: float | None = None
    tC: float | None = None
    sell_threshold_ordinal: int | None = None
    confirming_buy_ordinal: int | None = None
    threshold_sell: dict | None = None
    confirming_buy: dict | None = None
    end_reason: str | None = None
    ended_monotonic: float | None = None


class CounterAttack:
    trade_history_limit = 4096

    def __init__(self, feed, ba_config, options, *, writer=None, session_id=None):
        self.feed = feed
        # Freeze inheritance at session start, never follow later config mutations.
        self.cfg = ba_config.model_copy(deep=True)
        self.min_buy = options.min_buy_shares
        self.writer, self.session_id = writer, session_id
        self.states = {}
        self.source_times, self.tops, self.trade_floor = {}, {}, {}
        self.seen_trades, self.trade_keys = set(), deque()
        self.ordinal = self.counter = self.log_sequence = 0
        self.counts, self.rejected = Counter(), Counter()
        self.last_rejection = None
        self.failure = None
        self.closed = False
        self.phase = "PREFLIGHT"
        self.context_mono = feed.monotonic()
        self.context_elapsed = None
        # Only used by deterministic in-memory tests. Production has a CAWriter.
        self.records = deque(maxlen=512) if writer is None else None
        self.parameters = {"S_min": str(self.cfg.min_sell_shares), "B_min": str(self.min_buy),
            "T_obs": self.cfg.observation_sec, "T_life": self.cfg.signal_lifetime_sec,
            "max_trade_age_ms": self.cfg.max_sell_age_ms,
            "max_book_age_ms": self.cfg.max_book_age_ms,
            "max_entry_age_ms": self.cfg.max_entry_age_ms,
            "entry_min_confirmation_duration_ms_exclusive": 100,
            "max_pair_skew_ms": self.cfg.max_pair_skew_ms,
            "out_of_order_tolerance_ms": self.cfg.out_of_order_tolerance_ms,
            "network": self.cfg.network.model_dump()}
        self.emit("CA_SESSION", {"mode": self.mode, "parameters": self.parameters})

    @property
    def mode(self):
        return "NORMAL_ENTRY" if self.feed.normal_entry_mode == "ca_v1" else "OBSERVATION_ONLY"

    def set_context(self, phase, elapsed):
        self.phase, self.context_elapsed = phase, elapsed
        self.context_mono = self.feed.monotonic()

    def context(self, now):
        return {"coordinator_phase": self.phase, "phase_observed_monotonic": self.context_mono,
                "market_elapsed_s": (self.context_elapsed+now-self.context_mono
                                     if self.context_elapsed is not None else None)}

    def disable(self, reason):
        self.failure = self.failure or reason
        for ep in self.states.values():
            if ep.end_reason is None:
                ep.state, ep.end_reason = "UNAVAILABLE", self.failure
                ep.ended_monotonic = self.feed.monotonic()

    def healthy(self):
        if self.writer is not None and self.writer.failure:
            self.disable(self.writer.failure)
        return not self.failure and not self.closed

    def emit(self, event, payload):
        now = self.feed.monotonic()
        self.log_sequence += 1
        row = plain({"kind": "ca_observation", "event": event,
            "sequence": self.log_sequence, "session_id": self.session_id,
            "market_id": self.feed.meta.condition_id, "generation": self.feed.generation,
            "observed_monotonic": now, "observed_wall": self.feed.wall(),
            **self.context(now), "payload": payload})
        if self.writer is not None:
            if not self.writer.submit(row):
                self.disable(self.writer.failure or "CA_LOG_UNAVAILABLE")
        elif len(self.records) < self.records.maxlen:
            self.records.append(row)
        else:
            self.disable("CA_MEMORY_LOG_OVERFLOW")

    def record(self, ep, event, **evidence):
        self.counts[event] += 1
        self.emit(event, {"episode": asdict(ep), "parameters": self.parameters, **evidence})

    def finish(self, ep, reason, now, *, trigger=None, book=None):
        if ep.end_reason is None:
            ep.end_reason, ep.ended_monotonic = reason, now
            ep.state = reason
            evidence = {}
            if reason == "CA_BOOK_UNPROVEN":
                # Depth is validated before SessionFeed commits its shared books.
                # Preserve the exact book that failed, including intermediate BBO.
                failed_book = book if book is not None else self.feed.books[ep.token_id]
                evidence = {"trigger": trigger,
                            "book_validation": self.book_validation(failed_book, now)}
            self.record(ep, reason, **evidence)

    def invalidate(self, reason, token=None):
        now = self.feed.monotonic()
        for ep in self.states.values():
            if token is None or token == ep.token_id:
                self.finish(ep, reason, now)

    def reset(self):
        self.invalidate("CA_WS_GENERATION_CHANGED")
        self.states.clear()
        self.source_times.clear()
        self.tops.clear()
        self.seen_trades.clear()
        self.trade_keys.clear()
        self.trade_floor.clear()

    def legal(self, book, now):
        return (self.feed.connected and book.snapshot_generation == self.feed.generation
            and book.best_bid is not None and book.best_ask is not None
            and book.tick > 0 and 0 < book.best_bid < book.best_ask < 1
            and book.best_bid % book.tick == 0 and book.best_ask % book.tick == 0
            and self.feed.tick_verified.get(book.token_id) == book.tick
            and (book.reported_bid is None or book.reported_bid == book.best_bid)
            and (book.reported_ask is None or book.reported_ask == book.best_ask)
            and self.tops.get(book.token_id, (book.best_bid, book.best_ask))
                == (book.best_bid, book.best_ask)
            and 0 <= (self.feed.wall()-book.source_time)*1000 <= self.cfg.max_sell_age_ms
            and 0 <= (now-book.received_mono)*1000 <= self.cfg.max_book_age_ms)

    def book_validation(self, book, now):
        """Failure-only evidence matching legal(); never dump whole depth books."""
        wall = self.feed.wall()
        bid, ask, tick = book.best_bid, book.best_ask, book.tick
        top = self.tops.get(book.token_id, (bid, ask))
        verified_tick = self.feed.tick_verified.get(book.token_id)
        source_age = (wall - book.source_time) * 1000
        receipt_age = (now - book.received_mono) * 1000
        complete = bid is not None and ask is not None
        checks = {
            "WS_DISCONNECTED": not self.feed.connected,
            "BOOK_GENERATION_MISMATCH": book.snapshot_generation != self.feed.generation,
            "BOOK_EMPTY": not complete,
            "TICK_NON_POSITIVE": tick <= 0,
            "PRICE_RANGE_INVALID": complete and not 0 < bid < ask < 1,
            "BID_OFF_TICK": bid is not None and tick > 0 and bid % tick != 0,
            "ASK_OFF_TICK": ask is not None and tick > 0 and ask % tick != 0,
            "TICK_UNVERIFIED": verified_tick != tick,
            "REPORTED_BID_MISMATCH": book.reported_bid is not None and book.reported_bid != bid,
            "REPORTED_ASK_MISMATCH": book.reported_ask is not None and book.reported_ask != ask,
            "BBO_DEPTH_MISMATCH": top != (bid, ask),
            "SOURCE_TIME_IN_FUTURE": source_age < 0,
            "SOURCE_TOO_OLD": source_age > self.cfg.max_sell_age_ms,
            "RECEIPT_TIME_IN_FUTURE": receipt_age < 0,
            "RECEIPT_TOO_OLD": receipt_age > self.cfg.max_book_age_ms,
        }
        return {"failed_checks": [name for name, failed in checks.items() if failed],
                "checked_monotonic": now, "checked_wall": wall,
                "token_id": book.token_id, "connected": self.feed.connected,
                "feed_generation": self.feed.generation, "book_generation": book.snapshot_generation,
                "best_bid": bid, "best_ask": ask, "tick": tick, "verified_tick": verified_tick,
                "reported_bid": book.reported_bid, "reported_ask": book.reported_ask,
                "ca_top_bid": top[0], "ca_top_ask": top[1],
                "frame": book.frame, "applied_monotonic": book.applied_mono,
                "received_wall": book.received_wall,
                "source_time": book.source_time, "received_monotonic": book.received_mono,
                "source_age_ms": source_age, "receipt_age_ms": receipt_age,
                "max_source_age_ms": self.cfg.max_sell_age_ms,
                "max_receipt_age_ms": self.cfg.max_book_age_ms}

    def tick(self):
        """Called by a separate timer AND before a frame can refresh an old book."""
        if not self.healthy():
            return
        now = self.feed.monotonic()
        for token, ep in self.states.items():
            if ep.end_reason:
                continue
            book = self.feed.books[token]
            reason = (
                "CA_EXPIRED" if now >= ep.deadline else
                "CA_OBSERVATION_TIMEOUT" if ep.tC is None and now >= ep.observation_deadline else
                "CA_WS_GENERATION_CHANGED" if ep.generation != self.feed.generation else
                "CA_BOOK_STALE" if not 0 <= (now-book.received_mono)*1000 <= self.cfg.max_book_age_ms else
                "CA_BOOK_UNPROVEN" if not self.legal(book, now) else
                "CA_ANCHOR_CHANGED" if book.best_bid != ep.anchor_bid_price else None)
            if reason:
                self.finish(ep, reason, now, trigger="tick", book=book)

    def ordered(self, row, book, wall):
        self.ordinal += 1
        stamp = number(row["timestamp"])/1000
        if stamp <= 0 or stamp > number(wall)+Decimal(".002"):
            raise ValueError("CA_SOURCE_CLOCK_INVALID")
        previous = max(self.source_times.get(book.token_id, Decimal(0)), number(book.source_time))
        if stamp < previous:
            self.source_times[book.token_id] = previous
            if (previous-stamp)*1000 <= number(self.cfg.out_of_order_tolerance_ms):
                self.rejected["CA_EVENT_OUT_OF_ORDER_IGNORED"] += 1
                return None
            raise ValueError("CA_EVENT_OUT_OF_ORDER")
        self.source_times[book.token_id] = stamp
        return float(stamp)

    def reject(self, reason, row, book):
        self.rejected[reason] += 1
        self.last_rejection = {"reason": reason, "ordinal": self.ordinal,
            "token_id": book.token_id, "processed_monotonic": self.feed.monotonic(),
            **{k: str(row.get(k))[:80] for k in ("timestamp", "side", "price", "size")}}
        self.invalidate(reason, book.token_id)

    def fact(self, row, book, mono, wall, stamp, now):
        return {"ordinal": self.ordinal, "source_time": stamp,
                "received_monotonic": mono, "received_wall": wall,
                "processed_monotonic": now, "price": str(number(row["price"])),
                "size": str(number(row["size"])), "side": row["side"],
                "pre_bid": str(book.best_bid), "pre_ask": str(book.best_ask),
                "book_received_monotonic": book.received_mono,
                "book_source_time": book.source_time}

    def before_event(self, kind, row, book, mono, wall):
        if not self.healthy():
            return False
        try:
            if kind == "tick_size_change":
                self.invalidate("CA_TICK_CHANGED", book.token_id)
                return False
            stamp = self.ordered(row, book, wall)
            if stamp is None:
                return False
            if kind == "last_trade_price":
                self.trade(row, book, mono, wall, stamp)
            elif kind == "best_bid_ask":
                bid, ask = number(row["best_bid"]), number(row["best_ask"])
                self.tops[book.token_id] = (bid, ask)
                self.book_updated(book)
            return True
        except (KeyError, ValueError, TypeError, ArithmeticError) as exc:
            self.reject(str(exc), row, book)
            return False

    def trade(self, row, book, mono, wall, stamp):
        self.counts["trade_events"] += 1
        side, price, qty = row.get("side"), number(row["price"]), number(row["size"])
        if side not in {"SELL", "BUY"} or qty <= 0 or not 0 < price < 1:
            raise ValueError("CA_TRADE_INVALID")
        self.counts[side] += 1
        token = book.token_id
        key = (token, stamp, side, price, qty, row.get("transaction_hash"))
        if stamp <= self.trade_floor.get(token, 0):
            raise ValueError("CA_TRADE_HISTORY_UNCERTAIN")
        if key in self.seen_trades:
            raise ValueError("CA_DUPLICATE_TRADE")
        self.seen_trades.add(key)
        self.trade_keys.append(key)
        if len(self.trade_keys) > self.trade_history_limit:
            old = self.trade_keys.popleft()
            self.seen_trades.remove(old)
            self.trade_floor[old[0]] = max(old[1], self.trade_floor.get(old[0], 0))
            if stamp <= self.trade_floor.get(token, 0):
                raise ValueError("CA_TRADE_HISTORY_UNCERTAIN")
        now = self.feed.monotonic()
        age = (wall-stamp)+(now-mono)
        if not self.legal(book, now) or not 0 <= age*1000 <= self.cfg.max_sell_age_ms:
            raise ValueError("CA_TRADE_OR_BOOK_STALE")
        if price != (book.best_bid if side == "SELL" else book.best_ask):
            raise ValueError("CA_SELL_NOT_AT_BID" if side == "SELL" else "CA_BUY_NOT_AT_ASK")
        ep = self.states.get(token)
        started = False
        if ep is None or ep.end_reason:
            if side != "SELL":
                return
            self.counter += 1
            fact = self.fact(row, book, mono, wall, stamp, now)
            ep = CAEpisode(f"ca:{self.feed.generation}:{self.counter}",
                self.feed.meta.condition_id, token,
                "YES" if token == self.feed.meta.yes.token_id else "NO", self.feed.generation,
                price, mono, mono+self.cfg.observation_sec, mono+self.cfg.signal_lifetime_sec,
                self.ordinal, fact)
            self.states[token] = ep
            started = True
        # A queued first SELL cannot open an already-expired observation window.
        if now >= ep.deadline or ep.tC is None and now >= ep.observation_deadline:
            self.finish(ep, "CA_EXPIRED" if now >= ep.deadline else "CA_OBSERVATION_TIMEOUT", now)
            return
        if book.best_bid != ep.anchor_bid_price:
            self.finish(ep, "CA_ANCHOR_CHANGED", now)
            return
        if ep.tC is not None:
            return  # frozen evidence; further valid prints do not renew anything
        if side == "SELL":
            ep.sell_shares += qty
            if started:
                self.record(ep, "CA_STARTED")
            if ep.tS is None and ep.sell_shares >= self.cfg.min_sell_shares:
                ep.tS, ep.sell_threshold_ordinal = now, self.ordinal
                ep.threshold_sell = self.fact(row, book, mono, wall, stamp, now)
                ep.state = "WAIT_BUY"
                self.record(ep, "CA_SELL_THRESHOLD")
        elif ep.tS is not None and self.ordinal > ep.sell_threshold_ordinal:
            ep.buy_shares += qty
            if ep.buy_shares >= self.min_buy:
                ep.tC, ep.confirming_buy_ordinal = now, self.ordinal
                ep.confirming_buy = self.fact(row, book, mono, wall, stamp, now)
                ep.state = "CA_CONFIRMED"
                self.record(ep, "CA_CONFIRMED")

    def book_updated(self, book):
        ep = self.states.get(book.token_id)
        if ep is None or ep.end_reason:
            return
        now = self.feed.monotonic()
        if book.best_bid != ep.anchor_bid_price:
            self.finish(ep, "CA_ANCHOR_CHANGED", now)
        elif not self.legal(book, now):
            self.finish(ep, "CA_BOOK_UNPROVEN", now, trigger="book_updated", book=book)

    def depth(self, book):
        # Like BA, later real depth supersedes a BBO-only observation.
        self.tops.pop(book.token_id, None)
        self.book_updated(book)

    def snapshot(self):
        self.tick()
        now = self.feed.monotonic()
        self.healthy()
        healthy = self.failure is None  # complete history may remain valid after normal stop
        result = {"mode": self.mode, "observed_monotonic": now,
                  "generation": self.feed.generation, "data_complete": healthy,
                  "recording_active": not self.closed, "failure": self.failure,
                  **self.context(now), "outcomes": {}}
        for token in self.feed.books:
            side = "YES" if token == self.feed.meta.yes.token_id else "NO"
            ep = self.states.get(token)
            buy_age = ((now-ep.confirming_buy["received_monotonic"]
                        + ep.confirming_buy["received_wall"]-ep.confirming_buy["source_time"])*1000
                       if ep and ep.confirming_buy else None)
            entry_age = (now-ep.t0)*1000 if ep else None
            confirmation_duration = ((Decimal(str(ep.tC)) - Decimal(str(ep.t0))) * 1000
                                     if ep and ep.tC is not None else None)
            if not healthy:
                reason = "CA_OBSERVER_UNAVAILABLE"
            elif self.closed:
                reason = "CA_OBSERVATION_STOPPED"
            elif ep is None:
                reason = "NO_EPISODE"
            elif ep.end_reason:
                reason = ep.end_reason
            elif ep.tC is None:
                reason = ep.state
            elif not 0 <= buy_age <= self.cfg.max_sell_age_ms:
                reason = "CA_BUY_STALE"
            elif not 0 <= entry_age <= self.cfg.max_entry_age_ms:
                reason = "CA_ENTRY_STALE"
            else:
                reason = None
            result["outcomes"][side] = plain({"token_id": token,
                "episode_id": ep.episode_id if ep else None,
                "state": ep.state if ep else "NO_EPISODE", "confirmed": bool(ep and ep.tC is not None),
                "candidate_usable": reason is None, "unavailable_reason": reason,
                "entry_age_ms": entry_age, "confirmation_buy_age_ms": buy_age,
                "confirmation_duration_ms": confirmation_duration,
                "episode": asdict(ep) if ep else None})
        return result

    def post(self, window_id, direction, post_started, snapshot, signal_direction=None):
        self.emit("CA_NORMAL_POST", {"window_id": window_id, "direction": direction,
            "signal_direction": signal_direction or direction,
            "post_started_monotonic": post_started, "snapshot": snapshot})

    def diagnostics(self):
        return {"mode": self.mode, "parameters": self.parameters,
                "snapshot": self.snapshot(), "counts": dict(self.counts),
                "rejected": dict(self.rejected), "last_rejection": self.last_rejection,
                "writer": self.writer.status() if self.writer else None}

    def stop(self):
        if self.closed:
            return
        self.invalidate("CA_SESSION_END")
        self.emit("CA_SUMMARY", self.diagnostics())
        self.closed = True

    def close(self):
        self.stop()
        if self.writer:
            self.writer.close()

    def safe(self, method, *args):
        """A CA-only programming, serialization or sink failure cannot alter BA."""
        try:
            return getattr(self, method)(*args)
        except Exception as exc:
            self.disable("CA_OBSERVER_ERROR:"+type(exc).__name__)
            return None

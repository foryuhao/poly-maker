"""One causal public input adapter for M5–M11; no account or settlement inputs."""

import hashlib
import json
import re
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from weakref import WeakSet

from .model import EPS, Book, Registry, Trade, decimal
from .primitives import Flow, Pressure, Refill

PARAMETER_FILE = Path(__file__).with_name("parameters.json")
PARAMETERS = json.loads(PARAMETER_FILE.read_text())
PARAMETER_HASH = hashlib.sha256(PARAMETER_FILE.read_bytes()).hexdigest()


class ResearchFeed:
    def __init__(self, market, tokens, ticks, emit):
        self.market, self.tokens, self.emit = market, tuple(tokens), emit
        self.now = self.wall = 0.0
        self.seq, self.subindex, self.generation = 0, -1, 0
        self.watermark = 0.0
        self.connected = False
        self.books = {t: Book(t, tick=decimal(ticks[t]), tick_verified=True) for t in self.tokens}
        self.registry = Registry(lambda event, p: self.event(event, p))
        # Install tracking before constructing even the first shared observer.
        # Connect/gap replaces these objects, whose callbacks can otherwise keep
        # an unreachable feed cycle alive until automatic collection.
        self.runtime_observers = WeakSet()
        self.observers = []
        self.flows = {t: Flow() for t in self.tokens}
        self.pressures = {t: Pressure(self, t) for t in self.tokens}
        self.refills = {t: Refill(self, t) for t in self.tokens}
        self.good = {t: False for t in self.tokens}
        self.reasons, self.sources, self.seen_trades, self.check_times = {}, {}, {}, {}
        self.counts = Counter()
        self.strategies = None
        self.atomic = False
        self.self_contaminated = set()

    def release_runtime(self):
        """Release an ended, persisted market; never used for a data gap."""
        counts = {"observers": len(self.runtime_observers),
                  "facts": len(self.registry.facts)}
        for observer in tuple(self.runtime_observers):
            observer.release_runtime()
        self.runtime_observers.clear()
        self.observers.clear()
        if self.strategies is not None:
            counts["campaigns"] = self.strategies.release_runtime()
        for primitive in (*self.pressures.values(), *self.refills.values()):
            primitive.c = None
        self.strategies = self.emit = self.registry.emit = None
        for values in (self.registry.facts, self.registry.endings, self.registry.active,
                       self.pressures, self.refills, self.flows, self.books,
                       self.sources, self.seen_trades, self.check_times):
            values.clear()
        return counts

    @property
    def cursor(self):
        return self.now, self.seq, self.subindex

    def event(self, event, payload):
        self.counts[event] += 1
        self.emit(
            "RESEARCH_EVIDENCE",
            {
                "event": event,
                "spec_version": PARAMETERS["spec_version"],
                "parameter_hash": PARAMETER_HASH,
                "market": self.market,
                "input_seq": self.seq,
                "subindex": self.subindex,
                "available": self.now,
                "generation": self.generation,
                "payload": payload,
            },
        )

    def fact(self, kind, token, deadline, start, end, *, anchor=None, refs=(), values=None):
        return self.registry.add(
            kind=kind,
            token=token,
            generation=self.generation,
            confirmed=self.now,
            deadline=deadline,
            start=start,
            end=end,
            input_seq=self.seq,
            anchor=anchor,
            refs=refs,
            values={**(values or {}), "available_subindex": self.subindex},
        )

    def on_fact(self, fact):
        if self.strategies:
            self.strategies.on_fact(fact)

    def on_round(self, fact):
        if self.strategies:
            self.strategies.on_round(fact)

    def on_ap_verdict(self, token, watch, fact):
        if self.strategies:
            self.strategies.on_ap_verdict(token, watch, fact)

    def unavailable(self, token, reason):
        if self.reasons.get(token) != reason or self.good[token]:
            self.event("QUALITY_UNAVAILABLE", {"token": token, "reason": reason})
        self.good[token], self.reasons[token] = False, reason
        if reason in {
            "UNRECONSTRUCTABLE_SOURCE_ORDER",
            "BBO_DEPTH_INCONSISTENT",
        } or reason.startswith("PARSE_OR_IDENTITY_GAP"):
            # A later delta cannot prove the state after a lost/ambiguous book
            # update. A complete snapshot is the only restoration point.
            self.books[token].complete = False
        self.registry.invalidate_token(token, self.now, reason)
        if self.strategies:
            self.strategies.invalidate(token, reason)
        self.flows[token] = Flow()
        self.pressures[token] = Pressure(self, token)
        self.refills[token] = Refill(self, token)
        for observer in self.observers:
            if observer.token == token:
                observer.closed = True

    def connect(self, *, now, wall, seq):
        self.now, self.wall, self.seq = now, wall, seq
        self.generation += 1
        self.connected = True
        self.sources.clear()
        self.seen_trades.clear()
        for token, book in self.books.items():
            self.unavailable(token, "GENERATION_CHANGED")
            book.bids, book.asks = {}, {}
            book.complete = False
            book.generation = self.generation

    def gap(self, reason):
        self.connected = False
        for token in self.tokens:
            self.unavailable(token, reason)
            self.books[token].complete = False

    def set_clock(self, *, now, wall, seq, pending=()):
        if now < self.now - EPS:
            raise ValueError("RESEARCH_CLOCK_REGRESSION")
        self.now, self.wall, self.seq = now, wall, seq
        self.subindex = -1
        self.watermark = min([now, *pending])
        self.registry.expire(now)
        self.observers = [o for o in self.observers if not o.closed]
        self.seen_trades = {k: v for k, v in self.seen_trades.items() if v >= now - 2}
        for token in self.tokens:
            self.flows[token].trim(now)
            if self.good[token] and not self.legal(token):
                self.unavailable(token, "BOOK_QUALITY_EXPIRED")

    def legal(self, token):
        return (
            self.connected
            and token not in self.self_contaminated
            and self.books[token].legal(self.now, self.wall, self.generation)
        )

    def close(self, watermark):
        self.watermark = watermark
        for token in self.tokens:
            if not self.good[token]:
                continue
            pressure, refill = self.pressures[token], self.refills[token]
            pressure.close(watermark)
            if self.pressures[token] is not pressure or not self.good[token]:
                continue
            refill.observer.close(watermark)
            for observer in tuple(self.observers):
                if observer.token == token and self.good[token]:
                    observer.close(watermark)
            if self.good[token]:
                pressure.guard()
                refill.trim()
        if self.strategies:
            self.strategies.tick()

    def timer(self, *, now, wall, seq, pending=()):
        self.set_clock(now=now, wall=wall, seq=seq, pending=pending)
        self.close(self.watermark)
        self.publish()

    def publish(self):
        if self.strategies and not self.atomic:
            self.strategies.publish()

    def source(self, token, row):
        stamp = float(decimal(row["timestamp"]) / 1000)
        if not -0.002 - EPS <= self.wall - stamp <= 0.500 + EPS:
            self.unavailable(token, "EVENT_SOURCE_AGE")
            return False
        if stamp < self.sources.get(token, float("-inf")) - EPS:
            # A 20ms tolerance does not prove missing flow. No silent discard.
            self.unavailable(token, "UNRECONSTRUCTABLE_SOURCE_ORDER")
            return False
        self.sources[token] = stamp
        return True

    def _depth(self, token, row, changes, received):
        book = self.books[token]
        old_bids, old_asks = dict(book.bids), dict(book.asks)
        if row["event_type"] == "book":
            if row.get("truncated") or row.get("partial"):
                self.unavailable(token, "TRUNCATED_SNAPSHOT")
                book.complete = False
                return

            def levels(rows):
                result = {}
                for entry in rows:
                    p, q = decimal(entry["price"]), decimal(entry["size"])
                    if not 0 < p < 1 or p % book.tick or q < 0 or p in result:
                        raise ValueError("INVALID_BOOK_LEVEL")
                    if q:
                        result[p] = q
                return result

            book.bids, book.asks = levels(row["bids"]), levels(row["asks"])
            book.complete = True
            book.generation = self.generation
        elif not book.complete:
            self.unavailable(token, "DELTA_BEFORE_SNAPSHOT")
            return
        else:
            for change in changes:
                p, q = decimal(change["price"]), decimal(change["size"])
                if change["side"] not in {"BUY", "SELL"} or not 0 < p < 1 or p % book.tick or q < 0:
                    raise ValueError("INVALID_DEPTH_CHANGE")
                side = book.bids if change["side"] == "BUY" else book.asks
                if q:
                    side[p] = q
                else:
                    side.pop(p, None)
                    if (
                        self.strategies
                        and change["side"] == "SELL"
                        and old_asks
                        and p == min(old_asks)
                    ):
                        self.strategies.ask_removed(token, p)
                # Losses inside an atomic update remain evidence, but no
                # candidate is published until its final coherent book passes.
                for observer in (self.refills[token].observer, *tuple(self.observers)):
                    if observer.token == token:
                        observer.depth(received)
                if self.strategies:
                    self.strategies.intermediate_depth(token)
        for change in changes:
            for name, value in (("best_bid", book.bid), ("best_ask", book.ask)):
                if change.get(name) is not None and decimal(change[name]) != value:
                    self.unavailable(token, "BBO_DEPTH_INCONSISTENT")
                    return
        book.received, book.source = received, float(decimal(row["timestamp"]) / 1000)
        if not self.legal(token):
            self.unavailable(token, "BOOK_UNAVAILABLE")
            return
        self.good[token] = True
        self.reasons.pop(token, None)
        self.flows[token].coverage.append(received)
        for observer in (self.refills[token].observer, *tuple(self.observers)):
            if observer.token == token:
                observer.depth(received)
        if self.strategies:
            self.strategies.depth(token, old_bids, old_asks, received)

    def _trade(self, row, received):
        origin = row["asset_id"]
        book = self.books[origin]
        side, p, q = row["side"], decimal(row["price"]), decimal(row["size"])
        if side not in {"BUY", "SELL"} or not 0 < p < 1 or p % book.tick or q <= 0:
            raise ValueError("INVALID_TRADE")
        tx = row.get("transaction_hash")
        if not isinstance(tx, str) or re.fullmatch(r"0x[0-9a-fA-F]{64}", tx) is None:
            for token in self.tokens:
                self.unavailable(token, "TRADE_IDENTITY_AMBIGUOUS")
            return
        target = next(t for t in self.tokens if t != origin)
        views = [(origin, side, p, False)]
        other = self.books[target]
        aligned = self.legal(target)
        for left, right, reverse in ((book.bids, other.asks, True), (book.asks, other.bids, False)):
            aligned &= all(
                right.get(1 - price) == left[price] for price in sorted(left, reverse=reverse)[:3]
            )
            aligned &= all(
                left.get(1 - price) == right[price]
                for price in sorted(right, reverse=not reverse)[:3]
            )
        if aligned:
            views.append((target, "SELL" if side == "BUY" else "BUY", 1 - p, True))
        else:
            self.unavailable(target, "COMPLEMENT_VIEW_UNAVAILABLE")
        for token, direction, price, mirrored in views:
            identity = (
                tx.lower(),
                str(row["timestamp"]),
                token,
                direction,
                str(price.normalize()),
                str(q.normalize()),
            )
            if identity in self.seen_trades:
                self.counts["DUPLICATE_ECONOMIC_VIEW_IGNORED"] += 1
                continue
            if not self.source(token, row) or not self.good[token] or not self.legal(token):
                continue
            self.seen_trades[identity] = self.now
            t = Trade(
                hashlib.sha256(repr(identity).encode()).hexdigest(),
                token,
                direction,
                price,
                q,
                received,
                float(decimal(row["timestamp"]) / 1000),
                self.now,
                self.seq,
                self.subindex,
                origin,
                mirrored,
            )
            self.flows[token].trades.append(t)
            self.event("NORMALIZED_TRADE", asdict(t))
            self.pressures[token].trade(t)
            self.refills[token].observer.trade(t)
            for observer in tuple(self.observers):
                if observer.token == token:
                    observer.trade(t)
            self.pressures[token].guard()
            if self.strategies:
                self.strategies.trade(t)

    def apply(self, raw, *, now, wall, received, seq, pending=()):
        self.set_clock(now=now, wall=wall, seq=seq, pending=[received, *pending])
        if raw in ("PING", "PONG"):
            self.close(self.watermark)
            self.publish()
            return
        rows = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
        rows = rows if isinstance(rows, list) else [rows]
        self.atomic = True
        try:
            if not self.connected or received > now + EPS:
                raise ValueError("RESEARCH_INPUT_CLOCK_OR_CONNECTION")
            for index, row in enumerate(rows):
                self.subindex = index
                self.close(min([received, *pending]))
                kind = row["event_type"]
                if row.get("market") != self.market:
                    raise ValueError("RESEARCH_MARKET_IDENTITY")
                changes = row["price_changes"] if kind == "price_change" else [row]
                tokens = list(dict.fromkeys(ch["asset_id"] for ch in changes))
                if any(t not in self.books for t in tokens):
                    raise ValueError("RESEARCH_TOKEN_IDENTITY")
                for token in tokens:
                    if kind != "last_trade_price" and not self.source(token, row):
                        continue
                    book = self.books[token]
                    selected = [ch for ch in changes if ch["asset_id"] == token]
                    if kind in ("book", "price_change"):
                        self._depth(token, row, selected, received)
                    elif kind == "best_bid_ask":
                        if (
                            decimal(row["best_bid"]) != book.bid
                            or decimal(row["best_ask"]) != book.ask
                        ):
                            self.unavailable(token, "BBO_DEPTH_INCONSISTENT")
                        else:
                            book.received, book.source = (
                                received,
                                float(decimal(row["timestamp"]) / 1000),
                            )
                            if self.legal(token):
                                self.good[token] = True
                                self.flows[token].coverage.append(received)
                    elif kind == "tick_size_change":
                        self.unavailable(token, "TICK_CHANGED")
                        book.tick = decimal(row["new_tick_size"])
                        book.tick_verified = False
                    elif kind == "last_trade_price":
                        self._trade(row, received)
                    else:
                        self.unavailable(token, "UNKNOWN_EVENT")
            self.close(min([now, *pending]))
        except (KeyError, ValueError, TypeError, ArithmeticError) as error:
            for token in self.tokens:
                self.unavailable(token, "PARSE_OR_IDENTITY_GAP:" + str(error))
        finally:
            self.atomic = False
        self.publish()

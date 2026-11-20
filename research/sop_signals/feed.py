"""Source-preserving M12 input path; no complementary trade generation.

This is an observation contract, not proof of exchange matching-engine order.
An anchor quantity sharing/overrunning the hit's source timestamp cannot prove
pre-hit Q0 and is explicitly unknown. Raw parent frames remain the publication
boundary; each ordered depth sub-update still runs the guards.
"""

import json
import math
import re
from collections import Counter, deque

from maker_signals.model import Book, decimal

from .m12 import EPS, M12


class Feed:
    def __init__(self, market, tokens, ticks, emit, parameters=None, strategy="M12",
                 receiver_clock_evidence=None):
        self.market, self.tokens, self.emit = market, tuple(tokens), emit
        self.now = self.wall = 0.0
        self.seq, self.subindex, self.generation = 0, -1, 0
        self.book_epoch_serial = 0
        self.book_epochs = {t: 0 for t in tokens}
        self.connected = False
        self.books = {t: Book(t, tick=decimal(ticks[t]), tick_verified=True) for t in tokens}
        self.good = {t: False for t in tokens}
        self.reasons, self.sources, self.quantities, self.seen = {}, {}, {}, {}
        self.coverage = {t: deque() for t in tokens}
        self.counts = Counter()
        self.strategy = strategy
        if strategy == "M16":
            from .m16 import M16
            from .m16_clock import check_clock_evidence
            from .m16_timing import number

            checked = check_clock_evidence(receiver_clock_evidence)
            self.receiver_clock_check = checked
            self.receiver_clock_envelope = (
                (number(receiver_clock_evidence["valid_received_min"]),
                 number(receiver_clock_evidence["valid_received_max"]))
                if checked["status"] == "LOCAL_QUANTIZATION_PROVEN" else None
            )
            self.last_frame_received = None
            self.core = M16(self.event, parameters, receiver_quantum=checked["quantum"])
        elif strategy == "M13":
            from .m13 import M13

            self.core = M13(self.event, parameters)
        elif strategy == "M14":
            from .m14 import M14

            self.core = M14(self.event, parameters)
        elif strategy == "M15":
            from .m15 import M15

            self.core = M15(self.event, parameters)
        elif strategy == "M17":
            from .m17 import M17

            self.core = M17(self.event, parameters)
        elif strategy == "M18":
            from .m18 import M18

            self.core = M18(self.event, parameters)
        elif strategy == "M19":
            from .m19 import M19

            self.core = M19(self.event, parameters)
        elif strategy == "M21":
            from .m21 import M21

            self.core = M21(self.event, parameters)
        elif strategy == "M23":
            from .m23 import M23

            self.core = M23(self.event, parameters)
        elif strategy == "M24":
            from .m24 import ExecutionFeed, Parameters, Sweep

            self.core = (ExecutionFeed(self.event, parameters) if isinstance(parameters, Parameters)
                         else Sweep(self.event, parameters or (Parameters(),)))
        elif strategy == "M12":
            from .m12_overlap import Parameters as OverlapParameters, M12Overlap

            self.core = (M12Overlap(self.event, parameters)
                         if isinstance(parameters, OverlapParameters)
                         else M12(self.event, parameters))
        else:
            raise ValueError("UNIMPLEMENTED_SOP_STRATEGY:" + str(strategy))

    def event(self, kind, payload):
        if self.strategy in {"M17", "M18", "M19", "M21", "M23"} and kind == "QUALITY_UNKNOWN":
            # An internal per-print deadline/source failure needs the same
            # full-snapshot recovery as an adapter-detected quality failure.
            # Mutate the input gate without calling invalidate recursively.
            token = payload["token"]
            self.good[token], self.reasons[token] = False, payload["reason"]
            self.coverage[token].clear()
            self.books[token].complete = False
        self.counts[kind] += 1
        self.emit(
            "SOP_EVIDENCE",
            {
                "event": kind,
                "market": self.market,
                "input_seq": self.seq,
                "subindex": self.subindex,
                "generation": self.generation,
                "available": self.now,
                "payload": payload,
            },
        )

    def unavailable(self, token, reason, *, rebuild=False):
        if self.good[token] or self.reasons.get(token) != reason:
            self.core.invalidate(token, reason)
        self.good[token], self.reasons[token] = False, reason
        self.coverage[token].clear()
        if rebuild:
            self.books[token].complete = False

    def connect(self, *, now, wall, seq):
        self.clock(now=now, wall=wall, seq=seq)
        self.connected = True
        self.generation += 1
        self.sources.clear()
        self.quantities.clear()
        self.seen.clear()
        for t, b in self.books.items():
            self.unavailable(t, "GENERATION_CHANGED", rebuild=True)
            b.bids.clear()
            b.asks.clear()
            b.generation = self.generation

    def gap(self, reason):
        self.connected = False
        for t in self.tokens:
            self.unavailable(t, reason, rebuild=True)

    def clock(self, *, now, wall, seq):
        # Clock advancement itself can publish expiry/end facts. Bind the
        # outer evidence cursor first, so those facts use this input as well.
        self.now, self.wall, self.seq, self.subindex = now, wall, seq, -1
        self.core.clock(now, seq)
        self.seen = {k: v for k, v in self.seen.items() if v[1] >= now - 2}
        for t in self.tokens:
            while self.coverage[t] and self.coverage[t][0] < now - 2:
                self.coverage[t].popleft()
            if self.good[t] and not self.legal(t):
                self.unavailable(t, "BOOK_QUALITY_EXPIRED")

    def legal(self, token):
        return self.connected and self.books[token].legal(self.now, self.wall, self.generation)

    def covered(self, token, start, end):
        buckets = {math.floor(t / 0.250) for t in self.coverage[token] if start - 0.250 <= t <= end}
        return all(
            b in buckets
            for b in range(math.floor(start / 0.250), math.floor((end - EPS) / 0.250) + 1)
        )

    def close(self, watermark):
        self.core.close(watermark, self.covered)

    def timer(self, *, now, wall, seq, pending=()):
        self.clock(now=now, wall=wall, seq=seq)
        self.close(min([now, *pending]))
        self.core.publish()

    def source(self, token, row):
        if "timestamp" not in row:
            self.unavailable(token, "SOURCE_TIME_MISSING", rebuild=True)
            return None
        stamp = float(decimal(row["timestamp"]) / 1000)
        if not -0.002 - EPS <= self.wall - stamp <= 0.500 + EPS:
            self.unavailable(token, "EVENT_SOURCE_AGE", rebuild=True)
            return None
        if stamp < self.sources.get(token, float("-inf")) - EPS:
            self.unavailable(token, "UNRECONSTRUCTABLE_SOURCE_ORDER", rebuild=True)
            return None
        self.sources[token] = stamp
        return stamp

    def depth(self, token, row, changes, received, stamp, *, raw_indices=None):
        book = self.books[token]
        snapshot = row["event_type"] == "book"
        if self.strategy == "M23":
            old_best_bid, old_bids = book.bid, book.bids
            old_book_valid = self.good[token] and book.complete and self.legal(token)
        if snapshot:
            if row.get("partial") or row.get("truncated"):
                self.unavailable(token, "TRUNCATED_SNAPSHOT", rebuild=True)
                return
            sides = {}
            for side, name in (("BID", "bids"), ("ASK", "asks")):
                levels = {}
                for x in row[name]:
                    p, q = decimal(x["price"]), decimal(x["size"])
                    if not 0 < p < 1 or p % book.tick or q < 0 or p in levels:
                        raise ValueError("INVALID_SNAPSHOT_LEVEL")
                    if q:
                        levels[p] = q
                sides[side] = levels
            if not book.complete:
                self.book_epoch_serial += 1
                self.book_epochs[token] = self.book_epoch_serial
                self.core.new_generation(token, self.book_epochs[token])
            updates = [
                (side, p, levels.get(p, decimal(0)))
                for side, levels in sides.items()
                for p in sorted(set(levels) | set(book.bids if side == "BID" else book.asks))
            ]
            book.bids, book.asks = sides["BID"], sides["ASK"]
            book.complete, book.generation = True, self.generation
            if self.strategy in {"M16", "M17", "M18", "M19", "M21", "M23", "M24"}:
                # Full books are atomic; retain the original bids-array index,
                # not the sorted diff position used by legacy consumers.
                bid_indices = {decimal(x["price"]): i for i, x in enumerate(row["bids"])}
                if self.strategy in {"M19", "M21", "M24"}:
                    ask_indices = {decimal(x["price"]): i for i, x in enumerate(row["asks"])}
        else:
            if not book.complete:
                self.unavailable(token, "DELTA_BEFORE_SNAPSHOT")
                return
            updates = []
            for ch in changes:
                side = {"BUY": "BID", "SELL": "ASK"}[ch["side"]]
                updates.append((side, decimal(ch["price"]), decimal(ch["size"])))
        for n, (side, p, q) in enumerate(updates):
            if not 0 < p < 1 or p % book.tick or q < 0:
                raise ValueError("INVALID_DEPTH_CHANGE")
            if self.strategy == "M23" and side == "BID":
                preload_args = {
                    "previous_quantity": old_bids.get(p,decimal(0)) if snapshot else book.quantity(side,p),
                    "previous_quantity_ref": self.quantities.get((token,side,p)),
                    "pre_event_best_bid": old_best_bid if snapshot else book.bid,
                    "pre_event_valid": old_book_valid if snapshot else self.good[token] and self.legal(token),
                }
            if not snapshot:
                levels = book.bids if side == "BID" else book.asks
                if q:
                    levels[p] = q
                else:
                    levels.pop(p, None)
            # Source key includes the original parent row AND internal update.
            if (self.strategy in {"M16", "M17", "M18", "M23"} and side == "BID") or self.strategy in {"M19", "M21", "M24"}:
                original_index = (bid_indices if side == "BID" else ask_indices).get(p) if snapshot else raw_indices[n]
                if original_index is None:
                    # Absence in a complete snapshot ends that old fixed level;
                    # it is not an invented explicit P quantity endpoint.
                    self.core.level_missing(token, side, p, received,
                                            frame=self.seq, row=self.subindex, source=stamp)
                else:
                    ref = self.seq, self.subindex, original_index
                    self.quantities[token, side, p] = (received, stamp, ref)
                    self.core.depth(token, side, p, q, received,
                                    generation=self.book_epochs[token], input_ref=ref, source=stamp,
                                    kind=side+"_BOOK" if snapshot else side+"_PRICE_CHANGE",
                                    **(preload_args if self.strategy == "M23" else {}))
            else:
                self.quantities[token, side, p] = (received, stamp, (self.seq, self.subindex, n))
                self.core.depth(token, side, p, q, received)
            if not snapshot and side == "ASK" and hasattr(self.core, "book"):
                self.core.book(token, book.asks, received, self.book_epochs[token], self.covered,
                               input_ref=(self.seq, self.subindex, n))
            if not q:
                self.quantities.pop((token, side, p), None)
        if not snapshot:
            for ch in changes:
                if any(
                    ch.get(name) is not None and decimal(ch[name]) != value
                    for name, value in (("best_bid", book.bid), ("best_ask", book.ask))
                ):
                    self.unavailable(token, "BBO_DEPTH_INCONSISTENT", rebuild=True)
                    return
        book.received, book.source = received, stamp
        self.good[token] = self.legal(token)
        if not self.good[token]:
            reason = self.core.quality.get(token, "BOOK_UNAVAILABLE") if self.strategy in {"M17", "M18", "M19", "M21", "M23"} else "BOOK_UNAVAILABLE"
            self.unavailable(token, reason)
        else:
            self.reasons.pop(token, None)
            self.coverage[token].append(received)
            if snapshot and hasattr(self.core, "book"):
                self.core.book(token, book.asks, received, self.book_epochs[token], self.covered,
                               input_ref=(self.seq, self.subindex, "SNAPSHOT"))

    def m23_raw_presence_guard(self, row, received):
        """Preserve unproved original SELL presence before any lossy return."""
        if self.strategy != "M23" or row.get("side") == "BUY":
            return
        token, price = row["asset_id"], None
        try:
            price = decimal(row["price"])
            if not price.is_finite() or not 0 < price < 1:
                price = None
            quantity = decimal(row["size"])
            timestamp = decimal(row["timestamp"])
            match = row.get("match_id") or row.get("trade_id") or row.get("id")
            tx = row.get("transaction_hash")
            identified = bool(match) or (isinstance(tx,str) and re.fullmatch(r"0x[0-9a-fA-F]{64}",tx) is not None)
            valid = (row.get("side")=="SELL" and price is not None and quantity.is_finite()
                     and quantity>0 and timestamp.is_finite() and identified)
        except (KeyError,TypeError,ValueError,ArithmeticError):
            valid = False
        if not valid:
            self.core.observe_unproven_sell(token,price,received)

    def m21_raw_buy_presence_guard(self, row, received):
        """Unknown original BUY presence must survive a same-receipt rebuild."""
        if self.strategy != "M21" or row.get("side") == "SELL":
            return
        price = None
        try:
            price = decimal(row['price'])
            if not price.is_finite() or not 0 < price < 1:
                price = None
            quantity, timestamp = decimal(row['size']), decimal(row['timestamp'])
            match = row.get('match_id') or row.get('trade_id') or row.get('id')
            tx = row.get('transaction_hash')
            identified = bool(match) or (isinstance(tx,str) and re.fullmatch(r'0x[0-9a-fA-F]{64}',tx) is not None)
            valid = (row.get('side') == 'BUY' and price is not None and quantity.is_finite()
                     and quantity > 0 and timestamp.is_finite() and identified)
        except (KeyError,TypeError,ValueError,ArithmeticError):
            valid = False
        if not valid:
            self.core.observe_unproven_buy(row['asset_id'],price,received)

    def trade(self, row, received):
        token = row["asset_id"]
        if self.strategy in {"M21", "M24"}:
            # A proved retransmission belongs to its first original receipt.
            # Check before source regression can erase that identity or turn
            # an old BUY into an unproved BUY at a new seed's receipt.
            match = row.get('match_id') or row.get('trade_id') or row.get('id')
            try:
                signature = (token,row['side'],str(decimal(row['price']).normalize()),
                             str(decimal(row['size']).normalize()),str(row['timestamp']))
                old = self.seen.get((token,str(match))) if match else None
                if old is not None and old[0] == signature:
                    self.counts['DUPLICATE_DIRECT_TRADE'] += 1
                    return
            except (KeyError,ValueError,TypeError,ArithmeticError):
                pass
        stamp = self.source(token, row)
        if stamp is None:
            if self.strategy == "M21" and row.get("side") != "SELL":
                try:
                    price = decimal(row['price'])
                except (KeyError,ValueError,TypeError,ArithmeticError):
                    price = None
                self.core.observe_unproven_buy(token,price,received)
            if self.strategy == "M23" and row.get("side") != "BUY":
                try:
                    price = decimal(row['price'])
                except (KeyError,ValueError,TypeError,ArithmeticError):
                    price = None
                self.core.observe_unproven_sell(token,price,received)
            return
        side, p, q = row["side"], decimal(row["price"]), decimal(row["size"])
        book = self.books[token]
        if side not in {"SELL", "BUY"} or q <= 0 or not 0 < p < 1 or p % book.tick:
            raise ValueError("INVALID_DIRECT_TRADE")
        match = row.get("match_id") or row.get("trade_id") or row.get("id")
        tx = row.get("transaction_hash")
        if not match and (
            not isinstance(tx, str) or re.fullmatch(r"0x[0-9a-fA-F]{64}", tx) is None
        ):
            self.unavailable(token, "DIRECT_TRADE_IDENTITY_MISSING", rebuild=True)
            return
        signature = (token, side, str(p.normalize()), str(q.normalize()), str(row["timestamp"]))
        identity = (token, str(match)) if match else (str(tx).lower(), *signature)
        if identity in self.seen:
            old, _ = self.seen[identity]
            if old != signature or not match:
                if self.strategy == "M21":
                    self.core.observe_unproven_buy(token,None,received)
                if self.strategy == "M23":
                    self.core.observe_unproven_sell(token,None,received)
                self.unavailable(token, "DIRECT_TRADE_IDENTITY_AMBIGUOUS", rebuild=True)
            else:
                self.counts["DUPLICATE_DIRECT_TRADE"] += 1
            return
        self.seen[identity] = (signature, self.now)
        if self.strategy in {"M21", "M23"}:
            self.core.observe_direct_trade(token,side,p,received)
        if not self.good[token] or not self.legal(token):
            return
        if self.strategy == "M24":
            self.coverage[token].append(received)
            bid, ask = book.bid, book.ask
            self.core.trade(
                token=token, side=side, price=p, quantity=q, received=received,
                source=stamp, ref=(self.seq, self.subindex, 0),
                generation=self.book_epochs[token], book_source=book.source,
                bid=(bid, book.bids[bid], self.quantities.get((token, "BID", bid))),
                ask=(ask, book.asks[ask], self.quantities.get((token, "ASK", ask))),
            )
            self.core.finish_boundary(token)
            return
        level = "BID" if side == "SELL" else "ASK"
        provenance = self.quantities.get((token, level, p))
        if hasattr(self.core, "requires_pre_hit_for_trade"):
            if self.strategy in {"M19", "M21"}:
                pre_hit_args = {"pre_event_best_bid":book.bid,"pre_event_best_ask":book.ask}
            else:
                pre_hit_args = {"pre_event_best_bid": book.bid} if self.strategy in {"M17", "M18"} else {}
            needs_pre_hit = self.core.requires_pre_hit_for_trade(
                token=token, side=side, price=p, received=received, **pre_hit_args
            )
        else:
            needs_pre_hit = not hasattr(self.core, "requires_pre_hit") or self.core.requires_pre_hit(side)
        if needs_pre_hit and (token, level, p) not in self.core.rounds and (
            provenance is None or provenance[1] >= stamp - EPS or provenance[0] > received + EPS
        ):
            reason = ("ANCHOR_QUANTITY_PROVENANCE_UNAVAILABLE" if provenance is None
                      else "INITIAL_DEPTH_ORDER_UNKNOWN") if self.strategy == "M17" else "PRE_HIT_LEVEL_ORDER_UNPROVEN"
            self.unavailable(token, reason)
            return
        self.coverage[token].append(received)
        provenance_args = (
            {"input_ref": (self.seq, self.subindex, 0), "source": stamp}
            if self.strategy in {"M16", "M17", "M18", "M19", "M21", "M23"} else {}
        )
        if self.strategy == "M17":
            provenance_args.update(economic_identity=identity, initial_quantity_ref=provenance,
                                   pre_event_best_bid=book.bid)
        elif self.strategy == "M18":
            provenance_args["pre_event_best_bid"] = book.bid
        elif self.strategy == "M19":
            provenance_args.update(initial_quantity_ref=provenance,
                                   pre_event_best_bid=book.bid,pre_event_best_ask=book.ask)
        elif self.strategy == "M21":
            provenance_args.update(initial_quantity_ref=provenance,
                pre_event_best_bid=book.bid,pre_event_best_ask=book.ask,
                opposite_quantity=book.quantity('ASK',book.ask) if book.ask is not None else None,
                opposite_quantity_ref=self.quantities.get((token,'ASK',book.ask)))
        elif self.strategy == "M23":
            provenance_args.update(initial_quantity_ref=provenance)
        self.core.trade(
            token=token,
            side=side,
            price=p,
            quantity=q,
            received=received,
            q0=book.quantity(level, p),
            generation=self.book_epochs[token],
            **provenance_args,
        )

    def apply(self, raw, *, now, wall, received, seq, pending=()):
        self.clock(now=now, wall=wall, seq=seq)
        if self.strategy == "M16":
            from .m16_timing import number

            envelope = self.receiver_clock_envelope
            reason = None
            try:
                receipt = number(received)
                if envelope is None:
                    reason = "M16_RECEIVER_CLOCK_EVIDENCE_UNAVAILABLE"
                elif not envelope[0] <= receipt <= envelope[1]:
                    reason = "M16_RECEIVED_OUTSIDE_CLOCK_PROOF_ENVELOPE"
                elif self.last_frame_received is not None and receipt < self.last_frame_received:
                    reason = "M16_RECEIVED_CLOCK_REGRESSION"
                else:
                    self.last_frame_received = receipt
            except (TypeError, ValueError, ArithmeticError):
                reason = "M16_RECEIVED_CLOCK_INVALID"
            if reason:
                for token in self.tokens:
                    self.unavailable(token, reason, rebuild=True)
                return
        if raw in ("PING", "PONG"):
            self.close(min([now, *pending]))
            self.core.publish()
            return
        try:
            rows = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
            rows = rows if isinstance(rows, list) else [rows]
            if not self.connected or received > now + EPS:
                raise ValueError("CONNECTION_OR_RECEIPT_CLOCK")
            for index, row in enumerate(rows):
                if not isinstance(row, dict):
                    raise ValueError("RAW_EVENT_NOT_OBJECT")
                self.subindex = index
                self.core.clock(now, seq, index)
                self.close(min([received, *pending]))
                if row.get("market") != self.market:
                    raise ValueError("MARKET_IDENTITY")
                kind = row["event_type"]
                changes = row["price_changes"] if kind == "price_change" else [row]
                tokens = list(dict.fromkeys(ch["asset_id"] for ch in changes))
                if any(t not in self.books for t in tokens):
                    raise ValueError("TOKEN_IDENTITY")
                for token in tokens:
                    if kind == "last_trade_price":
                        self.m23_raw_presence_guard(row,received)
                        self.m21_raw_buy_presence_guard(row,received)
                        self.trade(row, received)
                        continue
                    stamp = self.source(token, row)
                    if stamp is None:
                        continue
                    b = self.books[token]
                    if kind in {"book", "price_change"}:
                        indices = ({"raw_indices": [i for i, ch in enumerate(changes)
                                                    if ch["asset_id"] == token]}
                                   if self.strategy in {"M16", "M17", "M18", "M19", "M21", "M23", "M24"} else {})
                        self.depth(
                            token,
                            row,
                            [ch for ch in changes if ch["asset_id"] == token],
                            received,
                            stamp,
                            **indices,
                        )
                        if self.strategy == "M24":
                            self.core.finish_boundary(token)
                    elif kind == "best_bid_ask":
                        if decimal(row["best_bid"]) != b.bid or decimal(row["best_ask"]) != b.ask:
                            self.unavailable(token, "BBO_DEPTH_INCONSISTENT", rebuild=True)
                        elif b.complete:
                            b.received, b.source = received, stamp
                            self.good[token] = self.legal(token)
                            if self.good[token]:
                                self.coverage[token].append(received)
                    elif kind == "tick_size_change":
                        self.unavailable(token, "TICK_CHANGED", rebuild=True)
                        b.tick, b.tick_verified = decimal(row["new_tick_size"]), False
                    else:
                        self.unavailable(token, "UNKNOWN_EVENT", rebuild=True)
            self.close(min([now, *pending]))
        except (KeyError, TypeError, ValueError, ArithmeticError) as error:
            for token in self.tokens:
                self.unavailable(token, "PARSE_OR_SOURCE_GAP:" + str(error), rebuild=True)
        self.core.publish()

    def release_runtime(self):
        self.core.release()
        for values in (
            self.books,
            self.quantities,
            self.seen,
            self.coverage,
            self.sources,
            self.book_epochs,
        ):
            values.clear()
        self.core.emit = self.emit = None

"""Causal AP/RF/EX evidence for M1, attached to the existing SessionFeed.

All times are local monotonic availability times. No price movement, inventory,
cost or outcome probability enters these detectors. Prices identify queues.
"""

import hashlib
import json
import re
from collections import Counter, deque
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from statistics import median

from polymaker.rdp_sm_signal import Signal, number

W = .750
PROCESSING = .050
ZERO = Decimal(0)
ALGORITHM_REVISION = "m1-hold-v2"


@dataclass
class Attack:
    id: str
    side: str
    start: float
    depth: Decimal
    window: float = W
    prints: list = field(default_factory=list)


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


@dataclass(frozen=True)
class Opportunity:
    episode_id: str
    token_id: str
    outcome: str
    anchor_bid_price: Decimal
    signal_start_mono: float
    order_deadline: float
    rf_id: str
    ex_id: str
    ap_id: str
    generation: int


class M1Signals:
    def __init__(self, feed, *, record=None, out_of_order_tolerance_ms=20, signal_config=None):
        from polymaker.config import RDPM1SignalConfig

        self.feed, self.record = feed, record
        self.cfg = signal_config or RDPM1SignalConfig()
        self.parameters = self.cfg.model_dump(mode="json")
        self.parameter_hash = hashlib.sha256(json.dumps(self.parameters, sort_keys=True).encode()).hexdigest()
        self.counter = 0
        self.states = {}
        self.source = {}
        self.trade_keys = deque(maxlen=8192)
        self.counts = Counter()
        self.events = deque(maxlen=4096)
        self.selected = self.claimed = None
        self.episode_id = self.episode_start = None
        self.used_rf, self.used_ex = set(), set()
        self.opportunities = {}
        self.own_active = False
        self.block_reason = None
        self.failure = None
        self.out_of_order_tolerance_ms = number(out_of_order_tolerance_ms)
        self.decision_reason = None

    def emit(self, event, **payload):
        self.counts[event] += 1
        if payload.get("side") in {"BUY", "SELL"}:
            self.counts[event+"_"+payload["side"]] += 1
        row = {"event": event, "available_monotonic": self.feed.monotonic(),
               "generation": self.feed.generation, "algorithm_revision": ALGORITHM_REVISION, **payload}
        if event != "EVENT_DECISION":
            row["parameter_hash"] = self.parameter_hash
        self.events.append(row)
        if self.record is not None and not self.record(row):
            self.failure = "M1_EVENT_RECORDING_FAILED"
            self.invalidate(self.failure)

    def new_id(self, kind):
        self.counter += 1
        return f"m1:{self.feed.generation}:{kind}:{self.counter}"

    def state(self, token):
        return self.states.setdefault(token, {"attacks": {}, "active_ap": {},
            "round": None, "rounds": deque(maxlen=self.cfg.rf_rounds), "rf": None, "ex": None,
            "ex_watch": None, "trades": deque(), "uncertain_trades": deque(),
            "coverage": deque(), "anchor": None})

    def end_refill(self, token, state):
        # AP/EX describe aggressive flow across queues. A changed RF queue must
        # not erase their independent trade windows or pressure reference.
        self.emit("RF_INVALIDATED", token_id=token, reason="M1_ANCHOR_CHANGED",
                  round_id=state["round"].id if state["round"] else None,
                  completed_rounds=len(state["rounds"]), rf_id=(state["rf"] or {}).get("id"))
        state["round"] = state["rf"] = None
        state["rounds"].clear()

    def invalidate(self, reason, token=None):
        removed = False
        interrupted = []
        for t in list(self.states):
            if token is None or t == token:
                state = self.states[t]
                interrupted.append({"token_id": t, "ap_sides": list(state["attacks"]),
                    "round_id": state["round"].id if state["round"] else None,
                    "completed_rounds": len(state["rounds"]),
                    "rf": state["rf"] is not None, "ex_watch": state["ex_watch"] is not None,
                    "ex": state["ex"] is not None})
                self.states.pop(t)
                removed = True
        self.block_reason = reason
        if not self.failure and (removed or reason.startswith("WS_")):
            self.emit("GAP" if reason in {"WS_QUEUE_OVERFLOW", "WS_DISCONNECTED", "WS_RECONNECT_REQUIRED"}
                      else "SIGNAL_INVALIDATED", token_id=token, reason=reason, interrupted=interrupted)

    def reset(self):
        self.invalidate("M1_GENERATION_CHANGED")
        self.source.clear()
        self.trade_keys.clear()
        self.selected = None

    def legal(self, book, now):
        return (self.feed.connected and book.snapshot_generation == self.feed.generation
                and book.best_bid is not None and book.best_ask is not None
                and 0 < book.best_bid < book.best_ask < 1 and book.tick > 0
                and self.feed.tick_verified.get(book.token_id) == book.tick
                and 0 <= now-book.received_mono <= .200 + 1e-9
                and -.002-1e-9 <= self.feed.wall()-book.source_time <= .500 + 1e-9
                and (book.reported_bid is None or book.reported_bid == book.best_bid)
                and (book.reported_ask is None or book.reported_ask == book.best_ask))

    def before_event(self, kind, row, book, received, wall, *, books=None):
        token, now = book.token_id, self.feed.monotonic()
        self.decision_reason = None
        if self.failure:
            return False
        try:
            stamp = number(row["timestamp"])/1000
            if not -.002-1e-9 <= (self.feed.wall()-float(stamp)) <= .500 + 1e-9:
                raise ValueError("M1_SOURCE_AGE")
            previous = max(self.source.get(token, ZERO), number(book.source_time))
            if stamp < previous:
                if (previous-stamp)*1000 <= self.out_of_order_tolerance_ms:
                    # CA semantics: ignore, never reorder/backfill or refresh a
                    # book. Keep excluded volume as a conservative EX/AP bound.
                    if kind == "last_trade_price":
                        side, q = row["side"], number(row["size"])
                        if side not in {"BUY", "SELL"} or q <= 0:
                            raise ValueError("M1_TRADE_INVALID")
                        self.state(token)["uncertain_trades"].append((received, side, q))
                    self.decision_reason = "M1_EVENT_OUT_OF_ORDER_IGNORED"
                    self.emit("EVENT_IGNORED", token_id=token, kind=kind,
                              reason=self.decision_reason, rollback_ms=str((previous-stamp)*1000),
                              tolerance_ms=str(self.out_of_order_tolerance_ms))
                    return False
                raise ValueError("M1_SOURCE_REGRESSION")
            self.source[token] = stamp
            if now-received > .200 or received > now:
                raise ValueError("M1_PROCESSING_GAP")
            if kind == "tick_size_change":
                self.invalidate("M1_TICK_CHANGED", token)
                return False
            if not self.legal(book, now):
                self.invalidate("M1_BOOK_UNAVAILABLE", token)
                self.decision_reason = "M1_BOOK_UNAVAILABLE"
                return kind == "book"
            self.tick(books=books)
            s = self.state(token)
            if kind == "best_bid_ask":
                if (number(row["best_bid"]) != book.best_bid
                        or number(row["best_ask"]) != book.best_ask):
                    raise ValueError("M1_BBO_NOT_ALIGNED")
                s["coverage"].append(received)
            if kind != "last_trade_price":
                return True
            side, p, q = row["side"], number(row["price"]), number(row["size"])
            if side not in {"BUY", "SELL"} or q <= 0 or not 0 < p < 1 or p % book.tick:
                raise ValueError("M1_TRADE_INVALID")
            aligned = p == (book.best_bid if side == "SELL" else book.best_ask)
            if not aligned and self.cfg.flow_requires_top_match:
                raise ValueError("M1_TRADE_UNALIGNED")
            key = (token, stamp, side, p, q, row.get("transaction_hash"))
            if key in self.trade_keys:
                raise ValueError("M1_DUPLICATE_AMBIGUITY")
            self.trade_keys.append(key)
            s["trades"].append((received, side, q))
            if not aligned:
                self.emit("FLOW_TRADE_OFF_TOP", token_id=token, side=side, price=str(p), size=str(q))
            # Own fills cannot establish another primary RF/AP opportunity.
            if self.own_active:
                self.emit("SELF_CONTAMINATED", token_id=token, kind=kind)
                self._check_ex(s, now)
                return True
            active = s["active_ap"].get(side)
            if active is None and side not in s["attacks"]:
                levels = book.bids if side == "SELL" else book.asks
                depth = sum((v for _, v in levels[:3]), ZERO)
                if depth <= 0:
                    raise ValueError("M1_DEPTH_UNKNOWN")
                s["attacks"][side] = Attack(self.new_id("AP"), side, received, depth,
                                            window=self.cfg.ap_window_sec)
                self.emit("AP_STARTED", token_id=token, side=side,
                          ap_id=s["attacks"][side].id, evidence_start=received)
            for attack in s["attacks"].values():
                if attack.start <= received < attack.start+attack.window:
                    attack.prints.append((received, side, q))
            if side == "SELL":
                quantity = next((v for level, v in book.bids if level == p), ZERO)
                if quantity <= 0 or (not self.cfg.track_visible_anchor and p != book.best_bid):
                    self._check_ex(s, now)
                    return True  # Known executed flow, no observable queue quantity for an RF hit.
                if (self.cfg.track_visible_anchor and s["anchor"] != p
                        and (s["round"] is not None or s["rounds"] or s["rf"] is not None)):
                    self._check_ex(s, now)
                    return True  # Different visible queue: flow, not an RF hit on our anchor.
                s["anchor"] = p
                r = s["round"]
                if r is None:
                    r = Round(self.new_id("ROUND"), received, p,
                              quantity, quantity)
                    s["round"] = r
                    self.emit("ROUND_STARTED", token_id=token, round_id=r.id,
                              anchor=str(p), initial=str(r.initial), evidence_start=received)
                if r.anchor == p and received < r.start+self.cfg.round_window_sec:
                    r.hit += q
            self._check_ex(s, now)
            return True
        except (KeyError, ValueError, TypeError, ArithmeticError) as exc:
            reason = str(exc) if str(exc).startswith("M1_") else "M1_EVENT_INVALID"
            self.emit("UNAVAILABLE", token_id=token, reason=reason)
            self.invalidate(reason, token)
            self.decision_reason = reason
            return False

    def mirror_trade(self, row, books, received, wall):
        """One reported fill, two complementary quote views; never a second fill.

        Binary books must agree at their visible top levels. Missing identity
        or a contradictory view invalidates the derived evidence, not liquidity.
        """
        origin = row.get("asset_id")
        tokens = (self.feed.meta.yes.token_id, self.feed.meta.no.token_id)
        if origin not in tokens:
            return
        target = tokens[1] if origin == tokens[0] else tokens[0]
        try:
            side, price, size = row["side"], number(row["price"]), number(row["size"])
            if side not in {"BUY", "SELL"} or not 0 < price < 1 or size <= 0:
                return  # The original input validator records malformed public data.
            if re.fullmatch(r"0x[0-9a-fA-F]{64}", row.get("transaction_hash") or "") is None:
                raise ValueError("M1_MIRROR_IDENTITY_UNKNOWN")
            source_book, target_book = books[origin], books[target]
            for left, right in ((source_book.bids, target_book.asks),
                                (source_book.asks, target_book.bids)):
                lm, rm = dict(left), dict(right)
                if (any(rm.get(1-p) != q for p, q in left[:3])
                        or any(lm.get(1-p) != q for p, q in right[:3])):
                    raise ValueError("M1_MIRROR_BOOK_UNALIGNED")
            derived = {**row, "asset_id": target, "side": "SELL" if side == "BUY" else "BUY",
                       "price": str(1-price), "m1_origin_asset_id": origin, "m1_mirrored": True}
            accepted = self.before_event("last_trade_price", derived, target_book, received, wall, books=books)
            self.emit("COMPLEMENT_TRADE_VIEW", origin_asset_id=origin, token_id=target,
                      source_side=side, source_price=str(price), side=derived["side"],
                      price=derived["price"], size=str(size), transaction_hash=row["transaction_hash"],
                      source_timestamp=row["timestamp"], accepted=accepted,
                      reason=self.decision_reason if not accepted else None,
                      source_frame=self.feed.m1_frame)
        except (KeyError, ValueError, TypeError, ArithmeticError) as exc:
            reason = str(exc) if str(exc).startswith("M1_") else "M1_MIRROR_INPUT_INVALID"
            self.emit("UNAVAILABLE", token_id=target, reason=reason)
            self.invalidate(reason, target)

    @staticmethod
    def recovery(r, quantity):
        loss = r.initial-r.low
        return min(Decimal(1), max(ZERO, quantity-r.low)/loss) if loss > 0 else ZERO

    def qualified(self, r):
        minimum = max(self.cfg.min_round_shares, r.initial*self.cfg.min_round_depth_fraction)
        return r.hit >= minimum and r.initial-r.low >= minimum

    def depth(self, book, received):
        token, now = book.token_id, self.feed.monotonic()
        if not self.legal(book, now):
            self.invalidate("M1_BOOK_UNAVAILABLE", token)
            return
        s = self.state(token)
        if self.anchor_lost(s, book):
            self.end_refill(token, s)
        if (not self.cfg.track_visible_anchor or s["anchor"] is None
                or not (s["round"] is not None or s["rounds"] or s["rf"] is not None)):
            s["anchor"] = book.best_bid
        s["coverage"].append(received)
        self.depth_quantity(book)

    def anchor_lost(self, state, book):
        anchor = state["anchor"]
        if anchor is None:
            return False
        return (not any(p == anchor and q > 0 for p, q in book.bids)
                if self.cfg.track_visible_anchor else anchor != book.best_bid)

    def depth_quantity(self, book):
        """Observe fixed-queue losses/refills inside one atomic delta message."""
        s = self.states.get(book.token_id)
        if s is None:
            return
        r = s["round"]
        if r is not None and not self.own_active:
            quantity = next((q for p, q in book.bids if p == r.anchor), ZERO)
            if quantity < r.low:
                # A new trough invalidates the earlier recovery/hold proof.
                # The next recovery must establish its own full 100ms hold.
                r.low, r.hold_start, r.held = quantity, None, False
            self._hold(r, quantity, self.feed.monotonic())

    def _hold(self, r, quantity, now):
        if not self.qualified(r):
            return
        recovery = self.recovery(r, quantity)
        if recovery < Decimal(".2"):
            r.held = False  # Established absorption has failed, even without a new low.
        if recovery < Decimal(".4"):
            r.hold_start = None
        elif r.hold_start is None and recovery >= Decimal(".5"):
            r.hold_start = now
        if r.hold_start is not None and min(now, self.feed.m1_watermark())-r.hold_start >= .100-1e-9:
            r.held = True

    def _attack_close(self, token, s, a, now):
        settings = self.cfg
        ap_window = a.window
        if now > a.start+ap_window+PROCESSING+1e-9:
            self.emit("UNAVAILABLE", token_id=token, reason="M1_AP_PROCESSING_LATE")
            return
        same = [(t, q) for t, side, q in a.prints if side == a.side]
        volume = sum((q for _, q in same), ZERO)
        total = sum((q for _, _, q in a.prints), ZERO)
        uncertain_same = [q for t, side, q in s["uncertain_trades"]
                          if a.start <= t < a.start+ap_window and side == a.side]
        uncertain_opposite = sum((q for t, side, q in s["uncertain_trades"]
                                  if a.start <= t < a.start+ap_window and side != a.side), ZERO)
        # A same-direction excluded print cannot lower directional share. It
        # contributes no primary evidence; its possible single-print dominance
        # still has to pass the worst-case bound below.
        total += uncertain_opposite
        concentration = (max([max((q for _, q in same), default=ZERO)/volume]
                             + [q/(volume+q) for q in uncertain_same]) if volume else Decimal(1))
        minimum = max(settings.ap_min_shares, a.depth*settings.ap_min_depth_fraction)
        occupied = {min(2, int((t-a.start)/(ap_window/3))) for t, _ in same}
        if not (total and volume >= minimum and volume/total >= settings.ap_min_share
                and len(same) >= settings.ap_min_records and len(occupied) >= 2 and 2 in occupied
                and concentration <= settings.ap_max_concentration):
            self.emit("AP_NOT_CONFIRMED", token_id=token, side=a.side, ap_id=a.id,
                      volume=str(volume), total_volume=str(total), minimum=str(minimum),
                      records=len(same), occupied_subwindows=sorted(occupied),
                      concentration=str(concentration) if volume else None,
                      uncertain_same_volume=str(sum(uncertain_same, ZERO)),
                      uncertain_opposite_volume=str(uncertain_opposite))
            return
        fact = {"id": a.id, "confirmed": now, "deadline": now+W, "evidence_start": a.start,
                "window": ap_window,
                "volume": volume, "minimum": minimum, "last_check": now}
        s["active_ap"][a.side] = fact
        s["active_ap"].pop("BUY" if a.side == "SELL" else "SELL", None)
        self.emit("AP_CONFIRMED", token_id=token, side=a.side,
                  ap_id=a.id, volume=str(volume), total_volume=str(total),
                  share=str(volume/total), depth=str(a.depth), minimum=str(minimum),
                  records=len(same), occupied_subwindows=sorted(occupied),
                  concentration=str(concentration),
                  uncertain_same_volume=str(sum(uncertain_same, ZERO)),
                  uncertain_opposite_volume=str(uncertain_opposite),
                  share_policy="DIRECTIONAL_LOWER_BOUND_WITH_DOMINANCE_UPPER_BOUND",
                  evidence_start=a.start, evidence_end=a.start+ap_window,
                  reference_window_sec=ap_window)
        if a.side == "SELL":
            s["ex_watch"] = {**fact, "start": now,
                "comparison_volume": volume*Decimal(str(W))/Decimal(str(ap_window))}
        else:
            if s["ex"] is not None or s["ex_watch"] is not None:
                self.emit("EX_ENDED", token_id=token, reason="REVERSE_AP_BUY", ap_id=a.id)
            s["ex"] = None
            s["ex_watch"] = None

    def _check_ex(self, s, now):
        ex = s["ex"]
        if ex and (now >= ex["deadline"] or
                   sum((q for t, side, q in (*s["trades"], *s["uncertain_trades"])
                        if now-W <= t < now and side == "SELL"), ZERO)
                   >= ex["volume"]*Decimal(".5")):
            s["ex"] = None

    def tick(self, *, books=None):
        now = self.feed.monotonic()
        watermark = self.feed.m1_watermark()
        if self.failure:
            return
        for token in list(self.states):
            book = (self.feed.books if books is None else books)[token]
            if not self.legal(book, now):
                self.invalidate("M1_BOOK_UNAVAILABLE", token)
                continue
            s = self.states[token]
            ends = [a.start+a.window for a in s["attacks"].values()]
            if s["round"] is not None:
                ends.append(s["round"].start+self.cfg.round_window_sec)
            if s["ex_watch"] is not None:
                ends.append(s["ex_watch"]["start"]+W)
            if any(watermark < end <= now-PROCESSING-1e-9 for end in ends):
                self.invalidate("M1_INPUT_BACKLOG", token)
                continue
            if self.anchor_lost(s, book):
                self.end_refill(token, s)
                s["anchor"] = book.best_bid
            while s["trades"] and s["trades"][0][0] < now-6:
                s["trades"].popleft()
            while s["coverage"] and s["coverage"][0] < now-6:
                s["coverage"].popleft()
            while s["uncertain_trades"] and s["uncertain_trades"][0][0] < now-6:
                s["uncertain_trades"].popleft()
            while s["rounds"] and now-s["rounds"][0][0] > self.cfg.rf_sequence_span_sec:
                s["rounds"].popleft()
            for side, a in list(s["attacks"].items()):
                if now >= a.start+a.window and watermark >= a.start+a.window:
                    del s["attacks"][side]
                    self._attack_close(token, s, a, now)
            for side, a in list(s["active_ap"].items()):
                faded = now >= a["deadline"]
                if now-a["last_check"] >= .250-1e-9:
                    a["last_check"] = now
                    recent = [(t, d, q) for t, d, q in s["trades"] if now-a["window"] <= t < now]
                    total = sum((q for _, _, q in recent), ZERO)
                    total += sum((q for t, d, q in s["uncertain_trades"]
                                  if now-a["window"] <= t < now and d != side), ZERO)
                    own = sum((q for _, d, q in recent if d == side), ZERO)
                    faded |= (not total or own/total < Decimal(".6")
                              or own < a["minimum"]/2
                              or not any(t >= now-a["window"]/3 and d == side for t, d, _ in recent))
                if faded:
                    s["active_ap"].pop(side, None)
            r = s["round"]
            if r is not None and not self.own_active:
                round_end = r.start+self.cfg.round_window_sec
                quantity = next((q for p, q in book.bids if p == r.anchor), ZERO)
                self._hold(r, quantity, min(now, round_end))
                if now >= round_end and watermark >= round_end:
                    s["round"] = None
                    if now > round_end+PROCESSING+1e-9:
                        self.invalidate("M1_ROUND_PROCESSING_LATE", token)
                        continue
                    if self.qualified(r):
                        recovery = self.recovery(r, quantity)
                        success = r.held and recovery >= Decimal(".5")
                        self.emit("ROUND_CLOSED", token_id=token, round_id=r.id,
                                  recovery=str(recovery), stable=success,
                                  initial=str(r.initial), trough=str(r.low), hit=str(r.hit),
                                  held=r.held, hold_start=r.hold_start,
                                  evidence_start=r.start, evidence_end=round_end)
                        if not success:
                            s["rounds"].clear()
                            s["rf"] = None
                        else:
                            s["rounds"].append((r.start, r.id, recovery))
                            if len(s["rounds"]) == self.cfg.rf_rounds:
                                if (now-s["rounds"][0][0] <= self.cfg.rf_sequence_span_sec
                                        and median(x[2] for x in s["rounds"]) >= self.cfg.rf_min_median_recovery):
                                    s["rf"] = {"id": self.new_id("RF"), "confirmed": now,
                                               "deadline": now+self.cfg.rf_ttl_sec, "rounds": list(s["rounds"])}
                                    self.emit("RF_CONFIRMED", token_id=token, rf_id=s["rf"]["id"],
                                              anchor_price=str(s["anchor"]),
                                              rounds=s["rf"]["rounds"], deadline=s["rf"]["deadline"])
                                else:
                                    self.emit("RF_NOT_CONFIRMED", token_id=token, rounds=list(s["rounds"]))
                                s["rounds"].clear()
                    else:
                        self.emit("ROUND_NOT_QUALIFIED", token_id=token, round_id=r.id,
                                  initial=str(r.initial), trough=str(r.low), hit=str(r.hit))
            if s["rf"] and now >= s["rf"]["deadline"]:
                s["rf"] = None
            w = s["ex_watch"]
            if w and now >= w["start"]+W and watermark >= w["start"]+W:
                s["ex_watch"] = None
                volumes = [sum((q for t, side, q in (*s["trades"], *s["uncertain_trades"])
                                if w["start"]+i*.250 <= t < w["start"]+(i+1)*.250
                                and side == "SELL"), ZERO) for i in range(3)]
                covered = all(any(w["start"]+i*.250 <= t < w["start"]+(i+1)*.250
                                  for t in s["coverage"]) for i in range(3))
                reference = w["comparison_volume"]
                if (not self.own_active and covered and now <= w["start"]+W+PROCESSING+1e-9
                        and all(v <= reference/6 for v in volumes)
                        and sum(volumes, ZERO) <= reference*Decimal(".25")):
                    s["ex"] = {"id": self.new_id("EX"), "ap_id": w["id"],
                               "confirmed": now, "deadline": now+self.cfg.ex_ttl_sec, "volume": reference,
                               "evidence_start": w["evidence_start"]}
                    self.emit("EX_CONFIRMED", token_id=token, ex_id=s["ex"]["id"], ap_id=w["id"],
                              reference_volume=str(w["volume"]), subwindow_volumes=[str(v) for v in volumes],
                              reference_comparison_volume=str(reference),
                              reference_window_sec=w["window"],
                              fade=str(sum(volumes, ZERO)/reference),
                              evidence_start=w["start"], evidence_end=w["start"]+W,
                              deadline=s["ex"]["deadline"])
                else:
                    self.emit("EX_NOT_CONFIRMED", token_id=token, ap_id=w["id"],
                              reference_volume=str(w["volume"]), subwindow_volumes=[str(v) for v in volumes],
                              reference_comparison_volume=str(reference),
                              covered=covered, processing_late=now > w["start"]+W+PROCESSING+1e-9)
            self._check_ex(s, now)

    def evaluate(self, snapshot):
        with self.feed.lock:
            self.tick()
            candidates = []
            if self.feed.connected and not self.failure and not self.own_active:
                for token, s in self.states.items():
                    rf, ex = s["rf"], s["ex"]
                    if not rf or not ex or rf["id"] in self.used_rf or ex["id"] in self.used_ex:
                        continue
                    first = min(rf.get("rounds", [(rf["confirmed"],)])[0][0],
                                ex.get("evidence_start", ex["confirmed"]))
                    if max(rf["confirmed"], ex["confirmed"])-first > self.cfg.combination_span_sec:
                        continue
                    key = (rf["id"], ex["id"])
                    if key not in self.opportunities:
                        self.opportunities[key] = Opportunity(self.new_id("OPPORTUNITY"), token,
                            "YES" if token == self.feed.meta.yes.token_id else "NO", s["anchor"],
                            max(rf["confirmed"], ex["confirmed"]), min(rf["deadline"], ex["deadline"]),
                            rf["id"], ex["id"], ex["ap_id"], self.feed.generation)
                        self.emit("OPPORTUNITY_IDENTIFIED", opportunity=asdict(self.opportunities[key]))
                    candidates.append(self.opportunities[key])
            # Stable session parity resolves exact-time ties without using price.
            if self.failure:
                candidates.clear()
            parity = sum(self.feed.meta.condition_id.encode()) % 2
            self.selected = min(candidates, key=lambda x: (x.signal_start_mono,
                                (x.outcome == "YES") == bool(parity))) if candidates else None
            self.episode_id = self.selected.episode_id if self.selected else None
            self.episode_start = self.selected.signal_start_mono if self.selected else None
            return Signal("VALID_"+self.selected.outcome if self.selected else "NONE",
                          snapshot.snapshot_id, event_mono=self.episode_start)

    def claim(self, signal):
        with self.feed.lock:
            if self.claimed is not None or self.selected is None or not signal.direction:
                return None
            self.claimed = (self.selected.episode_id, self.selected.outcome, signal.snapshot_id)
            return self.claimed

    def current(self, claim):
        with self.feed.lock:
            if not claim or claim != self.claimed:
                return False
            ep = next((v for v in self.opportunities.values() if v.episode_id == claim[0]), None)
            return ep is not None and self.active_reason(claim, None) is None

    def active_reason(self, claim, snapshot):
        with self.feed.lock:
            self.tick()
            ep = next((v for v in self.opportunities.values() if claim and v.episode_id == claim[0]), None)
            if self.failure:
                return self.failure
            if ep is None or ep.generation != self.feed.generation:
                return "M1_EPISODE_CHANGED"
            if self.feed.monotonic() >= ep.order_deadline:
                return "M1_OPPORTUNITY_EXPIRED"
            s = self.states.get(ep.token_id)
            if not s or not s["rf"] or not s["ex"]:
                return "M1_EVIDENCE_ENDED"
            if s["rf"]["id"] != ep.rf_id or s["ex"]["id"] != ep.ex_id:
                return "M1_EVIDENCE_CHANGED"
            return None

    def consume(self, claim):
        with self.feed.lock:
            if not self.current(claim):
                raise ValueError("M1_EPISODE_CHANGED")
            ep = next(v for v in self.opportunities.values() if v.episode_id == claim[0])
            if ep.rf_id in self.used_rf or ep.ex_id in self.used_ex:
                raise ValueError("M1_EPISODE_CONSUMED")
            self.used_rf.add(ep.rf_id)
            self.used_ex.add(ep.ex_id)
            self.own_active = True
            for state in self.states.values():
                state["attacks"].clear()
                state["round"] = None
                state["rounds"].clear()
                state["ex_watch"] = None
            self.emit("OPPORTUNITY_CONSUMED", opportunity=asdict(ep))

    def release(self, claim):
        with self.feed.lock:
            if self.claimed == claim:
                self.claimed = None
            if self.own_active:
                self.own_active = False
                self.invalidate("M1_ORDER_CLOSED_NEW_EVIDENCE_REQUIRED")

    def diagnostics(self):
        return {"counts": dict(self.counts), "failure": self.failure,
                "algorithm_revision": ALGORITHM_REVISION,
                "parameters": self.parameters, "parameter_hash": self.parameter_hash,
                "input_policy": "CA_TOLERANCE_WITH_CONSERVATIVE_FLOW_BOUNDS",
                "out_of_order_tolerance_ms": str(self.out_of_order_tolerance_ms),
                "block_reason": self.block_reason, "own_active": self.own_active,
                "selected": {k: str(v) if isinstance(v, Decimal) else v
                             for k, v in asdict(self.selected).items()} if self.selected else None}

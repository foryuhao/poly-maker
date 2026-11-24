"""Causal AP_BUY -> new RF_BID candidates, using the unchanged M1 primitives.

No BUY-fade threshold gates collection. The feed's legacy ``m1`` slot is only
used inside this independent observer. This detector never returns a trade signal.
"""

from collections import Counter, deque
from decimal import Decimal
from uuid import uuid4

from polymaker.m2_config import M2Config
from polymaker.rdp_sm_m1_signal import M1Signals
from polymaker.rdp_sm_signal import Signal

ZERO = Decimal(0)
REVISION = "m2-candidates-v1"
COMPONENTS = frozenset({"AP_STARTED", "AP_NOT_CONFIRMED", "AP_CONFIRMED", "RF_CONFIRMED",
                        "RF_INVALIDATED", "ROUND_CLOSED", "GAP", "SIGNAL_INVALIDATED"})


class M2Signals(M1Signals):
    def __init__(self, feed, *, settings=None, record=None):
        self.settings = settings or M2Config()
        self.namespace = uuid4().hex
        self.campaigns = {}
        self.candidates = {}
        self.attack_anchors = {}
        self.invalid_reasons = Counter()
        self.current_books = None
        self.event_anchor = None
        super().__init__(feed, record=record, signal_config=self.settings.signal,
                         out_of_order_tolerance_ms=self.settings.out_of_order_tolerance_ms)

    def new_id(self, kind):
        return f"m2:{self.namespace}:{super().new_id(kind)}"

    def state(self, token):
        # M1's setdefault builds several temporary deques even on a cache hit.
        # Reuse the identical state without that allocation on every depth delta.
        existing = self.states.get(token)
        return existing if existing is not None else super().state(token)

    def before_event(self, kind, row, book, received, wall, *, books=None):
        if kind == "last_trade_price" and row.get("side") == "BUY":
            self.event_anchor = {"ask": book.best_ask, "bid": book.best_bid,
                                 "trade_price": row.get("price")}
        try:
            return super().before_event(kind, row, book, received, wall, books=books)
        finally:
            self.event_anchor = None

    def emit(self, event, **payload):
        # Thousands of per-level EVENT_DECISION records are only counters in M2.
        # Avoid allocating the M1 diagnostic row/deque/callback for these, while
        # leaving input acceptance, state transitions and all evidence unchanged.
        if event not in COMPONENTS and not event.startswith("M2_"):
            self.counts[event] += 1
            side = payload.get("side")
            if side in {"BUY", "SELL"}:
                self.counts[event+"_"+side] += 1
            return
        super().emit(event, **payload)
        if self.failure:
            return
        token = payload.get("token_id")
        if event == "AP_STARTED" and payload.get("side") == "BUY":
            for key, value in list(self.attack_anchors.items()):
                if value["token_id"] == token:
                    self.attack_anchors.pop(key)
            self.attack_anchors[payload["ap_id"]] = {**(self.event_anchor or {}), "token_id": token}
        elif event == "AP_NOT_CONFIRMED":
            self.attack_anchors.pop(payload["ap_id"], None)
        elif event == "AP_CONFIRMED" and payload.get("side") == "BUY":
            self.start_campaign(token, payload)
        elif event == "RF_CONFIRMED":
            self.start_candidate(token, payload)
        elif event == "RF_INVALIDATED":
            self.end_candidate(token, payload.get("reason", "RF_INVALIDATED"))

    def end_candidate(self, token, reason):
        candidate = self.candidates.pop(token, None)
        if candidate:
            self.emit("M2_CANDIDATE_ENDED", candidate_id=candidate["candidate_id"],
                      token_id=token, reason=reason)

    def start_campaign(self, token, fact):
        now = self.feed.monotonic()
        self.end_candidate(token, "NEW_AP_BUY")
        state = self.state(token)
        # Clear all pre-confirmation RF evidence; AP/flow windows remain intact.
        if state["round"] is not None or state["rounds"] or state["rf"]:
            self.end_refill(token, state)
        state["anchor"] = None
        anchor = self.attack_anchors.pop(fact["ap_id"], {})
        campaign = {"ap_id": fact["ap_id"], "token_id": token,
                    "confirmed": now, "deadline": now+self.settings.attack_lifetime_sec,
                    "reference_volume": Decimal(fact["volume"]),
                    "reference_window_sec": fact["reference_window_sec"],
                    "attack_ask_anchor": anchor.get("ask"),
                    "attack_bid_anchor": anchor.get("bid"),
                    "attack_first_trade_price": anchor.get("trade_price"),
                    "attack_evidence_start": fact["evidence_start"],
                    "generation": self.feed.generation}
        self.campaigns[token] = campaign
        self.emit("M2_AP_BUY_REFERENCE", **campaign)

    def start_candidate(self, token, fact):
        now = self.feed.monotonic()
        campaign, state = self.campaigns.get(token), self.state(token)
        if not campaign or now >= campaign["deadline"]:
            return
        if any(row[0] < campaign["confirmed"] for row in fact["rounds"]):
            return
        self.end_candidate(token, "NEW_RF")
        book = (self.current_books or self.feed.books)[token]
        bid = state["anchor"]
        ask_anchor = campaign["attack_ask_anchor"]
        candidate = {**campaign, "candidate_id": self.new_id("CANDIDATE"),
                     "rf_id": fact["rf_id"], "anchor_bid_price": bid,
                     "outcome": "YES" if token == self.feed.meta.yes.token_id else "NO",
                     "rf_confirmed": now,
                     "rf_rounds": fact["rounds"],
                     "deadline": min(campaign["deadline"], fact["deadline"]),
                     "anchor_displacement": bid-ask_anchor if ask_anchor is not None else None,
                     "anchor_displacement_ticks": (bid-ask_anchor)/book.tick
                        if ask_anchor is not None else None,
                     "tick": book.tick, "last_observed": None}
        self.candidates[token] = candidate
        self.emit("M2_CANDIDATE", **candidate)

    def invalidate(self, reason, token=None):
        self.invalid_reasons[reason] += 1
        super().invalidate(reason, token)
        for key in list(self.campaigns):
            if token is None or key == token:
                self.end_candidate(key, reason)
                self.campaigns.pop(key, None)
        # A problem in one outcome must not erase the other outcome's anchor.
        for key, value in list(self.attack_anchors.items()):
            if token is None or value["token_id"] == token:
                self.attack_anchors.pop(key)

    def tick(self, *, books=None):
        previous = self.current_books
        self.current_books = books or self.feed.books
        try:
            super().tick(books=books)
            now = self.feed.monotonic()
            for token, campaign in list(self.campaigns.items()):
                if now >= campaign["deadline"]:
                    self.end_candidate(token, "AP_REFERENCE_EXPIRED")
                    self.campaigns.pop(token, None)
            for token, candidate in list(self.candidates.items()):
                state = self.states.get(token)
                rf = state.get("rf") if state else None
                if now >= candidate["deadline"] or not rf or rf["id"] != candidate["rf_id"]:
                    self.end_candidate(token, "RF_ENDED")
                    continue
                last = candidate["last_observed"]
                if last is None or now-last >= self.settings.observation_interval_sec-1e-9:
                    candidate["last_observed"] = now
                    self.observe(candidate, state, self.current_books[token], now)
        finally:
            self.current_books = previous

    def observe(self, candidate, state, book, now):
        window = candidate["reference_window_sec"]
        since = now-window
        # Entire comparison window must follow the AP confirmation.
        if since < candidate["confirmed"]-1e-9 or not self.legal(book, now):
            return
        covered = all(any(since+i*window/3 <= t < since+(i+1)*window/3
                          for t in state["coverage"]) for i in range(3))
        trades = [(side, q) for t, side, q in state["trades"] if since <= t < now]
        buy = sum((q for side, q in trades if side == "BUY"), ZERO)
        sell = sum((q for side, q in trades if side == "SELL"), ZERO)
        uncertain_buy = sum((q for t, side, q in state["uncertain_trades"]
                             if since <= t < now and side == "BUY"), ZERO)
        pending_age = max(0., now-self.feed.m1_watermark())
        valid = covered and pending_age <= .020+1e-9
        reference = candidate["reference_volume"]
        bid = candidate["anchor_bid_price"]
        visible = next((q for p, q in book.bids if p == bid), ZERO)
        self.emit("M2_OBSERVATION", **{k: v for k, v in candidate.items() if k != "last_observed"},
                  observation_valid=valid, coverage_complete=covered,
                  pending_input_age_ms=pending_age*1000,
                  buy_volume=buy, uncertain_buy_volume=uncertain_buy, sell_volume=sell,
                  buy_ratio=buy/reference, buy_ratio_upper=(buy+uncertain_buy)/reference,
                  visible_bid_quantity=visible, book_best_bid=book.best_bid,
                  book_best_ask=book.best_ask,
                  observation_window_start=since, observation_window_end=now)

    def evaluate(self, snapshot):
        self.tick()
        # Deliberately cannot authorize any existing trading controller.
        return Signal("NONE", snapshot.snapshot_id)

    def diagnostics(self):
        return {"revision": REVISION, "counts": dict(self.counts),
                "invalid_reasons": dict(self.invalid_reasons),
                "campaigns": len(self.campaigns), "candidates": len(self.candidates),
                "failure": self.failure, "settings_hash": self.settings.fingerprint}


class M2EventBuffer:
    """Bounded component/feature buffer; no disk I/O on the feed callback."""

    def __init__(self, *, capacity=4096, wall=None, context=None):
        self.rows = deque()
        self.capacity, self.wall = capacity, wall
        self.context = context
        self.last_diagnostic = {}

    def __call__(self, row):
        kind = row["event"]
        if not (kind.startswith("M2_") or kind in {
            "AP_CONFIRMED", "RF_CONFIRMED", "ROUND_CLOSED", "GAP", "SIGNAL_INVALIDATED",
        }):
            return True
        if kind in {"SIGNAL_INVALIDATED", "GAP"}:
            key = (row.get("token_id"), row.get("reason"))
            if row["available_monotonic"]-self.last_diagnostic.get(key, -1e9) < 1:
                return True
            self.last_diagnostic[key] = row["available_monotonic"]
        if len(self.rows) >= self.capacity:
            return False
        self.rows.append({**row, **(self.context(row["available_monotonic"]) if self.context else {}),
                          "m2_revision": REVISION,
                          "observed_wall": self.wall() if self.wall else self._wall()})
        return True

    @staticmethod
    def _wall():
        import time
        return time.time()

"""Counterfactual M1 maker orders on public data; never exchange/account I/O.

Visible quantity at arrival queues ahead. Only unique, accepted same-level SELL
prints reduce that queue. Unexplained depth decreases never create fills.
This is a queue model, not an assertion about actual exchange execution.
"""

import json
import re
from dataclasses import asdict, dataclass
from decimal import ROUND_FLOOR, Decimal

from polymaker.rdp_sm_ca import plain

D = Decimal
ZERO = D(0)
MODEL = "VISIBLE_QUEUE_TRADES_ONLY_V2"


@dataclass
class PaperOrder:
    id: str
    episode_id: str
    token_id: str
    outcome: str
    price: Decimal
    size: Decimal
    submitted: float
    arrival_due: float
    deadline: float
    rf_id: str
    ex_id: str
    state: str = "POSTING"
    filled: Decimal = ZERO
    queue_ahead: Decimal = ZERO
    arrived: float | None = None
    arrived_wall: float | None = None
    cancel_requested: float | None = None
    cancel_due: float | None = None
    closed: float | None = None
    close_reason: str | None = None
    uncertain: bool = False

    @property
    def remaining(self):
        return self.size-self.filled


class PaperBroker:
    def __init__(self, feed, cfg, *, cutoff_mono, emit, post_latency_ms=None, cancel_latency_ms=200):
        self.feed, self.cfg, self.emit = feed, cfg, emit
        self.cutoff_mono = cutoff_mono
        self.post_latency_ms = cfg.post_budget_floor_ms if post_latency_ms is None else post_latency_ms
        self.cancel_latency_ms = cancel_latency_ms
        if self.post_latency_ms < 0 or self.cancel_latency_ms < 0:
            raise ValueError("PAPER_LATENCY_INVALID")
        self.orders, self.signals, self.fills = [], [], []
        self.active = None
        self.spent = ZERO
        self.peak_committed = ZERO
        self.positions = {"YES": ZERO, "NO": ZERO}
        self.frame = None
        self.seen_trades = set()
        self.used_rf, self.used_ex = set(), set()
        self.error = None
        self.claim = None
        self.pending_release = None
        self.pending_release_frame = None
        self.record_depth = 0

    def record(self, event, **data):
        self.emit(plain({"event": event, "available_monotonic": self.feed.monotonic(),
                         "simulation": True, "model": MODEL, **data}))

    def committed(self):
        return self.spent+sum((o.remaining*o.price for o in self.orders
            if o is self.active or o.uncertain), ZERO)

    def post(self, marker):
        now = self.feed.monotonic()
        ep = marker["opportunity"]
        price, size = D(marker["price"]), D(marker["size"])
        reason = ("PAPER_ORDER_BUSY" if self.active else
                  "PAPER_EPISODE_ALREADY_SUBMITTED" if ep["rf_id"] in self.used_rf or ep["ex_id"] in self.used_ex else
                  "PAPER_SESSION_BUDGET" if self.committed()+price*size > 20 else None)
        self.signals.append({"episode_id": ep["episode_id"], "outcome": ep["outcome"],
                             "price": str(price), "size": str(size), "reason": reason})
        if reason:
            self.record("PAPER_POST_SKIPPED", reason=reason, episode_id=ep["episode_id"])
            return None
        if not 0 < price < 1 or size <= 0 or price*size > 5:
            raise ValueError("PAPER_ORDER_BUDGET_INVALID")
        signal = self.feed.m1.evaluate(self.feed.snapshot())
        if self.feed.m1.selected is None or self.feed.m1.selected.episode_id != ep["episode_id"]:
            self.signals[-1]["reason"] = "PAPER_EPISODE_CHANGED"
            return None
        claim = self.feed.m1.claim(signal)
        if claim is None:
            self.signals[-1]["reason"] = "PAPER_EPISODE_UNAVAILABLE"
            return None
        self.feed.m1.consume(claim)
        self.claim = claim
        order = PaperOrder(f"paper:{len(self.orders)+1}", ep["episode_id"], ep["token_id"],
            ep["outcome"], price, size, now, now+self.post_latency_ms/1000,
            min(ep["order_deadline"], self.cutoff_mono), ep["rf_id"], ep["ex_id"])
        self.active = order
        self.orders.append(order)
        self.used_rf.add(ep["rf_id"])
        self.used_ex.add(ep["ex_id"])
        self.peak_committed = max(self.peak_committed, self.committed())
        self.record("PAPER_POST_STARTED", order=asdict(order), committed=str(self.committed()))
        return order

    def cancel(self, reason, *, uncertain=False):
        o = self.active
        if o is None:
            return
        o.uncertain |= uncertain
        if o.cancel_requested is None:
            o.cancel_requested = self.feed.monotonic()
            o.close_reason = reason
            if o.arrived is not None:
                o.cancel_due = o.cancel_requested+self.cancel_latency_ms/1000
                o.state = "CANCELING"
            self.record("PAPER_CANCEL_STARTED", order_id=o.id, reason=reason, uncertain=o.uncertain)

    def _close(self, reason):
        o = self.active
        if o is None:
            return
        o.closed, o.close_reason = self.feed.monotonic(), reason
        o.state = "CLOSED"
        self.active = None
        self.pending_release = self.claim
        self.pending_release_frame = (self.frame or {}).get("frame") if self.record_depth else None
        self.claim = None
        self.record("PAPER_ORDER_CLOSED", order=asdict(o), committed=str(self.committed()))

    def _evidence_current(self, o):
        state = self.feed.m1.states.get(o.token_id)
        return (state and state["rf"] and state["ex"]
                and state["rf"]["id"] == o.rf_id and state["ex"]["id"] == o.ex_id
                and not self.feed.m1.failure)

    def advance(self):
        if self.pending_release is not None and (not self.record_depth
                or (self.frame or {}).get("frame") != self.pending_release_frame):
            claim, self.pending_release = self.pending_release, None
            self.feed.m1.release(claim)
        o, now = self.active, self.feed.monotonic()
        if o is None:
            return
        if now >= o.deadline or not self._evidence_current(o):
            self.cancel("PAPER_SIGNAL_ENDED")
        if o.state == "POSTING" and now >= o.arrival_due:
            book = self.feed.books[o.token_id]
            if not self.feed.m1.legal(book, now):
                o.uncertain = True
                self._close("PAPER_ARRIVAL_DATA_UNAVAILABLE")
                return
            # POST has already been sent in this model. Signal expiry in flight
            # requests a cancel; it cannot retroactively pretend POST was unsent.
            if o.price >= book.best_ask or o.price % book.tick:
                o.uncertain = False
                self._close("PAPER_POST_ONLY_REJECTED")
                return
            o.arrived, o.arrived_wall = now, self.feed.wall()
            o.queue_ahead = next((q for p, q in book.bids if p == o.price), ZERO)
            o.state = "LIVE"
            if o.cancel_requested is not None:
                o.state = "CANCELING"
                o.cancel_due = now+self.cancel_latency_ms/1000
            self.record("PAPER_ORDER_ARRIVED", order=asdict(o))
        if o.cancel_due is not None and now >= o.cancel_due:
            self._close(o.close_reason or "PAPER_CANCELED")

    def on_record(self, record):
        self.record_depth += 1
        try:
            self._on_record(record)
        finally:
            self.record_depth -= 1

    def _on_record(self, record):
        kind = record.get("event")
        if kind == "RAW_FRAME":
            self.frame = record
            self.advance()
            return
        if self.active is None:
            return
        token = record.get("token_id")
        if kind in {"GAP", "SIGNAL_INVALIDATED", "UNAVAILABLE"} and token in {None, self.active.token_id}:
            self.cancel("PAPER_DATA_UNAVAILABLE", uncertain=True)
        elif kind == "EVENT_IGNORED" and token == self.active.token_id and record.get("kind") == "last_trade_price":
            self.cancel("PAPER_EXCLUDED_TRADE", uncertain=True)
        elif kind == "EVENT_DECISION" and record.get("accepted") and record.get("kind") == "last_trade_price":
            if self.frame is None:
                return
            raw = self.frame["raw"]
            raw = json.loads(raw) if isinstance(raw, str) else raw
            raw = raw if isinstance(raw, list) else [raw]
            row = raw[record["row_index"]]
            self.trade(row["asset_id"], row["side"], row["price"], row["size"],
                       row.get("transaction_hash"), row["timestamp"], self.frame["received_monotonic"])
        elif kind == "COMPLEMENT_TRADE_VIEW" and record.get("accepted"):
            self.trade(token, record["side"], record["price"], record["size"],
                       record["transaction_hash"], record["source_timestamp"],
                       self.frame["received_monotonic"] if self.frame else self.feed.monotonic())

    def trade(self, token, side, price, size, tx, timestamp_ms, received):
        self.advance()
        o = self.active
        if o is None or o.arrived is None or side != "SELL" or token != o.token_id or D(price) != o.price:
            return
        if received < o.arrived or float(D(timestamp_ms)/1000) < o.arrived_wall:
            return  # A delayed pre-arrival fill cannot execute our future order.
        if not isinstance(tx, str) or not re.fullmatch(r"0x[0-9a-fA-F]{64}", tx):
            self.cancel("PAPER_TRADE_IDENTITY_UNAVAILABLE", uncertain=True)
            return
        identity = (tx.lower(), token, str(o.price))
        if identity in self.seen_trades:
            return
        self.seen_trades.add(identity)
        quantity = D(size)
        if not quantity.is_finite() or quantity <= 0:
            self.cancel("PAPER_TRADE_QUANTITY_UNAVAILABLE", uncertain=True)
            return
        ahead = min(o.queue_ahead, quantity)
        o.queue_ahead -= ahead
        fill = min(o.remaining, quantity-ahead).quantize(D(".000001"), rounding=ROUND_FLOOR)
        if fill <= 0:
            return
        o.filled += fill
        cost = fill*o.price
        self.spent += cost
        self.positions[o.outcome] += fill
        row = {"order_id": o.id, "episode_id": o.episode_id, "outcome": o.outcome,
               "size": str(fill), "price": str(o.price), "cost": str(cost),
               "transaction_hash": tx, "available_monotonic": self.feed.monotonic(),
               "during_cancel": o.state == "CANCELING"}
        self.fills.append(row)
        self.record("PAPER_FILL_ESTIMATED", **row)
        if o.remaining == 0:
            o.uncertain = False
            self._close("PAPER_FILLED")

    def finish(self):
        if self.active is not None:
            self.active.uncertain = True
            self._close("PAPER_STOP_BEFORE_CANCEL_ACK")
        self.advance()

    def summary(self):
        return plain({"model": MODEL, "execution_is_simulated": True,
            "post_latency_ms": self.post_latency_ms, "cancel_latency_ms": self.cancel_latency_ms,
            "queue_policy": "VISIBLE_AT_ARRIVAL_NO_CANCELLATION_CREDIT_UNIQUE_HASH_ONLY",
            "fee_usdc": "0", "rebates_usdc": "0", "network_costs_included": False,
            "spent_usdc": self.spent, "committed_usdc": self.committed(),
            "peak_committed_usdc": self.peak_committed,
            "positions": self.positions, "post_attempts": len(self.orders),
            "filled_orders": sum(o.filled > 0 for o in self.orders),
            "uncertain_orders": sum(o.uncertain for o in self.orders),
            "signals": self.signals, "orders": [asdict(o) for o in self.orders], "fills": self.fills})


def settle_paper(paper, resolution):
    if resolution is None:
        return {"status": "PENDING_OFFICIAL_RESOLUTION", "pnl_usdc": None}
    winner = resolution["winner"]
    positions = {k: D(v) for k, v in paper["positions"].items()}
    spent = D(paper["spent_usdc"])
    pairs = min(positions.values())
    payout = positions[winner]
    conditional = payout-spent
    lower = upper = conditional
    for o in paper["orders"]:
        if o["uncertain"]:
            remaining = D(o["size"])-D(o["filled"])
            effect = remaining*((D(1) if o["outcome"] == winner else ZERO)-D(o["price"]))
            lower += min(ZERO, effect)
            upper += max(ZERO, effect)
    return plain({"status": "RESOLVED_WITH_UNCERTAINTY" if paper["uncertain_orders"] else "RESOLVED",
        "resolution": resolution, "simulated_merge_usdc": pairs,
        "residual_payout_usdc": payout-pairs, "payout_usdc": payout,
        "spent_usdc": spent, "pnl_usdc": conditional if not paper["uncertain_orders"] else None,
        "pnl_if_no_unobserved_fills_usdc": conditional, "pnl_lower_usdc": lower, "pnl_upper_usdc": upper})

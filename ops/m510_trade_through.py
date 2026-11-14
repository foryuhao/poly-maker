"""Quantity-capped direct-SELL trade-through evidence, independent of L2 queues."""

import hashlib
import math
from collections import defaultdict
from pathlib import Path

from m511_sim import ResearchBroker

from polymaker.m1234_sim.broker import TERMINAL, dec
from polymaker.m1234_sim.events import digest

REVISION = "m510-direct-sell-minus-one-tick-quantity-capped-v1"
SCENARIO = "research/trade-through"
SCENARIO_SETTINGS = {"trade-through": {
    "post_ms": 100, "ack_ms": 200, "cancel_ms": 200, "ahead_cancel_fraction": "0",
}}


def definition():
    return {"version": REVISION, "scenario": SCENARIO,
            "implementation_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "evidence": "same-token direct SELL, price <= submitted price - frozen submitted tick",
            "window": "[model LIVE, frozen T240 deadline); late delivered pre-cutoff evidence allowed",
            "quantity": "min(remaining quantity, unused qualifying SELL quantity per independent profile)",
            "allocation": "price descending then model LIVE time then submission order",
            "price": "original submitted limit", "coverage": "unknown intervals flagged separately",
            "fees": "gross; zero unverified fees/rebates"}


class TradeThroughBroker(ResearchBroker):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.submitted_ticks = {}
        self.current_print_source = None

    def event(self, kind, **data):
        if kind == "MODELLED_FILL" and self.current_print_source is not None:
            data.update(source_mono=self.current_print_source, evidence_available_mono=self.now)
        super().event(kind, **data)

    def submit(self, intent):
        constraint = self.constraints.get(intent.token)
        if not constraint or not constraint.get("known"):
            raise ValueError("THROUGH_SUBMITTED_TICK_UNAVAILABLE")
        tick = dec(constraint["tick"])
        if not 0 < tick < 1 or dec(intent.price) % tick:
            raise ValueError("THROUGH_SUBMITTED_TICK_INVALID")
        order = super().submit(intent)
        self.submitted_ticks[order.id] = str(tick)
        self.event("THROUGH_SUBMISSION", order=order.id, tick=str(tick),
                   trigger_price=str(dec(intent.price)-tick), cutoff=intent.deadline,
                   rule=REVISION)
        return order

    def join_live_order(self, order, book, due):
        # Arrival precision/post-only/book checks are inherited; no queue model.
        self.event("THROUGH_LIVE", order=order.id,
                   tick=self.submitted_ticks[order.id], cutoff=order.intent.deadline)

    def apply_cancellations(self):
        # L2 depth reductions never provide trade-through evidence.
        return

    def trade(self, row, identity, *, source_mono=None):
        if row.get("derived") or row.get("side") != "SELL":
            return
        token, price, quantity = row["asset_id"], dec(row["price"]), dec(row["size"])
        if not 0 < price < 1 or quantity <= 0:
            raise ValueError("SIM_TRADE_INVALID")
        match = (row.get("match_id") or row.get("trade_id") or row.get("id")
                 or row.get("transaction_hash"))
        signature = digest(row)
        key = (token, str(match)) if match else ("anonymous", signature)
        if key in self.seen_trades:
            if self.seen_trades[key] == signature:
                self.event("DUPLICATE_TRADE", input=identity, match=match)
                if not match:
                    self.gap("ANONYMOUS_TRADE_REPETITION", token)
                return
            self.event("TRADE_IDENTITY_AMBIGUOUS", input=identity, raw=row)
            self.gap("TRADE_IDENTITY_AMBIGUOUS", token)
            return
        if len(self.seen_trades) >= 50000:
            raise RuntimeError("SIM_TRADE_IDENTITY_CAPACITY")
        self.seen_trades[key] = signature
        source = self.now if source_mono is None else source_mono
        if not math.isfinite(source):
            self.gap("DIRECT_TRADE_SOURCE_TIME_UNCERTAIN", token)
            return
        if source > self.now + 0.002:
            self.gap("DIRECT_TRADE_SOURCE_TIME_UNCERTAIN", token, source_mono=source)
            return
        by_profile = defaultdict(list)
        for serial, order in enumerate(self.orders.values()):
            cutoff = min(order.intent.deadline, order.cancel_requested
                         if order.cancel_requested is not None else order.intent.deadline)
            if (order.intent.token != token or order.live_at is None or order.remaining <= 0
                    or order.state in {"SUBMITTING", "REJECTED"}
                    or not order.live_at <= source < cutoff
                    or price > dec(order.intent.price)-dec(self.submitted_ticks[order.id])):
                continue
            by_profile[order.intent.profile].append((serial, order))
        for profile, candidates in by_profile.items():
            available = quantity
            for _, order in sorted(candidates, key=lambda x: (
                -dec(x[1].intent.price), x[1].live_at, x[0]
            )):
                used = min(available, order.remaining)
                if not used:
                    break
                # Positive print evidence survives unrelated missing intervals.
                # Reopen released funds for delayed, pre-cancel evidence only.
                previous, uncertain = order.state, order.uncertain
                cost = used * dec(order.intent.price)
                if cost > order.reserved:
                    account = self.account(profile)
                    reserve = cost-order.reserved
                    if reserve > account.cash-account.reserved:
                        self.uncertain(order, "LATE_EVIDENCE_RESERVE_UNAVAILABLE")
                        continue
                    order.reserved += reserve
                    account.reserved += reserve
                if previous in TERMINAL:
                    order.state = "LIVE"
                    self.event("THROUGH_LATE_EVIDENCE", order=order.id, input=identity,
                               source_mono=source, previous_state=previous)
                order.uncertain = False
                self.event("THROUGH_EVIDENCE", order=order.id, input=identity,
                           source_mono=source, print_price=str(price), print_quantity=str(quantity),
                           allocated_quantity=str(used), tick=self.submitted_ticks[order.id])
                self.current_print_source = source
                try:
                    self.fill(order, used, identity)
                finally:
                    self.current_print_source = None
                order.uncertain = uncertain and order.remaining > 0
                if previous == "CANCELLED" and order.remaining:
                    order.state = "CANCELLED"
                available -= used
            self.event("THROUGH_ALLOCATION", input=identity, profile=profile,
                       available=str(quantity), consumed=str(quantity-available))
        self.check()

    def gap(self, reason, token=None, *, source_mono=None, missing_since=None):
        # Missing evidence is not proof of no fill. Preserve known partial fills.
        start = source_mono if source_mono is not None else (
            self.now if missing_since is None else missing_since)-0.502
        for order in self.orders.values():
            if token is not None and order.intent.token != token:
                continue
            cutoff = min(order.intent.deadline, order.cancel_requested
                         if order.cancel_requested is not None else order.intent.deadline)
            if order.live_at is not None and order.remaining and order.live_at <= self.now and start < cutoff:
                self.uncertain(order, reason)
        self.event("DATA_GAP", reason=reason, token=token, source_mono=source_mono,
                   missing_since_received_mono=missing_since)
        self.check()

    def snapshot(self):
        return {**super().snapshot(), "fill_model": definition(),
                "submitted_ticks": dict(self.submitted_ticks)}

"""Fixed-anchor lifetime policy and quantity-capped evidence with cancel races."""

import math
import re
from collections import defaultdict
from dataclasses import asdict
from decimal import ROUND_FLOOR

from polymaker.m1234_sim.broker import TERMINAL, OrderIntent, SimBroker, dec
from polymaker.m1234_sim.events import digest
from polymaker.rdp_sm_m1_policy import budget_quantity

ANCHOR_FILL_MODEL = "ASSUMED_FULL_AT_FIXED_ANCHOR_ON_ADMISSION"


def sop_quantity(price, minimum, execution):
    """An explicit baseline step does not change historical SOP/live sizing."""
    step = execution.get("quantity_step")
    if step is None:
        return budget_quantity(price, minimum, dec(5))
    if step != "0.1" or execution.get("session_budget_usdc") != "10":
        raise ValueError("SOP_BASELINE_SIZING_CONTRACT_CHANGED")
    price, minimum = dec(price), dec(minimum)
    if not 0 < price < 1 or minimum <= 0:
        raise ValueError("SOP_BASELINE_INVALID_QUOTE")
    quantity = (dec(5) / price / dec(step)).to_integral_value(rounding=ROUND_FLOOR) * dec(step)
    if quantity < max(dec(5), minimum) or price * quantity > 5:
        raise ValueError("SOP_BASELINE_MINIMUM_EXCEEDS_BUDGET")
    return quantity


def fill_attribution(fact, source, execution_cutoff, input_seq):
    """Separate signal validity from the explicitly frozen execution window."""
    classification = "ORDERING_UNKNOWN"
    signal_end = min(fact["deadline"], fact.get("ended", float("inf"))) if fact else None
    if fact and source is not None:
        ambiguous = (
            fact.get("end_input_seq") == input_seq
            or abs(source - signal_end) <= 0.001
            or abs(source - execution_cutoff) <= 0.001
        )
        if not ambiguous:
            if source >= signal_end:
                classification = "AFTER_SIGNAL_END"
            elif source >= execution_cutoff:
                classification = "AFTER_EXECUTION_WINDOW"
            elif source >= fact["confirmed"]:
                classification = "WITHIN_SIGNAL_VALIDITY"
    return {
        "classification": classification,
        "signal_end": signal_end,
        "execution_cutoff": execution_cutoff,
        "after_signal_end": source >= signal_end
        if source is not None and signal_end is not None
        else None,
        "after_execution_cutoff": source >= execution_cutoff if source is not None else None,
    }


class Broker(SimBroker):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.submitted_ticks = {}
        self.fill_source = None

    def event(self, kind, **payload):
        if kind == "MODELLED_FILL":
            payload.update(source_mono=self.fill_source, evidence_available_mono=self.now)
        super().event(kind, **payload)

    def submit(self, intent):
        constraint = self.constraints.get(intent.token, {})
        if not constraint.get("known"):
            raise ValueError("SOP_SUBMITTED_TICK_UNKNOWN")
        tick = dec(constraint["tick"])
        if not 0 < tick < 1 or dec(intent.price) % tick:
            raise ValueError("SOP_SUBMITTED_TICK_INVALID")
        order = super().submit(intent)
        self.submitted_ticks[order.id] = str(tick)
        self.event(
            "THROUGH_SUBMISSION",
            order=order.id,
            tick=str(tick),
            trigger_price=str(dec(intent.price) - tick),
            signal_deadline=intent.deadline,
        )
        return order

    def join_live_order(self, order, book, due):
        self.event("THROUGH_LIVE", order=order.id, tick=self.submitted_ticks[order.id])

    def apply_cancellations(self):
        pass  # Depth loss is never fill evidence.

    def advance(self, now, **kwargs):
        # Original deadlines request cancellation even between detector polls.
        due = sorted(
            {
                o.intent.deadline
                for o in self.orders.values()
                if o.cancel_requested is None
                and o.state not in TERMINAL
                and o.intent.deadline <= now
            }
        )
        for deadline in due:
            super().advance(max(self.now, deadline), delivery=False)
            for o in self.orders.values():
                if o.intent.deadline <= self.now and o.cancel_requested is None:
                    self.cancel(o.id)
        return super().advance(now, **kwargs)

    def trade(self, row, identity, *, source_mono=None):
        if row.get("derived") or row.get("side") == "BUY":
            return
        if row.get("side") != "SELL":
            self.gap("DIRECT_TRADE_DIRECTION_UNKNOWN", row.get("asset_id"))
            return
        token = row.get("asset_id")
        try:
            price, quantity = dec(row["price"]), dec(row["size"])
            if not 0 < price < 1 or quantity <= 0:
                raise ValueError("SIM_TRADE_INVALID")
        except (KeyError, TypeError, ArithmeticError, ValueError):
            self.gap("DIRECT_TRADE_PRICE_OR_QUANTITY_UNKNOWN", token)
            return
        match = row.get("match_id") or row.get("trade_id") or row.get("id")
        tx = row.get("transaction_hash")
        if not match and (
            not isinstance(tx, str) or re.fullmatch(r"0x[0-9a-fA-F]{64}", tx) is None
        ):
            self.gap("DIRECT_TRADE_IDENTITY_UNKNOWN", token)
            return
        signature = digest(
            {
                k: row.get(k)
                for k in ("asset_id", "side", "price", "size", "timestamp", "transaction_hash")
            }
        )
        key = (token, str(match)) if match else (token, str(tx), signature)
        if key in self.seen_trades:
            if self.seen_trades[key] != signature or not match:
                self.gap("DIRECT_TRADE_IDENTITY_AMBIGUOUS", token)
            else:
                self.event("DUPLICATE_TRADE", input=identity)
            return
        if len(self.seen_trades) >= 50000:
            raise RuntimeError("SIM_TRADE_IDENTITY_CAPACITY")
        self.seen_trades[key] = signature
        source = self.now if source_mono is None else source_mono
        if not math.isfinite(source) or source > self.now + 0.002:
            self.gap("DIRECT_TRADE_SOURCE_TIME_UNCERTAIN", token)
            return
        profiles = defaultdict(list)
        for serial, o in enumerate(self.orders.values()):
            # Actual cancel arrival, not request or signal-end, ends exposure.
            stop = (
                o.cancel_requested + self.cancel_delay
                if o.cancel_requested is not None
                else float("inf")
            )
            if (
                o.intent.token == token
                and o.live_at is not None
                and o.remaining > 0
                and o.state not in {"SUBMITTING", "REJECTED"}
                and o.live_at <= source < stop
                and price <= dec(o.intent.price) - dec(self.submitted_ticks[o.id])
            ):
                profiles[o.intent.profile].append((serial, o))
        for profile, candidates in profiles.items():
            unused = quantity
            for _, o in sorted(
                candidates, key=lambda p: (-dec(p[1].intent.price), p[1].live_at, p[0])
            ):
                used = min(unused, o.remaining)
                if used <= 0:
                    break
                old_state, old_uncertain = o.state, o.uncertain
                reserve = max(dec(0), used * dec(o.intent.price) - o.reserved)
                account = self.account(profile)
                if reserve > account.cash - account.reserved:
                    self.uncertain(o, "LATE_EVIDENCE_RESERVE_UNAVAILABLE")
                    continue
                o.reserved += reserve
                account.reserved += reserve
                if old_state in TERMINAL:
                    o.state = "LIVE"
                o.uncertain = False
                self.event(
                    "THROUGH_EVIDENCE",
                    order=o.id,
                    input=identity,
                    source_mono=source,
                    print_price=str(price),
                    print_quantity=str(quantity),
                    allocated_quantity=str(used),
                    tick=self.submitted_ticks[o.id],
                )
                self.fill_source = source
                try:
                    self.fill(o, used, identity)
                finally:
                    self.fill_source = None
                o.uncertain = old_uncertain and o.remaining > 0
                if old_state == "CANCELLED" and o.remaining:
                    o.state = "CANCELLED"
                unused -= used
            self.event(
                "THROUGH_ALLOCATION",
                input=identity,
                profile=profile,
                available=str(quantity),
                consumed=str(quantity - unused),
            )
        self.check()

    def gap(self, reason, token=None, *, source_mono=None, missing_since=None):
        start = (
            source_mono
            if source_mono is not None
            else (self.now if missing_since is None else missing_since) - 0.502
        )
        for o in self.orders.values():
            stop = (
                o.cancel_requested + self.cancel_delay
                if o.cancel_requested is not None
                else float("inf")
            )
            if (
                (token is None or o.intent.token == token)
                and o.live_at is not None
                and o.remaining
                and o.live_at <= self.now
                and start < stop
            ):
                self.uncertain(o, reason)
        self.event("DATA_GAP", reason=reason, token=token, source_mono=source_mono)

    def snapshot(self):
        return {
            **super().snapshot(),
            "submitted_ticks": dict(self.submitted_ticks),
            "fill_model": "sop-direct-sell-through-with-cancel-races-r1",
        }


class Policy:
    def __init__(self, engine, broker=None):
        self.engine, self.broker = engine, broker
        self.used, self.seen_skips = set(), set()
        self.anchor_spent = dec(0)

    def poll(self, elapsed, uncertainty=0.0):
        e, b = self.engine, self.broker
        strategy = getattr(e, "strategy", "M12")
        feed = e.signals.sop
        if b is not None:
            for o in b.orders.values():
                signal = feed.core.signals.get(o.intent.opportunity)
                if (signal is None or elapsed + uncertainty >= 240
                        or strategy == "M24" and not signal["can_reference_for_new_action"]):
                    b.cancel(o.id)
        if elapsed < 15 or elapsed + uncertainty >= 240:
            return
        anchor_only = b is None
        now = e.clock.mono if anchor_only else b.now
        scenario = "sop/anchor" if anchor_only else b.scenario
        constraints = e.tick_verification.constraints if anchor_only else b.constraints
        post_delay = 0 if anchor_only else b.post_delay
        for candidate in feed.core.signals.values():
            token = candidate["token"]
            if token in self.used:
                continue
            p, book = dec(candidate["anchor_price"]), feed.books[token]
            deadline = min(candidate["deadline"], now + 240 - elapsed - uncertainty)
            constraint = constraints.get(token, {})
            reason = None
            if not feed.good[token] or not feed.legal(token):
                reason = "BOOK_UNAVAILABLE"
            elif strategy in {"M17", "M24"} and candidate.get("can_reference_for_new_action") is not True:
                reason = "SOP_PENDING_DEPTH_RECHECK"
            elif (
                e.signals.m1.m1_pending_received
                and min(e.signals.m1.m1_pending_received) < now - 1e-9
            ):
                reason = "PENDING_INPUTS"
            elif deadline - now <= post_delay + 1e-9:
                reason = "SIGNAL_EXPIRED" if anchor_only else "INSUFFICIENT_MODEL_POST_LIFE"
            elif not constraint.get("known") or dec(constraint["tick"]) != book.tick:
                reason = "OFFICIAL_TICK_UNKNOWN_OR_CONFLICT"
            elif not book.quantity("BID", p) or p > book.bid or p >= book.ask or p % book.tick:
                reason = "FIXED_ANCHOR_NOT_POST_ONLY_LEGAL"
            quantity = dec(0)
            if reason is None:
                try:
                    quantity = sop_quantity(p, e.meta.min_order_size,
                                            e.definition["settings"]["sop_execution"])
                except ValueError:
                    reason = "VENUE_SIZE_OR_PRECISION"
            if reason is None:
                cash = (dec(e.definition["settings"]["sop_execution"]["session_budget_usdc"]) - self.anchor_spent
                        if anchor_only else b.account(strategy).cash - b.account(strategy).reserved)
                if p * quantity > cash:
                    reason = "ECONOMIC_BUDGET_UNAVAILABLE"
            if reason:
                key = candidate["id"], reason
                if key not in self.seen_skips:
                    self.seen_skips.add(key)
                    e.emit(
                        "SKIP",
                        {
                            "scenario": scenario,
                            "profile": strategy,
                            "candidate_id": candidate["id"],
                            "reason": reason,
                        },
                    )
                continue
            intent = OrderIntent(
                strategy,
                strategy,
                candidate["version"],
                candidate["id"],
                e.meta.slug,
                token,
                "YES" if token == e.meta.yes.token_id else "NO",
                str(p),
                str(quantity),
                now,
                deadline,
                "IMMEDIATE_FIXED_ANCHOR" if anchor_only else "SOP_VALID_LIFETIME",
                order_type="ASSUMED_FIXED_ANCHOR" if anchor_only else "POST_ONLY_GTC",
            )
            intent_record = asdict(intent)
            e.emit(
                "INTENT",
                {
                    "scenario": scenario,
                    "intent": intent_record,
                    "signal_deadline": candidate["deadline"],
                    "budget_usdc": "5",
                },
            )
            if anchor_only:
                order_id = digest({"scenario": scenario, "intent": intent_record})
                e.emit("ANCHOR_ENTRY", {"scenario": scenario, "order": order_id, "intent": intent_record})
                self.anchor_spent += p * quantity
            else:
                order_id = b.submit(intent).id
            self.used.add(token)
            e.emit(
                "ASSUMED_FILL",
                {
                    "scenario": scenario if anchor_only else "sop/assumed",
                    "source_scenario": scenario,
                    "order": order_id,
                    "intent": intent_record,
                    "price": intent.price,
                    "quantity": intent.quantity,
                    "scope": ANCHOR_FILL_MODEL if anchor_only else "SAME_SUBMIT_FULL_QUANTITY",
                },
            )

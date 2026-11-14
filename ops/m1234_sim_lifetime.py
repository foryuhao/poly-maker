"""Offline M234 order-lifetime experiment using the production signal and broker code.

Kept outside src so installing an offline experiment cannot invalidate a running
capture's frozen implementation identity. No network or live order transport.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from copy import deepcopy
from dataclasses import asdict, replace
from pathlib import Path

from polymaker.m2_config import M2Config
from polymaker.m1234_sim.broker import TERMINAL, SimBroker, dec
from polymaker.m1234_sim.engine import Engine
from polymaker.m1234_sim.events import digest
from polymaker.m1234_sim.policy import M234Policy
from polymaker.m1234_sim.store import Store

REVISION = "m234-independent-lifetime-v1"
LIFETIMES = (3, 10, 30)


def experiment_definition():
    return {
        "revision": REVISION,
        "implementation_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "seconds": list(LIFETIMES),
        "clock": "original submission availability; cancel at first recorded policy poll >= deadline",
        "baseline": "unchanged M234Policy; original candidate-bound cancellation",
        "admission": "unchanged M234Policy per independent account; at most one POST per direction",
        "price_and_quantity": "original limit and original budget_quantity; no chasing or resubmission",
        "refresh": "same direction evidence does not replace order or extend absolute deadline",
        "early_cancel": [
            "ORDER_UNCERTAIN",
            "GENERATION_CHANGED",
            "EXECUTION_BOOK_UNAVAILABLE",
            "BID_BELOW_ENTRY_LIMIT",
            "NEW_OPPOSITE_SIGNAL",
            "M4_ASK_RECOVERED",
            "NEW_ORDER_WINDOW_CLOSED",
        ],
        "partial_fill": "retain filled inventory through official settlement; cancel remainder only",
        "fees": "gross; original broker fee/rebate assumptions unchanged",
        "audit": "economic/order events and candidate events retained; noisy observer diagnostics counted",
    }


class EvidenceStore(Store):
    """Retain the full economic trail without duplicating raw observer diagnostics."""

    def __init__(self, path):
        super().__init__(path)
        self.observed_counts = Counter()
        self.omitted_counts = Counter()

    def emit(self, kind, payload):
        self.observed_counts[kind] += 1
        keep = kind not in {"M1_POLICY", "DATA_GAP"}
        if kind == "SIGNAL":
            keep = payload.get("event") in {
                "OPPORTUNITY_IDENTIFIED",
                "M2_CANDIDATE",
                "CANDIDATE",
                "M2_CANDIDATE_ENDED",
                "CANDIDATE_ENDED",
            }
        if keep:
            super().emit(kind, payload)
        else:
            self.omitted_counts[kind] += 1


class AdmissionBroker:
    """Reuse original admission checks while giving order maintenance a new owner."""

    def __init__(self, owner):
        self.owner = owner

    def __getattr__(self, key):
        return getattr(self.owner.execution, key)

    def submit(self, intent, *args, **kwargs):
        return self.owner.execution.submit(self.owner.convert(intent), *args, **kwargs)

    def cancel(self, oid):
        # This call comes ONLY from M234Policy's candidate-presence check.
        # Explicit execution cancellations always use owner.execution directly.
        owner = self.owner
        order = owner.execution.orders[oid]
        if oid in owner.detached or order.state in TERMINAL:
            return
        owner.detached.add(oid)
        source = owner.admission.order_candidates[oid]
        owner.emit(
            "SIGNAL_LIFETIME_DETACHED",
            {
                "scenario": owner.execution.scenario,
                "order": oid,
                "at": owner.execution.now,
                "strategy": order.intent.strategy,
                "source_candidate": source["id"],
                "source_deadline": source["deadline"],
                "order_deadline": order.intent.deadline,
                "classification": "TRIGGER_EXPIRED"
                if owner.execution.now >= source["deadline"]
                else "TRIGGER_CHANGED_OR_COMPONENT_ENDED",
            },
        )


class LifetimePolicy:
    def __init__(self, signals, broker, emit, settings, seconds):
        if seconds not in LIFETIMES:
            raise ValueError("UNSUPPORTED_ORDER_LIFETIME")
        self.signals, self.execution, self.emit = signals, broker, emit
        self.settings, self.seconds = settings, seconds
        self.detached, self.known_orders = set(), set()
        self.admission = M234Policy(signals, AdmissionBroker(self), self.admission_emit, settings)
        self.m4_recoveries = {}

    def convert(self, intent):
        return replace(
            intent,
            deadline=intent.available + self.seconds,
            lifetime=f"INDEPENDENT_{self.seconds}S",
        )

    def admission_emit(self, kind, payload):
        if kind == "INTENT":
            # Keep INTENT and SUBMIT consistent; preserve the signal's own deadline separately.
            from polymaker.m1234_sim.broker import OrderIntent

            intent = OrderIntent(**payload["intent"])
            payload = {
                **payload,
                "signal_deadline": intent.deadline,
                "intent": asdict(self.convert(intent)),
            }
        self.emit(kind, payload)

    def observe_signal(self, row, at):
        if row.get("strategy") == "M4" and row.get("event") == "ASK_REFERENCE_CONFIRMED":
            # Existing M4 detector emits this only for stable ASK recovery >= 0.50.
            fact = row["payload"]
            token = fact.get("round", {}).get("token")
            if token is not None:
                self.m4_recoveries[token] = at

    def reason(self, order, source, elapsed, uncertainty, candidates):
        feed, now = self.signals.m234, self.execution.now
        if order.uncertain:
            return "ORDER_UNCERTAIN"
        if source["generation"] != feed.generation:
            return "GENERATION_CHANGED"
        if elapsed + uncertainty >= self.settings.cutoff_sec:
            return "NEW_ORDER_WINDOW_CLOSED"
        if now >= order.intent.deadline:
            return "ORDER_LIFETIME_EXPIRED"
        book = feed.books[order.intent.token]
        if not feed.m1.legal(book, now):
            return "EXECUTION_BOOK_UNAVAILABLE"
        # Applied after raw book/trade matching, never before consuming the print
        # that removes the support. A higher best bid is not a reason to cancel.
        if book.best_bid < dec(order.intent.price):
            return "BID_BELOW_ENTRY_LIMIT"
        if any(
            c["strategy"] == order.intent.strategy
            and c["outcome"] != order.intent.outcome
            and c["confirmed"] > source["confirmed"]
            for c in candidates
        ):
            return "NEW_OPPOSITE_SIGNAL"
        if (
            order.intent.strategy == "M4"
            and self.m4_recoveries.get(order.intent.token, -1) > order.intent.available
        ):
            return "M4_ASK_RECOVERED"
        return None

    def poll(self, elapsed, uncertainty=0.0):
        # Original observers already ticked in Engine; candidate refresh is causal.
        candidates = self.signals.m234.hub.candidates()
        for oid, source in self.admission.order_candidates.items():
            order = self.execution.orders[oid]
            if order.state in TERMINAL or order.cancel_requested is not None:
                continue
            reason = self.reason(order, source, elapsed, uncertainty, candidates)
            if reason:
                self.emit(
                    "LIFETIME_CANCEL_DECISION",
                    {
                        "scenario": self.execution.scenario,
                        "order": oid,
                        "strategy": order.intent.strategy,
                        "at": self.execution.now,
                        "reason": reason,
                        "filled": str(order.filled),
                        "deadline": order.intent.deadline,
                    },
                )
                self.execution.cancel(oid)
        self.admission.poll(elapsed, uncertainty)
        for oid, source in self.admission.order_candidates.items():
            if oid in self.known_orders:
                continue
            self.known_orders.add(oid)
            order = self.execution.orders[oid]
            stable = {
                "market": order.intent.market,
                "strategy": order.intent.strategy,
                "outcome": order.intent.outcome,
                "first_candidate": source["id"],
            }
            self.emit(
                "TRADE_INTENT_CREATED",
                {
                    "scenario": self.execution.scenario,
                    "order": oid,
                    "intent_id": digest(stable),
                    **stable,
                    "signal_deadline": source["deadline"],
                    "order_deadline": order.intent.deadline,
                    "budget_usdc": str(self.settings.risk_usdc),
                },
            )


class LifetimeEngine(Engine):
    """One signal stream, unchanged baseline and six independent experiment brokers."""

    def __init__(self, directory, *, compact=True):
        super().__init__(directory)
        if compact:
            self.store.close(commit=False)
            self.store.path.unlink()  # Only the empty, newly created experiment DB.
            self.store = EvidenceStore(self.directory / "simulation.sqlite3")
        self.lifetime_policies = []

    def emit(self, kind, payload):
        if kind == "SIGNAL":
            for policy in self.lifetime_policies:
                policy.observe_signal(payload, self.clock.mono)
        super().emit(kind, payload)

    async def _ingest(self, event):
        if event["kind"] == "INIT":
            event = deepcopy(event)
            event["definition"]["settings"]["order_lifetime_experiment"] = experiment_definition()
        await super()._ingest(event)
        if event["kind"] == "INIT":
            cfg = self.definition["settings"]
            settings = M2Config.model_validate(cfg["m234"])
            for seconds in LIFETIMES:
                for base_name, params in cfg["scenarios"].items():
                    name = f"hold-{seconds}s/{base_name}"
                    broker = SimBroker(name, self.emit, seed=cfg["seed"], **params)
                    broker.now = self.clock.mono
                    self.brokers[name] = broker
                    policy = LifetimePolicy(self.signals, broker, self.emit, settings, seconds)
                    self.extra_policies.append(policy)
                    self.lifetime_policies.append(policy)
                    for strategy in ("M2", "M3", "M4"):
                        broker.account(strategy)
                    broker.update_constraints(self.tick_verification.constraints)

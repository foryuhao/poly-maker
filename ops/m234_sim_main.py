"""M234 primary execution policy: fixed maker limit, keep remainder until T240.

The source signal stream and original brokers remain the baseline. This module
adds two independent accounts on the same events; it has no live order gateway.
"""

import hashlib
from copy import deepcopy
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path

from m1234_sim_lifetime import EvidenceStore

from polymaker.m2_config import M2Config
from polymaker.m1234_sim.broker import TERMINAL, OrderIntent, SimBroker, dec
from polymaker.m1234_sim.engine import Engine
from polymaker.rdp_sm_m1_policy import budget_quantity

REVISION = "m234-min-anchor-bid-t240-v1"


def experiment_definition():
    return {
        "revision": REVISION,
        "implementation_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "audit_helper_sha256": hashlib.sha256(
            Path(__file__).with_name("m1234_sim_lifetime.py").read_bytes()
        ).hexdigest(),
        "baseline": "unchanged original M234Policy and both M1 profiles",
        "price": "min(signal anchor, current best bid) at submission; no repricing",
        "budget_usdc": "5",
        "frequency": "one submitted order per market/strategy/direction, including rejected posts",
        "admission": "active original signal, valid observation, legal fresh book, venue precision and funds",
        "cancel": "only T240 (conservative official-clock upper bound); original cancel latency",
        "uncertainty": "retain unknown fills/risk; no fabricated fills or extra policy cancellation",
        "settlement": "hold confirmed inventory through official resolution",
        "fees": "gross PnL; original broker fee/rebate assumptions unchanged",
    }


class MainPolicy:
    def __init__(self, signals, broker, emit, settings):
        if settings.risk_usdc != Decimal(5) or settings.cutoff_sec != 240:
            raise ValueError("MAIN_REQUIRES_5_USDC_AND_T240")
        self.signals, self.broker, self.emit, self.settings = signals, broker, emit, settings
        self.used, self.seen_skips, self.order_candidates = set(), set(), {}

    def poll(self, elapsed, uncertainty=0.0):
        feed, broker = self.signals.m234, self.broker
        if elapsed + uncertainty >= 240:
            for oid in self.order_candidates:
                order = broker.orders[oid]
                if order.state not in TERMINAL and order.cancel_requested is None:
                    self.emit("MAIN_CANCEL_DECISION", {
                        "scenario": broker.scenario, "order": oid,
                        "strategy": order.intent.strategy, "at": broker.now,
                        "elapsed": elapsed, "clock_uncertainty": uncertainty,
                        "reason": "T240", "filled": str(order.filled),
                    })
                    broker.cancel(oid)
            return
        for candidate in feed.hub.candidates():
            strategy, token = candidate["strategy"], candidate["token"]
            book, reason = feed.books[token], None
            anchor = dec(candidate["price"])
            price, quantity = anchor, Decimal(0)
            if elapsed < self.settings.start_sec:
                reason = "NEW_ORDER_WINDOW_CLOSED"
            elif (strategy, candidate["outcome"]) in self.used:
                reason = "DIRECTION_ATTEMPT_CONSUMED"
            elif not feed.m1.legal(book, feed.monotonic()):
                reason = "BOOK_UNAVAILABLE"
            elif (not candidate.get("observation_valid", True)
                  or feed.monotonic() - feed.m1_watermark() > 0.020 + 1e-9):
                reason = "OBSERVATION_UNAVAILABLE"
            if not reason:
                price = min(anchor, book.best_bid)
                if price <= 0 or price >= book.best_ask or price % book.tick:
                    reason = "VENUE_PRICE_INVALID"
            if not reason:
                try:
                    quantity = budget_quantity(price, feed.meta.min_order_size, Decimal(5))
                except ValueError:
                    reason = "VENUE_MINIMUM_OR_PRECISION"
            if (not reason and price * quantity
                    > broker.account(strategy).cash - broker.account(strategy).reserved):
                reason = "ECONOMIC_BUDGET_UNAVAILABLE"
            if reason:
                key = (strategy, candidate["id"], reason)
                if key not in self.seen_skips:
                    self.seen_skips.add(key)
                    self.emit("SKIP", {"scenario": broker.scenario, "profile": strategy,
                                       "candidate": candidate, "reason": reason})
                continue
            intent = OrderIntent(
                strategy, strategy, REVISION, candidate["id"], feed.meta.slug, token,
                candidate["outcome"], str(price), str(quantity), feed.monotonic(),
                feed.monotonic() + max(0, 240 - elapsed - uncertainty), "GTC_UNTIL_T240",
            )
            self.emit("INTENT", {
                "scenario": broker.scenario, "intent": asdict(intent),
                "signal_anchor": str(anchor), "submission_best_bid": str(book.best_bid),
                "signal_deadline": candidate["deadline"], "budget_usdc": "5",
                "elapsed": elapsed, "clock_uncertainty": uncertainty,
            })
            order = broker.submit(intent)
            self.used.add((strategy, candidate["outcome"]))
            self.order_candidates[order.id] = deepcopy(candidate)


class MainEngine(Engine):
    def __init__(self, directory, *, compact=False, derive=False):
        super().__init__(directory)
        self.derive = derive
        if compact:
            self.store.close(commit=False)
            self.store.path.unlink()  # Newly created empty replay database only.
            self.store = EvidenceStore(self.directory / "simulation.sqlite3")

    async def _ingest(self, event):
        if event["kind"] == "INIT":
            current = event["definition"]["settings"].get("m234_execution_main")
            expected = experiment_definition()
            if current != expected:
                if not self.derive or current is not None:
                    raise ValueError("MAIN_DEFINITION_MISSING_OR_CHANGED")
                # Derived replay only. LiveRun records this setting before INIT.
                event = deepcopy(event)
                event["definition"]["settings"]["m234_execution_main"] = expected
        await super()._ingest(event)
        if event["kind"] == "INIT":
            cfg = self.definition["settings"]
            settings = M2Config.model_validate(cfg["m234"])
            for base_name, params in cfg["scenarios"].items():
                name = f"main/{base_name}"
                broker = SimBroker(name, self.emit, seed=cfg["seed"], **params)
                broker.now = self.clock.mono
                self.brokers[name] = broker
                self.extra_policies.append(MainPolicy(self.signals, broker, self.emit, settings))
                for strategy in ("M2", "M3", "M4"):
                    broker.account(strategy)
                broker.update_constraints(self.tick_verification.constraints)

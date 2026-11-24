"""Reuse M1Market admission; replace its physical submission port with SimBroker."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from decimal import Decimal
from types import SimpleNamespace

from polymaker.m1_live import M1Market
from polymaker.m1234_sim.broker import OrderIntent, dec
from polymaker.m1234_sim.events import digest
from polymaker.m1234_sim.m1_ledger import M1SimulationLedger
from polymaker.m1234_sim.signals import FeedView
from polymaker.rdp_sm_ca import plain
from polymaker.rdp_sm_m1_policy import budget_quantity


class Journal:
    ready = True

    async def flush(self):
        return None


class SimGateway:
    """No parent gateway, HTTP client, signer, wallet or live fallback exists."""

    def __init__(self, executor, broker, profile, emit):
        self.e, self.broker, self.profile, self.emit = executor, broker, profile, emit
        self._client = SimpleNamespace(known_ids=set())
        self.counter, self.waiting, self.faults = 0, {}, []

    async def prepare(self, quotes, meta, ticks):
        self.counter += 1
        oid = "0x" + digest(
            {
                "profile": self.profile,
                "scenario": self.broker.scenario,
                "market": meta.slug,
                "physical": self.counter,
                "quotes": [asdict(q) for q in quotes],
            }
        )
        return SimpleNamespace(
            order_ids=[oid], quotes=quotes, body_hash=digest([asdict(q) for q in quotes])
        )

    async def send_prepared(self, prepared, *, submit_guard, operation_context):
        attempt = operation_context["attempt"]
        if not submit_guard():
            return []
        operation_context["on_post_start"]()
        attempt.post_started = True
        quote = attempt.quote
        row = self.e.ledger.row(quote.window_id)
        evidence = json.loads(row["evidence"])
        previous = self.e.ledger.uncertain_posts()
        retry_of = next(
            (x["order_id"] for x in reversed(previous) if x["opportunity_id"] == row["id"]), None
        )
        intent = OrderIntent(
            self.profile,
            "M1",
            self.e.settings.signal.profile,
            row["id"],
            self.e.slug,
            row["token"],
            row["outcome"],
            str(quote.price),
            str(quote.size),
            self.e.monotonic(),
            evidence["order_deadline"],
            "GTC",
            retry_of=retry_of,
        )
        self.emit("INTENT", {"scenario": self.broker.scenario, "intent": asdict(intent)})
        self.broker.submit(
            intent, prepared.order_ids[0], fault=self.faults.pop(0) if self.faults else None
        )
        future = asyncio.get_running_loop().create_future()
        self.waiting[prepared.order_ids[0]] = future
        return await future

    def deliver(self, oid, client):
        future = self.waiting.pop(oid, None)
        if future is None:
            return
        raw = (
            {"success": True, "orderID": oid}
            if client == "ACK"
            else {"success": False, "errorMsg": "post only order crosses book"}
            if client == "CROSS_REJECT"
            else {"success": False, "errorMsg": "invalid order: price is not a multiple of tick size"}
            if client == "PRECISION_REJECT"
            else {"success": False, "errorMsg": "invalid order"}
            if client == "REJECTED"
            else {"status": "UNKNOWN"}
        )
        future.set_result(raw)
        self.emit(
            "RECEIPT_DELIVERED",
            {
                "scenario": self.broker.scenario,
                "order": oid,
                "client": client,
                "at": self.e.monotonic(),
            },
        )


class Executor:
    def __init__(self, feed, settings, broker, profile, directory, emit):
        self.meta, self.slug = feed.meta, feed.meta.slug
        self.settings, self.broker, self.profile = settings, broker, profile
        self.monotonic = feed.monotonic
        self.official_time, self.time_anchor = feed.wall(), feed.monotonic()
        self.official_time_uncertainty_s = 0.0
        self.fatal, self.closed = None, False
        self.stop = asyncio.Event()
        self.attempts, self.known = [], set()
        self.journal = Journal()
        self.reads = SimpleNamespace(
            feed=feed,
            network=SimpleNamespace(
                snapshot=lambda: SimpleNamespace(
                    allowed=feed.connected, reason="SIM_PUBLIC_DISCONNECTED"
                ),
                recent_writes=lambda: SimpleNamespace(post=SimpleNamespace(max_ms=0)),
            ),
        )
        self.ledger = M1SimulationLedger(
            directory / f"{profile}-{broker.scenario}.sqlite3",
            {"mode": "SIMULATION_ONLY", "market": self.slug, "settings": settings.fingerprint},
            limit=settings.session_budget_usdc,
            order_budget=settings.order_budget_usdc,
            wall=feed.wall,
        )
        self.gateway = SimGateway(self, broker, profile, emit)
        self.emit = emit

    def record(self, kind, payload, **kwargs):
        self.emit(
            "M1_POLICY",
            {
                "scenario": self.broker.scenario,
                "profile": self.profile,
                "event": kind,
                "at": self.monotonic(),
                "payload": plain(payload),
            },
        )

    @staticmethod
    def _placement_outcomes(raw, quotes):
        state = (
            "ACK"
            if isinstance(raw, dict) and raw.get("success") is True
            else "REJECTED"
            if isinstance(raw, dict) and raw.get("success") is False
            else "UNKNOWN"
        )
        return [(state, None)]

    async def account_snapshot(self):
        # Model actual economic cash; M1Market independently subtracts its
        # conservative admission commitments, preserving baseline semantics.
        return {
            "official_balance_usd": str(self.broker.account(self.profile).cash),
            "applicable_allowance_usd": "20",
            "source": "SIMULATION_ECONOMIC_ACCOUNT",
        }


class M1Policy:
    def __init__(self, base_feed, settings, broker, profile, directory, emit):
        self.feed = FeedView(base_feed)
        detector = self.feed.m1
        self.e = Executor(self.feed, settings, broker, profile, directory, emit)
        self.market = M1Market(self.e, settings, self.e.stop, display=lambda _: None)
        self.feed.m1 = detector
        self.market.restore()
        self.admission_seen = {}
        self.closed = False

    def flush_admission(self):
        for row in self.e.ledger.rows():
            signature = digest(row)
            if self.admission_seen.get(row["id"]) != signature:
                self.admission_seen[row["id"]] = signature
                self.e.emit(
                    "M1_ADMISSION",
                    {
                        "scenario": self.e.broker.scenario,
                        "profile": self.e.profile,
                        "row": row,
                        "committed_usdc": str(self.e.ledger.committed()),
                    },
                )

    async def poll(self, uncertainty=0.0, *, elapsed_lower=None):
        official_time = (
            self.feed.wall()
            if elapsed_lower is None
            else int(self.e.slug.rsplit("-", 1)[1]) + elapsed_lower
        )
        self.e.official_time, self.e.time_anchor = official_time, self.feed.monotonic()
        self.e.official_time_uncertainty_s = uncertainty
        await self.market.advance()

    async def close(self):
        # No simulated exchange effect is inferred from stopping the client.
        # Pending requests become client UNKNOWN while economic exposure stays.
        for oid in list(self.e.gateway.waiting):
            self.e.gateway.deliver(oid, "UNKNOWN")
        await self.market.drain()
        self.e.stop.set()
        self.market.finish_pending()
        self.flush_admission()
        self.e.ledger.close()
        self.closed = True

    async def abort(self):
        """Close owned local tasks and ledger without inventing exchange receipts."""
        if self.closed:
            return
        self.e.stop.set()
        jobs = [job for job in (self.market.post_task, self.market.account_task)
                if job is not None]
        for job in jobs:
            job.cancel()
        try:
            results = await asyncio.gather(*jobs, return_exceptions=True)
        finally:
            self.e.ledger.close()
            self.closed = True
        for result in results:
            if isinstance(result, Exception):
                raise result


class M234Policy:
    def __init__(self, signals, broker, emit, settings):
        self.signals, self.broker, self.emit, self.settings = signals, broker, emit, settings
        self.used, self.seen_skips, self.order_candidates = set(), set(), {}

    def poll(self, elapsed, uncertainty=0.0):
        feed, broker = self.signals.m234, self.broker
        candidates = feed.hub.candidates()
        active = {(c["strategy"], c["id"]) for c in candidates}
        for oid, c in self.order_candidates.items():
            if (c["strategy"], c["id"]) not in active:
                broker.cancel(oid)
        for candidate in candidates:
            strategy, token = candidate["strategy"], candidate["token"]
            reason = None
            book = feed.books[token]
            if (
                not self.settings.start_sec <= elapsed
                or elapsed + uncertainty >= self.settings.cutoff_sec
            ):
                reason = "NEW_ORDER_WINDOW_CLOSED"
            elif (strategy, candidate["outcome"]) in self.used:
                reason = "DIRECTION_ATTEMPT_CONSUMED"
            elif not feed.m1.legal(book, feed.monotonic()):
                reason = "BOOK_UNAVAILABLE"
            elif (
                not candidate.get("observation_valid", True)
                or feed.monotonic() - feed.m1_watermark() > 0.020 + 1e-9
            ):
                reason = "OBSERVATION_UNAVAILABLE"
            price = dec(candidate["price"])
            if not reason and (
                book.best_ask is None
                or price >= book.best_ask
                or price % book.tick
                or dict(book.bids).get(price, 0) <= 0
            ):
                reason = "MAKER_ANCHOR_UNAVAILABLE"
            quantity = Decimal(0)
            if not reason:
                try:
                    quantity = budget_quantity(
                        price, feed.meta.min_order_size, self.settings.risk_usdc
                    )
                except ValueError:
                    reason = "VENUE_MINIMUM_OR_PRECISION"
            if (
                not reason
                and price * quantity
                > broker.account(strategy).cash - broker.account(strategy).reserved
            ):
                reason = "ECONOMIC_BUDGET_UNAVAILABLE"
            if reason:
                key = (strategy, candidate["id"], reason)
                if key not in self.seen_skips:
                    self.seen_skips.add(key)
                    self.emit(
                        "SKIP",
                        {
                            "scenario": broker.scenario,
                            "profile": strategy,
                            "candidate": candidate,
                            "reason": reason,
                        },
                    )
                continue
            intent = OrderIntent(
                strategy,
                strategy,
                "current-" + strategy + "-sim-v1",
                candidate["id"],
                feed.meta.slug,
                token,
                candidate["outcome"],
                str(price),
                str(quantity),
                feed.monotonic(),
                candidate["deadline"],
                "CANDIDATE",
            )
            self.emit("INTENT", {"scenario": broker.scenario, "intent": asdict(intent)})
            order = broker.submit(intent)
            self.used.add((strategy, candidate["outcome"]))
            self.order_candidates[order.id] = candidate

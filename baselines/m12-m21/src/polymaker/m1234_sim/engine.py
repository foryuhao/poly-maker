"""One availability-ordered entry point used by live capture and offline replay."""

from __future__ import annotations

import asyncio
import hashlib
import heapq
import json
from dataclasses import asdict
from pathlib import Path

from polymaker.dashboard.settlement import validate_resolution
from polymaker.domain import MarketMeta, TokenMeta
from polymaker.m2_config import M2Config
from polymaker.m1234_sim import INPUT_SCHEMA, MODEL
from polymaker.m1234_sim.broker import TERMINAL, ConstraintConflict, SimBroker
from polymaker.m1234_sim.config import M1SimulationConfig
from polymaker.m1234_sim.events import Clock, digest, encode
from polymaker.m1234_sim.policy import M1Policy, M234Policy
from polymaker.m1234_sim.signals import Signals
from polymaker.m1234_sim.store import Store
from polymaker.m1234_sim.ticks import TickVerification
from polymaker.m1234_sim.validity import SNAPSHOT_SCHEMA, new_validity


def market_meta(data):
    return MarketMeta(**{**data, "tokens": tuple(TokenMeta(**x) for x in data["tokens"])})


class Engine:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.store = Store(self.directory / "simulation.sqlite3")
        self.clock = Clock()
        self.seq = 0
        self.signals = None
        self.m1_policies, self.extra_policies, self.brokers = [], [], {}
        self.ended = False
        self.needs_reconnect = False
        self.coverage = {
            "frames": 0,
            "generations": 0,
            "gaps": [],
            "warmed_before_t15": False,
            "observed_through_t300": False,
            "complete_window": False,
            "max_input_backlog": 0,
            "max_processing_lag_ms": 0.0,
            "source_event_counts": {},
            "strategy_health": {},
        }
        self.definition = None
        self.resolution = None
        self.official_clock = None
        self.last_books = None
        self.marks = []
        self.mark_targets, self.mark_seq = [], 0
        self.tick_verification = None
        self.validity = new_validity()
        self.closed = False
        self.source_failure = None
        self.close_errors = []

    def emit(self, kind, payload):
        if kind == "SIMULATION_INVALID":
            failure = {**payload["conflict"], "market": self.meta.slug,
                       "condition_id": self.meta.condition_id}
            payload = {**payload, "conflict": failure}
            if not any(row["id"] == failure["id"] for row in self.validity["failures"]):
                self.validity["failures"].append(failure)
            self.validity["status"] = "INVALID"
            self.coverage["complete_window"] = False
            self.store.record_failure(self.validity)
        self.store.emit(kind, payload)
        if kind == "SIMULATION_INVALID":
            self.persist()
        if kind == "MODELLED_FILL":
            order = self.brokers[payload["scenario"]].orders[payload["order"]]
            for horizon in self.definition["settings"].get("markout_seconds", [1, 5, 30]):
                due = payload["at"] + horizon
                self.mark_seq += 1
                heapq.heappush(self.mark_targets, (due, self.mark_seq, due, order.intent.token))

    def bounds(self):
        server, sent, received = self.official_clock
        lower = server + self.clock.mono - received - self.epoch
        return lower, lower + (received - sent) + 1

    def book_snapshot(self):
        if self.signals is None:
            return self.last_books
        return {
            view: {token: asdict(book) for token, book in feed.books.items()}
            for view, feed in self.signals.views.items()
        }

    async def ingest(self, event):
        try:
            await self._ingest(event)
        except Exception as exc:
            if self.definition is not None:
                self.invalidate(exc, event.get("kind"))
            raise

    def resolve_official_result(self, raw, identity):
        if not isinstance(raw, dict):
            return None, "PUBLIC_RESOLUTION_RESPONSE_NOT_OBJECT"
        return validate_resolution(raw, identity), None

    def invalidate(self, error, input_kind):
        if self.validity["status"] != "INVALID":
            failure = {
                "id": digest({"input_seq": self.seq, "reason": str(error)}),
                "reason": str(error), "error_type": type(error).__name__,
                "input_seq": self.seq, "available_mono": self.clock.mono,
                "market": self.meta.slug, "input_kind": input_kind,
            }
            self.emit("SIMULATION_INVALID", {"conflict": failure})
        self.persist()

    async def _ingest(self, event):
        if event.get("schema") != INPUT_SCHEMA:
            raise ValueError("SIM_INPUT_SCHEMA_VERSION")
        if event["seq"] != self.seq + 1:
            raise ValueError("SIM_ENGINE_SEQUENCE_GAP")
        self.seq = event["seq"]
        self.store.input_seq = self.seq
        self.clock.set(event["available_mono"], event["available_wall"])
        kind = event["kind"]
        if kind == "INIT":
            definition = json.loads(encode(event["definition"]))
            if (definition.get("model") != MODEL
                    or definition.get("input_schema") != INPUT_SCHEMA):
                raise ValueError("SIM_MODEL_OR_INPUT_VERSION_MISMATCH")
            root = Path(__file__).resolve().parents[3]  # noqa: ASYNC240 -- Startup source identity check, before opening the feed.
            identity = definition["settings"].get("implementation", {})
            actual_paths = {str(p.relative_to(root)) for p in (root / "src/polymaker").rglob("*.py")}
            if set(identity) != actual_paths:
                raise ValueError("SIM_IMPLEMENTATION_IDENTITY_INCOMPLETE")
            for name, expected in identity.items():
                source = (root / name).resolve()
                if root not in source.parents or source.suffix != ".py":
                    raise ValueError("SIM_INVALID_SOURCE_IDENTITY")
                if hashlib.sha256(source.read_bytes()).hexdigest() != expected:
                    raise ValueError("SIM_IMPLEMENTATION_VERSION_MISMATCH:" + name)
            self.meta = market_meta(definition["market"])
            self.definition = definition
            self.store.bind(definition)
            self.epoch = int(self.meta.slug.rsplit("-", 1)[1])
            self.official_clock = self.definition["official_clock"]
            settings = self.definition["settings"]
            self.initialize_strategies(settings)
            self.emit(
                "DEFINITION",
                {
                    "model": MODEL,
                    "definition_hash": digest(self.definition),
                    "definition": self.definition,
                },
            )
            return
        if self.definition is None:
            raise ValueError("SIM_MISSING_DEFINITION")
        if "pending_count" in event:
            count, heads = event["pending_count"], event.get("pending", [])
            if type(count) is not int or count < 0 or len(heads) != min(2, count):
                raise ValueError("SIM_PENDING_WATERMARK_INVALID")
        if self.validity["status"] == "INVALID":
            if kind == "END":
                await self.end_observation(event)
                self.persist()
                return
            raise ConstraintConflict(self.validity["failures"])
        for broker in self.brokers.values():
            # Exchange actions still precede this frame. Only attribution uses
            # the lower source-time frontier: even already-received inputs may
            # remain in either bounded queue. The 502ms guard preserves the
            # existing 500ms source-age admission plus timestamp tolerance.
            receipts = [self.clock.mono, *event.get("pending", [])]
            if kind == "FRAME":
                receipts.append(event["received_mono"])
            broker.input_seq = self.seq
            broker.input_received = event.get("received_mono", self.clock.mono)
            broker.advance(self.clock.mono, attribution_frontier=min(receipts) - 0.502)
        if kind == "CLOCK":
            self.official_clock = event["official_clock"]
        elif kind == "CONNECT":
            self.signals.connect()
            self.needs_reconnect = False
            self.coverage["generations"] += 1
            self.tick_verification.observe(self.signals, self.clock, kind)
            self.update_constraints()
        elif kind == "FRAME":
            self.coverage["frames"] += 1
            self.coverage["max_input_backlog"] = max(
                self.coverage["max_input_backlog"],
                event.get("pending_count", len(event.get("pending", [])))
            )
            self.coverage["max_processing_lag_ms"] = max(
                self.coverage["max_processing_lag_ms"],
                (self.clock.mono - event["received_mono"]) * 1000,
            )
            if self.needs_reconnect:
                self.emit("UNUSABLE_INPUT", {"reason": "AWAITING_RECONNECT", "input_seq": self.seq})
                return
            try:
                self.signals.apply(
                    event["raw"],
                    event["received_mono"],
                    event["received_wall"],
                    event.get("pending", []),
                )
            except ValueError as exc:
                self.gap(str(exc), missing_since=event["received_mono"])
                self.needs_reconnect = True
            else:
                self.tick_verification.observe(self.signals, self.clock, kind)
                source_times = {
                    t: book.received_mono + (book.source_time - book.received_wall)
                    for t, book in self.signals.m1.books.items()
                }
                for broker in self.brokers.values():
                    broker.update_books(self.signals.m1.books, source_times=source_times)
                self.match_frame(event)
            for feed in self.signals.views.values():
                if feed.m1_pending_received:
                    feed.m1_pending_received.popleft()
            lower, upper = self.bounds()
            if upper <= 15 and all(
                feed.connected
                and all(
                    b.snapshot_generation == feed.generation and b.bids and b.asks
                    for b in feed.books.values()
                )
                for feed in self.signals.views.values()
            ):
                self.coverage["warmed_before_t15"] = True
        elif kind in {"TIMER", "POLL"}:
            if not self.ended:
                for feed in self.signals.views.values():
                    feed.m1_pending_received.clear()
                    feed.m1_pending_received.extend(event.get("pending", []))
                try:
                    self.signals.tick()
                    lower, upper = self.bounds()
                    for broker in self.brokers.values():
                        for oid, client in broker.advance(self.clock.mono, delivery=True):
                            for policy in self.m1_policies:
                                if policy.e.broker is broker:
                                    policy.e.gateway.deliver(oid, client)
                    await self.drain_jobs()
                    for policy in self.m1_policies:
                        await policy.poll(upper - lower, elapsed_lower=lower)
                    for policy in self.extra_policies:
                        policy.poll(lower, upper - lower)
                    await self.drain_jobs()
                except Exception as exc:
                    self.gap("OBSERVER_OR_POLICY_FAILED:" + type(exc).__name__ + ":" + str(exc))
                    raise
                self.record_marks()
        elif kind == "GAP":
            self.gap(event["reason"], missing_since=event.get("missing_since_received_mono"))
        elif kind == "END":
            await self.end_observation(event)
        elif kind == "TICK_REQUEST":
            if self.ended:
                raise ValueError("SIM_TICK_REQUEST_AFTER_END")
            self.tick_verification.start(event["request"], self.signals, self.clock)
        elif kind == "TICK_RESULT":
            if self.ended:
                raise ValueError("SIM_TICK_RESULT_AFTER_END")
            self.tick_verification.finish(event["result"], self.signals, self.clock)
            self.update_constraints()
        elif kind == "SETTLEMENT":
            identity = {
                "slug": self.meta.slug,
                "condition_id": self.meta.condition_id,
                "token_ids": {"YES": self.meta.yes.token_id, "NO": self.meta.no.token_id},
            }
            # Public Gamma can temporarily return JSON null/non-object payloads.
            # Keep the raw response and retry while leaving settlement unresolved.
            raw = event["raw"]
            result, read_error = self.resolve_official_result(raw, identity)
            if result:
                result["observed_at"] = self.clock.wall
                if self.resolution is not None and result["winner"] != self.resolution["winner"]:
                    self.gap("OFFICIAL_SETTLEMENT_CONFLICT")
                    raise ValueError("SIM_SETTLEMENT_CONFLICT")
                self.resolution = result
                for broker in self.brokers.values():
                    broker.settle(result["winner"])
            payload = {"result": result, "raw": raw}
            if read_error:
                payload["read_error"] = read_error
            self.emit("OFFICIAL_RESULT", payload)
        elif kind != "RESOURCE":
            raise ValueError("SIM_UNKNOWN_EVENT:" + kind)
        if kind in {"END", "SETTLEMENT"}:
            self.persist()
        elif kind in {"TIMER", "POLL"} and self.seq % 40 == 0:
            self.store.commit()

    def initialize_strategies(self, settings):
        m1settings = {
            k: M1SimulationConfig.model_validate(v) for k, v in settings["profiles"].items()
        }
        m234 = M2Config.model_validate(settings["m234"])
        self.signals = Signals(
            self.meta, self.clock, next(iter(m1settings.values())), m234, self.emit
        )
        self.signals.ticks(self.definition["ticks"])
        self.tick_verification = TickVerification(self.signals, self.clock, self.emit)
        for name, params in settings["scenarios"].items():
            broker = SimBroker(name, self.emit, seed=settings["seed"], **params)
            broker.now = self.clock.mono
            self.brokers[name] = broker
            for profile, cfg in m1settings.items():
                self.m1_policies.append(
                    M1Policy(
                        self.signals.m1,
                        cfg,
                        broker,
                        profile,
                        self.directory / "prepost",
                        self.emit,
                    )
                )
            self.extra_policies.append(M234Policy(self.signals, broker, self.emit, m234))
            for profile in [*m1settings, "M2", "M3", "M4"]:
                broker.account(profile)
            broker.update_constraints(self.tick_verification.constraints)

    async def drain_jobs(self):
        # Shared policy tasks contain no network or sleeps other than scheduler
        # yields. Fixed microtask drainage gives live and replay equal scheduling.
        for _ in range(6):
            await asyncio.sleep(0)
        for policy in self.m1_policies:
            for task in (policy.market.post_task, policy.market.account_task):
                if task is not None and task.done():
                    task.result()
            policy.flush_admission()

    def update_constraints(self):
        failures = []
        for broker in self.brokers.values():
            try:
                broker.update_constraints(self.tick_verification.constraints)
            except ConstraintConflict as exc:
                failures.extend(exc.conflicts)
        if failures:
            raise ConstraintConflict(failures)

    def strategy_health(self):
        return {
            "M1": {"failure": self.signals.m1.m1.failure,
                   "counts": dict(self.signals.m1.m1.counts)},
            "M2": {"failure": self.signals.m234.m1.failure,
                   "counts": dict(self.signals.m234.m1.counts)},
            "M3_M4": {"failures": self.signals.m234.hub.failures,
                      "counts": dict(self.signals.m234.hub.counts)},
        }

    async def end_observation(self, event):
        if self.ended:
            return
        lower, _ = self.bounds()
        self.coverage["observed_through_t300"] = (
            lower >= 300 and event.get("reason") == "T300_OBSERVED"
        )
        self.coverage["observed_complete_window"] = (
            self.coverage["warmed_before_t15"] and self.coverage["observed_through_t300"]
        )
        self.coverage["complete_window"] = (
            self.coverage["observed_complete_window"] and self.validity["status"] == "VALID"
        )
        self.last_books = self.book_snapshot()
        self.coverage["strategy_health"] = self.strategy_health()
        for policy in self.m1_policies:
            await policy.close()
        if self.validity["status"] == "VALID":
            for broker in self.brokers.values():
                for order in list(broker.orders.values()):
                    if order.state not in TERMINAL:
                        order.uncertain = True
                        broker.event("UNKNOWN_TAIL", order=order.id, reserved=str(order.reserved))
        self.ended = True
        self.tick_verification.close(self.clock)
        self.coverage["tick_verification"] = self.tick_verification.snapshot(self.clock.mono)
        self.emit("OBSERVATION_END", {"coverage": self.coverage, "reason": event.get("reason")})
        self.signals = None
        self.m1_policies.clear()
        self.extra_policies.clear()

    def gap(self, reason, *, missing_since=None):
        self.coverage["gaps"].append(
            {"input_seq": self.seq, "at": self.clock.mono, "reason": reason}
        )
        if self.validity["status"] == "INVALID":
            self.emit("COVERAGE_GAP", {"reason": reason,
                                       "missing_since_received_mono": missing_since})
            return
        if self.signals:
            if self.tick_verification is not None:
                self.tick_verification.invalidate(self.signals, self.clock, reason)
            self.signals.gap(reason)
        for broker in self.brokers.values():
            broker.gap(reason, missing_since=missing_since)
            if self.tick_verification is not None:
                broker.update_constraints(self.tick_verification.constraints)
        self.emit("COVERAGE_GAP", {"reason": reason, "missing_since_received_mono": missing_since})

    def match_frame(self, event):
        raw = event["raw"]
        if raw in ("PING", "PONG"):
            return
        rows = json.loads(raw) if isinstance(raw, str) else raw
        rows = rows if isinstance(rows, list) else [rows]
        for row in rows:
            kind = row.get("event_type", "UNKNOWN")
            self.coverage["source_event_counts"][kind] = (
                self.coverage["source_event_counts"].get(kind, 0) + 1
            )
            if kind == "tick_size_change":
                self.tick_verification.notice(row, self.signals, self.clock,
                                              event["received_mono"], event["received_wall"])
        # A raw frame is atomic for constraints: apply all its tick facts before
        # matching any row, including a SELL earlier in the same message list.
        self.update_constraints()
        for index, row in enumerate(rows):
            kind = row.get("event_type", "UNKNOWN")
            if kind != "last_trade_price":
                continue
            source = float(row.get("timestamp", 0)) / 1000
            if not -0.002 <= event["received_wall"] - source <= 0.5:
                for broker in self.brokers.values():
                    broker.gap(
                        "DIRECT_TRADE_SOURCE_TIME_UNCERTAIN",
                        row.get("asset_id"),
                        source_mono=event["received_mono"] + (source - event["received_wall"]),
                    )
                continue
            if (
                row.get("asset_id") not in self.signals.m1.books
                or row.get("market") != self.meta.condition_id
            ):
                raise ValueError("SIM_TRADE_MARKET_IDENTITY")
            for broker in self.brokers.values():
                broker.trade(
                    row,
                    f"{self.seq}:{index}",
                    source_mono=event["received_mono"] + (source - event["received_wall"]),
                )

    def mark_book_legal(self, book, now):
        return self.signals.m1.m1.legal(book, now)

    def record_marks(self):
        now = self.clock.mono
        while self.mark_targets and self.mark_targets[0][0] <= now:
            _, seq, due, token = heapq.heappop(self.mark_targets)
            book = self.signals.m1.books[token]
            if now > due + 0.25:
                continue  # Missed/invalid future observation remains null.
            if self.mark_book_legal(book, now):
                self.marks.append(
                    {
                        "at": now,
                        "target": due,
                        "token": token,
                        "mid": str((book.best_bid + book.best_ask) / 2),
                    }
                )
            else:
                heapq.heappush(self.mark_targets, (now + 0.025, seq, due, token))

    def snapshot(self):
        return {
            "mode": "SIMULATION_ONLY",
            "model": MODEL,
            "input_schema": INPUT_SCHEMA,
            "snapshot_schema": SNAPSHOT_SCHEMA,
            "validity": self.validity,
            "input_seq": self.seq,
            "definition_hash": digest(self.definition),
            "market": asdict(self.meta),
            "coverage": self.coverage,
            "resolution": self.resolution,
            "books": self.book_snapshot(),
            "marks": self.marks,
            "brokers": {name: b.snapshot() for name, b in self.brokers.items()},
            "tick_verification": self.tick_verification.snapshot(self.clock.mono)
            if self.tick_verification else None,
        }

    def persist(self):
        self.store.snapshot("state", self.snapshot())

    async def close(self, *, persist=True):
        if self.closed:
            return
        if persist:
            if self.definition is not None:
                if not self.ended:
                    for policy in self.m1_policies:
                        await policy.close()
                self.persist()
            self.store.close()
            self.closed = True
            return
        errors = []
        for policy in self.m1_policies:
            try:
                if not policy.closed:
                    await policy.abort()
            except BaseException as exc:
                errors.append(exc)
                self.close_errors.append({"step": "POLICY_CLOSE", "profile": policy.e.profile,
                                          "scenario": policy.e.broker.scenario,
                                          "error_type": type(exc).__name__, "error": str(exc)})
        try:
            self.store.close(commit=False)
        except BaseException as exc:
            errors.append(exc)
            self.close_errors.append({"step": "DATABASE_CLOSE", "error_type": type(exc).__name__,
                                      "error": str(exc)})
        self.closed = self.store.closed and all(policy.closed for policy in self.m1_policies)
        if errors:
            raise errors[0]

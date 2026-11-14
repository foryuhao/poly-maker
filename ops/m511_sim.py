"""Add SOP M5–M11 without modifying M1–M4 detectors, accounts or order IDs."""

import hashlib
import json
import time
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from m234_sim_main import MainEngine, MainPolicy
from m234_sim_main_storage import DiagnosticStore
from m511_sample_quality import own_market_gate
from maker_signals.feed import PARAMETER_HASH, PARAMETERS, ResearchFeed
from maker_signals.strategies import (
    FREQUENCY_VARIANTS,
    M5_M8_FREQUENCY,
    M5_M10_NEXT,
    SIGNAL_VARIANTS,
    Strategies,
    resolve_frequency_parameters,
    strategy_version,
)

from polymaker.config import RDPObiConfig
from polymaker.m2_config import M2Config
from polymaker.m1234_sim.broker import SimBroker
from polymaker.m1234_sim.engine import Engine
from polymaker.m1234_sim.events import encode
from polymaker.m1234_sim.signals import Signals
from polymaker.m1234_sim.ticks import TickVerification
from polymaker.rdp_sm_marketdata import SessionFeed

M5_M10_GROUPS = ("M5", "M6", "M7", "M8", "M9", "M10")
NEXT_RESEARCH_GROUPS = ("M5", "M6", "M9", "M10")
M5_M10_REVISION = "m5-m10-direct-t240-v1"
M5_M10_THROUGH_REVISION = "m5-m10-direct-trade-through-t240-v2"
GROUPS = (
    *M5_M10_GROUPS,
    "M4+M11",
    "M9+M11",
    "M10+M11",
    "M4@1.05s",
    "M4+M11@1.05s",
    "M9@1.05s",
    "M9+M11@1.05s",
    "M10@1.05s",
    "M10+M11@1.05s",
    "MAKER_CONTROL",
)


def selected_groups(enabled_strategies=None):
    if enabled_strategies is None:
        return M5_M10_GROUPS
    if (not isinstance(enabled_strategies, (list, tuple)) or not enabled_strategies
            or len(set(enabled_strategies)) != len(enabled_strategies)
            or not set(enabled_strategies) <= set(M5_M10_GROUPS)):
        raise ValueError("INVALID_RESEARCH_STRATEGY_SELECTION")
    return tuple(s for s in M5_M10_GROUPS if s in enabled_strategies)


def definition(signal_variant=None, *, only_m5_m10=False, execution_model="trade-through",
               frequency_parameters=None, enabled_strategies=None):
    import maker_signals

    if execution_model not in {"trade-through", "legacy"}:
        raise ValueError("UNKNOWN_RESEARCH_EXECUTION_MODEL")
    root = Path(maker_signals.__file__).parent
    if only_m5_m10:
        if signal_variant not in (None, M5_M8_FREQUENCY, *FREQUENCY_VARIANTS):
            raise ValueError("UNSUPPORTED_M5_M10_DIRECT_VARIANT")
        signal_variant = signal_variant or M5_M10_NEXT
    if signal_variant is not None and signal_variant not in SIGNAL_VARIANTS:
        raise ValueError("UNKNOWN_M511_SIGNAL_VARIANT")
    if frequency_parameters is not None and not only_m5_m10:
        raise ValueError("FREQUENCY_PARAMETERS_REQUIRE_M5_M10_SCOPE")
    if enabled_strategies is not None and not only_m5_m10:
        raise ValueError("STRATEGY_SELECTION_REQUIRES_RESEARCH_ONLY_SCOPE")
    groups = selected_groups(enabled_strategies) if only_m5_m10 else GROUPS
    resolved = resolve_frequency_parameters(signal_variant, frequency_parameters)
    versioned = groups if only_m5_m10 else ("M7", "M9", "M10")
    versions = {s: strategy_version(s, signal_variant) for s in versioned}
    versions.update(SIGNAL_VARIANTS.get(signal_variant, {}))
    if only_m5_m10:
        versions = {s: v for s, v in versions.items() if s in groups}
    return {
        "revision": (M5_M10_THROUGH_REVISION if execution_model == "trade-through"
                     else M5_M10_REVISION) if only_m5_m10 else "m5-m11-sop11-t240-v1",
        "spec_version": PARAMETERS["spec_version"],
        **({"signal_variant": signal_variant} if signal_variant is not None else {}),
        "strategy_versions": versions,
        "parameter_sha256": PARAMETER_HASH,
        "groups": list(groups),
        "implementation": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.glob("*.py"))
        },
        "adapter_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "retained_strategies": [] if only_m5_m10 else ["M1", "M2", "M3", "M4"],
        "execution_override": {
            "version": "min-anchor-bid-5usdc-t240-v1",
            "price": "min(anchor,best_bid)",
            "budget_usdc": "5",
            "frequency": "once per market/strategy/token including rejected POST",
            "cancel": "T240_ONLY",
            "hold": "OFFICIAL_SETTLEMENT",
            "minimum_remaining_signal_ms": 0,
            "signal_expiry_cancels_order": False,
            "supersedes_SOP_section_10": True,
        },
        "assumed_fill": "independent admitted-SUBMIT comparison, not all candidates",
        **({} if only_m5_m10 else {
            "maker_control": "first legal poll at or after T15 per token, fixed best bid, same 5 USDC/T240"
        }),
        "M9_seed": "One stable public BID refill round and active EX_SELL",
        "real_orders": 0,
        **({"frequency_parameters": resolved}
           if signal_variant in FREQUENCY_VARIANTS else {}),
        **({"fill_model": __import__("m510_trade_through").definition()}
           if only_m5_m10 and execution_model == "trade-through" else {}),
    }


class ResearchStore(DiagnosticStore):
    def __init__(self, path, scope="M5_M11"):
        self.research_rows = 0
        self.research_scope = scope
        super().__init__(path)

    def emit(self, kind, payload):
        if kind == "RESEARCH_EVIDENCE" and payload.get("event") in {
            "NORMALIZED_TRADE",
            "QUALITY_UNAVAILABLE",
            "AP_STARTED",
            "AP_NOT_CONFIRMED",
            "LOCAL_AP_NOT_CONFIRMED",
            "LOCAL_AP_UNAVAILABLE",
            "EX_NOT_CONFIRMED",
            "ROUND_STARTED",
            "ROOT_DIAGNOSTIC_ONLY",
        }:
            self.archive.write(
                (
                    encode(
                        {
                            "input_seq": self.input_seq,
                            "diagnostic_seq": self.diagnostic_rows + 1,
                            "after_audit_seq": self.count,
                            "kind": kind,
                            "payload": payload,
                        }
                    )
                    + "\n"
                ).encode()
            )
            self.diagnostic_rows += 1
            self.research_rows += 1
            self.diagnostic_counts[
                (self.research_scope, payload.get("payload", {}).get("reason", payload["event"]))
            ] += 1
            return
        super().emit(kind, payload)

    def flush_archive(self, *, closed=False):
        super().flush_archive(closed=closed)
        value = json.loads(
            self.db.execute(
                "SELECT payload FROM metadata WHERE name='diagnostic_archive'"
            ).fetchone()[0]
        )
        value.update(
            signal_rows=self.diagnostic_rows - self.research_rows,
            research_rows=self.research_rows,
            research_quality=[r for r in value["quality"] if r["strategy"] == self.research_scope],
            quality=[r for r in value["quality"] if r["strategy"] != self.research_scope],
        )
        self.db.execute(
            "UPDATE metadata SET payload=? WHERE name='diagnostic_archive'", (encode(value),)
        )


class SignalExtension:
    def __init__(self, base, engine):
        self.base, self.engine = base, engine
        meta = engine.meta
        tokens = (meta.yes.token_id, meta.no.token_id)
        self.research = ResearchFeed(
            meta.condition_id, tokens, engine.definition["ticks"], engine.emit
        )
        self.research_signals = Strategies(
            self.research,
            dict(zip(tokens, ("YES", "NO"), strict=True)),
            signal_variant=engine.signal_variant,
            parent_filters=not engine.only_m5_m10,
            frequency_parameters=engine.frequency_parameters,
            enabled_strategies=engine.groups if engine.only_m5_m10 else None,
        )
        self.parents = {}
        self.m4_rows = []
        self.control_created = set()

    def __getattr__(self, name):
        return getattr(self.base, name)

    def connect(self):
        self.base.connect()
        e = self.engine
        self.research.connect(now=e.clock.mono, wall=e.clock.wall, seq=e.seq)
        self.drain_m4()

    def gap(self, reason):
        self.base.gap(reason)
        self.research.gap(reason)
        self.drain_m4()

    def ticks(self, ticks):
        self.base.ticks(ticks)
        for token, tick in ticks.items():
            b = self.research.books[token]
            b.tick = Decimal(str(tick))
            b.tick_verified = True

    def apply(self, raw, received, wall, pending=()):
        self.base.apply(raw, received, wall, pending)
        e = self.engine
        self.research.apply(
            raw, now=e.clock.mono, wall=e.clock.wall, received=received, seq=e.seq, pending=pending
        )
        self.drain_m4()

    def tick(self):
        self.base.tick()
        e = self.engine
        pending = tuple(self.base.m1.m1_pending_received)
        self.research.timer(now=e.clock.mono, wall=e.clock.wall, seq=e.seq, pending=pending)
        if e.only_m5_m10:
            return
        self.drain_m4()
        lower, _ = e.bounds()
        if 15 <= lower < 240:
            from maker_signals.strategies import Campaign

            for token in self.research.tokens:
                if (
                    token in self.control_created
                    or not self.research.good[token]
                    or not self.research.legal(token)
                ):
                    continue
                self.control_created.add(token)
                q = Campaign(
                    self.research_signals,
                    "MAKER_CONTROL",
                    token,
                    "T15:" + token,
                    e.clock.mono + 0.2,
                    self.research.books[token].bid,
                )
                q.confirm(
                    q.deadline,
                    [],
                    features={"predeclared_rule": "FIRST_LEGAL_POLL_AFTER_T15", "elapsed": lower},
                )
                self.research_signals.campaigns["MAKER_CONTROL", token] = q
        self.research.publish()

    def drain_m4(self):
        if self.engine.only_m5_m10:
            return
        c = self.research
        rows, self.m4_rows = self.m4_rows, []
        # Original M4 emits explicit queue/round evidence. Maintain each frozen
        # mother's validity; a new positive pointer alone never kills old proof.
        for row in rows:
            event, p = row["event"], row["payload"]
            token = p.get("token")
            for parent in self.parents.values():
                if parent.token != token:
                    continue
                reason = None
                if event in {"QUEUE_ENDED", "INPUT_UNAVAILABLE", "OBSERVER_FAILED"}:
                    reason = p.get("reason", event)
                if event == "ROUND_CLOSED":
                    if p["side"] == "BID" and not p["stable"]:
                        reason = "M4_BID_SUPPORT_FAILED"
                    if (
                        p["side"] == "ASK"
                        and p["stable"]
                        and Decimal(str(p["recovery"])) >= Decimal(".5")
                    ):
                        reason = "M4_ASK_RECOVERED"
                if event == "CANDIDATE_ENDED" and p.get("id") == parent.values["legacy_id"]:
                    reason = p.get("reason", "M4_PARENT_ENDED")
                if reason:
                    c.registry.end(parent, c.now, reason)
            if event == "CANDIDATE" and p.get("definition") == "m4-frequency-v2":
                # If current public quality cannot prove the original candidate,
                # record unavailable rather than fabricating a parent lifetime.
                if not c.good.get(token):
                    c.event(
                        "M11_PARENT_UNAVAILABLE",
                        {
                            "parent_id": p["id"],
                            "token": token,
                            "reason": "PUBLIC_QUALITY_UNAVAILABLE",
                        },
                    )
                    continue
                f = c.fact(
                    "M4_PARENT",
                    token,
                    float(p["deadline"]),
                    float(p["confirmed"]),
                    float(p["confirmed"]),
                    anchor=Decimal(str(p["price"])),
                    values={
                        "legacy_id": p["id"],
                        "version": "m4-frequency-v2",
                        "support_id": p["support_id"],
                        "df_id": p["df_id"],
                    },
                )
                self.parents[f.id] = f
                self.research_signals.parent("M4", f, p["root"])
        for parent in self.parents.values():
            if c.registry.valid(parent, c.now) and (
                not c.good.get(parent.token)
                or c.books[parent.token].bids.get(parent.anchor, 0) <= 0
            ):
                c.registry.end(parent, c.now, "M4_PARENT_QUALITY_OR_ANCHOR_UNKNOWN", "UNKNOWN")
        c.publish()


class FeedFacade:
    """Only the already-tested execution policy surface; no legacy feed mutation."""

    def __init__(self, extension, meta):
        self.extension, self.meta = extension, meta
        self.hub = SimpleNamespace(candidates=extension.research_signals.candidates)
        self.m1 = SimpleNamespace(legal=lambda book, now: extension.research.legal(book.token))

    @property
    def books(self):
        return {
            t: SimpleNamespace(token=t, best_bid=b.bid, best_ask=b.ask, tick=b.tick)
            for t, b in self.extension.research.books.items()
        }

    def monotonic(self):
        return self.extension.research.now

    def m1_watermark(self):
        return self.extension.research.watermark


class ResearchBroker(SimBroker):
    def __init__(self, *args, signal_variant=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.signal_variant = signal_variant

    def submit(self, intent):
        return super().submit(
            replace(intent, signal_version=strategy_version(intent.strategy, self.signal_variant))
        )


class ResearchEngine(MainEngine):
    only_m5_m10 = False

    def __init__(self, directory, *, derive=False, signal_variant=None, execution_model="trade-through",
                 frequency_parameters=None, enabled_strategies=None):
        self.execution_model = execution_model
        if self.only_m5_m10 and signal_variant not in (None, M5_M8_FREQUENCY, *FREQUENCY_VARIANTS):
            raise ValueError("UNSUPPORTED_M5_M10_DIRECT_VARIANT")
        if signal_variant is not None and ((not derive and not self.only_m5_m10)
                                           or signal_variant not in SIGNAL_VARIANTS):
            raise ValueError("M511_SIGNAL_VARIANT_REQUIRES_DERIVED_REPLAY")
        self.signal_variant = (signal_variant or M5_M10_NEXT) if self.only_m5_m10 else signal_variant
        if frequency_parameters is not None and not self.only_m5_m10:
            raise ValueError("FREQUENCY_PARAMETERS_REQUIRE_M5_M10_SCOPE")
        resolved = resolve_frequency_parameters(self.signal_variant, frequency_parameters)
        self.frequency_parameters = resolved if self.only_m5_m10 and self.signal_variant in FREQUENCY_VARIANTS else None
        if enabled_strategies is not None and not self.only_m5_m10:
            raise ValueError("STRATEGY_SELECTION_REQUIRES_RESEARCH_ONLY_SCOPE")
        self.groups = selected_groups(enabled_strategies) if self.only_m5_m10 else GROUPS
        super().__init__(directory, derive=derive)
        self.store.close(commit=False)
        self.store.path.unlink()
        self.store = ResearchStore(
            self.directory / "simulation.sqlite3",
            scope="M5_M10" if self.only_m5_m10 else "M5_M11",
        )
        self.research_extension = None
        self.fill_annotations = []

    def emit(self, kind, payload):
        if kind == "INTENT" and payload.get("scenario", "").startswith("research/"):
            payload = {
                **payload,
                "intent": {
                    **payload["intent"],
                    "signal_version": strategy_version(
                        payload["intent"]["strategy"], self.signal_variant
                    ),
                },
                "execution_version": "min-anchor-bid-5usdc-t240-v1",
            }
        if kind == "SIGNAL" and payload.get("strategy") in self.groups:
            payload = {
                **payload,
                "payload": {
                    **payload["payload"],
                    "run_id": getattr(self.store, "run_owner", None),
                    "execution_version": "min-anchor-bid-5usdc-t240-v1",
                },
            }
        super().emit(kind, payload)
        ext = self.research_extension
        if ext and kind == "SIGNAL" and payload.get("strategy") == "M4":
            ext.m4_rows.append(deepcopy(payload))
        if ext and kind == "MODELLED_FILL" and payload["scenario"].startswith("research/"):
            order = self.brokers[payload["scenario"]].orders[payload["order"]]
            f = ext.research.registry.facts.get(order.intent.opportunity)
            # Some fills are released after their original input. Record both
            # occurrence and availability; the historical end timestamp is used.
            self.fill_annotations.append((deepcopy(payload), order.intent, f))

    async def _ingest(self, event):
        if event["kind"] == "INIT":
            expected = json.loads(encode(definition(self.signal_variant, only_m5_m10=self.only_m5_m10,
                                                   execution_model=self.execution_model,
                                                   frequency_parameters=self.frequency_parameters,
                                                   enabled_strategies=self.groups if self.only_m5_m10 else None)))
            actual = event["definition"]["settings"].get("m5_m11")
            if actual != expected:
                if not self.derive or actual is not None:
                    raise ValueError("M511_DEFINITION_MISSING_OR_CHANGED")
                event = deepcopy(event)
                event["definition"]["settings"]["m5_m11"] = expected
        if self.only_m5_m10:
            await Engine._ingest(self, event)
        else:
            await super()._ingest(event)
        if event["kind"] == "INIT":
            self.research_extension = SignalExtension(self.signals, self)
            self.signals = self.research_extension
            # TickVerification also invokes the wrapper for official tick updates.
            settings = self.definition["settings"]
            if self.only_m5_m10 and self.execution_model == "trade-through":
                from m510_trade_through import SCENARIO_SETTINGS, TradeThroughBroker
                if settings["scenarios"] != SCENARIO_SETTINGS:
                    raise ValueError("THROUGH_SCENARIOS_REQUIRED")
            for scenario, params in settings["scenarios"].items():
                name = "research/" + scenario
                broker_class = (TradeThroughBroker if self.only_m5_m10
                                and self.execution_model == "trade-through" else ResearchBroker)
                broker = broker_class(
                    name, self.emit, seed=settings["seed"], signal_variant=self.signal_variant, **params
                )
                broker.now = self.clock.mono
                self.brokers[name] = broker
                facade = SimpleNamespace(m234=FeedFacade(self.research_extension, self.meta))
                self.extra_policies.append(
                    MainPolicy(facade, broker, self.emit, M2Config.model_validate(settings["m234"]))
                )
                for group in self.groups:
                    broker.account(group)
                broker.update_constraints(self.tick_verification.constraints)
        c = self.research_extension.research if self.research_extension else None
        for payload, intent, f in self.fill_annotations:
            occurred = payload.get("source_mono", payload["at"])
            end = c.registry.endings.get(f.id) if f else None
            self.store.emit(
                "RESEARCH_FILL_ATTRIBUTION",
                {
                    "scenario": payload["scenario"],
                    "order": payload["order"],
                    "candidate_id": f.id if f else intent.opportunity,
                    "fill": payload,
                    "fill_occurred_at": occurred,
                    "fill_available_at": self.clock.mono,
                    "signal_valid_before_fill_event": None
                    if f is None
                    else (
                        f.confirmed <= occurred < f.deadline
                        and (end is None or end["at"] > occurred)
                    ),
                    "same_event_invalidation": bool(end and end["at"] == occurred),
                    "fill_after_deadline": None if f is None else occurred >= f.deadline,
                    "signal_age_at_fill": None if f is None else occurred - f.confirmed,
                    "attribution_status": "UNKNOWN" if f is None else "AVAILABLE",
                    "execution_override": "GTC_TO_T240_REGARDLESS_OF_SIGNAL_END",
                },
            )
        self.fill_annotations.clear()

    async def end_observation(self, event):
        c = self.research_extension.research if self.research_extension else None
        health = (
            {
                "counts": dict(c.counts),
                "quality": dict(c.reasons),
                "candidate_count": len(c.strategies.published),
            }
            if c
            else None
        )
        await super().end_observation(event)
        if health and not self.only_m5_m10:
            self.coverage["strategy_health"]["M5_M11"] = health
            self.store.emit("RESEARCH_OBSERVATION_END", health)


class PublicBookSignals(Signals):
    """Reuse public book reconstruction without installing a legacy detector."""

    def __init__(self, meta, clock):
        self.m1 = SessionFeed(
            meta, RDPObiConfig(), monotonic=clock.monotonic, wall=clock.time, book_only=True
        )
        self.views = {"PUBLIC_BOOK": self.m1}

    def drain(self):
        pass

    def tick(self):
        pass


class ResearchOnlyEngine(ResearchEngine):
    only_m5_m10 = True

    def retire_runtime(self):
        """Drop the research graph only after the final durable snapshot/close.

        Engine.end_observation clears signals, but this extension still owns the
        same feed, registry, campaigns and callbacks back into the engine.
        """
        if not self.ended or not self.store.closed:
            raise RuntimeError("RESEARCH_RETIRE_BEFORE_DURABLE_CLOSE")
        if self.research_extension is None:
            return
        started, cpu_started = time.perf_counter(), time.process_time()
        self.retirement_released_objects = self.research_extension.research.release_runtime()
        self.research_extension = None
        self.fill_annotations.clear()
        self.signals = None
        self.extra_policies.clear()
        self.retirement_release_seconds = time.perf_counter()-started
        self.retirement_release_cpu_seconds = time.process_time()-cpu_started
        # Compatibility fields distinguish explicit reference release from the
        # previous global, stop-the-world collector. Python automatic GC stays on.
        self.retirement_collected_objects = 0
        self.retirement_gc_seconds = 0.0

    def initialize_strategies(self, settings):
        self.signals = PublicBookSignals(self.meta, self.clock)
        self.signals.ticks(self.definition["ticks"])
        self.tick_verification = TickVerification(self.signals, self.clock, self.emit)
        self.record_sample_gate()

    def record_sample_gate(self):
        project = next(p for p in Path(__file__).resolve().parents
                       if (p / "ops/m511_sample_quality.py").is_file())
        gate = own_market_gate(project, self.meta.slug)
        self.store.db.execute(
            "INSERT OR REPLACE INTO metadata VALUES('research_sample_eligibility',?)",
            (encode(gate),),
        )

    def strategy_health(self):
        feed = self.research_extension.research
        self.record_sample_gate()
        return {"M5_M10": {
            "counts": dict(feed.counts),
            "quality": dict(feed.reasons),
            "candidate_count": len(feed.strategies.published),
        }}

    def mark_book_legal(self, book, now):
        return self.research_extension.research.legal(book.token_id)

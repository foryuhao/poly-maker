"""Single selected SOP on public data, with isolated simulated order ledgers."""

import hashlib
import json
from pathlib import Path

from m1223_execution import ANCHOR_FILL_MODEL, Broker, Policy, fill_attribution
from sop_signals.feed import Feed
from sop_signals.m12 import VERSION, Parameters, parameter_identity, signal_version
from sop_signals.parameters import strategy_from_parameters

from polymaker.config import RDPObiConfig
from polymaker.m1234_sim.engine import Engine
from polymaker.m1234_sim.events import encode
from polymaker.m1234_sim.signals import Signals
from polymaker.m1234_sim.ticks import TickVerification
from polymaker.rdp_sm_marketdata import SessionFeed


def definition(parameters=None):
    import sop_signals

    package = Path(sop_signals.__file__).parent
    parameters = parameters or Parameters()
    strategy = strategy_from_parameters(parameters)
    version, implementation_version = signal_version(parameters), VERSION
    if getattr(parameters, "variant_id", None) == "M12_VALIDITY_OVERLAP":
        from sop_signals.m12_overlap import VERSION as implementation_version
        from sop_signals.m12_overlap import signal_version as overlap_version

        version = overlap_version(parameters)
    if strategy == "M7":
        from sop_signals.m7 import VERSION as implementation_version
        from sop_signals.m7 import signal_version as m7_version

        version = m7_version(parameters)
    elif strategy == "M9":
        from sop_signals.m9 import VERSION as implementation_version
        from sop_signals.m9 import signal_version as m9_version

        version = m9_version(parameters)
    elif strategy == "M13":
        from sop_signals.m13 import VERSION as implementation_version
        from sop_signals.m13 import signal_version as m13_version

        version = m13_version(parameters)
    elif strategy == "M14":
        from sop_signals.m14 import VERSION as implementation_version
        from sop_signals.m14 import signal_version as m14_version

        version = m14_version(parameters)
    elif strategy == "M15":
        from sop_signals.m15 import VERSION as implementation_version
        from sop_signals.m15 import signal_version as m15_version

        version = m15_version(parameters)
    elif strategy == "M16":
        from sop_signals.m16 import VERSION as implementation_version
        from sop_signals.m16 import signal_version as m16_version

        version = m16_version(parameters)
    elif strategy == "M17":
        from sop_signals.m17 import VERSION as implementation_version
        from sop_signals.m17 import signal_version as m17_version

        version = m17_version(parameters)
    elif strategy == "M18":
        from sop_signals.m18 import VERSION as implementation_version
        from sop_signals.m18 import signal_version as m18_version

        version = m18_version(parameters)
    elif strategy == "M19":
        from sop_signals.m19 import VERSION as implementation_version
        from sop_signals.m19 import signal_version as m19_version

        version = m19_version(parameters)
    elif strategy == "M21":
        from sop_signals.m21 import VERSION as implementation_version
        from sop_signals.m21 import signal_version as m21_version

        version = m21_version(parameters)
    elif strategy == "M23":
        from sop_signals.m23 import VERSION as implementation_version
        from sop_signals.m23 import signal_version as m23_version

        version = m23_version(parameters)
    elif strategy == "M24":
        from sop_signals.m24 import SOP_VERSION, ALIGNMENT_VERSION

        implementation_version = f"{SOP_VERSION}/{ALIGNMENT_VERSION}"
        version = f"{implementation_version}/p-{parameters.digest[:12]}"
    parameter_values, parameter_hash = parameter_identity(parameters)
    project = Path(__file__).resolve().parents[1]
    docs = project / "research/signals"
    if not docs.exists():
        docs = project / "documents/research/signals"
    input_path = project / "config/m1223-input.json"
    input_contract = json.loads(input_path.read_text())
    result = {
        "strategy": strategy,
        "version": version,
        "implementation_version": implementation_version,
        "parameters": parameter_values,
        "parameter_sha256": parameter_hash,
        "signal_contract": "fixed-anchor-signal-v1",
        "execution_contract": "fixed-anchor-execution-v1",
        "implementation": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(package.glob("*.py"))
        },
        "adapter_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "adapter_input_version": "single-validated-parent-decode-r1",
        "runtime_retirement_version": "durable-seal-release-public-feed-and-policies-r1",
        "execution_sha256": hashlib.sha256(
            Path(__file__).with_name("m1223_execution.py").read_bytes()
        ).hexdigest(),
        "source_order": "OBSERVED_RAW_ORDER_STRICT_SOURCE_NONREGRESSION_PREHIT_QUANTITY_STRICTLY_EARLIER_TIMESTAMP",
        "input_capability": "REQUIRES_RAW_RECONSTRUCTION_UNKNOWN_WHERE_NOT_PROVABLE",
        "groups": [strategy],
        "real_orders": 0,
        "input_contract": input_contract,
        "input_contract_sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
        "sop_references": {
            name: hashlib.sha256((docs / name).read_bytes()).hexdigest()
            for name in (
                strategy + ".md",
                "COMMON.md",
                "FIXED_ANCHOR_CONTRACT.md",
                "FIXED_ANCHOR_REVIEW.md",
                "INPUT_CAPABILITIES.md",
                "CAUSAL_HANDOFF_REVIEW.md",
            )
        },
    }
    if getattr(parameters, "variant_id", None) == "M12_VALIDITY_OVERLAP":
        from sop_signals.m12_overlap import SOP_FILE, SOP_SHA256, DOCUMENT_PARAMETERS_SHA256

        actual = hashlib.sha256((docs / SOP_FILE).read_bytes()).hexdigest()
        if actual != SOP_SHA256:
            raise ValueError("M12_OVERLAP_SELECTED_SOP_CHANGED")
        result.update(variant_id=parameters.variant_id, selected_sop_file=SOP_FILE,
                      selected_sop_sha256=SOP_SHA256,
                      selected_document_parameters_sha256=DOCUMENT_PARAMETERS_SHA256)
        result["sop_references"][SOP_FILE] = actual
    elif strategy == "M24":
        from sop_signals.m24 import VARIANT, SOP_FILE, SOP_SHA256, DOCUMENT_PARAMETERS_SHA256

        actual = result["sop_references"][SOP_FILE]
        if actual != SOP_SHA256:
            raise ValueError("M24_SELECTED_SOP_CHANGED")
        result.update(variant_id=VARIANT, selected_sop_file=SOP_FILE, selected_sop_sha256=actual,
                      selected_document_parameters_sha256=DOCUMENT_PARAMETERS_SHA256,
                      adapter_input_version="m24-fixed-level-proof-and-simulated-order-lifetime-v1")
    elif strategy == "M21":
        from sop_signals.m21 import SOP_FILE, SOP_SHA256, DOCUMENT_PARAMETERS_SHA256

        actual = hashlib.sha256((docs / SOP_FILE).read_bytes()).hexdigest()
        if actual != SOP_SHA256:
            raise ValueError("M21_SELECTED_SOP_CHANGED")
        result.update(variant_id=parameters.variant_id,selected_sop_file=SOP_FILE,
                      selected_sop_sha256=SOP_SHA256,
                      selected_document_parameters_sha256=DOCUMENT_PARAMETERS_SHA256,
                      adapter_input_version="single-parent-m21-joint-endpoint-raw-buy-presence-r1")
        result['sop_references'][SOP_FILE] = actual
    elif strategy in {"M7", "M9"}:
        from importlib import import_module
        from sop_signals.legacy_variant_feed import PINNED, verify_sources

        selected_contract=import_module('sop_signals.'+strategy.lower())
        SOP_FILE=selected_contract.SOP_FILE
        SOP_SHA256=selected_contract.SOP_SHA256
        DOCUMENT_PARAMETERS_SHA256=selected_contract.DOCUMENT_PARAMETERS_SHA256
        verify_sources()
        actual = hashlib.sha256((docs / SOP_FILE).read_bytes()).hexdigest()
        if actual != SOP_SHA256:
            raise ValueError(strategy+"_SELECTED_SOP_CHANGED")
        result.update(variant_id=parameters.variant_id,selected_sop_file=SOP_FILE,
                      selected_sop_sha256=SOP_SHA256,
                      selected_document_parameters_sha256=DOCUMENT_PARAMETERS_SHA256,
                      adapter_input_version=("selected-v7-compatible-complement-with-level-provenance-r1"
                          if strategy=="M7" else "selected-v8-complement-continuous-ex-provenance-r1"),
                      legacy_compatibility_sources=PINNED)
        result['sop_references'][SOP_FILE]=actual
        result['input_contract_base_sha256']=result['input_contract_sha256']
        result['input_contract']={**input_contract,
            'version':('m7-v7-complement-plus-fixed-level-provenance-r1' if strategy=='M7'
                       else 'm9-v8-complement-plus-fixed-level-provenance-r1'),
            'direct_trade_source':'DIRECT_OR_VERIFIED_COMPLEMENT_VIEW_ONCE_PER_TOKEN',
            'economic_trade_identity':'FROZEN_TX_TIMESTAMP_TOKEN_DIRECTION_PRICE_QUANTITY_TUPLE',
            'mapping':'complement-top3-identity-v1',
            'legacy_compatibility_sources':PINNED}
        result['input_contract_sha256']=hashlib.sha256(json.dumps(result['input_contract'],sort_keys=True).encode()).hexdigest()
    elif strategy == "M23":
        from sop_signals.m23 import SOP_FILE, SOP_SHA256, DOCUMENT_PARAMETERS_SHA256

        actual = hashlib.sha256((docs / SOP_FILE).read_bytes()).hexdigest()
        if actual != SOP_SHA256:
            raise ValueError("M23_SELECTED_SOP_CHANGED")
        result.update(variant_id=parameters.variant_id,selected_sop_file=SOP_FILE,
                      selected_sop_sha256=SOP_SHA256,
                      selected_document_parameters_sha256=DOCUMENT_PARAMETERS_SHA256,
                      adapter_input_version="single-parent-m23-pre-normalization-sell-presence-r2")
        result['sop_references']['PRELOAD_SEQUENCE_REVIEW.md'] = hashlib.sha256(
            (docs/'PRELOAD_SEQUENCE_REVIEW.md').read_bytes()).hexdigest()
        result['sop_references'][SOP_FILE] = actual
    if strategy == "M14" and parameters.round_seconds != .5:
        from sop_signals.m14 import observation_windows

        result["implementation_version"] += "/ask-window-bid500-stable100-v2"
        result["observation_windows"] = observation_windows(parameters)
    if strategy == "M16":
        result["adapter_input_version"] = "single-validated-parent-m16-explicit-receiver-r1"
        result["timing_input_contract"] = {
            "version": "m16-local-receiver-quantization-v1",
            "receiver_clock_evidence_required": True,
            "depth_provenance": "RAW_FRAME_ROW_GLOBAL_CHANGE_OR_ORIGINAL_BIDS_INDEX",
            "elapsed_clock": "RECEIVED_MONOTONIC_ONLY",
            "crossing_bracket_max_ms": 50, "receiver_clock_quantum_max_ms": 1,
            "clock_padding_quanta": 2,
        }
        result["sop_references"]["TRADE_TIMING_CAPABILITIES.md"] = hashlib.sha256(
            (docs / "TRADE_TIMING_CAPABILITIES.md").read_bytes()).hexdigest()
    if strategy == "M17":
        result["adapter_input_version"] = "single-validated-parent-m17-explicit-level-rechecks-r1"
        result["admission_signal_basis"] = "EVER_REFERENCE_READY_BEFORE_ORIGINAL_DEADLINE"
        result["recheck_input_contract"] = {
            "window_ms": 750, "subwindow_ms": 250, "recheck_ms": 100,
            "deadline": "TRADE_RECEIVED_PLUS100MS_EXCLUSIVE_RECEIVED_AND_AVAILABLE",
            "quantity_provenance": "EXPLICIT_ORIGINAL_BID_RAW_FRAME_ROW_GLOBAL_CHANGE_OR_BIDS_INDEX",
            "source_order": "STRICTLY_LATER_SOURCE_AND_INPUT_KEY_FOR_EVERY_PRINT",
            "pending_new_action": "BLOCK_ONLY_NEW_SUBMISSIONS_KEEP_EXISTING_EXPOSURE_UNTIL_END",
            "publication": "FIRST_COMPLETE_PARENT_AFTER_WATERMARK_MAX50MS_CURRENT90PCT",
        }
        result["sop_references"]["P_QR.md"] = hashlib.sha256((docs / "P_QR.md").read_bytes()).hexdigest()
    if strategy == "M18":
        result["adapter_input_version"] = "single-validated-parent-m18-explicit-terminal-depth-r1"
        result["terminal_quantity_input_contract"] = {
            "window_ms": int(parameters.round_seconds * 1000), "stable_continuous_hold_ms": 100,
            "terminal_quantity_received_before_window_end": True,
            "terminal_quantity_key_after_all_counted_sells": True,
            "terminal_quantity_source_strictly_after_max_sell_source": True,
            "quantity_provenance": "EXPLICIT_ORIGINAL_BID_RAW_FRAME_ROW_GLOBAL_CHANGE_OR_BIDS_INDEX",
            "publication_lateness_max_ms": 50,
            "m16_speed_measurement_required": False,
            "m17_per_print_recheck_required": False,
        }
        if parameters.round_seconds == .25:
            from sop_signals.m18 import SHORT_ROUND_MARKER

            result["explicit_primitive_numeric_override"] = {
                "version": SHORT_ROUND_MARKER, "original_window_ms": 500, "window_ms": 250,
                "stable_continuous_hold_ms": 100, "stable_recovery_min": "0.50",
                "applies_to": "FORMATION_AND_FOLLOWUP_ROUNDS", "source_quality_contract_changed": False}
    if strategy == "M19":
        result["adapter_input_version"] = "single-validated-parent-m19-two-sided-explicit-terminal-r1"
        result["sop_role"] = "USER_AUTHORIZED_MAIN_HISTORICAL_SOP_NOT_DIAGNOSTIC_REPLACEMENT"
        result["terminal_quantity_input_contract"] = {
            "sides": ["BID", "ASK"], "window_ms": 500,
            "bid_stable_continuous_hold_ms": 100, "ask_stable_required_for_formation": False,
            "quantity_provenance": "RAW_FRAME_ROW_SIDE_GLOBAL_CHANGE_OR_ORIGINAL_SIDE_ARRAY_INDEX",
            "terminal_quantity_received_before_window_end": True,
            "terminal_quantity_key_after_all_counted_trades": True,
            "terminal_quantity_source_strictly_after_max_trade_source": True,
            "publication_lateness_max_ms": 50,
            "m16_speed_measurement_required": False, "m17_per_print_recheck_required": False}
    return result


class PublicSignals(Signals):
    def __init__(self, meta, clock):
        self.m1 = SessionFeed(
            meta, RDPObiConfig(), monotonic=clock.monotonic, wall=clock.time, book_only=True
        )
        self.views = {"PUBLIC_BOOK": self.m1}

    def drain(self):
        pass

    def tick(self):
        pass


class SopSignals:
    def __init__(self, engine):
        self.e = engine
        self.base = PublicSignals(engine.meta, engine.clock)
        if engine.strategy in {'M7','M9'}:
            from sop_signals.legacy_variant_feed import LegacyVariantFeed

            self.sop=LegacyVariantFeed(engine.meta.condition_id,
                (engine.meta.yes.token_id,engine.meta.no.token_id),engine.definition['ticks'],
                engine.emit,engine.parameters,strategy=engine.strategy)
            return
        self.sop = Feed(
            engine.meta.condition_id,
            (engine.meta.yes.token_id, engine.meta.no.token_id),
            engine.definition["ticks"],
            engine.emit,
            parameters=engine.parameters,
            strategy=engine.strategy,
            receiver_clock_evidence=engine.definition["settings"].get("sop_receiver_clock"),
        )

    def __getattr__(self, name):
        return getattr(self.base, name)

    def connect(self):
        self.base.connect()
        self.sop.connect(now=self.e.clock.mono, wall=self.e.clock.wall, seq=self.e.seq)

    def synchronize_clock(self):
        # GAP, REST tick responses and other non-frame inputs have availability
        # clocks too. Do not backdate invalidation/cancellation to the last frame.
        if self.sop.now != self.e.clock.mono or self.sop.seq != self.e.seq:
            self.sop.clock(now=self.e.clock.mono, wall=self.e.clock.wall, seq=self.e.seq)

    def ticks(self, ticks):
        self.synchronize_clock()
        self.base.ticks(ticks)
        for t, tick in ticks.items():
            b = self.sop.books[t]
            from decimal import Decimal

            b.tick, b.tick_verified = Decimal(str(tick)), True

    def gap(self, reason):
        self.synchronize_clock()
        self.base.gap(reason)
        self.sop.gap(reason)

    def apply(self, raw, received, wall, pending=()):
        self.e._decoded_parent_frame = None
        parsed = raw
        if raw not in ("PING", "PONG"):
            try:
                decoded = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
                rows = decoded if isinstance(decoded, list) else [decoded]
                if any(not isinstance(row, dict) for row in rows):
                    raise ValueError("SOP_RAW_EVENT_NOT_OBJECT")
            except (TypeError, ValueError) as exc:
                raise ValueError("SOP_RAW_FRAME_SHAPE") from exc
            # Both consumers accept decoded objects and leave them untouched.
            # Keep original raw bytes in the recorder; share only this current
            # validated parent to avoid parsing it four times on the hot path.
            parsed = decoded
            self.e._decoded_parent_frame = (self.e.seq, rows)
        self.base.apply(parsed, received, wall, pending)
        self.sop.apply(
            parsed,
            now=self.e.clock.mono,
            wall=self.e.clock.wall,
            received=received,
            seq=self.e.seq,
            pending=pending,
        )

    def tick(self):
        self.sop.timer(
            now=self.e.clock.mono,
            wall=self.e.clock.wall,
            seq=self.e.seq,
            pending=tuple(self.base.m1.m1_pending_received),
        )


class SopEngine(Engine):
    def __init__(self, directory, *, parameters=None, execution=None, sop_definition=None):
        super().__init__(directory)
        self.parameters = parameters or Parameters()
        self.strategy = strategy_from_parameters(self.parameters)
        self._decoded_parent_frame = None
        self.sop_runtime = None
        self.signal_history, self.assumed_orders = {}, {}
        self.execution = execution
        self.sop_definition = sop_definition

    def initialize_strategies(self, settings):
        if (settings.get("sop") != (self.sop_definition or definition(self.parameters))
                or settings.get("profiles") != {}):
            raise ValueError("SOP_DEFINITION_MISSING_OR_CHANGED")
        from m1223_sim_forward import execution_configuration

        if settings.get("sop_execution") != (self.execution or execution_configuration()):
            raise ValueError("SOP_EXECUTION_CONTRACT_MISSING_OR_CHANGED")
        if self.strategy == "M16":
            from sop_signals.m16_clock import check_clock_evidence

            if check_clock_evidence(settings.get("sop_receiver_clock"))["status"] != "LOCAL_QUANTIZATION_PROVEN":
                raise ValueError("M16_RECEIVER_CLOCK_EVIDENCE_REQUIRED")
        self.signals = SopSignals(self)
        self.sop_runtime = self.signals.sop
        self.signals.ticks(self.definition["ticks"])
        self.tick_verification = TickVerification(self.signals, self.clock, self.emit)
        if settings["sop_execution"].get("fill_model") == ANCHOR_FILL_MODEL:
            if settings["scenarios"] != {"anchor": {}}:
                raise ValueError("SOP_ANCHOR_ONLY_SCENARIO_REQUIRED")
            self.extra_policies.append(Policy(self))
            return
        if set(settings["scenarios"]) != {"trade-through"}:
            raise ValueError("SOP_REQUIRES_ONE_EXECUTION_MODEL")
        for name, params in settings["scenarios"].items():
            b = Broker("sop/" + name, self.emit, seed=settings["seed"],
                       initial_cash=settings["sop_execution"].get("session_budget_usdc", "20"),
                       **params)
            b.now = self.clock.mono
            self.brokers[b.scenario] = b
            b.account(self.strategy)
            b.update_constraints(self.tick_verification.constraints)
            self.extra_policies.append(Policy(self, b))

    def emit(self, kind, payload):
        super().emit(kind, payload)
        if kind == "SOP_EVIDENCE":
            event, fact = payload["event"], payload["payload"]
            if event == "SIGNAL_CONFIRMED":
                self.signal_history[fact["id"]] = dict(fact)
                # Compatibility projection only; the canonical SOP event remains
                # the single source of candidate identity in SOP reports.
                super().emit("SIGNAL", {"strategy": self.strategy, "event": "CANDIDATE", "payload": fact})
            elif event in {"SIGNAL_ENDED", "SIGNAL_SUSPENDED"}:
                if fact["id"] in self.signal_history:
                    history = self.signal_history[fact["id"]]
                    if event == "SIGNAL_ENDED":
                        history.update(ended=fact["available"], end_input_seq=self.seq,
                                       end_reason=fact["reason"])
                    else:
                        history.update(suspended=fact["available"], suspension_input_seq=self.seq,
                                       can_reference_for_new_action=False)
                for b in self.brokers.values():
                    for order in b.orders.values():
                        if order.intent.opportunity == fact["id"]:
                            b.cancel(order.id)
            elif event in {"SIGNAL_REFERENCE_READY", "SIGNAL_REFERENCE_PENDING"}:
                history = self.signal_history.get(fact["id"])
                if history is not None:
                    ready = event == "SIGNAL_REFERENCE_READY"
                    history.update(can_reference_for_new_action=ready,
                                   pending_rechecks=fact["pending_rechecks"],
                                   last_reference_update_available=fact["available"])
                    if ready and not history.get("ever_reference_ready"):
                        history.update(ever_reference_ready=True, first_reference_ready=fact["available"])
            elif event == "QUALITY_UNKNOWN":
                for b in self.brokers.values():
                    b.gap(fact["reason"], fact.get("token"))
        elif kind == "ASSUMED_FILL":
            self.assumed_orders[payload["order"]] = {**payload, "settled": False}
        elif kind == "MODELLED_FILL":
            order = self.brokers[payload["scenario"]].orders[payload["order"]]
            fact = self.signal_history.get(order.intent.opportunity)
            source = payload.get("source_mono")
            recheck_state = {}
            if self.strategy == "M17":
                current = (self.sop_runtime.core.signals.get(order.intent.opportunity)
                           if self.sop_runtime is not None else None)
                recheck_state = {
                    "pending_rechecks_at_evidence_availability": current.get("pending_rechecks") if current else None,
                    "last_known_pending_rechecks_before_end": (fact or {}).get("pending_rechecks") if not current else None,
                    "pending_rechecks_at_source_time": None,
                    "source_pending_state_reason": "NO_SHARED_EXCHANGE_AND_OBSERVER_STATE_CLOCK",
                    "pending_state_scope": "AVAILABILITY_STATE_NOT_EXCHANGE_MATCH_TIME",
                }
            attribution = fill_attribution(fact, source, order.intent.deadline, self.seq)
            if self.strategy == "M24" and fact and "suspended" in fact:
                recheck_state.update(signal_suspended_at=fact["suspended"],
                                     suspension_input_seq=fact["suspension_input_seq"])
                if (attribution["classification"] == "WITHIN_SIGNAL_VALIDITY"
                        and (source >= fact["suspended"] - 0.001
                             or self.seq == fact["suspension_input_seq"])):
                    attribution["classification"] = "SIGNAL_REFERENCE_UNPROVEN"
            self.store.emit(
                "SOP_FILL_ATTRIBUTION",
                {
                    "order": order.id,
                    "fill": payload,
                    "source_mono": source,
                    **attribution,
                    **recheck_state,
                    "preserved_in_economic_ledger": True,
                },
            )

    async def _ingest(self, event):
        try:
            await super()._ingest(event)
        finally:
            # No raw-parent cache survives a frame, error, timer, or settlement.
            # Matching runs inside the native ingest and may use this value.
            self._decoded_parent_frame = None
        if self.signals is not None:
            self.signals.synchronize_clock()
            for token, constraint in self.tick_verification.constraints.items():
                if not constraint.get("known"):
                    self.sop_runtime.books[token].tick_verified = False
                    self.sop_runtime.unavailable(token, "OFFICIAL_TICK_UNVERIFIED")
        if event["kind"] == "SETTLEMENT" and self.resolution:
            from decimal import Decimal

            for oid, order in self.assumed_orders.items():
                if order["settled"]:
                    continue
                i = order["intent"]
                cost = Decimal(i["quantity"]) * Decimal(i["price"])
                payout = (
                    Decimal(i["quantity"])
                    if i["outcome"] == self.resolution["winner"]
                    else Decimal(0)
                )
                order.update(
                    settled=True,
                    gross_pnl=str(payout - cost),
                    winner=self.resolution["winner"],
                    payout=str(payout),
                )
                self.emit("ASSUMED_SETTLEMENT", {"order": oid, **order})
            self.persist()

    def match_frame(self, event):
        raw = event["raw"]
        if raw in ("PING", "PONG"):
            return
        current = self._decoded_parent_frame
        if current is not None and current[0] == self.seq:
            rows = current[1]
        else:
            rows = json.loads(raw) if isinstance(raw, str) else raw
            rows = rows if isinstance(rows, list) else [rows]
        for row in rows:
            kind = row.get("event_type", "UNKNOWN")
            counts = self.coverage["source_event_counts"]
            counts[kind] = counts.get(kind, 0) + 1
            if kind == "tick_size_change":
                self.tick_verification.notice(
                    row, self.signals, self.clock, event["received_mono"], event["received_wall"]
                )
        self.update_constraints()
        if not self.brokers:
            return
        for index, row in enumerate(rows):
            if row.get("event_type") != "last_trade_price":
                continue
            token = row.get("asset_id")
            try:
                source = float(row["timestamp"]) / 1000
                if (
                    token not in self.signals.m1.books
                    or row.get("market") != self.meta.condition_id
                    or not -0.002 <= event["received_wall"] - source <= 0.5
                ):
                    raise ValueError("DIRECT_TRADE_SOURCE_UNPROVEN")
            except (KeyError, ValueError, TypeError, ArithmeticError):
                for b in self.brokers.values():
                    b.gap("DIRECT_TRADE_SOURCE_UNPROVEN", token)
                continue
            for b in self.brokers.values():
                b.trade(
                    row,
                    f"{self.seq}:{index}",
                    source_mono=(event["received_mono"] + source - event["received_wall"]),
                )

    async def end_observation(self, event):
        settings = self.definition["settings"]
        if settings.get("shared_capture") == "m122124-shared-public-72h-v1":
            gate = event["research_sample_eligibility"]
        else:
            from m511_sample_quality import own_market_gate

            project = settings.get("own_activity_project", "/home/ubuntu/polymaker")
            gate = own_market_gate(project, self.meta.slug)
        self.store.db.execute(
            "INSERT OR REPLACE INTO metadata VALUES('research_sample_eligibility',?)",
            (encode(gate),),
        )
        for b in self.brokers.values():
            for oid in b.orders:
                b.cancel(oid)
        await super().end_observation(event)

    def strategy_health(self):
        r = self.sop_runtime
        if self.strategy == "M24":
            model, = r.core.variants
            return {"M24": {"counts": dict(r.counts), "quality": dict(r.reasons),
                            "candidate_count": model.counts["SIGNAL_CONFIRMED"],
                            "formation_counts": dict(model.counts),
                            "retained_roots": len(model.roots)}}
        return {
            self.strategy: {
                "counts": dict(r.counts),
                "quality": dict(r.reasons),
                "candidate_count": r.core.counts["SIGNAL_CONFIRMED"],
                "retained_rounds": len(r.core.rounds),
                **({"retained_preload_roots":len(r.core.roots),
                    "retained_sell_presence_records":len(r.core.last_sell)} if self.strategy=="M23" else {}),
                **({"retained_campaigns": len(r.core.campaigns)} if self.strategy == "M18" else {}),
                **({"retained_ask_roots": len(r.core.roots),
                    "retained_closing_rounds": len(r.core.closing_rounds),
                    "pending_bid_publications": len(r.core.pending_bids)} if self.strategy == "M19" else {}),
                "retained_components": len(r.core.components),
                "retained_withdrawals": len(getattr(r.core, "withdrawals", {})),
                "retained_buy_records": sum(len(v) for v in getattr(r.core, "buy_history", {}).values()),
                "pending_retest_publications": len(getattr(r.core, "ready_retests", {})),
                **({"retained_recheck_records": sum(len(root.pending) for root in r.core.roots.values()),
                    "retained_root_trade_ids": sum(len(root.seen) for root in r.core.roots.values())}
                   if self.strategy == "M17" else {}),
            }
        }

    def mark_book_legal(self, book, now):
        return self.sop_runtime.legal(book.token_id)

    def snapshot(self):
        return {
            **super().snapshot(),
            "sop_signal_history": self.signal_history,
            "assumed_ledger": self.assumed_orders,
        }

    def retire_runtime(self):
        if not self.ended or not self.store.closed:
            raise RuntimeError("SOP_RETIRE_BEFORE_DURABLE_CLOSE")
        if self.sop_runtime:
            self.sop_runtime.release_runtime()
            self.sop_runtime = None
        self.signal_history.clear()
        self.assumed_orders.clear()
        self._decoded_parent_frame = None
        # The coordinator retains lightweight Engine identities for its final
        # report. Those identities must not retain the public book consumer or
        # the policy -> broker -> order graph after the durable seal. Pending
        # or uncertain economics remain in the closed audit/snapshot; deferred
        # settlement recovery uses that evidence, never these retired objects.
        self.extra_policies.clear()
        self.m1_policies.clear()
        self.brokers.clear()
        self.tick_verification = None
        self.last_books = None
        self.marks.clear()
        self.mark_targets.clear()
        self.signals = None

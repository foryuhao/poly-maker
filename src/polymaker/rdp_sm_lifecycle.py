"""Shared order primitives for the independent NORMAL/REBALANCE controllers.

Preparation may wait for disk and SDK reads. Activation consumes a new socket
event and uses the original signatures without waiting for a diagnostic writer.
"""

import asyncio
import time
import uuid
from contextlib import suppress
from dataclasses import asdict, dataclass, replace
from decimal import Decimal
from threading import Event

from py_clob_client_v2.order_builder.builder import ROUNDING_CONFIG

from polymaker.domain import Quote, Side
from polymaker.rdp_sm import _require
from polymaker.rdp_sm_execution import CAPITAL_LIMIT, Placement, WindowResult
from polymaker.rdp_sm_quotes import QuoteUnavailable
from polymaker.rdp_sm_signal import evaluate


@dataclass(frozen=True)
class Candidate:
    direction: str
    quotes: tuple
    signed: object
    sizing: dict


@dataclass(frozen=True)
class PreparedSlot:
    slot_id: str
    window_id: str
    phase: str
    snapshot: object
    yes: Decimal
    no: Decimal
    revision: int
    account: dict
    candidates: tuple
    prepared_at: float
    persisted_at: float = 0


@dataclass(frozen=True)
class ExecutionIntent:
    window_id: str
    slot_id: str
    phase: str
    direction: str
    snapshot: object
    yes: Decimal
    no: Decimal
    revision: int
    quotes: tuple
    deadline: float | None
    claim: tuple | None
    prepared_at: float
    persisted_at: float
    frozen_at: float
    episode_start: float | None


class BatchLifecycle:
    def __init__(self, executor):
        self.executor = executor
        self.feed = executor.reads.feed
        self.used_slots = set()
        self.busy = False
        self.metrics = {"prepared": 0, "hit": 0, "posted": 0, "invalidated": 0, "not_ready": 0}
        self.account = None
        self.cached_slot = None
        self.first_eligible = {}
        self.final_evidence = None
        self.guard_reason = None
        self.wait_reason = None
        self.prepared_ids = set()

    def wait(self, reason, snapshot, health=None):
        diagnostics = self.feed.diagnostics()
        key = (reason, snapshot.generation, diagnostics["failure_count"])
        if key == self.wait_reason:
            return
        self.wait_reason = key
        self.executor.record(
            "sm_obi_wait",
            {
                "reason": reason,
                "snapshot_id": snapshot.snapshot_id,
                "phase_inventory": self.executor.inventory_observation,
                "signal": asdict(evaluate(snapshot, self.executor.cfg.rdp_sm.obi.threshold)),
                "ws_diagnostics": diagnostics,
                "health": asdict(health) if health else None,
                "metrics": dict(self.metrics),
            },
        )

    def clock(self):
        e = self.executor
        return {
            "now": e.official_time + e.monotonic() - e.time_anchor,
            "official_time": e.official_time,
            "official_time_read_duration_s": e.official_time_read_duration_s,
            "max_age": e.cfg.rdp_sm.obi.max_book_age_ms / 1000,
        }

    def snapshot(self, controller, yes, no):
        meta = self.executor.meta
        repair = (
            (meta.no.token_id if yes > no else meta.yes.token_id)
            if controller.phase == "REBALANCE"
            else None
        )
        return self.feed.snapshot(repair_token=repair)

    async def prepare(self, controller, yes, no, window_id):
        e = self.executor
        _require(not self.busy, "BATCH_ALREADY_ACTIVE")
        _require(window_id not in e.used_windows, "WINDOW_ID_ALREADY_USED")
        _require(not e.attempts or e.safe_to_continue, "PREVIOUS_WINDOW_UNRESOLVED")
        _require(e.journal.ready, "ASYNC_JOURNAL_NOT_READY")
        snapshot = self.snapshot(controller, yes, no)
        if not snapshot.valid:
            self.wait(snapshot.reason, snapshot)
            return None
        direction, signal = controller.select(snapshot)
        health = e.network_health()
        e.drain_network_events()
        if not health.allowed:
            self.wait(health.reason, snapshot, health)
            return None
        if signal and direction:
            self.first_eligible.setdefault(self.feed.episodes.episode_id, snapshot.signal_event)
        cached = self.cached_slot
        if (
            cached
            and cached.slot_id not in self.used_slots
            and cached.phase == controller.phase
            and cached.window_id == window_id
            and cached.revision == e.revision
            and cached.yes == yes
            and cached.no == no
            and cached.snapshot.generation == snapshot.generation
            and all(
                (a.tick, a.best_bid, a.best_ask) == (b.tick, b.best_bid, b.best_ask)
                for a, b in zip(
                    (cached.snapshot.yes, cached.snapshot.no),
                    (snapshot.yes, snapshot.no),
                    strict=True,
                )
            )
            and cached.account["basic_spendable_budget_usd"]
            == self.account["basic_spendable_budget_usd"]
        ):
            # Re-evaluate Favorite/sizing as well as price, without re-signing.
            plans = controller.candidates(snapshot, yes, no, cached.window_id)
            if all(
                c.direction in plans and plans[c.direction][0] == c.quotes
                for c in cached.candidates
            ):
                return cached
        try:
            plans = controller.candidates(snapshot, yes, no, window_id)
        except QuoteUnavailable as exc:
            self.wait(str(exc), snapshot)
            return None
        if not plans:
            self.wait("NO_LEGAL_CANDIDATE", snapshot)
            return None
        # Warm SDK constraints in preparation, and require agreement with the WS.
        client = e.gateway._client
        ticks = {}
        for token in self.feed.books:
            client._ClobClient__tick_sizes.pop(token, None)
            ticks[token] = float(await e.gateway._io(client.get_tick_size, token))
        try:
            self.feed.verify_ticks(ticks)
        except ValueError as exc:
            self.wait(str(exc), snapshot)
            return None  # REST/WS tick transition is a retryable preparation pause.
        _require(
            await e.gateway._io(client._ClobClient__resolve_version) == 2,
            "M2_ORDER_VERSION_UNSUPPORTED",
        )
        candidates = []
        for direction, (quotes, sizing) in plans.items():
            for q in quotes:
                e._validate(q, window_id, snapshot.books(), e.meta)
                _require(f"{ticks[q.token_id]:g}" in ROUNDING_CONFIG, "SDK_TICK_UNSUPPORTED")
                _require(
                    Decimal(str(q.price)) * Decimal(str(q.size)) >= 1,
                    "MAKER_BUY_NOTIONAL_BELOW_MINIMUM",
                )
            commitment = sum(
                (Decimal(str(q.price)) * Decimal(str(q.size)) for q in quotes), Decimal(0)
            )
            # NORMAL budgets a full dollar per submitted share for the cycle.
            capital_required = (
                sum((Decimal(str(q.size)) * Decimal("1.00") for q in quotes), Decimal(0))
                if controller.phase == "NORMAL" else commitment
            )
            if capital_required + e.committed() > CAPITAL_LIMIT or commitment > Decimal(
                str(self.account["basic_spendable_budget_usd"])
            ):
                continue
            signed = await e.gateway.prepare(
                [Quote(q.token_id, q.side, q.price, q.size) for q in quotes], e.meta, ticks
            )
            candidates.append(Candidate(direction, quotes, signed, dict(sizing)))
        if not candidates:
            self.wait("OFFICIAL_OR_SESSION_CAPITAL_INSUFFICIENT", snapshot)
            return None
        ids = [oid for c in candidates for oid in c.signed.order_ids]
        _require(
            len(ids) == len(set(ids))
            and not self.prepared_ids.intersection(ids)
            and not e.known.intersection(ids),
            "PREPARED_ID_ALREADY_USED",
        )
        slot = PreparedSlot(
            uuid.uuid4().hex,
            window_id,
            controller.phase,
            snapshot,
            yes,
            no,
            e.revision,
            dict(self.account),
            tuple(candidates),
            e.monotonic(),
        )
        # A single WAL record covers all mutually exclusive possibilities. It
        # contains identities/economics, never reusable signatures or API keys.
        e.preparation_started = True
        await e.journal.durable(
            "sm_prepared",
            {
                "session_id": e.session_id,
                "payload": {
                    "slot_id": slot.slot_id,
                    "window_id": window_id,
                    "phase": slot.phase,
                    "snapshot_id": snapshot.snapshot_id,
                    "generation": snapshot.generation,
                    "revision": slot.revision,
                    "yes": str(yes),
                    "no": str(no),
                    "candidates": [
                        {
                            "direction": c.direction,
                            "order_ids": c.signed.order_ids,
                            "quotes": [asdict(q) for q in c.quotes],
                            "body_hash": c.signed.body_hash,
                        }
                        for c in candidates
                    ],
                    "risk_upper_bound": str(
                        max(
                            sum(
                                (Decimal(str(q.price)) * Decimal(str(q.size)) for q in c.quotes),
                                Decimal(0),
                            )
                            for c in candidates
                        )
                    ),
                    "status": "POSSIBLY_SENT; NOT_AN_ACK",
                },
            },
            time.time(),
        )
        self.metrics["prepared"] += 1
        slot = replace(slot, persisted_at=e.monotonic())
        self.cached_slot = slot
        self.prepared_ids.update(ids)
        return slot

    def guard(self, controller, intent, revision, deadline):
        e, cfg = self.executor, self.executor.cfg.rdp_sm.obi
        if e.shared_reads is not None and not e.shared_reads.admission_current():
            self.guard_reason = "SYNC_CACHE_EVIDENCE_CHANGED"
            return False
        now = e.monotonic()
        current = self.snapshot(controller, intent.yes, intent.no)
        elapsed = e.official_time + now - e.time_anchor - int(e.slug.rsplit("-", 1)[1])
        age = (now - intent.snapshot.signal_event) * 1000
        health = e.network_health(age if intent.phase == "NORMAL" else 0)
        self.final_evidence = (current, health)
        checks = [
            ("SESSION_CLOSED", not e.closed),
            (e.fatal or "FATAL_EXECUTION_STATE", e.fatal is None),
            ("ASYNC_JOURNAL_NOT_READY", e.journal.ready),
            ("CAPITAL_EVIDENCE_CHANGED_BEFORE_SUBMIT", e.revision == revision),
            ("ACCOUNT_PAGINATION_UNRESOLVED", e.gateway._client.pagination_failure is None),
            ("ACCOUNT_EVIDENCE_STALE", e.monotonic() - e.time_anchor <= e.cfg.risk.ws_stale_halt_s),
            ("MARKET_SUBMISSION_CUTOFF", 15 <= elapsed < controller.cutoff),
            (current.reason or "WS_INVALID", current.valid),
            ("WS_GENERATION_CHANGED", current.generation == intent.snapshot.generation),
            (
                "WS_TICK_CHANGED",
                all(
                    a.tick == b.tick
                    for a, b in zip(
                        (current.yes, current.no),
                        (intent.snapshot.yes, intent.snapshot.no),
                        strict=True,
                    )
                ),
            ),
            ("INTENT_EXPIRED", deadline[0] is None or now < deadline[0]),
            (
                "SIGNAL_STALE_OR_EPISODE_CHANGED",
                (
                    intent.phase != "NORMAL"
                    or 0 <= age <= cfg.max_signal_age_ms
                    and self.feed.episodes.current(intent.claim)
                ),
            ),
            (health.reason, health.allowed),
            ("FINAL_QUOTES_OR_DIRECTION_CHANGED", controller.matches(intent, current)),
        ]
        self.guard_reason = next((reason for reason, valid in checks if not valid), None)
        return self.guard_reason is None

    async def activate(self, controller, slot):
        e = self.executor
        if slot.slot_id in self.used_slots:
            raise ValueError("PREPARED_SLOT_ALREADY_USED")
        # The candidate was persisted before this event. Never retime an old
        # socket event to hide preparation or writer latency.
        sequence = self.feed.sequence
        await self.feed.wait_after(sequence, 0.5)
        snapshot = self.snapshot(controller, slot.yes, slot.no)
        direction, signal = controller.select(snapshot)
        if (
            snapshot.sequence <= slot.snapshot.sequence
            or snapshot.sequence <= sequence
            or not snapshot.valid
        ):
            self.metrics["not_ready"] += 1
            self.wait(snapshot.reason or "NO_NEW_BOOK_EVENT", snapshot)
            return None
        candidate = next((c for c in slot.candidates if c.direction == direction), None)
        if candidate is None:
            self.wait("NO_LEGAL_SIGNAL_CANDIDATE" if direction else "OBI_NO_SIGNAL", snapshot)
            return None
        claim = self.feed.episodes.claim(signal) if signal else None
        if signal and claim is None:
            self.wait("SIGNAL_EPISODE_UNAVAILABLE", snapshot)
            return None
        intent = ExecutionIntent(
            slot.window_id,
            slot.slot_id,
            slot.phase,
            direction,
            snapshot,
            slot.yes,
            slot.no,
            slot.revision,
            candidate.quotes,
            controller.deadline(snapshot) if slot.phase == "NORMAL" else None,
            claim,
            slot.prepared_at,
            slot.persisted_at,
            e.monotonic(),
            self.feed.episodes.episode_start,
        )
        activated = False
        try:
            async with e.lock:
                reason = next((name for name, failed in (
                    ("BATCH_ALREADY_ACTIVE", self.busy),
                    ("CAPITAL_EVIDENCE_CHANGED_BEFORE_SUBMIT", e.revision != slot.revision),
                    ("ASYNC_JOURNAL_NOT_READY", not e.journal.ready),
                    ("PREVIOUS_WINDOW_UNRESOLVED", bool(e.attempts and not e.safe_to_continue)),
                    ("FINAL_QUOTES_OR_DIRECTION_CHANGED", not controller.matches(intent, snapshot)),
                ) if failed), None)
                if reason:
                    self.metrics["invalidated"] += 1
                    self.wait(reason, snapshot)
                    return None
                self.used_slots.add(slot.slot_id)
                _require(slot.window_id not in e.used_windows, "WINDOW_ID_ALREADY_USED")
                e.used_windows.add(slot.window_id)
                self.busy = True
                activated = True
                self.metrics["hit"] += 1
                return await self._send(controller, intent, candidate, signal)
        finally:
            if activated:
                self.busy = False
            if claim:
                self.feed.episodes.release(claim)

    async def _send(self, controller, intent, candidate, signal):
        e, client = self.executor, self.executor.gateway._client
        result = WindowResult(
            e.session_id,
            intent.window_id,
            e.meta.condition_id,
            time.time(),
            requested_quotes=list(intent.quotes),
        )
        batch = [
            Placement(uuid.uuid4().hex, q, expected_order_id=oid)
            for q, oid in zip(intent.quotes, candidate.signed.order_ids, strict=True)
        ]
        e.attempts.extend(batch)
        e.invalidate("PREPARED_ACTIVATION")
        revision = e.revision
        deadline = [intent.deadline]
        close_requested = Event()
        post_done = asyncio.Event()
        verification_done = asyncio.Event()
        prospective = list(candidate.signed.order_ids)
        client.known_ids.update(prospective)  # cancel permission, not acceptance
        client.batch_attempts, client.attempt = batch, batch[0]
        client.network_batch_id = intent.window_id
        client.submit_guard = lambda: (
            not close_requested.is_set() and self.guard(controller, intent, revision, deadline)
        )
        post_start = [None]
        final_evidence = [None]
        close_evidence = [None]

        def request_close(reason, **evidence):
            if not close_requested.is_set():
                close_requested.set()
                close_evidence[0] = {
                    "window_id": intent.window_id, "reason": reason,
                    "requested_monotonic": e.monotonic(), "deadline": deadline[0],
                    **evidence,
                }
                # A diagnostic failure must never prevent cancellation.
                with suppress(Exception):
                    e.record("sm_obi_close_requested", close_evidence[0])

        def on_post_start():
            final_evidence[0] = self.final_evidence
            _require(e.reads.network.begin_batch(intent.window_id), "NETWORK_ADMISSION_CHANGED")
            if intent.claim:
                self.feed.episodes.consume(intent.claim)
            post_start[0] = e.monotonic()
            self.metrics["posted"] += 1
            if deadline[0] is None:
                deadline[0] = post_start[0] + e.cfg.rdp_sm.obi.rebalance_order_ttl_sec

        client.on_post_start = on_post_start

        async def cancel_possible():
            began = e.monotonic()
            raw = await e.gateway.cancel_detailed(prospective)
            e.record(
                "sm_cancel_ack",
                {
                    "ids": list(prospective),
                    "result": asdict(raw),
                    "delete_requested": began,
                    "delete_sdk_started": raw.sdk_started_monotonic,
                    "delete_ack": raw.ack_monotonic,
                    "deadline_lag_s": (
                        max(0, raw.sdk_started_monotonic - deadline[0])
                        if deadline[0] is not None and raw.sdk_started_monotonic is not None
                        else None
                    ),
                },
            )
            # An early not-found is never a terminal proof. Repeat through a
            # late acceptance and reconcile official facts after POST drains.

        async def watch():
            while True:
                now = e.monotonic()
                current = self.snapshot(controller, intent.yes, intent.no)
                elapsed = e.official_time + now - e.time_anchor - int(e.slug.rsplit("-", 1)[1])
                health = e.network_health()
                e.drain_network_events()
                normal = intent.phase == "NORMAL"
                # Entry still requires fresh books. Once sent, a quiet book
                # alone does not revoke the original fixed order lease.
                invalid = not current.valid and not (
                    normal and current.reason == "WS_BOOK_STALE"
                )
                active_signal = evaluate(current, e.cfg.rdp_sm.obi.threshold) if normal else None
                signal_age_ms = (now - current.signal_event) * 1000
                reversed_signal = (
                    active_signal is not None
                    and active_signal.direction in {"YES", "NO"}
                    and active_signal.direction != intent.direction
                    and current.signal_event > intent.snapshot.signal_event
                    and 0 <= signal_age_ms <= e.cfg.rdp_sm.obi.max_signal_age_ms
                )
                reason = next((name for name, failed in (
                    ("ORDER_DEADLINE", deadline[0] is not None and now >= deadline[0]),
                    (current.reason or "WS_INVALID", invalid),
                    ("NETWORK_EXECUTION_FAILURE", health.close_active),
                    ("ASYNC_JOURNAL_NOT_READY", not e.journal.ready),
                    (e.fatal or "FATAL_EXECUTION_STATE", e.fatal is not None),
                    ("MARKET_SUBMISSION_CUTOFF", elapsed >= controller.cutoff),
                    ("SIGNAL_REVERSED", reversed_signal),
                ) if failed), None)
                if reason:
                    evidence = {}
                    if reason == "SIGNAL_REVERSED":
                        evidence = {
                            "original_direction": intent.direction,
                            "signal": asdict(active_signal),
                            "signal_age_ms": signal_age_ms,
                            "snapshot": current.evidence(),
                        }
                    request_close(
                        reason, snapshot_id=current.snapshot_id, health=asdict(health), **evidence
                    )
                if close_requested.is_set() and any(a.post_started for a in batch):
                    if (e.shared_reads is not None and post_done.is_set()
                            and e.shared_reads.terminal_observed(prospective)):
                        return  # The final close still verifies fills and reserves.
                    await cancel_possible()
                    if verification_done.is_set():
                        return
                    await asyncio.sleep(e.cfg.rdp_sm.obi.cancel_retry_sec)
                elif post_done.is_set() and (
                    close_requested.is_set() or not all(a.outcome == "ACK" for a in batch)
                ):
                    request_close("PLACEMENT_NOT_ACKNOWLEDGED")
                    if not any(a.post_started for a in batch):
                        return
                else:
                    await asyncio.sleep(0.005)

        watcher = asyncio.create_task(watch(), name="rdp-persistent-close")
        raw, error = None, None
        write = e.shared_reads.begin_write("POST", prospective) if e.shared_reads else None
        try:
            try:
                raw = await e._await_owned(
                    e.gateway.send_prepared(candidate.signed, submit_guard=client.submit_guard),
                    propagate_cancel=False,
                )
            except Exception as exc:
                error = type(exc).__name__
            finally:
                if write is not None:
                    e.shared_reads.end_write(write)
            post_returned = e.monotonic()
            outcomes = (
                e._placement_outcomes(raw, candidate.signed.quotes)
                if batch[0].post_started
                else [("ABORTED", None)] * len(batch)
            )
            # Register EVERY possible ID before any ACK diagnostics or awaits.
            for attempt, (outcome, order) in zip(batch, outcomes, strict=True):
                attempt.outcome = outcome
                if not attempt.post_started or outcome == "REJECTED":
                    continue
                oid = attempt.expected_order_id
                attempt.order_id = oid
                e.known.add(oid)
                e.processor.register_order(
                    oid,
                    e.meta.condition_id,
                    attempt.quote.token_id,
                    Side.BUY,
                    attempt.quote.size,
                    attempt.quote.price,
                    "rdp_sm_prepared",
                    quiet=True,
                )
                if outcome == "ACK" and order.order_id != oid:
                    attempt.outcome = "UNKNOWN"
                    e.fatal = "SIGNED_ORDER_ID_MISMATCH"
                elif outcome == "ACK":
                    e.store.upsert_order(order)
            if not all(a.outcome == "ACK" for a in batch):
                request_close("PLACEMENT_NOT_ACKNOWLEDGED", outcomes=[a.outcome for a in batch])
            post_done.set()
            if close_requested.is_set() and batch[0].post_started:
                await cancel_possible()
            # Diagnostics only follow the operation and complete ID registration.
            anchor_book = max((intent.snapshot.yes, intent.snapshot.no), key=lambda b: b.received_mono)
            post_wall = (
                (
                    anchor_book.received_wall
                    + post_start[0]
                    - anchor_book.received_mono
                )
                if post_start[0] is not None
                else time.time()
            )
            e.record(
                "sm_strategy_window",
                {
                    "window_id": intent.window_id,
                    "state": intent.phase,
                    "delta": str(intent.yes - intent.no),
                    "inventory_yes": str(intent.yes),
                    "inventory_no": str(intent.no),
                    "books": {label: {**b.as_book(), "bid_levels_count": len(b.bids),
                                      "ask_levels_count": len(b.asks)}
                              for label, b in zip(("YES", "NO"),
                                  (final_evidence[0][0].yes, final_evidence[0][0].no)
                                  if final_evidence[0] else (intent.snapshot.yes, intent.snapshot.no),
                                  strict=True)},
                    "elapsed_s": e.official_time
                    + (post_start[0] or e.monotonic())
                    - e.time_anchor
                    - int(e.slug.rsplit("-", 1)[1]),
                    "quotes": [asdict(q) for q in intent.quotes],
                    "sizing": candidate.sizing,
                    "decision_source": "OBI_EXECUTION_WS; observer slots are comparison only",
                    "execution_snapshot_id": final_evidence[0][0].snapshot_id
                    if final_evidence[0]
                    else None,
                    "post_started": batch[0].post_started,
                },
                event_wall=post_wall,
            )
            e.record(
                "sm_obi_final_decision",
                {
                    "mode": "obi_v1",
                    "intent": {
                        "phase": intent.phase,
                        "window_id": intent.window_id,
                        "slot_id": intent.slot_id,
                        "snapshot_id": intent.snapshot.snapshot_id,
                        "snapshot": intent.snapshot.evidence(),
                        "final_snapshot": final_evidence[0][0].evidence()
                        if final_evidence[0]
                        else None,
                        "final_signal": (
                            asdict(controller.select(final_evidence[0][0])[1])
                            if final_evidence[0] and intent.phase == "NORMAL"
                            else None
                        ),
                        "signal": asdict(signal) if signal else None,
                        "claim": intent.claim,
                        "quotes": [asdict(q) for q in intent.quotes],
                        "offset_ticks": {
                            q.outcome: str(
                                (
                                    Decimal(str(q.price))
                                    - intent.snapshot.books()[q.token_id]["best_bid"]
                                )
                                / Decimal(str(intent.snapshot.books()[q.token_id]["tick"]))
                            )
                            for q in intent.quotes
                        },
                        "book_age_ms": {
                            b.token_id: (post_start[0] - b.received_mono) * 1000
                            for b in (intent.snapshot.yes, intent.snapshot.no)
                        }
                        if post_start[0] is not None
                        else None,
                        "revision": revision,
                        "prepared_at": intent.prepared_at,
                        "persisted_at": intent.persisted_at,
                        "frozen_at": intent.frozen_at,
                        "episode_start": intent.episode_start,
                        "first_eligible_event": self.first_eligible.get(intent.claim[0])
                        if intent.claim
                        else None,
                        "signal_event": intent.snapshot.signal_event,
                        "post_started": post_start[0],
                        "deadline": deadline[0],
                        "post_returned": post_returned,
                        "post_error": error,
                        "aborted_reason": self.guard_reason if not batch[0].post_started else None,
                        "health": asdict(final_evidence[0][1]) if final_evidence[0] else None,
                        "metrics": dict(self.metrics),
                        "pair_price_sum": str(
                            sum((Decimal(str(q.price)) for q in intent.quotes), Decimal(0))
                        ) if len(intent.quotes) == 2 else None,
                        "order_policy": candidate.sizing.get("order_policy", "DEFICIT_SIDE_ONLY"),
                        "residual_before": str(intent.yes - intent.no),
                    },
                    "sizing": candidate.sizing,
                },
            )
            for attempt in batch:
                e.record("sm_placement_result", asdict(attempt))

            async def await_close_request():
                while not close_requested.is_set():
                    if watcher.done():
                        watcher.result()
                        return
                    await asyncio.sleep(0.005)

            await e._await_owned(await_close_request(), propagate_cancel=False)
            safe = await e._await_owned(e._verify_close(cancel=True), propagate_cancel=False)
            if not safe and e.pending_confirmations:
                await e._await_owned(e._wait_for_confirmation(), propagate_cancel=False)
            verification_done.set()
            await e.journal.flush()
        finally:
            client.on_post_start = None
            client.attempt, client.batch_attempts = None, None
            if not close_requested.is_set():
                request_close("EXECUTION_CLEANUP")
            post_done.set()
            verification_done.set()
            if not watcher.done():

                async def drain_watcher():
                    await asyncio.shield(watcher)

                await e._await_owned(drain_watcher(), propagate_cancel=False)
            watcher.result()
            e.reads.network.complete_batch(
                intent.window_id, safe=not any(a.post_started for a in batch) or e.safe_to_continue,
            )
            client.network_batch_id = None
            e.drain_network_events()
        result.t0_monotonic = (
            intent.snapshot.signal_event if intent.phase == "NORMAL" else post_start[0]
        )
        result.fill_deadline_monotonic = deadline[0]
        result.close_monotonic = e.monotonic()
        result.end_time = time.time()
        result.placement_results = [asdict(a) for a in batch]
        result.known_order_ids = sorted(e.known)
        result.confirmed_fills = [asdict(f) for f in e.confirmed()]
        result.yes_inventory, result.no_inventory = (
            float(
                sum((Decimal(str(f.size)) for f in e.confirmed() if f.token_id == t), Decimal(0))
                - e.merged_pairs
            )
            for t in (e.meta.yes.token_id, e.meta.no.token_id)
        )
        result.delta = result.yes_inventory - result.no_inventory
        result.capital_committed = float(e.committed())
        result.close_barrier = "SAFE_TO_CONTINUE" if e.safe_to_continue else "BLOCKED"
        result.block_reason = (
            e.reason or e.fatal or (self.guard_reason if not batch[0].post_started else None)
        )
        e.record(
            "sm_obi_signal_changes",
            {
                "window_id": intent.window_id,
                "policy": (
                    "FIXED_DEADLINE; fresh valid opposite signal cancels; "
                    "NONE or WS_BOOK_STALE keeps active order; no reprice or automatic flip"
                    if intent.phase == "NORMAL"
                    else "FIXED_DEADLINE; REBALANCE independent of OBI"
                ),
                **self.feed.changes_since(intent.snapshot.signal_event),
            },
        )
        e.record("sm_window_result", result.to_dict())
        await e.journal.flush()
        return result, candidate.sizing

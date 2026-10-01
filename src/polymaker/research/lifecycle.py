"""M4A.3 one-market, observation-only Episode and StateSnapshot projection."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections import deque
from decimal import ROUND_FLOOR, Decimal
from typing import Any

from polymaker.research.capture import CausalCapture
from polymaker.research.schema import Episode, FeatureVector, ResearchEvent, StateSnapshot


def dec(value: Any) -> Decimal:
    return Decimal(str(value))


def ds(value: Decimal | float | int) -> str:
    return str(value)


class EpisodeLifecycle:
    """Project admitted evidence only. All mutation runs under CausalCapture's lock."""

    def __init__(self, capture: CausalCapture, *, dataset_id: str, market_slug: str,
                 condition_id: str, yes_token_id: str, no_token_id: str,
                 market_start_us: int, market_end_us: int, strategy_version: str,
                 strategy_config_id: str, venue_min_order_qty: float,
                 forced_pair_s: float = 240, order_cutoff_s: float = 270,
                 interval_s: float = 1, prefill_s: float = 20) -> None:
        self.capture = capture
        self.dataset_id = dataset_id
        self.market_slug = market_slug
        self.condition_id = condition_id
        self.yes_token_id = yes_token_id
        self.no_token_id = no_token_id
        self.market_start_us = market_start_us
        self.market_end_us = market_end_us
        self.strategy_version = strategy_version
        self.strategy_config_id = strategy_config_id
        self.venue_min_order_qty = venue_min_order_qty
        self.forced_pair_s = forced_pair_s
        self.order_cutoff_s = order_cutoff_s
        self.interval_ns = int(interval_s * 1_000_000_000)
        self.prefill_us = int(prefill_s * 1_000_000)
        self.prefill: deque[tuple[int, int]] = deque(maxlen=4096)
        self.books: dict[str, dict[str, Any]] = {}
        self.ticks: dict[str, str] = {}
        self.orders: dict[str, dict[str, Any]] = {}
        self.fills: dict[tuple[str, str], dict[str, Any]] = {}
        self.episode_id: str | None = None
        self.revision = 0
        self.snapshot_seq = 0
        self.first_event: ResearchEvent | None = None
        self.capture_start_us: int | None = None
        self.first_side: str | None = None
        self.first_order_id: str | None = None
        self.first_finalized_at_us: int | None = None
        self.pair_complete_at_us: int | None = None
        self.pair_start_at_us: int | None = None
        self.baseline_state = "UNKNOWN"
        self.baseline_trigger: str | None = None
        self.baseline_peak: str | None = None
        self.baseline_quote: dict[str, Any] | None = None
        self.effective_quote: dict[str, Any] | None = None
        self.first_finalized = False
        self.pair_terminal = False
        self.fill_truth_ready = False
        self.order_state_resolved = True
        self.unresolved_order_ids: set[str] = set()
        self.market_recovery_pending = False
        self.market_connected = False
        self.user_connected = False
        self.market_generation = 0
        self.user_generation = 0
        self.safety_reasons: set[str] = set()
        self.quality_reasons: set[str] = set()
        self.peak_quoted: Decimal | None = None
        self.capture_status = "OPEN"
        self.next_deadline_ns: int | None = None
        self.first_monotonic_ns: int | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task[None] | None = None
        self._closed = False
        capture.add_listener(self._on_event)

    def start(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        self._loop = loop or asyncio.get_running_loop()
        if self.episode_id is not None:
            self._ensure_task()

    def _ensure_task(self) -> None:
        if self._loop is not None and self._task is None and not self._closed:
            self._task = self._loop.create_task(self._schedule(), name="research-snapshots")

    async def _schedule(self) -> None:
        try:
            while not self._closed and self.next_deadline_ns is not None:
                wait_s = max(0.0, (self.next_deadline_ns - time.monotonic_ns()) / 1e9)
                await asyncio.sleep(wait_s)
                self.capture.synchronized(lambda seq, us, ns: self._tick(seq, us, ns))
                if self.capture_status != "OPEN":
                    return
        except asyncio.CancelledError:
            raise
        except Exception:
            self.capture.failures += 1
            self.capture.synchronized(lambda seq, us, ns: self._finish(seq, us, ns,
                "CENSORED", "SCHEDULER_FAILURE"))

    def tick(self, now_us: int, monotonic_ns: int) -> None:
        """Deterministic scheduler seam; production uses _schedule."""
        self.capture.synchronized(lambda seq, _us, _ns: self._tick(seq, now_us, monotonic_ns))

    def _tick(self, seq: int, now_us: int, mono: int) -> None:
        if self.episode_id is None or self.capture_status != "OPEN":
            return
        if self.next_deadline_ns is None:
            return
        if mono >= self.next_deadline_ns and now_us <= self.market_end_us:
            assert self.first_event is not None
            scheduled_us = (self.first_event.available_at_us
                + (self.next_deadline_ns - (self.first_monotonic_ns or 0)) // 1000)
            self._snapshot("PERIODIC", seq, now_us, mono, scheduled_us)
            advance = 1 + max(0, (mono - self.next_deadline_ns) // self.interval_ns)
            if advance > 1:
                self.quality_reasons.add("SCHEDULED_SNAPSHOT_GAP")
            self.next_deadline_ns += self.interval_ns * advance
        if now_us >= self.market_end_us:
            interrupted = (not self.market_connected or not self.user_connected
                or bool(self.quality_reasons & {"MARKET_DISCONNECT", "USER_DISCONNECT",
                    "SCHEDULED_SNAPSHOT_GAP"}))
            self._finish(seq, now_us, mono, "CENSORED" if interrupted else "COMPLETE",
                ("SCHEDULED_SNAPSHOT_GAP" if "SCHEDULED_SNAPSHOT_GAP" in self.quality_reasons
                 else "INTERRUPTED_CONNECTION") if interrupted else None)

    async def close(self, reason: str = "PROCESS_SHUTDOWN") -> None:
        self._closed = True
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        def finish(seq: int, us: int, ns: int) -> None:
            if reason != "WRITER_FAILURE":
                self._tick(seq, us, ns)
            self._finish(seq, us, ns,
                "COMPLETE" if us >= self.market_end_us and reason != "WRITER_FAILURE" else "CENSORED",
                None if us >= self.market_end_us and reason != "WRITER_FAILURE" else reason)
        self.capture.synchronized(finish)
        self.prefill.clear()

    def _on_event(self, event: ResearchEvent) -> None:
        if (event.condition_id not in (self.condition_id, "") or self._closed
                or self.capture_status != "OPEN"):
            return
        if self.capture.lost_events:
            self.quality_reasons.add("RESEARCH_PERSISTENCE_GAP")
        if self.episode_id is None:
            self.prefill.append((event.event_seq, event.available_at_us))
            while self.prefill and event.available_at_us - self.prefill[0][1] > self.prefill_us:
                self.prefill.popleft()
        payload = event.payload
        kind = event.kind
        order_changed = False
        terminal_baseline_changed = False
        if kind in ("BOOK", "REST_BOOK"):
            self._book(event, replace=True)
        elif kind == "DELTA":
            self._book(event, replace=False)
        elif kind == "TICK":
            token = str(payload.get("token_id", ""))
            if token in (self.yes_token_id, self.no_token_id):
                self.ticks[token] = ds(payload["tick_size"])
        elif kind == "CONNECTION":
            stream = payload.get("stream")
            up = payload.get("state") == "UP"
            if stream == "market":
                self.market_connected = up
                self.market_generation = event.connection_generation or 0
                if not up:
                    self.quality_reasons.add("MARKET_DISCONNECT")
                    for book in self.books.values():
                        book["valid"] = False
            elif stream == "user":
                self.user_connected = up
                self.user_generation = event.connection_generation or 0
                if not up:
                    self.quality_reasons.add("USER_DISCONNECT")
        elif kind in ("ORDER_SUBMIT", "ORDER_ACK", "CANCEL_REQUEST", "CANCEL_ACK", "ORDER_STATE"):
            order_changed = self._order(event)
        elif kind == "BASELINE_DECISION":
            phase = payload.get("phase")
            if phase is None:
                prior_state = self.baseline_state
                self.baseline_state = str(payload.get("strategy_state", "UNKNOWN"))
                self.baseline_trigger = payload.get("pair_trigger")
                self.baseline_peak = (ds(payload["peak_locked_pnl"])
                    if payload.get("peak_locked_pnl") is not None else None)
                self.baseline_quote = self._quote(payload.get("pure_quote"))
                if self.episode_id is not None and self.baseline_state != prior_state and self.baseline_state in (
                    "COMPLETE", "HALTED", "UNPAIRABLE_RESIDUAL"):
                    self._revision(event.available_at_us)
                    terminal_baseline_changed = True
            elif phase == "EFFECTIVE_QUOTE":
                self.effective_quote = self._quote(payload.get("quote"))
        elif kind == "SAFETY":
            self._safety(event)
            if self.episode_id is not None and payload.get("transition") in (
                "FIRST_FINALIZED", "PAIR_TERMINAL", "FILL_TRUTH_RECONCILE"):
                self._revision(event.available_at_us)
        elif kind == "FILL_REVISION":
            self._fill(event)
        if self.episode_id is not None and self.capture_status == "OPEN":
            if kind == "FILL_REVISION":
                self._snapshot("FILL", event.event_seq, event.available_at_us, event.monotonic_ns)
            elif kind in ("ORDER_STATE", "ORDER_ACK", "CANCEL_REQUEST", "CANCEL_ACK") and order_changed:
                self._snapshot("ORDER", event.event_seq, event.available_at_us, event.monotonic_ns)
            elif (kind == "SAFETY" and payload.get("transition")) or terminal_baseline_changed:
                self._snapshot("SAFETY", event.event_seq, event.available_at_us, event.monotonic_ns)

    def _book(self, event: ResearchEvent, *, replace: bool) -> None:
        p = event.payload
        token = str(p.get("token_id", ""))
        if token not in (self.yes_token_id, self.no_token_id):
            return
        if replace:
            bids = {ds(price): ds(qty) for price, qty in p.get("bids", ()) if dec(qty) > 0}
            asks = {ds(price): ds(qty) for price, qty in p.get("asks", ()) if dec(qty) > 0}
            self.books[token] = {"bids": bids, "asks": asks, "valid": True,
                "exchange_at_us": event.exchange_at_us, "received_at_us": event.received_at_us,
                "applied_at_us": event.available_at_us, "book_seq": event.event_seq,
                "snapshot_generation": p.get("snapshot_generation") or event.connection_generation or 0,
                "source": "REST" if event.kind == "REST_BOOK" else "WS"}
            if p.get("tick_size") is not None:
                self.ticks[token] = ds(p["tick_size"])
        elif token in self.books and self.books[token]["valid"]:
            book = self.books[token]
            side = "bids" if p.get("side") == "BUY" else "asks"
            price = ds(p["price"])
            if dec(p["size"]) > 0:
                book[side][price] = ds(p["size"])
            else:
                book[side].pop(price, None)
            book.update(exchange_at_us=event.exchange_at_us,
                received_at_us=event.received_at_us, applied_at_us=event.available_at_us,
                book_seq=event.event_seq)

    def _order(self, event: ResearchEvent) -> bool:
        p = event.payload
        changed = False
        if event.kind in ("CANCEL_REQUEST", "CANCEL_ACK"):
            ids = set(p.get("order_ids") or ())
            token = p.get("token_id")
            for oid, row in self.orders.items():
                if oid not in ids and token != row.get("token_id") and p.get("scope") != "ALL":
                    continue
                before = dict(row)
                if event.kind == "CANCEL_REQUEST":
                    row["cancel_requested_at_us"] = event.available_at_us
                    row["request_id"] = p.get("request_id")
                elif p.get("outcome") == "ACK":
                    row["cancel_acknowledged_at_us"] = event.available_at_us
                changed |= row != before
            return changed
        if isinstance(p.get("orders"), tuple):
            visible_ids = {str(row.get("order_id")) for row in p["orders"]}
            token = p.get("token_id")
            for oid, row in self.orders.items():
                if row.get("token_id") == token and oid not in visible_ids and row.get("working_visible", True):
                    row["working_visible"] = False
                    changed = True
            for row in p["orders"]:
                changed |= self._order_one(row, event)
        else:
            changed |= self._order_one(p, event)
        return changed

    def _order_one(self, p: Any, event: ResearchEvent) -> bool:
        oid = p.get("order_id") if hasattr(p, "get") else None
        if not oid:
            return False
        old = self.orders.get(str(oid), {})
        role = p.get("role") or old.get("role")
        if role not in ("FIRST", "PAIR"):
            return False
        row = dict(old)
        row.update({k: p[k] for k in ("token_id", "price", "qty", "remaining_qty", "state",
            "status", "request_id") if k in p})
        if "remaining_qty" in p or "remaining_size" in p or "qty" in p:
            filled_at_update = sum((dec(r["qty"]) for r in self.fills.values()
                if r["accepted"] and r["order_id"] == str(oid)), Decimal(0))
            row["reported_remaining_qty"] = ds(p.get("remaining_size",
                p.get("remaining_qty", p.get("qty", 0))))
            # WS size_matched may already include fills discovered by Research later.
            row["filled_at_update"] = ds(max(filled_at_update,
                dec(p["size_matched"]) if p.get("size_matched") is not None else Decimal(0)))
        row["role"] = role
        row["order_id"] = str(oid)
        row.setdefault("submitted_at_us", event.available_at_us)
        if event.kind == "ORDER_ACK":
            row["acknowledged_at_us"] = event.available_at_us
        if event.kind == "CANCEL_REQUEST":
            row["cancel_requested_at_us"] = event.available_at_us
        if event.kind == "CANCEL_ACK":
            row["cancel_acknowledged_at_us"] = event.available_at_us
        row["lifecycle"] = str(p.get("state") or p.get("status") or p.get("transition")
            or old.get("lifecycle", "UNKNOWN"))
        row["terminal_authoritative"] = p.get("cancel") is True or row["lifecycle"].upper() in (
            "CANCELED", "CANCELLED", "REJECTED", "DONE", "PAIR_TERMINAL")
        if p.get("quarantined") is True or row["lifecycle"].upper() == "QUARANTINED":
            self.unresolved_order_ids.add(str(oid))
            self.quality_reasons.add("UNRESOLVED_ORDER")
        elif p.get("quarantined") is False:
            self.unresolved_order_ids.discard(str(oid))
        self._refresh_order_state_resolved()
        row["working_visible"] = not row["terminal_authoritative"]
        self.orders[str(oid)] = row
        return row != old

    def _refresh_order_state_resolved(self) -> None:
        self.order_state_resolved = not self.unresolved_order_ids and not self.market_recovery_pending

    def _safety(self, event: ResearchEvent) -> None:
        p = event.payload
        if p.get("first_finalized") or p.get("transition") == "FIRST_FINALIZED":
            self.first_finalized = True
            if self.first_finalized_at_us is None:
                self.first_finalized_at_us = event.available_at_us
        if p.get("pair_terminal") or p.get("transition") == "PAIR_TERMINAL":
            self.pair_terminal = True
        fq, _, pq, _ = self._inventory()
        if self.first_finalized and fq > 0 and pq >= fq and self.pair_complete_at_us is None:
            self.pair_complete_at_us = event.available_at_us
        if isinstance(p.get("fill_truth_ready"), bool):
            self.fill_truth_ready = p["fill_truth_ready"]
        if p.get("transition") == "FILL_TRUTH_RECONCILE" and p.get("order_id"):
            order_id = str(p["order_id"])
            if p.get("outcome") == "COMPLETE":
                self.unresolved_order_ids.discard(order_id)
            else:
                self.unresolved_order_ids.add(order_id)
                self.quality_reasons.add("UNRESOLVED_ORDER")
        if "market_recovery_pending" in p:
            self.market_recovery_pending = bool(p["market_recovery_pending"])
        self._refresh_order_state_resolved()
        for key in ("blind", "market_recovery_pending"):
            reason = key.upper()
            if key in p:
                if p[key]:
                    self.safety_reasons.add(reason)
                else:
                    self.safety_reasons.discard(reason)
        if p.get("halt_reason"):
            self.safety_reasons.add(str(p["halt_reason"]))

    def _fill(self, event: ResearchEvent) -> None:
        p = event.payload
        role = p.get("role")
        if role not in ("FIRST", "PAIR") or p.get("side") != "BUY":
            return
        key = (str(p.get("trade_id", "")), str(p.get("maker_order_id", "")))
        if not all(key):
            return
        accepted = p.get("accepted") is True and not p.get("quarantined") and dec(p.get("qty", 0)) > 0
        old = self.fills.get(key)
        row = {"role": role, "token_id": p.get("token_id"), "qty": ds(p.get("qty", 0)),
               "price": ds(p.get("price", 0)), "accepted": accepted,
               "status": p.get("status"), "order_id": key[1]}
        changed = old != row
        self.fills[key] = row
        if isinstance(p.get("fill_truth_ready"), bool):
            self.fill_truth_ready = p["fill_truth_ready"]
        if p.get("quarantined") or p.get("disposition") in ("FAILED", "QUARANTINED"):
            self.quality_reasons.add("UNRESOLVED_FILL")
            self.unresolved_order_ids.add(key[1])
            self._refresh_order_state_resolved()
        if self.episode_id is None and role == "FIRST" and accepted:
            self.first_event = event
            self.first_order_id = key[1]
            if p.get("token_id") not in (self.yes_token_id, self.no_token_id):
                self.quality_reasons.add("UNKNOWN_FIRST_TOKEN")
                self.first_event = None
                self.first_order_id = None
                return
            self.first_side = "YES" if p.get("token_id") == self.yes_token_id else "NO"
            raw = json.dumps([self.condition_id, key[1]], separators=(",", ":"))
            self.episode_id = hashlib.sha256(raw.encode()).hexdigest()
            self.capture_start_us = self.prefill[0][1] if self.prefill else event.available_at_us
            self.first_monotonic_ns = event.monotonic_ns
            self.next_deadline_ns = event.monotonic_ns + self.interval_ns
            self._revision(event.available_at_us)
            self._ensure_task()
        elif self.episode_id is not None and changed:
            if role == "PAIR" and accepted and self.pair_start_at_us is None:
                self.pair_start_at_us = event.available_at_us
            fq, _, pq, _ = self._inventory()
            if self.first_finalized and fq > 0 and pq >= fq and self.pair_complete_at_us is None:
                self.pair_complete_at_us = event.available_at_us
            self._revision(event.available_at_us)

    def _inventory(self) -> tuple[Decimal, Decimal, Decimal, Decimal]:
        fq = fc = pq = pc = Decimal(0)
        for row in self.fills.values():
            if not row["accepted"]:
                continue
            q = dec(row["qty"])
            cost = q * dec(row["price"])
            if row["role"] == "FIRST":
                fq += q
                fc += cost
            else:
                pq += q
                pc += cost
        return fq, fc, pq, pc

    def _revision(self, now_us: int, *, status: str | None = None) -> None:
        if self.episode_id is None or self.first_event is None or self.first_order_id is None:
            return
        self.revision += 1
        fq, fc, pq, pc = self._inventory()
        p = self.first_event.payload
        first_ids = sorted({r["order_id"] for r in self.fills.values() if r["role"] == "FIRST"}
            | {oid for oid, r in self.orders.items() if r["role"] == "FIRST"})
        pair_ids = sorted({r["order_id"] for r in self.fills.values() if r["role"] == "PAIR"}
            | {oid for oid, r in self.orders.items() if r["role"] == "PAIR"})
        paired = min(fq, pq)
        if self.first_side == "YES":
            yq, nq = fq, pq
        else:
            yq, nq = pq, fq
        row = Episode.model_validate({"schema_version": 1, "dataset_id": self.dataset_id,
            "record_id": f"{self.episode_id}:{self.revision}", "created_at_us": now_us,
            "source_refs": (), "episode_id": self.episode_id, "revision": self.revision,
            "revision_known_at_us": now_us, "run_ids": (self.capture.session_id,),
            "market_slug": self.market_slug, "condition_id": self.condition_id,
            "yes_token_id": self.yes_token_id, "no_token_id": self.no_token_id,
            "market_start_us": self.market_start_us, "market_end_us": self.market_end_us,
            "strategy_version": self.strategy_version, "strategy_config_id": self.strategy_config_id,
            "first_side": self.first_side, "first_order_id": self.first_order_id,
            "first_fill_key": f"{len(str(p['trade_id']))}:{p['trade_id']}{self.first_order_id}",
            "first_fill_exchange_us": self.first_event.exchange_at_us,
            "first_fill_known_at_us": self.first_event.available_at_us,
            "first_match_price": ds(p["price"]), "first_match_qty": ds(p["qty"]),
            "first_leg_finalized_at_us": self.first_finalized_at_us,
            "first_leg_final_qty": ds(fq) if self.first_finalized else None,
            "first_leg_final_cost_usd": ds(fc) if self.first_finalized else None,
            "first_leg_final_vwap": ds(fc / fq) if self.first_finalized and fq else None,
            "first_order_ids": tuple(first_ids), "pair_order_ids": tuple(pair_ids),
            "actual_pair_start_us": self.pair_start_at_us,
            "actual_pair_complete_us": self.pair_complete_at_us,
            "actual_exit_trigger": self.baseline_trigger if self.baseline_trigger in
                ("EMERGENCY_STOP", "PROBATION_FAILED", "TRAILING_STOP", "TIME_FORCED") else "NONE",
            "actual_terminal_state": self.baseline_state,
            "actual_paired_qty": ds(paired), "actual_residual_yes_qty": ds(yq - paired),
            "actual_residual_no_qty": ds(nq - paired),
            "actual_gross_pnl_usd": None, "actual_fees_usd": None,
            "actual_rebate_usd": None, "actual_settlement_cost_usd": None,
            "actual_net_pnl_usd": None, "actual_cash_received_usd": None,
            "payout_yes": None, "payout_no": None, "resolution_known_at_us": None,
            "resolution_source_ref": None, "capture_start_us": self.capture_start_us,
            "capture_end_us": now_us if status in ("COMPLETE", "CENSORED", "INVALID") else None,
            "capture_status": status or "OPEN",
            "fill_truth_status": "INVALID" if any(not r["accepted"] and r["status"] == "FAILED"
                for r in self.fills.values()) else ("UNRESOLVED" if "UNRESOLVED_FILL" in self.quality_reasons
                else ("COMPLETE" if self.fill_truth_ready and self.first_finalized
                    and self.pair_terminal else "PROVISIONAL")),
            "quality_reasons": tuple(sorted(self.quality_reasons))})
        if not self.capture.observer.observe(row):
            self.capture.failures += 1
            self.quality_reasons.add("RESEARCH_PERSISTENCE_GAP")

    def _book_state(self, token: str, now_us: int) -> dict[str, Any]:
        book = self.books.get(token)
        if book is None:
            book = {"bids": {}, "asks": {}, "valid": False, "exchange_at_us": None,
                "received_at_us": None, "applied_at_us": now_us, "book_seq": 0,
                "snapshot_generation": 0, "source": "WS"}
        bids = sorted(book["bids"].items(), key=lambda x: dec(x[0]), reverse=True)[:5]
        asks = sorted(book["asks"].items(), key=lambda x: dec(x[0]))[:5]
        bb = dec(bids[0][0]) if bids else None
        ba = dec(asks[0][0]) if asks else None
        return {"token_id": token, "bids": tuple({"price": p, "qty": q} for p, q in bids),
            "asks": tuple({"price": p, "qty": q} for p, q in asks),
            "levels_recorded": max(len(bids), len(asks)),
            "best_bid": ds(bb) if bb is not None else None,
            "best_ask": ds(ba) if ba is not None else None,
            "best_bid_qty": bids[0][1] if bids else None,
            "best_ask_qty": asks[0][1] if asks else None,
            "mid": ds((bb + ba) / 2) if bb is not None and ba is not None else None,
            "spread": ds(ba - bb) if bb is not None and ba is not None else None,
            "exchange_at_us": book["exchange_at_us"], "received_at_us": book["received_at_us"],
            "applied_at_us": book["applied_at_us"], "book_seq": book["book_seq"],
            "snapshot_generation": book["snapshot_generation"], "source": book["source"],
            "valid": book["valid"]}

    @staticmethod
    def _quote(raw: Any) -> dict[str, Any] | None:
        if not raw or raw.get("side") != "BUY":
            return None
        return {"token_id": str(raw["token_id"]), "side": "BUY",
            "price": ds(raw["price"]), "qty": ds(raw.get("qty", raw.get("size", 0)))}

    def _snapshot(self, kind: str, seq: int, now_us: int, mono: int,
                  scheduled_us: int | None = None) -> None:
        if self.episode_id is None or self.first_event is None or self.first_side is None:
            return
        self.snapshot_seq += 1
        fq, fc, pq, pc = self._inventory()
        paired = min(fq, pq)
        queue = self.capture.observer.queue
        if self.capture.lost_events or (queue is not None and queue.counters.rejected_full):
            self.quality_reasons.add("RESEARCH_PERSISTENCE_GAP")
        yq, nq = (fq, pq) if self.first_side == "YES" else (pq, fq)
        ybook = self._book_state(self.yes_token_id, now_us)
        nbook = self._book_state(self.no_token_id, now_us)
        working = []
        for oid, order in sorted(self.orders.items()):
            if (order.get("terminal_authoritative") or not order.get("working_visible", True)
                    or order.get("price") is None):
                continue
            filled = sum((dec(r["qty"]) for r in self.fills.values() if r["accepted"]
                and r["order_id"] == oid), Decimal(0))
            remaining = max(Decimal(0), dec(order.get("reported_remaining_qty",
                order.get("remaining_qty", order.get("qty", 0))))
                - max(Decimal(0), filled - dec(order.get("filled_at_update", 0))))
            if remaining <= 0:
                continue
            working.append({"order_id": oid, "role": order["role"],
                "quote": {"token_id": str(order.get("token_id") or
                    (self.yes_token_id if order["role"] == "FIRST" and self.first_side == "YES"
                     else self.no_token_id)), "side": "BUY", "price": ds(order["price"]),
                    "qty": ds(order.get("qty", remaining + filled))},
                "submitted_at_us": order.get("submitted_at_us"),
                "acknowledged_at_us": order.get("acknowledged_at_us"),
                "remaining_qty": ds(remaining), "cumulative_fill_qty": ds(filled),
                "lifecycle": order.get("lifecycle", "UNKNOWN"),
                "cancel_requested_at_us": order.get("cancel_requested_at_us"),
                "cancel_acknowledged_at_us": order.get("cancel_acknowledged_at_us"),
                "terminal_authoritative": False})
        elapsed = (now_us - self.market_start_us) / 1e6
        since = (now_us - self.first_event.available_at_us) / 1e6
        exchange_since = ((now_us - self.first_event.exchange_at_us) / 1e6
            if self.first_event.exchange_at_us is not None else None)
        tick = self.ticks.get(self.yes_token_id) or self.ticks.get(self.no_token_id) or "0"
        opp = nbook if self.first_side == "YES" else ybook
        unpaired = fq - paired
        realized_pair = paired * (Decimal(1) - fc / fq) - pc if fq else Decimal(0)
        theoretical = (realized_pair + unpaired * (Decimal(1) - fc / fq - dec(opp["mid"]))
            if fq and opp["valid"] and opp["mid"] is not None else None)
        tick_decimal = dec(tick)
        legal_price = (((dec(opp["best_ask"]) / tick_decimal)
            .to_integral_value(rounding=ROUND_FLOOR) - 1) * tick_decimal
            if opp["valid"] and opp["best_ask"] is not None and tick_decimal > 0 else None)
        candidate = ({"token_id": opp["token_id"], "side": "BUY",
            "price": ds(legal_price), "qty": ds(unpaired)}
            if legal_price is not None and legal_price > 0 and unpaired > 0 else None)
        quoted = (realized_pair + unpaired * (Decimal(1) - fc / fq - legal_price)
            if fq and legal_price is not None and legal_price > 0 else None)
        if quoted is not None:
            self.peak_quoted = max(self.peak_quoted or quoted, quoted)
        drawdown = self.peak_quoted - quoted if self.peak_quoted is not None and quoted is not None else None
        feasible = (candidate is not None and unpaired >= dec(self.venue_min_order_qty)
            and elapsed < self.order_cutoff_s and self.fill_truth_ready
            and self.first_finalized and not self.pair_terminal)
        features: dict[str, Any] = {name: None for name in FeatureVector.model_fields}
        features.update(first_side=self.first_side, first_fill_price=float(dec(self.first_event.payload["price"])),
            first_fill_qty=float(dec(self.first_event.payload["qty"])), first_qty=float(fq),
            first_cost_usd=float(fc), first_vwap=float(fc / fq) if fq else None,
            yes_qty=float(yq), no_qty=float(nq), paired_qty=float(paired),
            unpaired_qty=float(fq - paired), market_elapsed_s=elapsed,
            since_first_fill_s=since, time_to_end_s=(self.market_end_us - now_us) / 1e6,
            time_to_forced_s=self.forced_pair_s - elapsed,
            time_to_cutoff_s=self.order_cutoff_s - elapsed,
            theoretical_pair_pnl_usd=float(theoretical) if theoretical is not None else None,
            quoted_pair_pnl_usd=float(quoted) if quoted is not None else None,
            peak_quoted_pair_pnl_usd=float(self.peak_quoted) if self.peak_quoted is not None else None,
            drawdown_from_peak_usd=float(drawdown) if drawdown is not None else None,
            data_valid=bool(ybook["valid"] and nbook["valid"]), btc_available=False)
        reasons = set(self.quality_reasons)
        if self.capture.lost_events:
            reasons.add("RESEARCH_EVENT_LOSS")
        if self.capture.failures:
            reasons.add("RESEARCH_CAPTURE_FAILURE")
        if not ybook["valid"] or not nbook["valid"]:
            reasons.add("BOOK_UNAVAILABLE")
        if tick == "0":
            reasons.add("TICK_UNKNOWN")
        if (self.ticks.get(self.yes_token_id) is not None
                and self.ticks.get(self.no_token_id) is not None
                and self.ticks[self.yes_token_id] != self.ticks[self.no_token_id]):
            reasons.add("TICK_MISMATCH")
        if not self.fill_truth_ready:
            reasons.add("FILL_TRUTH_NOT_READY")
        row = StateSnapshot.model_validate({"schema_version": 1, "dataset_id": self.dataset_id,
            "record_id": f"{self.episode_id}:{self.capture.session_id}:{self.snapshot_seq}",
            "created_at_us": now_us, "source_refs": (),
            "snapshot_id": f"{self.episode_id}:{self.capture.session_id}:{self.snapshot_seq}",
            "episode_id": self.episode_id, "session_id": self.capture.session_id,
            "snapshot_seq": self.snapshot_seq, "sample_kind": kind,
            "scheduled_at_us": scheduled_us, "available_at_us": now_us,
            "monotonic_ns": mono, "knowledge_seq": seq,
            "episode_revision": self.revision, "market_elapsed_s": elapsed,
            "seconds_to_end": (self.market_end_us - now_us) / 1e6,
            "seconds_to_forced_pair": self.forced_pair_s - elapsed,
            "seconds_to_order_cutoff": self.order_cutoff_s - elapsed,
            "since_first_known_fill_s": since, "since_first_exchange_fill_s": exchange_since,
            "first_side": self.first_side, "first_qty": ds(fq), "first_cost_usd": ds(fc),
            "first_vwap": ds(fc / fq) if fq else None,
            "pair_qty": ds(pq), "pair_cost_usd": ds(pc),
            "pair_vwap": ds(pc / pq) if pq else None,
            "yes_qty": ds(yq), "no_qty": ds(nq), "paired_qty": ds(paired),
            "unpaired_first_qty": ds(fq - paired), "unpaired_opposite_qty": ds(pq - paired),
            "first_finalized": self.first_finalized,
            "pair_terminal_authoritative": self.pair_terminal,
            "baseline_state": self.baseline_state, "baseline_pair_trigger": self.baseline_trigger,
            "baseline_peak_quoted_pnl_usd": self.baseline_peak,
            "baseline_quote": self.baseline_quote, "effective_quote": self.effective_quote,
            "working_orders": tuple(working), "yes_book": ybook, "no_book": nbook,
            "market_connected": self.market_connected, "user_connected": self.user_connected,
            "market_generation": self.market_generation, "user_generation": self.user_generation,
            "fill_truth_ready": self.fill_truth_ready,
            "order_state_resolved": self.order_state_resolved,
            "safety_reasons": tuple(sorted(self.safety_reasons)), "tick_size": tick,
            "venue_min_order_qty": ds(self.venue_min_order_qty),
            "theoretical_mid_pair_pnl_usd": ds(theoretical) if theoretical is not None else None,
            "quoted_pair_pnl_usd": ds(quoted) if quoted is not None else None,
            "candidate_pair_quote": candidate, "pair_submission_feasible": bool(feasible),
            "all_postfill_peak_quoted_pnl_usd": ds(self.peak_quoted) if self.peak_quoted is not None else None,
            "drawdown_quoted_pnl_usd": ds(drawdown) if drawdown is not None else None,
            "execution_estimate": None, "features": features,
            "coverage_valid": not reasons,
            "quality_reasons": tuple(sorted(reasons))})
        if not self.capture.observer.observe(row):
            self.capture.failures += 1
            self.quality_reasons.add("RESEARCH_PERSISTENCE_GAP")

    def _finish(self, seq: int, now_us: int, mono: int, status: str,
                reason: str | None) -> None:
        if self.episode_id is None:
            return
        late_writer_failure = (status == "CENSORED" and reason == "WRITER_FAILURE"
            and self.capture_status in ("COMPLETE", "CENSORED")
            and reason not in self.quality_reasons)
        if self.capture_status != "OPEN" and not late_writer_failure:
            return
        self.capture_status = status
        if reason:
            self.quality_reasons.add(reason)
        self._revision(now_us, status=status)
        self._snapshot("FINAL", seq, now_us, mono)

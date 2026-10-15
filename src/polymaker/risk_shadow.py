"""Independent, explicitly enabled public-market observer. Never loads Config/keys.

Run: python -m polymaker.risk_shadow --enable --slug btc-updown-5m-<epoch>
Optional --journal/--session-id follows an already authorized execution read-only.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import re
import sys
import time
import uuid
from collections import OrderedDict, deque
from datetime import datetime
from pathlib import Path
from queue import Empty, Full, Queue
from threading import Thread

from polymaker.catalog.gamma import GammaClient
from polymaker.marketdata.service import MarketDataService
from polymaker.risk_shadow_model import (
    CHECKPOINTS,
    NA,
    UNKNOWN,
    Market,
    Quote,
    ShadowModel,
    compare_decision,
    hold_candidate,
    settlement,
    volatility_risk,
)

FILES = (
    "market_snapshots",
    "risk_snapshots",
    "hypothetical_risk",
    "decision_comparison",
    "market_events",
    "execution_events",
    "shadow_errors",
)
BASELINE = "a728c4db3d01dab2fddb2b9ac162c9c5bf4bb3a1"


class Writer:
    """Bounded FIFO; all serialization/write/flush happens in the daemon thread."""

    def __init__(self, directory, capacity=4096):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=False)
        self.queue = Queue(maxsize=capacity)
        self.sequence = 0
        self.dropped = 0
        self.errors = deque(maxlen=32)
        self.error_count = 0
        self.written = {name: 0 for name in FILES}
        self.closed = False
        self.thread = Thread(target=self._run, name="risk-shadow-writer", daemon=True)
        self.thread.start()

    def error(self, exc):
        self.error_count += 1
        # Exception class only: no raw payload, credential or signature exposure.
        event = {"event": "SHADOW_ERROR", "timestamp": time.time(), "reason": type(exc).__name__}
        self.errors.append(event)
        print(json.dumps(event), file=sys.stderr)

    def emit(self, name, record):
        self.sequence += 1
        if self.closed:
            self.dropped += 1
            return False
        try:
            self.queue.put_nowait(
                (
                    name,
                    {
                        **record,
                        "observer_sequence": self.sequence,
                        "shadow_dropped_event_count_so_far": self.dropped,
                    },
                )
            )
            return True
        except Full:
            self.dropped += 1
            return False

    def _run(self):
        handles = {}
        try:
            handles = {name: (self.directory / f"{name}.jsonl").open("x") for name in FILES}
            while not self.closed or not self.queue.empty():
                try:
                    name, record = self.queue.get(timeout=0.1)
                except Empty:
                    continue
                try:
                    handles[name].write(json.dumps(record, allow_nan=False) + "\n")
                    handles[name].flush()
                    self.written[name] += 1
                except Exception as exc:
                    self.error(exc)
                    # Best effort error artifact; stderr remains available on disk failure.
                    try:
                        handles["shadow_errors"].write(
                            json.dumps(
                                {**record, "event": "SHADOW_ERROR", "reason": type(exc).__name__}
                            )
                            + "\n"
                        )
                        handles["shadow_errors"].flush()
                    except Exception:
                        pass  # Already explicitly recorded through stderr and error counter.
                finally:
                    self.queue.task_done()
        except Exception as exc:
            self.error(exc)
        finally:
            for handle in handles.values():
                try:
                    handle.close()
                except Exception as exc:
                    self.error(exc)

    def close(self):
        self.closed = True
        self.thread.join(timeout=5)
        if self.thread.is_alive():
            self.error(TimeoutError())
        return not self.thread.is_alive()


class Capture:
    """Adapter to the existing MarketDataService Research receive-time interface."""

    def __init__(self, model, writer):
        self.model, self.writer = model, writer
        self.service = None
        self.failures = 0
        self.last_clock = None

    def error(self, reason):
        self.failures += 1
        self.writer.emit(
            "shadow_errors",
            {**self.model.base(time.time()), "event": "SHADOW_ERROR", "reason": reason},
        )

    def emit(
        self,
        kind,
        condition_id,
        payload,
        *,
        source,
        exchange_at_us=None,
        received_at_us=None,
        received_monotonic_ns=None,
        connection_generation=None,
    ):
        try:
            now = received_at_us / 1e6 if received_at_us is not None else time.time()
            mono = (
                received_monotonic_ns / 1e9
                if received_monotonic_ns is not None
                else time.monotonic()
            )
            available = time.time()
            if self.last_clock is not None:
                old_wall, old_mono = self.last_clock
                if mono < old_mono or abs((now - old_wall) - (mono - old_mono)) > 0.5:
                    self.model.vol.buckets.clear()
                    self.model.vol.last_final = None
                    self.model.vol.finalized_bucket = -1000000000
                    self.model.vol.realized_gaps += 1
                    self.model.quotes = {
                        s: Quote(tick=q.tick) for s, q in self.model.quotes.items()
                    }
                    self.error("CLOCK_RESET")
            self.last_clock = now, mono
            market = self.model.market
            token = payload.get("token_id")
            outcome = (
                "YES"
                if token == market.yes_token_id
                else "NO"
                if token == market.no_token_id
                else None
            )
            event = {
                **self.model.base(available),
                "event_type": kind,
                "local_receive_timestamp": now,
                "local_receive_monotonic": mono,
                "available_timestamp": available,
                "source": source,
                "source_timestamp": exchange_at_us / 1e6 if exchange_at_us else UNKNOWN,
                "connection_generation": connection_generation,
                "event_payload": dict(payload),
            }
            if kind == "CONNECTION":
                if payload.get("state") == "UP":
                    self.model.quotes = {
                        s: Quote(tick=q.tick) for s, q in self.model.quotes.items()
                    }
                event["market_data_gap"] = payload.get("state") == "DOWN"
            elif outcome is None or condition_id != market.condition_id:
                self.error("INVALID_IDENTITY")
                return
            elif kind in {"BOOK", "DELTA"}:
                view = self.service.view(token)
                event_id = f"{connection_generation}:{self.service.message_id}:{token}:{kind}:{payload.get('side', '')}:{payload.get('price', '')}"
                quote = Quote(
                    view.best_bid if view.best_bid is not None else UNKNOWN,
                    view.best_ask if view.best_ask is not None else UNKNOWN,
                    now,
                    mono,
                    exchange_at_us / 1e6 if exchange_at_us else UNKNOWN,
                    self.service.book(token).tick_size,
                    connection_generation or 0,
                    True,
                    event_id,
                )
                self.model.quote_update(outcome, quote)
                event.update(quote.fields(now), token_id=token, event_id=event_id)
            elif kind == "TICK":
                # A tick event is not a fresh price event.
                old = self.model.quotes[outcome]
                self.model.tick_updated_at[outcome] = now
                self.model.quotes[outcome] = Quote(
                    old.bid,
                    old.ask,
                    old.received,
                    old.mono,
                    old.source,
                    payload["tick_size"],
                    old.generation,
                    old.identity,
                    old.event_id,
                )
            self.writer.emit("market_events", event)
        except Exception as exc:
            self.error(type(exc).__name__)


class ObserverMarketData(MarketDataService):
    """Reuse WS/parser/cache. Guards apply only to this independent instance."""

    def __init__(self, capture):
        super().__init__(research=capture)
        capture.service = self
        self.capture = capture
        self.seen = OrderedDict()
        self.message_id = ""

    def _handle(self, raw):
        try:
            parsed = json.loads(raw)
            messages = parsed if isinstance(parsed, list) else [parsed]
            if any(not isinstance(m, dict) for m in messages):
                raise ValueError("malformed WS frame")
        except (ValueError, TypeError):
            self.capture.error("MALFORMED_WS_MESSAGE")
            return
        super()._handle(raw)

    def _dispatch(self, msg):
        kind = msg.get("event_type")
        if kind not in {"book", "price_change", "last_trade_price", "tick_size_change"}:
            return
        tokens = (
            [c.get("asset_id") for c in msg.get("price_changes", [])]
            if kind == "price_change"
            else [msg.get("asset_id")]
        )
        if (
            any(t not in self.books for t in tokens)
            or msg.get("market", self.capture.model.market.condition_id)
            != self.capture.model.market.condition_id
            or kind in {"book", "price_change"}
            and not msg.get("market")
        ):
            self.capture.error("INVALID_IDENTITY")
            return
        digest = hashlib.sha256(json.dumps(msg, sort_keys=True).encode()).hexdigest()
        key = self.connection_generation, digest
        if key in self.seen:
            return
        self.seen[key] = None
        if len(self.seen) > 4096:
            self.seen.popitem(last=False)
        self.message_id = digest
        try:
            if kind == "book":
                for levels in (msg["bids"], msg["asks"]):
                    if not isinstance(levels, list):
                        raise ValueError()
                    for level in levels:
                        if not (
                            0 <= float(level["price"]) <= 1
                            and math.isfinite(float(level["size"]))
                            and float(level["size"]) >= 0
                        ):
                            raise ValueError()
            if kind == "price_change":
                for change in msg["price_changes"]:
                    if not (
                        0 <= float(change["price"]) <= 1
                        and math.isfinite(float(change["size"]))
                        and float(change["size"]) >= 0
                        and change["side"] in {"BUY", "SELL"}
                    ):
                        raise ValueError()
            if kind == "tick_size_change":
                tick = float(msg.get("new_tick_size", msg.get("tick_size")))
                if tick not in (0.1, 0.01, 0.001, 0.0001):
                    raise ValueError()
            if (
                kind == "book"
                and "tick_size" in msg
                and float(msg["tick_size"]) not in (0.1, 0.01, 0.001, 0.0001)
            ):
                raise ValueError()
            super()._dispatch(msg)
        except (ValueError, KeyError, TypeError, OverflowError):
            # Never leave a last-known quote looking VALID after malformed update.
            for token in tokens:
                outcome = "YES" if token == self.capture.model.market.yes_token_id else "NO"
                self.capture.model.quotes[outcome] = Quote(tick=UNKNOWN)
            self.capture.error("MALFORMED_WS_MESSAGE")


class ExecutionJournalReader:
    """Read-only follower. Copies authoritative snapshots, never sums fills.

    Past records at attachment are not retroactively treated as live knowledge.
    Comparison uses the latest already-written shadow slot at/before decision.
    """

    def __init__(self, path, model, writer, history, *, from_start=False):
        self.path, self.model, self.writer, self.history = Path(path), model, writer, history
        self.offset = 0 if from_start or not self.path.exists() else self.path.stat().st_size
        self.inventory_source_timestamp = UNKNOWN
        self.buffer = b""
        self.decisions = {}
        self.stopped = False
        self.order_windows = {}
        self.comparisons = deque(maxlen=4096)
        self.decision_sequence = 0

    def poll(self, now):
        try:
            with self.path.open("rb") as handle:
                handle.seek(self.offset)
                chunk = handle.read(262144)  # bounded per poll
                self.offset = handle.tell()
        except FileNotFoundError:
            return  # Current Session can still be in M1, before Journal creation.
        self.buffer += chunk
        if len(self.buffer) > 4 * 1024 * 1024:
            self.buffer = b""
            raise ValueError("oversized execution record")
        while b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)
            if self.model.session_id.encode() in line:
                self.accept(json.loads(line), max(now, time.time()))

    async def follow(self, capture):
        while True:
            try:
                self.poll(time.time())
            except Exception as exc:
                self.model.inventory_update(timestamp=time.time())
                capture.error(type(exc).__name__)
            await asyncio.sleep(0.02)  # Only in the independent Observer process.

    def accept(self, row, now):
        data = row.get("data", {})
        if data.get("session_id") != self.model.session_id:
            return
        payload = data.get("payload", {})
        kind, stamp = row["kind"], row["ts"]
        if stamp > now:
            raise ValueError("future execution timestamp")
        market = self.model.market
        if (
            payload.get("market_id", payload.get("condition_id", market.condition_id))
            != market.condition_id
        ):
            raise ValueError("execution identity mismatch")
        event = {
            **self.model.base(now),
            "event_type": kind,
            "journal_timestamp": stamp,
            "execution_state_source": "AUTHORITATIVE_RDP_JOURNAL",
            "window_id": payload.get("window_id", UNKNOWN),
        }
        # Explicit whitelist: no request hash/raw responses/signatures/credentials.
        for key in (
            "order_id",
            "maker_order_id",
            "trade_id",
            "status",
            "close_barrier",
            "yes_inventory",
            "no_inventory",
            "confirmed_yes",
            "confirmed_no",
            "execution_status",
            "final_close_barrier",
            "delta",
            "state",
            "size",
            "price",
            "token_id",
            "post_started",
        ):
            if key in payload:
                event[key] = payload[key]
        self.writer.emit("execution_events", event)
        if kind == "sm_window_result":
            self.model.inventory_update(
                payload.get("yes_inventory", UNKNOWN),
                payload.get("no_inventory", UNKNOWN),
                validity="VALID" if payload.get("close_barrier") == "SAFE_TO_CONTINUE" else UNKNOWN,
                timestamp=now,
                state=self.model.inventory.state,
            )
            self.inventory_source_timestamp = stamp
        elif kind in {"sm_placement_intent", "sm_trade", "sm_merge_intent", "sm_merge_result"}:
            # Until the next authoritative execution snapshot, conservatively UNKNOWN.
            self.model.inventory_update(timestamp=now, state=self.model.inventory.state)
        elif kind == "sm_run_result":
            self.model.inventory_update(
                payload.get("confirmed_yes", UNKNOWN),
                payload.get("confirmed_no", UNKNOWN),
                validity="VALID"
                if payload.get("execution_status") == "COMPLETED"
                and payload.get("final_close_barrier") == "SAFE_TO_CONTINUE"
                else UNKNOWN,
                timestamp=now,
                state="STOPPED",
            )
            self.inventory_source_timestamp = stamp
            self.stopped = True  # Observer lifecycle does not depend on this flag.
        elif kind == "sm_quote_refreshed":
            previous = self.decisions.get(payload.get("window_id"))
            if previous:
                self.accept(
                    {
                        "ts": stamp,
                        "kind": "sm_strategy_window",
                        "data": {
                            "session_id": self.model.session_id,
                            "payload": {
                                "state": "REBALANCE",
                                "window_id": payload["window_id"],
                                "quotes": payload.get("quotes", []),
                                "delta": previous["actual_decision_delta"],
                                "decision_source": "sm_quote_refreshed",
                            },
                        },
                    },
                    now,
                )
        elif kind == "sm_strategy_window":
            state = payload.get("state", UNKNOWN)
            inv = self.model.inventory
            self.model.inventory_update(
                inv.q_yes, inv.q_no, validity=inv.validity, timestamp=inv.updated_at, state=state
            )
            if state != "REBALANCE":
                return
            quotes = payload.get("quotes", [])
            q = quotes[0] if len(quotes) == 1 else {}
            if q and (
                q.get("market_id") != market.condition_id
                or q.get("token_id")
                != {"YES": market.yes_token_id, "NO": market.no_token_id}.get(q.get("outcome"))
            ):
                raise ValueError("decision identity mismatch")
            window = payload["window_id"]
            self.decision_sequence += 1
            actual = {
                **self.model.base(now),
                "market_id": market.condition_id,
                "decision_id": f"{window}:d{self.decision_sequence}",
                "window_id": window,
                "decision_timestamp": stamp,
                "decision_source": payload.get("decision_source", kind),
                "comparison_observed_timestamp": now,
                "actual_state": state,
                "actual_action": "BUY" if q else "NONE",
                "actual_side": f"BUY {q.get('outcome')}" if q else UNKNOWN,
                "actual_intended_price": q.get("price", UNKNOWN),
                "actual_intended_size": q.get("size", UNKNOWN),
                "actual_submitted_price": UNKNOWN,
                "actual_submitted_size": UNKNOWN,
                "actual_order_id": UNKNOWN,
                "actual_ack_status": UNKNOWN,
                "actual_confirmed_fill": UNKNOWN,
                "actual_decision_delta": payload.get("delta", UNKNOWN),
                "record_stage": "INTENDED",
            }
            eligible = [
                (s, r)
                for s, r in self.history
                if s["timestamp"] <= stamp and stamp - s["timestamp"] <= 1
            ]
            if eligible:
                snapshot, risk = eligible[-1]
                snapshot = dict(snapshot)
                risk = dict(risk)
                actual["comparison_input_policy"] = "LAST_PRIOR_SLOT_MAX_1S"
                actual["market_input_snapshot_timestamp"] = snapshot["timestamp"]
                # A window result may immediately precede the next decision between
                # one-second market slots. Copy its authority; never sum/rebuild fills.
                if (
                    self.model.inventory.validity == "VALID"
                    and isinstance(self.inventory_source_timestamp, (int, float))
                    and self.inventory_source_timestamp <= stamp
                ):
                    risk.update(self.model.inventory.fields())
                    risk.update(settlement(risk["delta"], risk["p_yes"]))
                    actual["inventory_source_timestamp"] = self.inventory_source_timestamp
                snapshot["elapsed_sec"] = stamp - market.start
                snapshot["remaining_sec"] = max(0, 300 - snapshot["elapsed_sec"])
                for outcome in ("YES", "NO"):
                    tick_time = self.model.tick_updated_at[outcome]
                    if isinstance(tick_time, (int, float)) and tick_time <= stamp:
                        snapshot[f"{outcome.lower()}_official_tick_size"] = self.model.quotes[
                            outcome
                        ].tick
                    elif isinstance(tick_time, (int, float)):
                        # A newer tick cannot be used to reconstruct a past decision.
                        snapshot[f"{outcome.lower()}_official_tick_size"] = UNKNOWN
                actual["official_tick_size"] = {
                    outcome: snapshot[f"{outcome.lower()}_official_tick_size"]
                    for outcome in ("YES", "NO")
                }
                for outcome in ("yes", "no"):
                    received = snapshot[f"{outcome}_local_receive_timestamp"]
                    if isinstance(received, (int, float)) and stamp - received > 5:
                        snapshot[f"{outcome}_quote_validity"] = "STALE"
                        if outcome == "yes":
                            snapshot["probability_validity"] = UNKNOWN
                            risk.update(
                                p_yes=UNKNOWN,
                                sigma_hat=UNKNOWN,
                                v_hat=UNKNOWN,
                                r_settle=UNKNOWN,
                                settlement_sd=UNKNOWN,
                                inventory_win_prob=UNKNOWN,
                                inventory_lose_prob=UNKNOWN,
                            )
                risk.update(
                    volatility_risk(risk["delta"], risk["v_hat"], snapshot["remaining_sec"])
                )
            else:
                snapshot = self.model.market_snapshot(stamp)
                # Do not use subsequently received quotes/known fills for a past decision.
                snapshot.update(
                    probability_validity=UNKNOWN, p_yes=UNKNOWN, quote_validity="MISSING"
                )
                risk = self.model.risk(snapshot, time.monotonic())
                risk.update(
                    delta=UNKNOWN,
                    inventory_validity=UNKNOWN,
                    risk_snapshot_id=UNKNOWN,
                    sigma_hat=UNKNOWN,
                    r_vol_proxy=UNKNOWN,
                    r_vol_realized=UNKNOWN,
                    r_settle=UNKNOWN,
                    settlement_sd=UNKNOWN,
                    inventory_win_prob=UNKNOWN,
                    inventory_lose_prob=UNKNOWN,
                )
            try:
                delta_matches = (
                    float(payload.get("delta")) == risk["delta"]
                    and self.model.inventory.validity == "VALID"
                    and self.model.inventory.delta == risk["delta"]
                )
            except (ValueError, TypeError):
                delta_matches = False
            if not delta_matches:
                risk = {
                    **risk,
                    "inventory_validity": UNKNOWN,
                    "delta": UNKNOWN,
                    "r_vol_proxy": UNKNOWN,
                    "r_vol_realized": UNKNOWN,
                    "r_settle": UNKNOWN,
                    "settlement_sd": UNKNOWN,
                    "inventory_win_prob": UNKNOWN,
                    "inventory_lose_prob": UNKNOWN,
                }
            # Keep the joined decision risk separate from immutable one-second slots.
            risk = {
                **risk,
                **self.model.base(stamp),
                "record_type": "DECISION",
                "observed_at": now,
                "decision_timestamp": stamp,
                "market_input_snapshot_timestamp": actual.get(
                    "market_input_snapshot_timestamp", UNKNOWN
                ),
                "inventory_source_timestamp": actual.get("inventory_source_timestamp", UNKNOWN),
                "elapsed_sec": stamp - market.start,
                "remaining_sec": max(0, 300 - (stamp - market.start)),
                "risk_snapshot_id": f"{actual['decision_id']}:risk",
            }
            self.writer.emit("risk_snapshots", risk)
            comparison = compare_decision(actual, snapshot, risk, market.min_size)
            self.decisions[window] = comparison
            self.comparisons.append(comparison)
            if len(self.decisions) > 300:
                self.decisions.pop(next(iter(self.decisions)))
            self.writer.emit("decision_comparison", comparison)
        # Order lifecycle rows remain separate append-only records linked by window/order IDs.
        quote = payload.get("quote", {})
        if kind in {"sm_placement_intent", "sm_placement_ack", "sm_placement_result"}:
            window = quote.get("window_id")
            if window in self.decisions:
                if payload.get("order_id"):
                    self.order_windows[payload["order_id"]] = window
                    if len(self.order_windows) > 4096:
                        self.order_windows.pop(next(iter(self.order_windows)))
                submitted = payload.get("post_started") is True
                stage = (
                    "ACK"
                    if kind == "sm_placement_ack"
                    else "SUBMISSION_INTENT"
                    if kind == "sm_placement_intent"
                    else "RESULT"
                )
                self.writer.emit(
                    "decision_comparison",
                    {
                        **self.model.base(now),
                        "decision_id": self.decisions[window]["decision_id"],
                        "window_id": window,
                        "record_stage": stage,
                        "decision_timestamp": self.decisions[window]["decision_timestamp"],
                        "journal_timestamp": stamp,
                        "actual_submitted_price": quote.get("price", UNKNOWN)
                        if submitted
                        else UNKNOWN,
                        "actual_submitted_size": quote.get("size", UNKNOWN)
                        if submitted
                        else UNKNOWN,
                        "actual_order_id": payload.get("order_id") or UNKNOWN,
                        "actual_ack_status": "ACK" if stage == "ACK" else UNKNOWN,
                        "actual_confirmed_fill": UNKNOWN,
                    },
                )
        if kind == "sm_trade" and payload.get("status") == "CONFIRMED":
            window = self.order_windows.get(payload.get("maker_order_id"))
            if window in self.decisions:
                self.writer.emit(
                    "decision_comparison",
                    {
                        **self.model.base(now),
                        "decision_id": self.decisions[window]["decision_id"],
                        "window_id": window,
                        "record_stage": "CONFIRMED_FILL",
                        "journal_timestamp": stamp,
                        "actual_order_id": payload["maker_order_id"],
                        "actual_confirmed_fill": {
                            k: payload.get(k, UNKNOWN)
                            for k in ("trade_id", "token_id", "size", "price", "status")
                        },
                    },
                )


def metadata(raw, slug):
    if not raw or raw.get("slug") != slug or not re.fullmatch(r"btc-updown-5m-[0-9]+", slug):
        raise ValueError("market identity unavailable")
    # startDate is creation/listing time; eventStartTime is actual market T0.
    start = datetime.fromisoformat(raw["eventStartTime"].replace("Z", "+00:00")).timestamp()
    end = datetime.fromisoformat(raw["endDate"].replace("Z", "+00:00")).timestamp()
    if start != int(slug.rsplit("-", 1)[1]) or end - start != 300:
        raise ValueError("official market time mismatch")
    tokens = raw["clobTokenIds"]
    outcomes = raw["outcomes"]
    tokens = json.loads(tokens) if isinstance(tokens, str) else tokens
    outcomes = json.loads(outcomes) if isinstance(outcomes, str) else outcomes
    mapping = dict(zip(outcomes, tokens, strict=True))
    if set(mapping) != {"Up", "Down"} or len(set(tokens)) != 2:
        raise ValueError("official outcome mapping mismatch")
    if not all(isinstance(t, str) and t.isdigit() and 0 < int(t) < 2**256 for t in tokens):
        raise ValueError("official token identity invalid")
    if not re.fullmatch(r"0x[0-9a-fA-F]{64}", raw["conditionId"]):
        raise ValueError("condition identity invalid")
    tick = float(raw["orderPriceMinTickSize"])
    if tick not in (0.1, 0.01, 0.001, 0.0001):
        raise ValueError("official tick unknown")
    return Market(
        slug,
        raw["conditionId"],
        mapping["Up"],
        mapping["Down"],
        start,
        tick,
        float(raw["orderMinSize"]),
    )


class Summary:
    def __init__(self, model, started):
        self.model = model
        self.data = {
            **model.base(started),
            "market_id": model.market.condition_id,
            "baseline_commit": BASELINE,
            "observation_start": started,
            "expected_snapshot_slots": 300,
            "snapshot_slot_record_count": 0,
            "observed_snapshot_slots": 0,
            "valid_yes_snapshot_slots": 0,
            "valid_no_snapshot_slots": 0,
            "market_data_gap_count": 0,
            "volatility_valid_count": 0,
            "volatility_unknown_count": 0,
            "inventory_valid_count": 0,
            "inventory_unknown_count": 0,
            "settlement_risk_valid_count": 0,
            "settlement_risk_unknown_count": 0,
            "rebalance_decision_count": 0,
            "shadow_comparison_count": 0,
            "same_quote_count": 0,
            "price_difference_count": 0,
            "hold_candidate_count": 0,
            "undetermined_count": 0,
            "checkpoints": {},
            "mode": "OBSERVATION_ONLY" if model.session_id == NA else "EXECUTION_ATTACHED_SHADOW",
            **{f"t{t}_valid": UNKNOWN for t in (180, 210, 240, 270)},
        }

    def sample(self, s, r):
        d = self.data
        d["snapshot_slot_record_count"] += 1
        d["observed_snapshot_slots"] += s["slot_validity"] == "OBSERVED"
        for outcome in ("yes", "no"):
            d[f"valid_{outcome}_snapshot_slots"] += s[f"{outcome}_quote_validity"] == "VALID"
        d["market_data_gap_count"] += s["quote_validity"] != "VALID"
        for name, field in (
            ("volatility", "volatility_validity"),
            ("inventory", "inventory_validity"),
            ("settlement_risk", "settlement_risk_validity"),
        ):
            d[f"{name}_valid_count"] += r[field] == "VALID"
            d[f"{name}_unknown_count"] += r[field] != "VALID"
        d["hold_candidate_count"] += hold_candidate(s, r) is True
        slot = s["slot_index"]
        if slot in CHECKPOINTS:
            d["checkpoints"][str(slot)] = {
                k: s[k]
                for k in (
                    "slot_index",
                    "actual_snapshot_time",
                    "yes_local_receive_timestamp",
                    "no_local_receive_timestamp",
                    "yes_quote_age_ms",
                    "no_quote_age_ms",
                    "yes_quote_validity",
                    "no_quote_validity",
                    "probability_validity",
                    "slot_validity",
                )
            }
            d["checkpoints"][str(slot)].update(
                volatility_validity=r["volatility_validity"],
                inventory_validity=r["inventory_validity"],
            )
            if slot in (180, 210, 240, 270):
                d[f"t{slot}_valid"] = s["probability_validity"] == "VALID"

    def finish(self, ended, writer, service, follower=None):
        d = self.data
        d.update(
            observation_end=ended,
            observed_market_duration_sec=max(
                0,
                min(ended, self.model.market.start + 300)
                - max(d["observation_start"], self.model.market.start),
            ),
            shadow_dropped_event_count=writer.dropped,
            writer_error_count=writer.error_count,
            writer_errors=list(writer.errors),
            written_records=dict(writer.written),
            ws_metrics=dict(service.metrics),
            realized_valid_duration_sec=self.model.vol.realized_duration,
            realized_gap_count=self.model.vol.realized_gaps,
            realized_vol_coverage=self.model.vol.realized_fields(),
            checkpoints_missing=[t for t in CHECKPOINTS if str(t) not in d["checkpoints"]],
        )
        if follower:
            records = follower.comparisons
            d.update(
                rebalance_decision_count=len(records),
                shadow_comparison_count=len(records),
                same_quote_count=sum(
                    r["decision_difference_type"] == "SAME_QUOTE" for r in records
                ),
                price_difference_count=sum(
                    r["decision_difference_type"] == "PRICE_DIFFERENCE" for r in records
                ),
                undetermined_count=sum(r["shadow_action"] == "UNDETERMINED" for r in records),
            )
        return d


async def observe(
    slug,
    output_root,
    *,
    enabled=False,
    journal=None,
    session_id=NA,
    journal_from_start=False,
    expected_identity=None,
):
    if not enabled:
        return {"shadow_enabled": False}  # no network, keys, filesystem or tasks
    if bool(journal) != (session_id != NA):
        raise ValueError("attached mode requires both journal and session-id")
    async with GammaClient() as gamma:
        raw = await gamma.market_by_slug(slug)
    market = metadata(raw, slug)
    if expected_identity is not None and expected_identity != (
        market.condition_id,
        market.yes_token_id,
        market.no_token_id,
    ):
        raise ValueError("Shadow market differs from execution market")
    began = time.time()
    if began >= market.start + 300:
        raise ValueError("market already ended")
    model = ShadowModel(market, session_id)
    directory = Path(output_root) / market.slug / uuid.uuid4().hex
    writer = Writer(directory)
    capture = Capture(model, writer)
    service = ObserverMarketData(capture)
    service.set_markets([(market.condition_id, [market.yes_token_id, market.no_token_id])])
    for token in (market.yes_token_id, market.no_token_id):
        service.book(token).set_tick_size(market.tick)  # official metadata, never cache default
    writer.emit(
        "market_events",
        {
            **model.base(began),
            "event_type": "OFFICIAL_METADATA",
            "market_start_utc": market.start,
            "market_end_utc": market.start + 300,
            "yes_token_id": market.yes_token_id,
            "no_token_id": market.no_token_id,
            "official_tick_size": market.tick,
            "min_order_size": market.min_size,
        },
    )
    history = deque(maxlen=300)
    follower = (
        ExecutionJournalReader(journal, model, writer, history, from_start=journal_from_start)
        if journal
        else None
    )
    summary = Summary(model, began)
    task = asyncio.create_task(service.run())
    follow_task = asyncio.create_task(follower.follow(capture)) if follower else None
    try:
        for slot in range(300):
            scheduled = market.start + slot
            await asyncio.sleep(max(0, scheduled - time.time()))
            now, mono = time.time(), time.monotonic()
            s, r, h = model.sample(slot, now, mono)
            writer.emit("market_snapshots", s)
            writer.emit("risk_snapshots", r)
            if session_id == NA:
                writer.emit("hypothetical_risk", h)
            history.append((s, r))
            summary.sample(s, r)
        await asyncio.sleep(max(0, market.start + 300 - time.time()))
        writer.emit(
            "market_events",
            {
                **model.base(time.time()),
                "event_type": "OBSERVATION_END",
                "elapsed_sec": time.time() - market.start,
            },
        )
    except (Exception, asyncio.CancelledError) as exc:
        capture.error(type(exc).__name__)
        # Remaining slots are explicit MISSING rows, never filled from later cache.
        for slot in range(300):
            if slot not in model.slots:
                s, r, h = model.sample(slot, market.start + 301, time.monotonic())
                writer.emit("market_snapshots", s)
                writer.emit("risk_snapshots", r)
                if session_id == NA:
                    writer.emit("hypothetical_risk", h)
                summary.sample(s, r)
    finally:
        ended = time.time()
        model.vol.finalize(300)
        service.stop()
        task.cancel()
        if follow_task is not None:
            follow_task.cancel()
        await asyncio.gather(task, *([follow_task] if follow_task else []), return_exceptions=True)
        drained = await asyncio.to_thread(writer.close)
        result = summary.finish(ended, writer, service, follower)
        result.update(
            output_directory=str(directory),
            shadow_error_count=capture.failures,
            writer_drained=drained,
            trading_permission="NONE",
            rpc_used=False,
            actual_inventory_source="N/A" if session_id == NA else "AUTHORITATIVE_RDP_JOURNAL",
        )
        try:
            (directory / "market_summary.json").write_text(
                json.dumps(result, indent=2, allow_nan=False) + "\n"
            )
        except Exception as exc:
            writer.error(exc)
            result["summary_write_failed"] = True
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--enable", action="store_true", help="explicitly enable the standalone observer"
    )
    parser.add_argument("--slug", help="one exact official BTC5m slug")
    parser.add_argument("--output-root", default="research_outputs/rdp_risk_shadow")
    parser.add_argument(
        "--journal", help="read-only execution Journal of an independently authorized session"
    )
    parser.add_argument("--session-id", default=NA)
    parser.add_argument("--journal-from-start", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--expected-condition-id", help=argparse.SUPPRESS)
    parser.add_argument("--expected-yes-token-id", help=argparse.SUPPRESS)
    parser.add_argument("--expected-no-token-id", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.enable and not args.slug:
        parser.error("--enable requires --slug")
    try:
        result = asyncio.run(
            observe(
                args.slug,
                args.output_root,
                enabled=args.enable,
                journal=args.journal,
                session_id=args.session_id,
                journal_from_start=args.journal_from_start,
                expected_identity=(
                    args.expected_condition_id,
                    args.expected_yes_token_id,
                    args.expected_no_token_id,
                )
                if args.expected_condition_id
                else None,
            )
        )
        print(json.dumps(result, allow_nan=False))
    except Exception as exc:
        print(json.dumps({"event": "SHADOW_ERROR", "reason": type(exc).__name__}), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

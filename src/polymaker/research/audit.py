"""Offline, read-only M4A session and collection acceptance audit.

The final manifest is the only writer completion certificate. This module never
changes a raw artifact, and every unavailable proof remains unestablished.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from polymaker.research.schema import DatasetManifest, Episode, ResearchEvent, StateSnapshot
from polymaker.strategy.favorite_first import StrategyState

ARTIFACTS = ("events.jsonl", "episodes.jsonl", "states.jsonl")
TERMINAL_BASELINE_STATES = frozenset((StrategyState.COMPLETE.value,
    StrategyState.HALTED.value, StrategyState.UNPAIRABLE_RESIDUAL.value))
SECRET_KEY = re.compile(
    r"(?:private.?key|signing.?secret|wallet.?secret|api.?(?:key|secret)|"
    r"builder.?(?:key|secret)|passphrase|credential|authorization|"
    r"auth(?:entication)?.?(?:token|header)|access.?token|password)", re.I)
SECRET_VALUE = re.compile(r"(?:-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
                          r"\b(?:Bearer|Basic)\s+[A-Za-z0-9+/_=.-]{8,}\b)", re.I)
MAX_EXAMPLES = 20
MAX_ROWS = {"events.jsonl": 500_000, "episodes.jsonl": 20_000,
            "states.jsonl": 20_000}


class Findings(list[dict[str, Any]]):
    """Bounded diagnostic examples with an exact failure count."""

    def __init__(self) -> None:
        super().__init__()
        self.total = 0

    def append(self, item: dict[str, Any]) -> None:
        self.total += 1
        if len(self) < MAX_EXAMPLES:
            super().append(item)


def _decimal(value: Any) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("invalid decimal") from exc
    if not result.is_finite():
        raise ValueError("nonfinite decimal")
    return result


def _issue(issues: list[dict[str, Any]], gate: str, where: str, detail: str) -> None:
    issues.append({"gate": gate, "where": SECRET_VALUE.sub("[REDACTED]", where),
                   "detail": SECRET_VALUE.sub("[REDACTED]", detail)})


def _scan_secrets(value: Any, where: str, issues: list[dict[str, Any]]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            location = f"{where}.{key}"
            if SECRET_KEY.search(key):
                _issue(issues, "secrets", location, "sensitive field name; value redacted")
            _scan_secrets(child, location, issues)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _scan_secrets(child, f"{where}[{index}]", issues)
    elif isinstance(value, str) and SECRET_VALUE.search(value):
        _issue(issues, "secrets", where, "probable credential value; value redacted")


def _parse_json(source: str) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, child in items:
            if key in value:
                raise ValueError("duplicate JSON key")
            value[key] = child
        return value

    def constant(_: str) -> None:
        raise ValueError("nonfinite JSON constant")

    return json.loads(source, object_pairs_hook=pairs, parse_constant=constant)


def _read_json(path: Path, issues: list[dict[str, Any]]) -> dict[str, Any] | None:
    try:
        value = _parse_json(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("expected object")
        _scan_secrets(value, path.name, issues)
        return value
    except (OSError, UnicodeError, ValueError) as exc:
        _issue(issues, "artifacts", path.name, type(exc).__name__)
        return None


def _read_jsonl(path: Path, model: type[ResearchEvent] | type[Episode] | type[StateSnapshot],
                issues: list[dict[str, Any]]) -> list[Any]:
    records: list[Any] = []
    try:
        with path.open(encoding="utf-8") as file:
            for line_number, line in enumerate(file, 1):
                where = f"{path.name}:{line_number}"
                if line_number > MAX_ROWS[path.name]:
                    _issue(issues, "resource_limit", where,
                           "session exceeds bounded in-memory audit limit")
                    break
                try:
                    raw = _parse_json(line)
                    _scan_secrets(raw, where, issues)
                    records.append(model.model_validate_json(json.dumps(raw, allow_nan=False)))
                except (ValueError, ValidationError, TypeError) as exc:
                    _issue(issues, "schema", where, type(exc).__name__)
    except (OSError, UnicodeError) as exc:
        _issue(issues, "artifacts", path.name, type(exc).__name__)
    return records


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_episode_id(value: str) -> str:
    if re.fullmatch(r"[0-9a-f]{64}", value):
        return value
    return "invalid:" + hashlib.sha256(value.encode()).hexdigest()[:16]


def _quote_projection(raw: Any) -> tuple[str, str, Decimal, Decimal] | None:
    """Project only the BUY quote fields copied by EpisodeLifecycle."""
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ValueError("invalid quote")
    if raw.get("side") != "BUY":
        return None
    try:
        return (str(raw["token_id"]), "BUY", _decimal(raw["price"]),
                _decimal(raw.get("qty", raw.get("size", 0))))
    except KeyError as exc:
        raise ValueError("invalid quote") from exc


def _check_source_refs(records: Iterable[Any], issues: list[dict[str, Any]],
                       cache: dict[Path, tuple[str, int]]) -> None:
    for record in records:
        references = getattr(record, "source_refs", None)
        if references is None:
            reference = getattr(record, "source_ref", None)
            references = (reference,) if reference is not None else ()
        for reference in references:
            where = f"source_ref:{getattr(record, 'record_id', getattr(record, 'event_seq', '?'))}"
            source = Path(reference.path)
            if not source.is_absolute() or reference.sha256 is None:
                _issue(issues, "source_ref", where,
                       "relative source path or missing closed-source checksum")
                continue
            try:
                if source not in cache:
                    cache[source] = (_hash(source), source.stat().st_size)
                digest, size = cache[source]
                if digest != reference.sha256 or reference.offset_bytes + reference.length_bytes > size:
                    _issue(issues, "source_ref", where, "source checksum or byte range differs")
            except OSError as exc:
                _issue(issues, "source_ref", where, type(exc).__name__)


class _Replay:
    """Independent prefix projection for the fields M4A promises to reconstruct."""

    def __init__(self, manifest: DatasetManifest) -> None:
        self.manifest = manifest
        self.books: dict[str, dict[str, Any]] = {}
        self.ticks: dict[str, Decimal] = {}
        self.fills: dict[tuple[str, str], dict[str, Any]] = {}
        self.orders: dict[str, dict[str, Any]] = {}
        self.unresolved: set[str] = set()
        self.recovery_pending = False
        self.connected = {"market": False, "user": False}
        self.generation = {"market": 0, "user": 0}
        self.first_finalized = False
        self.pair_terminal = False
        self.first_finalized_at: int | None = None
        self.first_fill: ResearchEvent | None = None
        self.first_side: str | None = None
        self.material_order_seqs: set[int] = set()
        self.baseline_state = "UNKNOWN"
        self.baseline_trigger: str | None = None
        self.baseline_peak: Decimal | None = None
        self.baseline_quote: tuple[str, str, Decimal, Decimal] | None = None
        self.effective_quote: tuple[str, str, Decimal, Decimal] | None = None
        self.last_baseline_event_seq = 0

    def apply(self, event: ResearchEvent, issues: list[dict[str, Any]]) -> None:
        p = event.payload
        kind = event.kind
        if kind in ("BOOK", "REST_BOOK"):
            token = str(p.get("token_id", ""))
            if token not in (self.manifest.yes_token_id, self.manifest.no_token_id):
                return
            try:
                bids = {str(price): _decimal(qty) for price, qty in p["bids"] if _decimal(qty) > 0}
                asks = {str(price): _decimal(qty) for price, qty in p["asks"] if _decimal(qty) > 0}
                self.books[token] = dict(bids=bids, asks=asks, valid=True,
                    seq=event.event_seq, generation=p.get("snapshot_generation")
                    or event.connection_generation or 0,
                    source="REST" if kind == "REST_BOOK" else "WS")
                if p.get("tick_size") is not None:
                    self.ticks[token] = _decimal(p["tick_size"])
            except (KeyError, TypeError, ValueError) as exc:
                _issue(issues, "book_reconstruction", f"events:{event.event_seq}", type(exc).__name__)
        elif kind == "DELTA":
            token = str(p.get("token_id", ""))
            book = self.books.get(token)
            if book is None or not book["valid"]:
                _issue(issues, "book_reconstruction", f"events:{event.event_seq}",
                       "delta lacks valid prior full book")
                return
            try:
                side = "bids" if p["side"] == "BUY" else "asks"
                price = str(p["price"])
                qty = _decimal(p["size"])
                if qty > 0:
                    book[side][price] = qty
                else:
                    book[side].pop(price, None)
                book["seq"] = event.event_seq
            except (KeyError, TypeError, ValueError) as exc:
                _issue(issues, "book_reconstruction", f"events:{event.event_seq}", type(exc).__name__)
        elif kind == "TICK":
            token = str(p.get("token_id", ""))
            if token in (self.manifest.yes_token_id, self.manifest.no_token_id):
                try:
                    self.ticks[token] = _decimal(p["tick_size"])
                except (KeyError, ValueError) as exc:
                    _issue(issues, "tick_reconstruction", f"events:{event.event_seq}", type(exc).__name__)
        elif kind == "CONNECTION":
            stream = p.get("stream")
            if stream in self.connected:
                self.connected[stream] = p.get("state") == "UP"
                self.generation[stream] = event.connection_generation or 0
                if stream == "market" and not self.connected[stream]:
                    for book in self.books.values():
                        book["valid"] = False
        elif kind in ("ORDER_STATE", "ORDER_ACK", "ORDER_SUBMIT", "CANCEL_REQUEST", "CANCEL_ACK"):
            before = {oid: dict(row) for oid, row in self.orders.items()}
            self._order(event)
            if (kind in ("ORDER_STATE", "ORDER_ACK", "CANCEL_REQUEST", "CANCEL_ACK")
                and self.orders != before):
                self.material_order_seqs.add(event.event_seq)
        elif kind == "FILL_REVISION":
            self._fill(event, issues)
        elif kind == "BASELINE_DECISION":
            if p.get("phase") is None:
                self.baseline_state = str(p.get("strategy_state", "UNKNOWN"))
                self.baseline_trigger = p.get("pair_trigger")
                self.baseline_peak = (_decimal(p["peak_locked_pnl"])
                    if p.get("peak_locked_pnl") is not None else None)
                self.baseline_quote = _quote_projection(p.get("pure_quote"))
                self.last_baseline_event_seq = event.event_seq
            elif p.get("phase") == "EFFECTIVE_QUOTE":
                self.effective_quote = _quote_projection(p.get("quote"))
        elif kind == "SAFETY":
            if p.get("first_finalized") or p.get("transition") == "FIRST_FINALIZED":
                self.first_finalized = True
                if self.first_finalized_at is None:
                    self.first_finalized_at = event.available_at_us
            if p.get("pair_terminal") or p.get("transition") == "PAIR_TERMINAL":
                self.pair_terminal = True
            if p.get("transition") == "FILL_TRUTH_RECONCILE" and p.get("order_id"):
                order_id = str(p["order_id"])
                if p.get("outcome") == "COMPLETE":
                    self.unresolved.discard(order_id)
                else:
                    self.unresolved.add(order_id)
            if "market_recovery_pending" in p:
                self.recovery_pending = bool(p["market_recovery_pending"])

    def _order(self, event: ResearchEvent) -> None:
        p = event.payload
        if event.kind in ("CANCEL_REQUEST", "CANCEL_ACK"):
            ids = set(p.get("order_ids") or ())
            for oid, row in self.orders.items():
                if oid not in ids and p.get("token_id") != row.get("token_id") and p.get("scope") != "ALL":
                    continue
                if event.kind == "CANCEL_REQUEST":
                    row["cancel_requested_at_us"] = event.available_at_us
                elif p.get("outcome") == "ACK":
                    row["cancel_acknowledged_at_us"] = event.available_at_us
            return
        rows = p.get("orders")
        if isinstance(rows, (list, tuple)):
            visible = {str(row.get("order_id")) for row in rows}
            for oid, row in self.orders.items():
                if row.get("token_id") == p.get("token_id") and oid not in visible:
                    row["visible"] = False
        else:
            rows = [p]
        for incoming in rows:
            incoming_id = incoming.get("order_id") if isinstance(incoming, Mapping) else None
            if not incoming_id:
                continue
            oid = str(incoming_id)
            old = self.orders.get(oid, {})
            role = incoming.get("role") or old.get("role")
            if role not in ("FIRST", "PAIR"):
                continue
            row = dict(old)
            row.update({key: incoming[key] for key in ("token_id", "price", "qty", "remaining_qty")
                        if key in incoming})
            row["role"] = role
            row.setdefault("submitted_at_us", event.available_at_us)
            if event.kind == "ORDER_ACK":
                row["acknowledged_at_us"] = event.available_at_us
            if any(key in incoming for key in ("remaining_size", "remaining_qty", "qty")):
                filled = sum((_decimal(fill["qty"]) for fill in self.fills.values()
                              if fill["accepted"] and fill["order_id"] == oid), Decimal(0))
                row["reported_remaining"] = _decimal(incoming.get("remaining_size",
                    incoming.get("remaining_qty", incoming.get("qty", 0))))
                row["filled_at_update"] = max(filled, _decimal(incoming.get("size_matched", 0)))
            lifecycle = str(incoming.get("state") or incoming.get("status") or
                            incoming.get("transition") or row.get("lifecycle", "UNKNOWN"))
            row["lifecycle"] = lifecycle
            row["terminal"] = incoming.get("cancel") is True or lifecycle.upper() in (
                "CANCELED", "CANCELLED", "REJECTED", "DONE", "PAIR_TERMINAL")
            row["visible"] = not row["terminal"]
            if incoming.get("quarantined") is True or lifecycle.upper() == "QUARANTINED":
                self.unresolved.add(oid)
            elif incoming.get("quarantined") is False:
                self.unresolved.discard(oid)
            self.orders[oid] = row

    def _fill(self, event: ResearchEvent, issues: list[dict[str, Any]]) -> None:
        p = event.payload
        if p.get("role") not in ("FIRST", "PAIR") or p.get("side") != "BUY":
            return
        key = (str(p.get("trade_id", "")), str(p.get("maker_order_id", "")))
        if not all(key):
            _issue(issues, "fill_identity", f"events:{event.event_seq}", "missing fill key")
            return
        try:
            qty, price = _decimal(p.get("qty", 0)), _decimal(p.get("price", 0))
        except ValueError:
            _issue(issues, "fill_identity", f"events:{event.event_seq}", "nonfinite fill economics")
            return
        accepted = p.get("accepted") is True and not p.get("quarantined") and qty > 0
        if accepted and (p.get("disposition") in ("FAILED", "QUARANTINED")
                         or p.get("status") == "FAILED"):
            _issue(issues, "fill_identity", f"events:{event.event_seq}",
                   "failed/quarantined fill marked economic")
        if accepted:
            registered = self.orders.get(key[1])
            if (registered is None or registered.get("role") != p["role"] or
                registered.get("token_id") != p.get("token_id")):
                _issue(issues, "fill_identity", f"events:{event.event_seq}",
                       "economic fill lacks matching registered owned order")
        prior = self.fills.get(key)
        if prior and accepted and prior["accepted"] and (prior["qty"] != qty or prior["price"] != price):
            _issue(issues, "fill_identity", f"events:{event.event_seq}",
                   "accepted fill key changed economic quantity or price")
        self.fills[key] = dict(role=p["role"], token_id=p.get("token_id"), qty=qty,
                               price=price, accepted=accepted, order_id=key[1])
        if p.get("quarantined") or p.get("disposition") in ("FAILED", "QUARANTINED"):
            self.unresolved.add(key[1])
        if self.first_fill is None and p["role"] == "FIRST" and accepted:
            self.first_fill = event
            self.first_side = ("YES" if p.get("token_id") == self.manifest.yes_token_id else
                               "NO" if p.get("token_id") == self.manifest.no_token_id else None)
            if self.first_side is None:
                _issue(issues, "fill_identity", f"events:{event.event_seq}",
                       "FIRST fill token outside selected condition")

    def inventory(self) -> tuple[Decimal, Decimal, Decimal, Decimal]:
        fq = fc = pq = pc = Decimal(0)
        for fill in self.fills.values():
            if not fill["accepted"]:
                continue
            qty = fill["qty"]
            if fill["role"] == "FIRST":
                fq += qty
                fc += qty * fill["price"]
            else:
                pq += qty
                pc += qty * fill["price"]
        return fq, fc, pq, pc

    def check_snapshot(self, state: StateSnapshot, issues: list[dict[str, Any]]) -> None:
        where = f"states:{state.snapshot_seq}"
        observed_quote = (_quote_projection(state.baseline_quote.model_dump())
            if state.baseline_quote is not None else None)
        observed_effective = (_quote_projection(state.effective_quote.model_dump())
            if state.effective_quote is not None else None)
        observed_peak = (_decimal(state.baseline_peak_quoted_pnl_usd)
            if state.baseline_peak_quoted_pnl_usd is not None else None)
        if (state.baseline_state != self.baseline_state or
            state.baseline_pair_trigger != self.baseline_trigger or
            observed_peak != self.baseline_peak or
            observed_quote != self.baseline_quote or
            observed_effective != self.effective_quote):
            gate = ("terminal_decision_material_snapshot" if
                state.sample_kind == "SAFETY" and
                state.knowledge_seq == self.last_baseline_event_seq and
                self.baseline_state in TERMINAL_BASELINE_STATES else
                "baseline_reconstruction")
            _issue(issues, gate, where,
                   "FavoriteFirst state/trigger/peak/quote differs from causal decision prefix")
        fq, fc, pq, pc = self.inventory()
        expected = {"first_qty": fq, "first_cost_usd": fc,
                    "pair_qty": pq, "pair_cost_usd": pc,
                    "paired_qty": min(fq, pq),
                    "unpaired_first_qty": fq - min(fq, pq),
                    "unpaired_opposite_qty": pq - min(fq, pq)}
        if self.first_side in ("YES", "NO"):
            expected["yes_qty"] = fq if self.first_side == "YES" else pq
            expected["no_qty"] = pq if self.first_side == "YES" else fq
        for field, amount in expected.items():
            if _decimal(getattr(state, field)) != amount:
                _issue(issues, "inventory", where, f"{field}: expected {amount}; observed {getattr(state, field)}")
        if state.first_finalized != self.first_finalized or state.pair_terminal_authoritative != self.pair_terminal:
            _issue(issues, "lifecycle", where, "finalization/terminal flag differs from causal events")
        if state.order_state_resolved != (not self.unresolved and not self.recovery_pending):
            _issue(issues, "working_orders", where, "order resolution differs from causal events")
        self._check_orders(state, issues)
        self._check_books(state, issues)
        self._check_features(state, issues)

    def _check_features(self, state: StateSnapshot, issues: list[dict[str, Any]]) -> None:
        features = state.features
        where = f"states:{state.snapshot_seq}"
        direct = {"first_qty": state.first_qty, "first_cost_usd": state.first_cost_usd,
                  "yes_qty": state.yes_qty, "no_qty": state.no_qty,
                  "paired_qty": state.paired_qty,
                  "unpaired_qty": state.unpaired_first_qty,
                  "first_vwap": state.first_vwap,
                  "market_elapsed_s": state.market_elapsed_s,
                  "since_first_fill_s": state.since_first_known_fill_s,
                  "time_to_end_s": state.seconds_to_end,
                  "yes_bid": state.yes_book.best_bid,
                  "yes_ask": state.yes_book.best_ask,
                  "no_bid": state.no_book.best_bid,
                  "no_ask": state.no_book.best_ask}
        for field, expected in direct.items():
            observed = getattr(features, field)
            if observed is not None and expected is not None and not math.isclose(
                    observed, float(expected), abs_tol=1e-8, rel_tol=1e-9):
                _issue(issues, "features", where, f"{field} differs from as-known snapshot")
        if features.first_side != state.first_side:
            _issue(issues, "features", where, "first side differs from as-known snapshot")
        if self.first_fill is not None:
            for field, payload_key in (("first_fill_price", "price"), ("first_fill_qty", "qty")):
                observed = getattr(features, field)
                if observed is not None and not math.isclose(
                        observed, float(_decimal(self.first_fill.payload[payload_key])),
                        abs_tol=1e-8, rel_tol=1e-9):
                    _issue(issues, "features", where, f"{field} differs from FIRST fill")
        if (features.executable_pair_ev_usd is not None or
            features.second_leg_fill_probability_3s is not None or
            features.btc_available is True or
            any(getattr(features, field) is not None for field in (
                "btc_realized_vol_20s", "btc_acceleration_3s", "btc_open_distance_bps",
                "btc_direction_persistence_5s", "btc_log_return_1s", "btc_log_return_3s",
                "btc_log_return_5s", "btc_log_return_10s", "btc_log_return_20s"))):
            _issue(issues, "features", where, "M4A feature contains unsupported model/BTC value")

    def _check_orders(self, state: StateSnapshot, issues: list[dict[str, Any]]) -> None:
        expected: dict[str, Decimal] = {}
        for oid, row in self.orders.items():
            if row.get("terminal") or not row.get("visible", True) or row.get("price") is None:
                continue
            filled = sum((f["qty"] for f in self.fills.values()
                          if f["accepted"] and f["order_id"] == oid), Decimal(0))
            remaining = max(Decimal(0), row.get("reported_remaining",
                _decimal(row.get("remaining_qty", row.get("qty", 0))))
                - max(Decimal(0), filled - row.get("filled_at_update", Decimal(0))))
            if remaining > 0:
                expected[oid] = remaining
        observed = {row.order_id: _decimal(row.remaining_qty) for row in state.working_orders}
        if expected != observed:
            _issue(issues, "working_orders", f"states:{state.snapshot_seq}",
                   "working order identities or remaining quantities differ")
        for working in state.working_orders:
            order = self.orders.get(working.order_id)
            if order is None:
                continue
            filled = sum((f["qty"] for f in self.fills.values()
                          if f["accepted"] and f["order_id"] == working.order_id), Decimal(0))
            if _decimal(working.cumulative_fill_qty) != filled or working.terminal_authoritative:
                _issue(issues, "working_orders", f"states:{state.snapshot_seq}",
                       f"invalid working fill/terminal state for {working.order_id}")
            if (working.role != order["role"] or
                working.quote.token_id != (order.get("token_id") or
                    (self.manifest.yes_token_id if order["role"] == "FIRST" and
                     state.first_side == "YES" else self.manifest.no_token_id)) or
                _decimal(working.quote.price) != _decimal(order["price"]) or
                _decimal(working.quote.qty) != _decimal(order.get("qty", expected.get(working.order_id, Decimal(0)) + filled)) or
                working.submitted_at_us != order.get("submitted_at_us") or
                working.acknowledged_at_us != order.get("acknowledged_at_us") or
                working.cancel_requested_at_us != order.get("cancel_requested_at_us") or
                working.cancel_acknowledged_at_us != order.get("cancel_acknowledged_at_us") or
                working.lifecycle != order.get("lifecycle")):
                _issue(issues, "working_orders", f"states:{state.snapshot_seq}",
                       "working order quote/role/lifecycle differs")

    def _check_books(self, state: StateSnapshot, issues: list[dict[str, Any]]) -> None:
        for token, snapshot in ((self.manifest.yes_token_id, state.yes_book),
                                (self.manifest.no_token_id, state.no_book)):
            book = self.books.get(token)
            valid = bool(book and book["valid"])
            where = f"states:{state.snapshot_seq}:{token}"
            if snapshot.token_id != token or snapshot.valid != valid:
                _issue(issues, "book_reconstruction", where, "token or validity mismatch")
                continue
            if not valid:
                continue
            assert book is not None
            bids = sorted(book["bids"].items(), key=lambda pair: _decimal(pair[0]), reverse=True)[:5]
            asks = sorted(book["asks"].items(), key=lambda pair: _decimal(pair[0]))[:5]
            for side, expected, actual in (("bids", bids, snapshot.bids),
                                           ("asks", asks, snapshot.asks)):
                projected = [(_decimal(price), qty) for price, qty in expected]
                recorded = [(_decimal(level.price), _decimal(level.qty)) for level in actual]
                if projected != recorded:
                    _issue(issues, "book_reconstruction", where, f"{side} top-five differs")
            best_bid = _decimal(bids[0][0]) if bids else None
            best_ask = _decimal(asks[0][0]) if asks else None
            expected_top = {"best_bid": best_bid, "best_ask": best_ask,
                            "best_bid_qty": bids[0][1] if bids else None,
                            "best_ask_qty": asks[0][1] if asks else None,
                            "mid": (best_bid + best_ask) / 2 if best_bid is not None and best_ask is not None else None,
                            "spread": best_ask - best_bid if best_bid is not None and best_ask is not None else None}
            if any((_decimal(getattr(snapshot, field)) if getattr(snapshot, field) is not None else None) != value
                   for field, value in expected_top.items()):
                _issue(issues, "book_reconstruction", where, "derived top-of-book differs")
            if snapshot.book_seq != book["seq"] or snapshot.snapshot_generation != book["generation"] or snapshot.source != book["source"]:
                _issue(issues, "book_reconstruction", where, "sequence/generation/source differs")
        tick = self.ticks.get(self.manifest.yes_token_id) or self.ticks.get(self.manifest.no_token_id) or Decimal(0)
        if _decimal(state.tick_size) != tick:
            _issue(issues, "tick_reconstruction", f"states:{state.snapshot_seq}",
                   f"expected {tick}; observed {state.tick_size}")
        if (state.market_connected != self.connected["market"] or
            state.user_connected != self.connected["user"] or
            state.market_generation != self.generation["market"] or
            state.user_generation != self.generation["user"]):
            _issue(issues, "connection", f"states:{state.snapshot_seq}",
                   "connection state/generation differs from causal events")
        if state.coverage_valid and (state.quality_reasons or not state.market_connected or
            not state.user_connected or not all(book.valid for book in
                (state.yes_book, state.no_book))):
            _issue(issues, "coverage", f"states:{state.snapshot_seq}",
                   "snapshot claims valid coverage despite unavailable input")
        if not state.coverage_valid and not state.quality_reasons:
            _issue(issues, "coverage", f"states:{state.snapshot_seq}",
                   "invalid snapshot lacks quality reason")


def _episode_checks(manifest: DatasetManifest, events: list[ResearchEvent],
                    episodes: list[Episode], states: list[StateSnapshot],
                    issues: list[dict[str, Any]]) -> dict[str, Episode]:
    by_id: dict[str, list[Episode]] = defaultdict(list)
    for episode in episodes:
        by_id[episode.episode_id].append(episode)
        if (episode.dataset_id != manifest.dataset_id or episode.condition_id != manifest.condition_id
            or episode.yes_token_id != manifest.yes_token_id or episode.no_token_id != manifest.no_token_id
            or episode.market_slug != manifest.market_slug or
            episode.strategy_config_id != manifest.strategy_config_id or
            manifest.session_id not in episode.run_ids):
            _issue(issues, "identity", f"episodes:{episode.record_id}", "manifest identity mismatch")
    first = next((event for event in events if event.kind == "FILL_REVISION" and
                  event.payload.get("role") == "FIRST" and event.payload.get("side") == "BUY" and
                  event.payload.get("accepted") is True and not event.payload.get("quarantined") and
                  _safe_positive(event.payload.get("qty"))), None)
    latest: dict[str, Episode] = {}
    for episode_id, revisions in by_id.items():
        latest[episode_id] = revisions[-1]
        if first is None:
            _issue(issues, "episode_lifecycle", episode_id, "no authoritative FIRST fill")
            continue
        p = first.payload
        expected_id = hashlib.sha256(json.dumps([manifest.condition_id,
            str(p.get("maker_order_id"))], separators=(",", ":")).encode()).hexdigest()
        if episode_id != expected_id or revisions[0].first_fill_known_at_us != first.available_at_us:
            _issue(issues, "episode_lifecycle", episode_id, "FIRST fill identity/knowledge mismatch")
        try:
            economics_match = (_decimal(revisions[0].first_match_qty) == _decimal(p.get("qty"))
                and _decimal(revisions[0].first_match_price) == _decimal(p.get("price")))
        except ValueError:
            economics_match = False
        if revisions[0].first_order_id != str(p.get("maker_order_id")) or not economics_match:
            _issue(issues, "episode_lifecycle", episode_id, "FIRST fill economics differ")
        registered = any(event.event_seq < first.event_seq and event.kind in ("ORDER_STATE", "ORDER_ACK")
                         and event.payload.get("order_id") == p.get("maker_order_id")
                         and event.payload.get("role") == "FIRST" for event in events)
        if not registered:
            _issue(issues, "fill_identity", episode_id, "FIRST fill lacks prior registered owned order")
        prior_status = "OPEN"
        finalized_at: int | None = None
        for index, revision in enumerate(revisions, 1):
            where = f"episodes:{revision.record_id}"
            if revision.revision != index or revision.record_id != f"{episode_id}:{index}":
                _issue(issues, "episode_lifecycle", where, "revision progression/identity invalid")
            if revision.revision_known_at_us < first.available_at_us or (
                index > 1 and revision.revision_known_at_us < revisions[index - 2].revision_known_at_us):
                _issue(issues, "episode_lifecycle", where, "revision knowledge moves backward")
            if prior_status != "OPEN" and not (prior_status == "COMPLETE" and revision.capture_status == "CENSORED"):
                _issue(issues, "episode_lifecycle", where, "illegal revision after terminal state")
            if revision.capture_status == "CENSORED" and prior_status == "COMPLETE" and "WRITER_FAILURE" not in revision.quality_reasons:
                _issue(issues, "episode_lifecycle", where, "late censor lacks writer failure reason")
            prior_status = revision.capture_status
            if revision.first_leg_finalized_at_us is not None:
                if finalized_at is None:
                    finalized_at = revision.first_leg_finalized_at_us
                elif finalized_at != revision.first_leg_finalized_at_us:
                    _issue(issues, "episode_lifecycle", where, "first finalization timestamp changed")
            elif finalized_at is not None:
                _issue(issues, "episode_lifecycle", where, "first finalization timestamp disappeared")
        associated = [state for state in states if state.episode_id == episode_id]
        if not associated or associated[0].sample_kind != "FILL":
            _issue(issues, "material_snapshots", episode_id, "missing FIRST FILL snapshot")
        if revisions[-1].capture_status in ("COMPLETE", "CENSORED") and not any(
                state.sample_kind == "FINAL" for state in associated):
            _issue(issues, "material_snapshots", episode_id, "missing FINAL snapshot")
        if revisions[-1].capture_status == "COMPLETE" and (revisions[-1].capture_end_us is None or
                revisions[-1].capture_end_us < revisions[-1].market_end_us):
            _issue(issues, "observation_window", episode_id, "COMPLETE before market end/T+300")
        if revisions[-1].capture_status == "OPEN":
            _issue(issues, "episode_lifecycle", episode_id, "writer closed with OPEN Episode")
        if revisions[-1].capture_status == "CENSORED" and not revisions[-1].quality_reasons:
            _issue(issues, "episode_lifecycle", episode_id, "censor reason absent")
        material = {(state.knowledge_seq, state.sample_kind, state.available_at_us)
                    for state in associated}
        prior_baseline_state = "UNKNOWN"
        for event in events:
            if event.event_seq < first.event_seq or event.available_at_us > revisions[-1].market_end_us:
                continue
            if (revisions[-1].capture_status == "CENSORED" and
                revisions[-1].capture_end_us is not None and
                event.available_at_us > revisions[-1].capture_end_us):
                continue
            causal_snapshot = (event.event_seq, "FILL", event.available_at_us)
            if event.kind == "FILL_REVISION" and causal_snapshot not in material:
                _issue(issues, "material_snapshots", f"events:{event.event_seq}", "missing fill snapshot")
            if event.kind == "SAFETY" and event.payload.get("transition") and (
                event.event_seq, "SAFETY", event.available_at_us) not in material:
                _issue(issues, "material_snapshots", f"events:{event.event_seq}", "missing safety snapshot")
            if event.kind == "BASELINE_DECISION" and event.payload.get("phase") is None:
                current = str(event.payload.get("strategy_state", "UNKNOWN"))
                if current != prior_baseline_state and current in TERMINAL_BASELINE_STATES and (
                    event.event_seq, "SAFETY", event.available_at_us) not in material:
                    _issue(issues, "terminal_decision_material_snapshot",
                           f"events:{event.event_seq}", "missing immediate terminal decision snapshot")
                prior_baseline_state = current
    return latest


def _safe_positive(value: Any) -> bool:
    try:
        return _decimal(value) > 0
    except ValueError:
        return False


def _coverage_passes(valid: int, expected: int) -> bool:
    """Compare the 99% threshold exactly, without floating-point rounding."""
    return expected > 0 and valid * 100 >= expected * 99


def _cadence(episode: Episode, states: list[StateSnapshot],
             events: list[ResearchEvent], issues: list[dict[str, Any]]) -> dict[str, Any]:
    periodic = [state for state in states if state.episode_id == episode.episode_id
                and state.sample_kind == "PERIODIC"]
    first_us = episode.first_fill_known_at_us
    intended_end_us = min(episode.market_end_us, first_us + 300_000_000)
    if episode.capture_status == "CENSORED":
        if episode.capture_end_us is None or episode.capture_end_us < first_us:
            _issue(issues, "observation_window", episode.episode_id,
                   "censored Episode lacks coherent capture end")
        end_us = min(intended_end_us, episode.capture_end_us or first_us)
    else:
        end_us = intended_end_us
    expected = max(0, (end_us - first_us) // 1_000_000)
    expected_times = [first_us + second * 1_000_000 for second in range(1, expected + 1)]
    # A disconnected interval is excluded only when durable connection evidence
    # brackets it. A solitary DOWN with no later UP remains disconnected to end.
    disconnected: set[int] = set()
    connected = False
    market_events = [e for e in events if e.kind == "CONNECTION" and e.payload.get("stream") == "market"]
    index = 0
    for second, target in enumerate(expected_times, 1):
        while index < len(market_events) and market_events[index].available_at_us <= target:
            connected = market_events[index].payload.get("state") == "UP"
            index += 1
        if not connected:
            disconnected.add(second)
    observed: dict[int, StateSnapshot] = {}
    for state in periodic:
        if state.scheduled_at_us is None:
            _issue(issues, "cadence", f"states:{state.snapshot_seq}", "periodic schedule absent")
            continue
        offset = state.scheduled_at_us - first_us
        if offset <= 0 or offset % 1_000_000:
            _issue(issues, "cadence", f"states:{state.snapshot_seq}", "periodic schedule off 1s grid")
            continue
        second = offset // 1_000_000
        if second in observed:
            _issue(issues, "cadence", f"states:{state.snapshot_seq}", "duplicate periodic slot")
        observed[second] = state
    required = [second for second in range(1, expected + 1) if second not in disconnected]
    valid = sum(1 for second in required if second in observed and observed[second].coverage_valid)
    invalid = sum(1 for second in required if second in observed and not observed[second].coverage_valid)
    missing = len(required) - valid - invalid
    coverage = valid / len(required) if required else None
    if coverage is None:
        _issue(issues, "cadence", episode.episode_id, "no connected post-fill interval to evaluate")
    elif not _coverage_passes(valid, len(required)):
        _issue(issues, "cadence", episode.episode_id, f"valid coverage {coverage:.4%} < 99%")
    longest = run = 0
    for second in range(1, expected + 1):
        if second in disconnected:
            run = 0
            continue
        if second not in observed:
            run += 1
            longest = max(longest, run)
        else:
            run = 0
    if longest > 2:
        _issue(issues, "snapshot_gap", episode.episode_id,
               f"unexplained connected missing run {longest}s > 2s")
    return {"expected_connected": len(required), "observed_valid": valid,
            "observed_invalid": invalid, "missing": missing,
            "excluded_disconnected": len(disconnected),
            "excluded_censored": max(0, (intended_end_us - end_us) // 1_000_000),
            "coverage": coverage,
            "longest_unexplained_gap_s": longest}


def audit_session(path: Path) -> dict[str, Any]:
    """Inspect one session without mutating it; all findings are deterministic."""
    path = Path(path)
    issues = Findings()
    raw_manifest = _read_json(path / "manifest.json", issues)
    raw_quality = _read_json(path / "quality.json", issues)
    manifest: DatasetManifest | None = None
    if raw_manifest is not None:
        try:
            manifest = DatasetManifest.model_validate_json(json.dumps(raw_manifest, allow_nan=False))
        except ValidationError:
            _issue(issues, "manifest", "manifest.json", "schema validation failed")
    if manifest is None:
        return {"path": SECRET_VALUE.sub("[REDACTED]", str(path)), "integrity": "FAIL", "issues": issues,
                "issue_count": issues.total, "counts": {}, "episodes": [],
                "session_id": path.name, "dataset_id": path.parent.name, "manifest_parsed": False,
                "timing": "UNESTABLISHED"}
    try:
        manifest_sha256 = _hash(path / "manifest.json")
    except OSError as exc:
        _issue(issues, "artifacts", "manifest.json", type(exc).__name__)
        manifest_sha256 = "UNAVAILABLE"
    if path.name != manifest.session_id or path.parent.name != manifest.dataset_id:
        _issue(issues, "identity", "manifest.json", "directory and manifest IDs differ")
    config_bytes = json.dumps(dict(manifest.effective_strategy_config), sort_keys=True,
                              separators=(",", ":"), allow_nan=False).encode()
    if manifest.config_sha256 != hashlib.sha256(config_bytes).hexdigest():
        _issue(issues, "manifest", "manifest.json", "effective configuration hash differs")
    if not re.fullmatch(r"[0-9a-f]{40}", manifest.code_commit):
        _issue(issues, "manifest", "manifest.json", "source commit identity unavailable")
    if dict(manifest.payload_versions) != {"ResearchEvent": 1, "Episode": 1, "StateSnapshot": 1}:
        _issue(issues, "manifest", "manifest.json", "payload version inventory differs")
    if manifest.close_status != "COMPLETE" or manifest.ended_at_us is None:
        _issue(issues, "eligibility", "manifest.json", "session lacks COMPLETE close certificate")
    if manifest.ended_at_us is not None and manifest.ended_at_us < manifest.started_at_us:
        _issue(issues, "manifest", "manifest.json", "end before start")
    if (manifest.writer_error or manifest.observer_failures or manifest.rejected_full or
        manifest.rejected_closed or manifest.lost_event_count or manifest.gap_count or
        manifest.accepted_count != manifest.persisted_count):
        _issue(issues, "eligibility", "manifest.json", "known loss or writer degradation")
    if raw_quality is None:
        _issue(issues, "eligibility", "quality.json", "quality evidence absent")
    else:
        if (raw_quality.get("dataset_id") != manifest.dataset_id or
            raw_quality.get("session_id") != manifest.session_id or
            raw_quality.get("status") != "FINALIZING" or
            raw_quality.get("clean_close") is not None or
            raw_quality.get("accepted") != manifest.accepted_count or
            raw_quality.get("persisted") != manifest.persisted_count or
            raw_quality.get("rejected_full") != manifest.rejected_full or
            raw_quality.get("rejected_closed") != manifest.rejected_closed or
            raw_quality.get("lost_event_count") != manifest.lost_event_count or
            raw_quality.get("gap_count") != manifest.gap_count or
            raw_quality.get("durable_event_watermark") != manifest.event_watermark or
            raw_quality.get("observer_failures") != manifest.observer_failures):
            _issue(issues, "quality", "quality.json", "quality contradicts manifest or is degraded")
    if set(manifest.file_sha256) != set(ARTIFACTS):
        _issue(issues, "artifacts", "manifest.json", "artifact inventory differs from required files")
    for name in ARTIFACTS:
        artifact = path / name
        if name not in manifest.file_sha256:
            _issue(issues, "artifacts", name, "manifest checksum absent")
        try:
            if _hash(artifact) != manifest.file_sha256.get(name):
                _issue(issues, "artifacts", name, "SHA-256 differs from manifest")
        except OSError as exc:
            _issue(issues, "artifacts", name, type(exc).__name__)
    events: list[ResearchEvent] = _read_jsonl(path / ARTIFACTS[0], ResearchEvent, issues)
    episodes: list[Episode] = _read_jsonl(path / ARTIFACTS[1], Episode, issues)
    states: list[StateSnapshot] = _read_jsonl(path / ARTIFACTS[2], StateSnapshot, issues)
    source_cache: dict[Path, tuple[str, int]] = {}
    for records in (events, episodes, states):
        _check_source_refs(records, issues, source_cache)
    if len(events) + len(episodes) + len(states) != manifest.persisted_count:
        _issue(issues, "counts", "manifest.json", "persisted count differs from JSONL records")
    if raw_quality is not None and raw_quality.get("gap_rows_persisted") != sum(
            event.kind == "GAP" for event in events):
        _issue(issues, "quality", "quality.json", "GAP row count differs from JSONL")
    last_seq = last_us = 0
    for event in events:
        where = f"events:{event.event_seq}"
        if event.session_id != manifest.session_id or event.condition_id not in (manifest.condition_id, ""):
            _issue(issues, "identity", where, "event identity differs from manifest")
        if (event.available_at_us < manifest.started_at_us or
            (manifest.ended_at_us is not None and event.available_at_us > manifest.ended_at_us)):
            _issue(issues, "causal_clock", where, "event availability outside session clock bounds")
        if event.available_at_us < last_us or (event.received_at_us is not None and
                                               event.available_at_us < event.received_at_us):
            _issue(issues, "causal_clock", where, "available time moves backward or precedes receipt")
        if event.event_seq <= last_seq:
            _issue(issues, "event_seq", where, "duplicate or reversed event sequence")
        if event.kind == "GAP":
            try:
                start = int(event.payload["lost_start_seq"])
                end = int(event.payload["lost_end_seq"])
                if (start != event.event_seq or end < start or
                    int(event.payload["count"]) != end - start + 1 or
                    not isinstance(event.payload.get("reason"), str)):
                    raise ValueError("invalid GAP range")
                if start != last_seq + 1:
                    _issue(issues, "event_seq", where, "missing sequence before GAP")
                last_seq = end
            except (KeyError, ValueError, TypeError):
                _issue(issues, "event_seq", where, "invalid GAP payload")
                last_seq = event.event_seq
            _issue(issues, "eligibility", where, "known Research GAP")
        else:
            if event.event_seq != last_seq + 1:
                _issue(issues, "event_seq", where, f"missing sequence {last_seq + 1}..{event.event_seq - 1}")
            last_seq = event.event_seq
        last_us = event.available_at_us
    if last_seq != manifest.event_watermark:
        _issue(issues, "event_seq", "manifest.json", "event watermark differs from stream")
    latest = _episode_checks(manifest, events, episodes, states, issues)
    replay = _Replay(manifest)
    event_index = 0
    previous_snapshot: dict[str, int] = {}
    previous_knowledge: dict[str, int] = {}
    previous_available: dict[str, int] = {}
    for state in states:
        where = f"states:{state.snapshot_seq}"
        if state.dataset_id != manifest.dataset_id or state.session_id != manifest.session_id:
            _issue(issues, "identity", where, "snapshot identity differs from manifest")
        expected_snapshot_id = f"{state.episode_id}:{manifest.session_id}:{state.snapshot_seq}"
        if (state.snapshot_id != expected_snapshot_id or state.record_id != expected_snapshot_id or
            state.episode_id not in latest):
            _issue(issues, "identity", where, "snapshot/Episode record identity differs")
        prior = previous_snapshot.get(state.episode_id, 0)
        if state.snapshot_seq != prior + 1:
            _issue(issues, "snapshot_seq", where, "snapshot sequence not contiguous")
        previous_snapshot[state.episode_id] = state.snapshot_seq
        if (state.knowledge_seq < previous_knowledge.get(state.episode_id, 0) or
            state.available_at_us < previous_available.get(state.episode_id, 0)):
            _issue(issues, "knowledge_seq", where, "snapshot causal boundary moved backward")
        previous_knowledge[state.episode_id] = state.knowledge_seq
        previous_available[state.episode_id] = state.available_at_us
        if state.knowledge_seq > manifest.event_watermark:
            _issue(issues, "knowledge_seq", where, "snapshot refers beyond event watermark")
        while event_index < len(events) and events[event_index].event_seq <= state.knowledge_seq:
            try:
                replay.apply(events[event_index], issues)
            except (KeyError, TypeError, ValueError, IndexError) as exc:
                _issue(issues, "reconstruction", f"events:{events[event_index].event_seq}",
                       type(exc).__name__)
            event_index += 1
        if (event_index and events[event_index - 1].kind == "GAP" and
            state.knowledge_seq < int(events[event_index - 1].payload.get("lost_end_seq", 0))):
            _issue(issues, "knowledge_seq", where, "snapshot inside lost sequence range")
        if event_index and events[event_index - 1].available_at_us > state.available_at_us:
            _issue(issues, "knowledge_seq", where, "snapshot contains future event")
        if state.created_at_us != state.available_at_us:
            _issue(issues, "causal_clock", where, "snapshot creation differs from availability")
        revisions = [episode for episode in episodes if episode.episode_id == state.episode_id and
                     episode.revision == state.episode_revision]
        if not revisions or revisions[0].revision_known_at_us > state.available_at_us:
            _issue(issues, "knowledge_seq", where, "Episode revision not yet known")
        try:
            replay.check_snapshot(state, issues)
        except (ValueError, TypeError) as exc:
            _issue(issues, "reconstruction", where, type(exc).__name__)
    for event in events[event_index:]:
        try:
            replay.apply(event, issues)
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            _issue(issues, "reconstruction", f"events:{event.event_seq}", type(exc).__name__)
    if replay.first_fill is not None:
        material_orders = {(state.knowledge_seq, state.sample_kind, state.available_at_us)
                           for state in states}
        event_times = {event.event_seq: event.available_at_us for event in events}
        for seq in replay.material_order_seqs:
            if seq >= replay.first_fill.event_seq and (
                seq, "ORDER", event_times[seq]) not in material_orders:
                _issue(issues, "material_snapshots", f"events:{seq}", "missing changed-order snapshot")
    if replay.first_fill is not None and not episodes:
        _issue(issues, "episode_lifecycle", "episodes.jsonl", "authoritative FIRST fill lacks Episode")
    cadence = {episode_id: _cadence(episode, states, events, issues)
               for episode_id, episode in latest.items()}
    eligible = [episode_id for episode_id, episode in latest.items()
                if episode.capture_status == "COMPLETE" and episode.fill_truth_status == "COMPLETE"
                and not episode.quality_reasons]
    market_days: set[str] = set()
    for episode_id in eligible:
        try:
            market_days.add(datetime.fromtimestamp(latest[episode_id].market_start_us / 1e6,
                                                    UTC).date().isoformat())
        except (OverflowError, OSError, ValueError):
            _issue(issues, "market_day", _safe_episode_id(episode_id),
                   "market start outside supported UTC calendar")
    return {"path": SECRET_VALUE.sub("[REDACTED]", str(path)),
            "dataset_id": SECRET_VALUE.sub("[REDACTED]", manifest.dataset_id),
            "manifest_sha256": manifest_sha256,
            "code_commit": manifest.code_commit,
            "config_sha256": manifest.config_sha256,
            "session_id": SECRET_VALUE.sub("[REDACTED]", manifest.session_id),
            "manifest_parsed": True,
            "integrity": "FAIL" if issues else "PASS",
            "issues": list(issues), "issue_count": issues.total,
            "counts": {"events": len(events), "episode_revisions": len(episodes),
                       "snapshots": len(states), "gaps": sum(e.kind == "GAP" for e in events)},
            "episodes": [_safe_episode_id(eid) for eid in eligible],
            "excluded_episodes": len(latest) - len(eligible),
            "market_days": sorted(market_days),
            "cadence": {_safe_episode_id(eid): value for eid, value in cadence.items()},
            "timing": "UNESTABLISHED"}


def audit_collection(paths: Iterable[Path], *, timing_evidence: dict[str, Any] | None = None,
                     real_sessions: dict[str, dict[str, str]] | None = None) -> dict[str, Any]:
    """Audit sessions; caller assertions never certify real runs or engineering gates."""
    sessions = [audit_session(path) for path in sorted(map(Path, paths))]
    ids: set[tuple[str, str]] = set()
    issues = Findings()
    candidate_episodes: set[str] = set()
    candidate_market_days: set[str] = set()
    excluded = Counter[str]()
    for session in sessions:
        identity = (session.get("dataset_id", ""), session.get("session_id", ""))
        if identity in ids:
            _issue(issues, "collection", session["path"], "duplicate session identity")
        ids.add(identity)
        if session["integrity"] == "PASS":
            for episode in session["episodes"]:
                if episode in candidate_episodes:
                    _issue(issues, "collection", episode, "duplicate Episode across sessions")
                candidate_episodes.add(episode)
            candidate_market_days.update(session["market_days"])
            excluded["unverified_real_provenance"] += len(session["episodes"])
            excluded["episode_not_eligible"] += session.get("excluded_episodes", 0)
        else:
            excluded["session_integrity_fail"] += (
                len(session.get("episodes", [])) + session.get("excluded_episodes", 0))
    isolation: dict[str, Any] = {"status": "UNESTABLISHED", "reason":
        "no architecture-defined trusted controlled trading traces"}
    timing: dict[str, Any] = {"status": "UNESTABLISHED", "reason":
        "M4A.4 artifacts contain no trusted controlled callback or loop-lag measurements"}
    # The accepted M4A schema has no runtime LIVE attestation or controlled
    # timing/isolation evidence contract. Optional CLI files are assertions,
    # never a route to PASS. Do not echo their potentially sensitive contents.
    assertions = {"real_sessions_supplied": real_sessions is not None,
                  "timing_supplied": timing_evidence is not None,
                  "authority": "CALLER_ASSERTION", "accepted_for_gates": False}
    integrity = "FAIL" if issues or any(s["integrity"] != "PASS" for s in sessions) or not sessions else "PASS"
    collection = "UNESTABLISHED"
    overall = "FAIL" if integrity == "FAIL" else "INCOMPLETE"
    manifest_parsed = sum(s.get("manifest_parsed") is True for s in sessions)
    clean_sessions = sum(s["integrity"] == "PASS" for s in sessions)
    return {"audit_version": 1, "integrity": integrity, "collection": collection,
            "overall": overall, "session_count": len(sessions),
            "candidate_session_count": len(sessions),
            "manifest_parsed_session_count": manifest_parsed,
            "pre_manifest_failure_count": len(sessions) - manifest_parsed,
            "integrity_clean_session_count": clean_sessions,
            "collection_eligible_session_count": 0,
            "collection_ineligible_session_count": len(sessions),
            "eligible_episode_count": 0, "market_day_count": 0, "market_days": [],
            "candidate_episode_count": len(candidate_episodes),
            "candidate_market_day_count": len(candidate_market_days),
            "candidate_collection_threshold_met": (
                len(candidate_episodes) >= 20 and len(candidate_market_days) >= 3),
            "excluded_episode_reasons": dict(excluded),
            "live_attestation": {"status": "UNESTABLISHED", "reason":
                "no architecture-defined trusted runtime LIVE provenance"},
            "timing": timing, "isolation": isolation, "caller_assertions": assertions,
            "sessions": sessions, "issues": list(issues), "issue_count": issues.total,
            "meaning": "Telemetry readiness only; no Dynamic Exit EV edge conclusion"}


def write_audit(result: dict[str, Any], output_dir: Path) -> None:
    """Write derived results only; caller must place output outside raw sessions."""
    output_dir = Path(output_dir).resolve()
    for session in result["sessions"]:
        raw = Path(session["path"]).resolve()
        if output_dir == raw or output_dir.is_relative_to(raw):
            raise ValueError("audit output cannot be inside a raw session")
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "audit.json").write_text(json.dumps(result, indent=2, sort_keys=True,
        allow_nan=False) + "\n", encoding="utf-8")
    lines = ["# M4A.5 Audit", "", f"Overall: **{result['overall']}**",
             f"Integrity: **{result['integrity']}**", f"Collection: **{result['collection']}**",
             f"Timing: **{result['timing']['status']}**",
             f"Isolation: **{result['isolation']['status']}**", "",
             f"Sessions: {result['session_count']}; eligible Episodes: {result['eligible_episode_count']}; "
             f"UTC market-days: {result['market_day_count']}.", "",
             f"Session candidates: {result['candidate_session_count']}; manifests parsed: "
             f"{result['manifest_parsed_session_count']}; pre-manifest failures: "
             f"{result['pre_manifest_failure_count']}; integrity clean: "
             f"{result['integrity_clean_session_count']}.", "",
             f"Integrity-clean Episode candidates: {result['candidate_episode_count']}; "
             f"candidate UTC market-days: {result['candidate_market_day_count']}. "
             "These are not verified real collection.", "",
             "Caller LIVE/timing assertions cannot certify collection, latency or isolation.", "",
             "M4A readiness does not establish Dynamic Exit EV edge.", "", "## Findings", ""]
    if result["timing"]["status"] in ("PASS", "FAIL") and "callback" in result["timing"]:
        callback = result["timing"]["callback"]
        lag = result["timing"]["incremental_loop_lag"]
        lines.extend([f"Callback p99: {callback['p99_ms']} ms ({callback['samples']} samples).",
                      f"Incremental loop-lag p99: {lag['p99_ms']} ms ({lag['samples']} samples).", ""])
    elif result["timing"]["status"] == "UNESTABLISHED":
        lines.extend([result["timing"]["reason"], ""])
    lines.append(f"Excluded Episodes by reason: {result['excluded_episode_reasons']}.")
    lines.append("")
    for session in result["sessions"]:
        lines.append(f"- {session['path']}: {session['integrity']} ({session.get('issue_count', len(session['issues']))} findings)")
        for episode_id, cadence in session.get("cadence", {}).items():
            label = episode_id if re.fullmatch(r"[0-9a-f]{64}", episode_id) else "[invalid episode id]"
            lines.append(f"  - Episode {label}: connected expected {cadence['expected_connected']}, "
                         f"valid {cadence['observed_valid']}, invalid {cadence['observed_invalid']}, "
                         f"missing {cadence['missing']}, excluded disconnected "
                         f"{cadence['excluded_disconnected']}, excluded censored "
                         f"{cadence['excluded_censored']}, coverage {cadence['coverage']}, "
                         f"longest unexplained gap {cadence['longest_unexplained_gap_s']}s")
        for issue in session["issues"]:
            lines.append(f"  - {issue['gate']} {issue['where']}: {issue['detail']}")
    (output_dir / "audit_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

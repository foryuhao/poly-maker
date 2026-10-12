"""Single-market SM execution composition; no strategy, Engine or restart recovery.

Only this opt-in library path can write orders. M1 and FL entry points are unchanged.
An executor is single-use after unresolved exposure. Only complete, safely closed
zero-attempt history or time-expired known orders permit a new market session.
Library callers reserve expired order costs unless M2 proved zero-fill cancellation.
The CLI instead starts from official balance under the operator-managed funding
policy; historical events remain intact and no trading session is resumed.
Time and HTTP/WS boundaries may be replaced in offline tests.
"""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import math
import os
import re
import time
import uuid
from contextlib import suppress
from dataclasses import asdict, dataclass, field, replace
from decimal import ROUND_CEILING, Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from py_clob_client_v2.client import ClobClient

from polymaker.catalog.gamma import GammaClient
from polymaker.config import Config
from polymaker.domain import Quote, Side, TradeState
from polymaker.execution.filltruth import ReconciliationOutcome, reconcile_owned_order
from polymaker.execution.gateway import PlacementUnknown
from polymaker.journal import Journal
from polymaker.rdp_sm import (
    NORMAL_END_S,
    OFFICIAL_HOSTS,
    TRADING_END_S,
    TRADING_START_S,
    ReadOnlyClobClient,
    SessionStatus,
    _account,
    _book,
    _Issue,
    _market,
    _Reads,
    _require,
    _token_id,
    run_preflight,
)
from polymaker.state.store import StateStore
from polymaker.state.tracker import OrderEvent, TradeEvent, UserEventProcessor
from polymaker.userstream.parse import normalize_order, parse_trade, strict_number

CAPITAL_LIMIT = Decimal("30")
MIN_MAKER_BUY_NOTIONAL = Decimal("1")
MATCHED_REMAINING_TOLERANCE_SHARES = Decimal(".1")
SAFE_QUOTE_SKIP_REASONS = frozenset({
    "QUOTE_WOULD_TAKE", "QUOTE_PRICE_TICK_INVALID", "QUOTE_PRICE_TICK_OR_SIZE_INVALID",
    "NORMAL_LEG_NOTIONAL_BELOW_MINIMUM", "MAKER_BUY_NOTIONAL_BELOW_MINIMUM",
    "MARKET_SUBMISSION_CUTOFF",
})
# Same conservative fixed shutdown buffer used by the existing BTC5m runner.
CANCEL_BUFFER_S = 15.0


def maker_buy_size(price: Decimal, inventory_gap: Decimal) -> Decimal:
    """Smallest 0.01-share quantity covering the gap and the minimum BUY amount."""
    return max(inventory_gap, (MIN_MAKER_BUY_NOTIONAL / price).quantize(
        Decimal(".01"), rounding=ROUND_CEILING))


def _explicit_maker_rejection(item, notional) -> bool:
    """Recognize the venue's observed error response despite its success flag.

    Require the complete rejection shape and absence of all acceptance/fill
    evidence. An empty order ID alone, or an arbitrary error, remains UNKNOWN.
    """
    if not isinstance(item, dict):
        return False
    error = item.get("errorMsg")
    minimum = re.fullmatch(
        r"invalid amount for a marketable BUY order \(\$([0-9]+(?:\.[0-9]+)?)\), min size: \$1",
        error if isinstance(error, str) else "")
    known_error = error == "invalid post-only order: order crosses book" or (
        minimum is not None and Decimal(minimum[1]) == notional
        and 0 < notional < MIN_MAKER_BUY_NOTIONAL)
    return (
        isinstance(item, dict)
        and item.get("success") is True
        and known_error
        and all(item.get(k) == "" for k in ("orderID", "status", "makingAmount", "takingAmount"))
        and all(item.get(k) in (None, "") for k in ("order_id", "orderId", "id"))
        and all(item.get(k) in (None, []) for k in (
            "tradeIDs", "tradeIds", "trade_ids", "transactionsHashes", "transactionHashes",
            "transactions_hashes"))
    )


@dataclass(frozen=True)
class WindowQuote:
    market_id: str
    token_id: str
    outcome: str
    side: Side
    price: float
    size: float
    window_id: str


@dataclass
class Placement:
    attempt_id: str
    quote: WindowQuote
    outcome: str = "INTENT"
    order_id: str | None = None
    post_started: bool = False
    request_hash: str | None = None
    raw_response: Any = None

    @property
    def maximum(self) -> Decimal:
        return Decimal(str(self.quote.price)) * Decimal(str(self.quote.size))


@dataclass
class WindowResult:
    session_id: str
    window_id: str
    market_id: str
    start_time: float
    end_time: float = 0.0
    requested_quotes: list[WindowQuote] = field(default_factory=list)
    placement_results: list[dict[str, Any]] = field(default_factory=list)
    known_order_ids: list[str] = field(default_factory=list)
    confirmed_fills: list[dict[str, Any]] = field(default_factory=list)
    yes_inventory: float = 0.0
    no_inventory: float = 0.0
    delta: float = 0.0
    capital_committed: float = 0.0
    close_barrier: str = "BLOCKED"
    block_reason: str | None = None
    journal_references: list[str] = field(default_factory=list)
    t0_monotonic: float | None = None
    fill_deadline_monotonic: float | None = None
    close_monotonic: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RiskJournal(Journal):
    """Locked risk evidence, compatible with conservative and startup-balance funding."""

    @staticmethod
    def _history_cost_total(session, check):
        # New CLI accounting preserves the old evidence without charging it.
        if "capital_basis" not in session:
            check("historical_pending_cost_observed_usd" not in session)
            return Decimal(str(session["historical_pending_cost_reserved_usd"]))
        check(session["capital_basis"] == "OFFICIAL_BALANCE_AT_SESSION_START"
              and type(session["historical_pending_cost_reserved_usd"]) in (int, float)
              and session["historical_pending_cost_reserved_usd"] == 0)
        observed = session["historical_pending_cost_observed_usd"]
        check(type(observed) in (int, float) and math.isfinite(observed) and observed >= 0)
        return Decimal(str(observed))

    @staticmethod
    def _check_starting_observations(session, check):
        check(session["preflight_account_incomplete"] is None)
        for key in ("preflight_observed_order_ids", "preflight_observed_trade_ids"):
            ids = session[key]
            if "capital_basis" not in session:
                check(ids == [])
            else:
                check(isinstance(ids, list) and all(isinstance(i, str) and bool(i)
                      and i.strip() == i for i in ids) and len(ids) == len(set(ids)))

    @staticmethod
    def _check_zero_windows(group, result, check):
        """Prove absence of execution from events, including pre-submit failures."""
        check(all(e["kind"] in {
            "sm_session", "sm_strategy_window", "sm_quote_skipped", "sm_quote_refreshed",
            "sm_window_result", "sm_close_barrier", "sm_run_result"} for e in group))
        check(result["final_close_barrier"] == "SAFE_TO_CONTINUE"
              and result["execution_status"] in {"COMPLETED", "BLOCKED"})
        check(all(type(result[k]) is int and result[k] == 0
                  for k in ("confirmed_fills", "active_cycles")))
        check(all(type(result[k]) in (int, float) and result[k] == 0 for k in (
            "confirmed_yes", "confirmed_no", "delta", "completed_pairs",
            "residual_shares", "theoretical_pair_pnl")))
        check(all(type(result[k]) is int and result[k] >= 0 for k in (
            "total_windows", "normal_windows", "rebalance_windows", "blocked_windows")))
        windows = [e["data"]["payload"] for e in group if e["kind"] == "sm_window_result"]
        decisions = [e["data"]["payload"] for e in group if e["kind"] == "sm_strategy_window"]
        check(result["total_windows"] == result["normal_windows"] + result["rebalance_windows"]
              and isinstance(result["windows"], list)
              and len(result["windows"]) == len(windows) == len(decisions) == result["total_windows"])
        check([w["result"] for w in result["windows"]] == windows)
        check(len({w["window_id"] for w in windows}) == len(windows))
        blocked = 0
        for window, decision, summary in zip(windows, decisions, result["windows"], strict=True):
            check(set(window) == set(WindowResult.__dataclass_fields__))
            check(window["session_id"] == result["session_id"]
                  and window["market_id"] == result["market_id"]
                  and window["window_id"] == decision["window_id"]
                  and summary["state"] == decision["state"] == "NORMAL"
                  and Decimal(decision["delta"]) == 0)
            check(window["placement_results"] == window["known_order_ids"]
                  == window["confirmed_fills"] == [])
            check(all(type(window[k]) in (int, float) and window[k] == 0 for k in (
                "capital_committed", "yes_inventory", "no_inventory", "delta")))
            check(window["t0_monotonic"] is None and window["fill_deadline_monotonic"] is None)
            if window["close_barrier"] == "BLOCKED":
                check(window["block_reason"] == "OFFICIAL_CAPITAL_INSUFFICIENT")
                blocked += 1
            else:
                check(window["close_barrier"] == "SAFE_TO_CONTINUE")
        check(result["blocked_windows"] == blocked and result["rebalance_windows"] == 0)
        closes = [e for e in group if e["kind"] == "sm_close_barrier"]
        check(bool(closes) and closes[-1]["ts"] >= max(
            (e["ts"] for e in group if e["kind"] == "sm_window_result"), default=group[0]["ts"]))
        for event in closes:
            p = event["data"]["payload"]
            check(set(p) == {"result", "proofs", "confirmed_fills"}
                  and p["result"] == "SAFE_TO_CONTINUE" and p["proofs"] == {}
                  and p["confirmed_fills"] == [])
        check(isinstance(result["skipped_quotes"], list))
        for skip in result["skipped_quotes"]:
            check(set(skip) == {"elapsed_s", "reason"}
                  and skip["reason"] in SAFE_QUOTE_SKIP_REASONS)

    @staticmethod
    def _historical_no_fill_closed(group, oid, quote, funder):
        """A final M2 proof plus matching REST evidence, never a cancel ACK alone."""
        try:
            closes = [e for e in group if e["kind"] == "sm_close_barrier"]
            final_close_index = max(i for i, e in enumerate(group) if e["kind"] == "sm_close_barrier")
            final = closes[-1]["data"]["payload"]
            if (group[-1]["data"]["payload"]["final_close_barrier"] != "SAFE_TO_CONTINUE"
                    or final["result"] != "SAFE_TO_CONTINUE"):
                return False
            proof = final["proofs"][oid]
            row, trades = proof["order"], proof["trades"]
            ev = normalize_order(row)
            if not (set(proof) == {"order", "trades", "outcome"}
                    and proof["outcome"] == "COMPLETE" and ev is not None
                    and ev.order_id == oid and ev.market_id == quote["market_id"]
                    and ev.token_id == quote["token_id"] and ev.side is Side.BUY
                    and row["maker_address"].lower() == funder.lower()
                    and Decimal(str(row["price"])) == Decimal(quote["price"])
                    and Decimal(str(row["original_size"])) == Decimal(quote["size"])
                    and ev.status in {"CANCELED", "CANCELLED"} and ev.size_matched == 0
                    and row["associate_trades"] == [] and isinstance(trades, list)):
                return False
            evidence = [e for e in group if e["kind"] == "sm_official_reconciliation"
                        and e["data"]["payload"].get("order_id") == oid]
            latest = evidence[-1]["data"]["payload"]
            if (latest != {"order_id": oid, "order": row, "trades": trades, "filltruth": "COMPLETE"}
                    or evidence[-1]["ts"] > closes[-1]["ts"]):
                return False
            # Any fill evidence conflicts with release, even if a later summary says zero.
            terminal_seen = False
            for index, event in enumerate(group):
                p = event["data"]["payload"]
                if event["kind"] == "sm_trade" and p.get("maker_order_id") == oid:
                    return False
                if event["kind"] == "sm_order" and p.get("order_id") == oid:
                    if (p.get("size_matched") != 0 or index > final_close_index
                            or (terminal_seen and p.get("status") not in {"CANCELED", "CANCELLED"})):
                        return False
                    terminal_seen |= p.get("status") in {"CANCELED", "CANCELLED"}
                if event["kind"] == "sm_official_reconciliation":
                    if p.get("order_id") == oid:
                        if index > final_close_index:
                            return False
                        observed = normalize_order(p["order"])
                        if not (observed is not None and observed.order_id == oid
                                and observed.market_id == quote["market_id"]
                                and observed.token_id == quote["token_id"] and observed.side is Side.BUY
                                and observed.original_size == ev.original_size and observed.price == ev.price
                                and observed.size_matched == 0):
                            return False
                        if terminal_seen and observed.status not in {"CANCELED", "CANCELLED"}:
                            return False
                        terminal_seen |= observed.status in {"CANCELED", "CANCELLED"}
                    for raw in p["trades"]:
                        parsed = parse_trade(raw)
                        if parsed.taker_order_id == oid or any(leg.order_id == oid for leg in parsed.maker_legs):
                            return False
            return not any(f.get("maker_order_id") == oid for f in final["confirmed_fills"])
        except (KeyError, TypeError, ValueError, ArithmeticError, IndexError, AttributeError):
            return False  # Missing, malformed or contradictory evidence retains the reserve.

    @staticmethod
    def _read_zero_history(history, funder):
        # Planning is allowed; any placement, order or trade event still blocks.
        def check(condition):
            if not condition:
                raise ValueError("unproven zero-attempt history")

        def number(value):
            return type(value) in (int, float) and math.isfinite(value) and value >= 0

        def unique_object(pairs):
            result = {}
            for key, value in pairs:
                check(key not in result)
                result[key] = value
            return result

        def invalid_constant(_value):
            raise ValueError("nonfinite JSON")

        session_keys = {
            "session_id", "slug", "signer", "funder", "signature_type", "live_authority",
            "starting_spendable_usdc", "starting_official_balance_usdc", "test_capital_limit_usdc",
            "historical_pending_cost_reserved_usd", "historical_matched",
            "preflight_observed_order_ids", "preflight_observed_trade_ids", "preflight_account_incomplete",
        }
        counts = ["total_windows", "normal_windows", "rebalance_windows", "active_cycles",
                  "confirmed_fills", "blocked_windows"]
        economics = ["confirmed_yes", "confirmed_no", "delta", "completed_pairs",
                     "residual_shares", "theoretical_pair_pnl"]
        result_keys = set(counts + economics) | {
            "market_id", "session_id", "start_time", "stop_time", "stop_reason",
            "average_yes_cost", "average_no_cost", "final_close_barrier", "execution_status",
            "live_authority", "windows", "skipped_quotes", "stop_elapsed_s",
        }
        seen, retained, group = set(), {}, []
        slug_markets, market_slugs = {}, {}
        identity, previous_ts, sequence = None, 0, 0
        for line in history:
            check(line.endswith("\n"))
            row = json.loads(line, object_pairs_hook=unique_object, parse_constant=invalid_constant)
            check(isinstance(row, dict) and set(row) == {"ts", "kind", "data"})
            check(number(row["ts"]) and row["ts"] >= previous_ts)
            previous_ts = row["ts"]
            check(row["kind"] in {"sm_session", "sm_strategy_window", "sm_quote_skipped",
                                  "sm_quote_refreshed", "sm_window_result", "sm_close_barrier", "sm_run_result"})
            check((not group and row["kind"] == "sm_session")
                  or (bool(group) and row["kind"] != "sm_session"))
            data = row["data"]
            check(isinstance(data, dict) and set(data) == {"session_id", "payload"})
            sid, payload = data["session_id"], data["payload"]
            check(isinstance(sid, str) and sid.strip() == sid and bool(sid))
            check(isinstance(payload, dict))
            check(not group or sid == group[0]["data"]["session_id"])
            group.append(row)
            sequence += 1
            if row["kind"] != "sm_run_result":
                continue
            check(sid not in seen)
            seen.add(sid)
            session, result = group[0]["data"]["payload"], payload
            check(set(session) in (session_keys, session_keys | {
                "capital_basis", "historical_pending_cost_observed_usd"}) and set(result) == result_keys)
            check(session["session_id"] == result["session_id"] == sid)
            check(isinstance(session["slug"], str)
                  and re.fullmatch(r"btc-updown-5m-[0-9]+", session["slug"]) is not None)
            check(int(session["slug"].rsplit("-", 1)[1]) % 300 == 0)
            for key in ("signer", "funder"):
                check(isinstance(session[key], str)
                      and re.fullmatch(r"0x[0-9a-fA-F]{40}", session[key]) is not None)
            check(session["funder"].lower() == funder.lower())
            check(type(session["signature_type"]) is int and session["signature_type"] in (0, 2))
            current_identity = (session["signer"].lower(), session["signature_type"])
            check(identity is None or identity == current_identity)
            identity = current_identity
            check(session["live_authority"] == {0: "LOCAL_EOA_KEY_VERIFIED", 2: "NOT_VERIFIED_FOR_LIVE"}[
                session["signature_type"]]
                  and result["live_authority"] == session["live_authority"])
            RiskJournal._check_starting_observations(session, check)
            check(session["test_capital_limit_usdc"] == str(CAPITAL_LIMIT))
            for key in ("starting_spendable_usdc", "starting_official_balance_usdc",
                        "historical_pending_cost_reserved_usd"):
                check(number(session[key]))
            reserve = Decimal(str(session["historical_pending_cost_reserved_usd"]))
            available = max(0.0, session["starting_official_balance_usdc"] - float(reserve))
            check(session["starting_spendable_usdc"] <= available)
            matched = session["historical_matched"]
            check(isinstance(matched, dict))
            cost = Decimal(0)
            for tid, evidence in matched.items():
                check(isinstance(tid, str) and bool(tid) and tid.strip() == tid)
                check(isinstance(evidence, dict) and set(evidence) == {
                    "status", "decision", "pending_cost_reserved_usd", "redeem"})
                check(evidence["status"] in {"MATCHED", "MINED", "RETRYING"}
                      and evidence["decision"] == "HISTORICAL_MATCHED_NONBLOCKING"
                      and evidence["redeem"] == "NOT_VERIFIED")
                check(isinstance(evidence["pending_cost_reserved_usd"], str))
                amount = Decimal(evidence["pending_cost_reserved_usd"])
                check(amount.is_finite() and amount > 0)
                cost += amount
                # Same trade across sessions is one retained commitment.
                check(tid not in retained or retained[tid] == amount)
                retained[tid] = amount
            check(cost == RiskJournal._history_cost_total(session, check))
            check(set(retained) <= set(matched))  # no disappearance across sessions
            RiskJournal._check_zero_windows(group, result, check)
            check(result["average_yes_cost"] is None and result["average_no_cost"] is None)
            check(result["final_close_barrier"] == "SAFE_TO_CONTINUE"
                  and result["execution_status"] in {"BLOCKED", "COMPLETED"})
            check((result["stop_reason"], result["execution_status"]) in {
                ("BOOK_SIDE_MISSING", "BLOCKED"), ("MARKET_CUTOFF", "COMPLETED"),
                ("OFFICIAL_CAPITAL_INSUFFICIENT", "BLOCKED")})
            check(isinstance(result["market_id"], str)
                  and re.fullmatch(r"0x[0-9a-fA-F]{64}", result["market_id"]) is not None)
            slug, market = session["slug"], result["market_id"].lower()
            check(slug not in slug_markets or slug_markets[slug] == market)
            check(market not in market_slugs or market_slugs[market] == slug)
            slug_markets[slug], market_slugs[market] = market, slug
            check(number(result["start_time"]) and number(result["stop_time"]))
            check(result["start_time"] <= group[0]["ts"]
                  <= group[-2]["ts"] <= result["stop_time"] <= group[-1]["ts"])
            check(result["stop_elapsed_s"] is None or number(result["stop_elapsed_s"]))
            group = []
        check(not group)
        return sequence, retained, identity, seen

    @staticmethod
    def _read_history(history, funder, *, current_slug=None, official_time=None,
                      current_market_id=None, expired_orders=None):
        import io

        lines = list(history)
        try:
            return RiskJournal._read_zero_history(lines, funder)
        except (ValueError, TypeError, KeyError, ArithmeticError):
            if current_slug is None or official_time is None:
                raise

        def check(condition):
            if not condition:
                raise ValueError("unproven expired-market history")

        def unique(pairs):
            obj = {}
            for key, value in pairs:
                check(key not in obj)
                obj[key] = value
            return obj

        def invalid(_):
            raise ValueError("nonfinite JSON")

        check(re.fullmatch(r"btc-updown-5m-[0-9]+", current_slug) is not None)
        current_start = int(current_slug.rsplit("-", 1)[1])
        check(current_start > 0 and current_start % 300 == 0
              and math.isfinite(official_time) and current_start <= official_time < current_start + 300)
        seen, retained, orders, group = set(), {}, {}, []
        identity, previous_ts = None, 0
        slug_markets, market_slugs = {}, {}
        allowed = {"sm_session", "sm_strategy_window", "sm_placement_intent", "sm_placement_ack",
                   "sm_placement_result", "sm_cancel_ack", "sm_trade", "sm_order",
                   "sm_official_reconciliation", "sm_close_barrier", "sm_window_result",
                   "sm_run_result", "sm_quote_skipped", "sm_placement_unknown",
                   "sm_unknown_official_lookup", "sm_quote_refreshed"}
        for line in lines:
            check(line.endswith("\n"))
            row = json.loads(line, object_pairs_hook=unique, parse_constant=invalid)
            check(isinstance(row, dict) and set(row) == {"ts", "kind", "data"})
            check(type(row["ts"]) in (int, float) and math.isfinite(row["ts"])
                  and row["ts"] >= previous_ts)
            previous_ts = row["ts"]
            check(row["kind"] in allowed)
            data = row["data"]
            check(isinstance(data, dict) and set(data) == {"session_id", "payload"}
                  and isinstance(data["payload"], dict))
            sid = data["session_id"]
            check(isinstance(sid, str) and bool(sid) and sid.strip() == sid)
            check((not group and row["kind"] == "sm_session")
                  or (bool(group) and row["kind"] != "sm_session"
                      and sid == group[0]["data"]["session_id"]))
            group.append(row)
            if row["kind"] != "sm_run_result":
                continue
            check(sid not in seen)
            seen.add(sid)
            session, result = group[0]["data"]["payload"], data["payload"]
            try:
                _, costs, who, _ = RiskJournal._read_zero_history(
                    io.StringIO("".join(json.dumps(r) + "\n" for r in group)), funder)
            except (ValueError, TypeError, KeyError, ArithmeticError):
                check(session["session_id"] == result["session_id"] == sid)
                slug = session["slug"]
                check(isinstance(slug, str) and re.fullmatch(r"btc-updown-5m-[0-9]+", slug) is not None)
                start = int(slug.rsplit("-", 1)[1])
                check(start > 0 and start % 300 == 0
                      and start + 300 <= current_start and start + 300 <= official_time)
                for key in ("signer", "funder"):
                    check(isinstance(session[key], str)
                          and re.fullmatch(r"0x[0-9a-fA-F]{40}", session[key]) is not None)
                check(session["funder"].lower() == funder.lower())
                mode = session["signature_type"]
                check(type(mode) is int and mode in (0, 2))
                who = (session["signer"].lower(), mode)
                check(session["live_authority"] == result["live_authority"]
                      == {0: "LOCAL_EOA_KEY_VERIFIED", 2: "NOT_VERIFIED_FOR_LIVE"}[mode]
                      and session["test_capital_limit_usdc"] == str(CAPITAL_LIMIT))
                RiskJournal._check_starting_observations(session, check)
                cid = result["market_id"]
                check(isinstance(cid, str) and re.fullmatch(r"0x[0-9a-fA-F]{64}", cid) is not None)
                check(current_market_id is None or cid.lower() != current_market_id.lower())
                check(result["execution_status"] in {"INCOMPLETE", "BLOCKED", "COMPLETED"}
                      and result["final_close_barrier"] in {"BLOCKED", "SAFE_TO_CONTINUE"})
                check(type(result["start_time"]) in (int, float)
                      and type(result["stop_time"]) in (int, float)
                      and 0 <= result["start_time"] <= group[0]["ts"]
                      <= result["stop_time"] <= row["ts"])
                costs = {}
                for tid, evidence in session["historical_matched"].items():
                    amount = Decimal(evidence["pending_cost_reserved_usd"])
                    check(amount.is_finite() and amount > 0
                          and evidence["status"] in {"MATCHED", "MINED", "RETRYING"}
                          and evidence["decision"] == "HISTORICAL_MATCHED_NONBLOCKING"
                          and evidence["redeem"] == "NOT_VERIFIED")
                    costs[tid] = amount
                check(sum(costs.values(), Decimal(0))
                      == RiskJournal._history_cost_total(session, check))
                intents, completed, acknowledged = {}, {}, {}
                order_attempts = {}
                rejected_unknown = set()
                submitted = [e["data"]["payload"]["quote"] for e in group
                             if e["kind"] == "sm_placement_intent"]
                for event in group[1:-1]:
                    kind, payload = event["kind"], event["data"]["payload"]
                    if kind not in {"sm_placement_intent", "sm_placement_ack", "sm_placement_result",
                                    "sm_placement_unknown"}:
                        continue
                    aid, quote = payload["attempt_id"], payload["quote"]
                    check(isinstance(aid, str) and bool(aid))
                    check(quote["market_id"] == cid and quote["side"] == "BUY"
                          and quote["outcome"] in {"YES", "NO"})
                    _token_id(quote["token_id"])
                    price, size = Decimal(str(quote["price"])), Decimal(str(quote["size"]))
                    check(price.is_finite() and size.is_finite() and 0 < price < 1 and size > 0)
                    if kind == "sm_placement_intent":
                        check(aid not in intents and payload["outcome"] == "INTENT"
                              and payload["post_started"] is False and payload["order_id"] is None)
                        intents[aid] = quote
                        continue
                    check(aid in intents and quote == intents[aid])
                    outcome, oid = payload["outcome"], payload["order_id"]
                    if outcome == "UNKNOWN":
                        raw = payload.get("raw_response")
                        batch = [q for q in submitted if q["window_id"] == quote["window_id"]]
                        if isinstance(raw, dict) and len(batch) == 1:
                            raw = [raw]
                        check(isinstance(raw, list) and len(raw) == len(batch)
                              and 1 <= len(batch) <= 2 and batch.count(quote) == 1
                              and _explicit_maker_rejection(raw[batch.index(quote)], price * size)
                              and oid is None and payload["post_started"] is True)
                        rejected_unknown.add(aid)
                        outcome = "REJECTED"
                        if kind == "sm_placement_unknown":
                            continue
                    check(outcome in {"ACK", "REJECTED", "ABORTED"})
                    if outcome == "ACK":
                        check(payload["post_started"] is True and isinstance(oid, str)
                              and re.fullmatch(r"[A-Za-z0-9_-]+", oid) is not None)
                        check(kind != "sm_placement_result" or acknowledged.get(aid) == oid)
                        if kind == "sm_placement_ack":
                            check(aid not in acknowledged and oid not in order_attempts)
                            acknowledged[aid] = oid
                            order_attempts[oid] = aid
                        commitment = {"market_id": cid, "slug": slug, "token_id": quote["token_id"],
                                      "price": str(price), "size": str(size)}
                        check(oid not in orders or orders[oid] == commitment)
                        orders[oid] = commitment
                    else:
                        check(kind == "sm_placement_result" and oid is None
                              and aid not in acknowledged)
                        check((outcome == "ABORTED" and payload["post_started"] is False)
                              or (outcome == "REJECTED" and payload["post_started"] is True))
                    if kind == "sm_placement_result":
                        check(aid not in completed)
                        completed[aid] = outcome
                check(set(intents) == set(completed))
                if not intents:
                    RiskJournal._check_zero_windows(group, result, check)
                for oid in acknowledged.values():
                    if RiskJournal._historical_no_fill_closed(group, oid, orders[oid], funder):
                        del orders[oid]
                check(not any(e["kind"] == "sm_unknown_official_lookup" for e in group)
                      or bool(rejected_unknown))
            slug, market = session["slug"], result["market_id"].lower()
            check(slug not in slug_markets or slug_markets[slug] == market)
            check(market not in market_slugs or market_slugs[market] == slug)
            slug_markets[slug], market_slugs[market] = market, slug
            check(identity is None or identity == who)
            identity = who
            check(set(retained) <= set(costs))
            for tid, cost in costs.items():
                check(tid not in retained or retained[tid] == cost)
                retained[tid] = cost
            group = []
        check(not group)
        if expired_orders is not None:
            expired_orders.update(orders)
        return len(lines), retained, identity, seen

    def __init__(self, directory: str | Path, funder: str, *, current_slug=None,
                 official_time=None, current_market_id=None):
        directory = Path(directory).resolve()
        # A session/market UUID cannot select a different ledger to bypass risk.
        super().__init__(directory, day=f"rdp_sm_{funder.lower()}")
        try:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = os.fstat(self._fh.fileno())
            try:
                with Path(self._fh.name).open(encoding="utf-8") as history:
                    opened = os.fstat(history.fileno())
                    if (locked.st_dev, locked.st_ino) != (opened.st_dev, opened.st_ino):
                        raise ValueError("journal identity changed")
                    self.historical_orders = {}
                    (self.sequence, self.historical_reserve, self.historical_identity,
                     self.historical_session_ids) = self._read_history(
                        history, funder, current_slug=current_slug, official_time=official_time,
                        current_market_id=current_market_id,
                        expired_orders=self.historical_orders)
                    # Preserve validated historical payloads byte-for-byte in
                    # meaning when the CLI changes its funding basis. This is
                    # archival evidence, not confirmed inventory or a reserve.
                    self.historical_evidence = {}
                    history.seek(0)
                    for line in history:
                        row = json.loads(line)
                        if row["kind"] == "sm_session":
                            self.historical_evidence.update(row["data"]["payload"]["historical_matched"])
            except (ValueError, TypeError, KeyError, ArithmeticError, UnicodeError, RecursionError) as exc:
                raise RuntimeError("HISTORICAL_SM_RISK_REQUIRES_MANUAL_REVIEW") from exc
            self._fh.flush()
            os.fsync(self._fh.fileno())
            # Persist the directory entry as well as subsequent file contents.
            for parent in (directory, directory.parent):
                fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
        except BaseException:
            self.close()
            raise
        self.path = str(directory / f"rdp_sm_{funder.lower()}.jsonl")

    def verify_retained_reserve(self, reads, account):
        if self.historical_identity is not None:
            _require(self.historical_identity == (reads.gateway.address.lower(),
                                                 reads.cfg.wallet.signature_type),
                     "HISTORICAL_SM_ACCOUNT_IDENTITY_CONFLICT")
        if not self.historical_reserve:
            return
        policy = reads.account_policy
        if policy is not None and policy.use_starting_balance:
            _require(account["capital_basis"] == "OFFICIAL_BALANCE_AT_SESSION_START",
                     "STARTING_CAPITAL_BASIS_CHANGED")
            # Old statuses/costs remain in the Journal; they do not reduce the
            # operator-funded balance or require old fills to become CONFIRMED.
            policy.historical.update(self.historical_evidence)
            return
        _require(policy is not None, "HISTORICAL_SM_RESERVE_EVIDENCE_MISSING")
        for tid, cost in self.historical_reserve.items():
            fresh = policy.historical.get(tid)
            _require(fresh is not None and fresh["decision"] == "HISTORICAL_MATCHED_NONBLOCKING"
                     and fresh["redeem"] == "NOT_VERIFIED"
                     and Decimal(fresh["pending_cost_reserved_usd"]) == cost,
                     "HISTORICAL_SM_RESERVE_EVIDENCE_MISSING_OR_CHANGED")
        _require(Decimal(str(account.get("historical_pending_cost_reserved_usd", 0)))
                 >= sum(self.historical_reserve.values(), Decimal(0)),
                 "HISTORICAL_SM_RESERVE_DECREASED")

    def write(self, kind: str, payload: Any, ts: float) -> None:
        if self._fh is None:
            raise RuntimeError("SM_JOURNAL_CLOSED")
        super().write(kind, payload, ts)
        self._fh.flush()
        os.fsync(self._fh.fileno())
        self.sequence += 1


class ExecutionClobClient(ReadOnlyClobClient):
    """Actual FL SDK with SM-specific transport permissions and POST observation.

    A window issues one POST containing one or two orders; only known-ID cancellation is enabled.
    All other SDK writes (including key creation, RFQ and settlement) are denied
    on this transport. No success inference is made from Gateway's empty list.
    """

    def create_order(self, order_args, options=None):
        # The SDK floors size*100. Binary float 5.02*100 is below 502 and
        # otherwise signs 5.01 shares. Preserve the validated 0.01-share size;
        # the existing signed-quote equality check remains unchanged.
        return super().create_order(
            replace(order_args, size=Decimal(str(order_args.size))), options=options)

    def _get(self, endpoint, headers=None, params=None):
        parsed = urlsplit(endpoint)
        extra = parsed.path == "/data/trades" or parsed.path in {
            f"/data/order/{oid}"
            for oid in getattr(self, "known_ids", set())
            | getattr(self, "observed_open_rows", {}).keys()
        }
        if not extra:
            raw = super()._get(endpoint, headers=headers, params=params)
            if parsed.path == "/data/orders":
                self._observe_order_page(raw["data"])
            return raw
        _require(
            f"{parsed.scheme}://{parsed.netloc}" == OFFICIAL_HOSTS["clob_host"]
            and not parsed.fragment,
            "UNAPPROVED_EXECUTION_READ",
        )
        if parsed.path == "/data/trades":
            cursor = (params or {}).get("next_cursor")
            _require(cursor not in self.trade_cursors, "TRADE_PAGINATION_UNRESOLVED")
            self.trade_cursors.add(cursor)
        raw = ClobClient._get(self, endpoint, headers=headers, params=params)
        if parsed.path == "/data/trades":
            _require(
                isinstance(raw, dict) and isinstance(raw.get("data"), list),
                "TRADE_PAGE_UNKNOWN",
            )
            for trade in raw["data"]:
                parsed_trade = parse_trade(trade)
                for leg in parsed_trade.maker_legs:
                    if leg.order_id in self.observed_open_rows or leg.order_id in self.known_ids:
                        self.observed_trade_legs.setdefault(
                            (parsed_trade.trade_id, leg.order_id), (parsed_trade.status, leg)
                        )
                        if leg.order_id in self.observed_open_rows:
                            self.observed_open_rows[leg.order_id]["_sm_fill_observed"] = True
            _require(
                isinstance(raw.get("next_cursor"), str) and bool(raw["next_cursor"]),
                "TRADE_PAGE_UNKNOWN",
            )
        else:
            _require(isinstance(raw, dict), "ORDER_STATE_UNKNOWN")
            _token_id(raw.get("asset_id"))
        return raw

    def get_trades(self, *args, **kwargs):
        self.trade_cursors = set()
        try:
            rows = super().get_trades(*args, **kwargs)
            self.last_trades = rows
            return rows
        except Exception:
            self.pagination_failure = self.pagination_failure or "TRADES_PAGINATION_INCOMPLETE"
            raise

    def get_open_orders(self, *args, **kwargs):
        self._sm_order_cursors = set()
        try:
            self.last_open_rows = super().get_open_orders(*args, **kwargs)
            return self.last_open_rows
        except Exception:
            self.pagination_failure = self.pagination_failure or "OPEN_ORDERS_PAGINATION_INCOMPLETE"
            raise

    def _observe_order_page(self, rows):
        # Keep valid page evidence before the SDK requests any later page.
        # Observations are account facts, not SM ownership or inventory.
        for row in rows:
            oid = next(
                row[k].strip()
                for k in ("id", "orderID", "order_id")
                if isinstance(row.get(k), str) and row[k].strip()
            )
            observed = self.observed_open_rows.setdefault(oid, dict(row))
            matched = strict_number(row["size_matched"])
            _require(
                0 <= matched <= strict_number(row["original_size"]),
                "OPEN_ORDER_CUMULATIVE_UNKNOWN",
            )
            observed["_sm_fill_observed"] = (
                observed.get("_sm_fill_observed", False)
                or matched > 0
                or bool(row.get("associate_trades"))
            )

    def _post(self, endpoint, headers=None, data=None, params=None):
        attempt = getattr(self, "attempt", None)
        attempts = getattr(self, "batch_attempts", None) or ([attempt] if attempt is not None else [])
        _require(
            endpoint == f"{OFFICIAL_HOSTS['clob_host']}/orders"
            and bool(attempts)
            and all(not a.post_started for a in attempts)
            and self.pagination_failure is None
            and self.submit_guard(),
            "SM_POST_NOT_PERMITTED",
        )
        rows = json.loads(data)
        _require(
            len(rows) == len(attempts) and 1 <= len(rows) <= 2
            and all(r.get("postOnly") is True and r.get("orderType") == "GTC" for r in rows),
            "SM_MAKER_ONLY",
        )
        for row, current in zip(rows, attempts, strict=True):
            signed, quote = row["order"], current.quote
            _require(
                signed["side"] == "BUY"
                and signed["signatureType"] == self.builder.signature_type
                and signed["tokenId"] == quote.token_id
                and signed["maker"].lower() == self.builder.funder.lower()
                and signed["signer"].lower() == self.get_address().lower()
                and Decimal(str(signed["takerAmount"])) == Decimal(str(quote.size)) * 1_000_000
                and Decimal(str(signed["makerAmount"])) == current.maximum * 1_000_000,
                "SIGNED_QUOTE_ECONOMICS_CHANGED",
            )
        for current in attempts:
            current.post_started = True
            current.request_hash = hashlib.sha256(data.encode()).hexdigest()
        raw = ClobClient._post(self, endpoint, headers=headers, data=data, params=params)
        for current in attempts:
            current.raw_response = raw
        return raw

    def _delete(self, endpoint, headers=None, data=None, params=None):
        ids = json.loads(data)
        _require(
            endpoint == f"{OFFICIAL_HOSTS['clob_host']}/orders"
            and isinstance(ids, list)
            and bool(ids)
            and set(ids) <= self.known_ids,
            "SM_CANCEL_SCOPE_INVALID",
        )
        return ClobClient._delete(self, endpoint, headers=headers, data=data, params=params)


class _ExecutionReads(_Reads):
    def __init__(self, cfg: Config, *, risk_reads: _ExecutionReads | None = None,
                 account_policy=None):
        super().__init__(cfg)
        self.risk_reads = risk_reads
        self.account_policy = (risk_reads.account_policy if risk_reads is not None
                               else account_policy)

    async def connect_account(self) -> None:
        await super().connect_account()
        gateway = self.gateway
        previous = gateway._client
        gateway._client = ExecutionClobClient(
            host=self.cfg.wallet.clob_host,
            chain_id=self.cfg.wallet.chain_id,
            key=self.cfg.secrets.pk,
            signature_type=self.cfg.wallet.signature_type,
            funder=gateway.funder,
            creds=previous.creds,
            retry_on_error=False,
        )
        gateway._client.known_ids = set()
        gateway._client.observed_open_rows = {}
        gateway._client.observed_trade_legs = {}
        gateway._client.pagination_failure = None
        _require(
            gateway._client.get_address() == gateway.address
            and gateway._client.builder.funder == gateway.funder,
            "ACCOUNT_IDENTITY_MISMATCH",
        )
        if self.risk_reads is not None:
            # Preflight closes its HTTP pool, but its observations remain facts.
            # Carry them across readers/waiting, not a cached READY or balance.
            previous = self.risk_reads.gateway._client
            _execution_identity(self.risk_reads)
            _execution_identity(self)
            _require(
                previous.get_address() == gateway._client.get_address()
                and previous.builder.funder == gateway._client.builder.funder
                and previous.builder.signature_type == gateway._client.builder.signature_type,
                "PREFLIGHT_RISK_ACCOUNT_MISMATCH",
            )
            gateway._client.observed_open_rows = {
                oid: dict(row) for oid, row in previous.observed_open_rows.items()
            }
            gateway._client.observed_trade_legs = dict(previous.observed_trade_legs)
            gateway._client.pagination_failure = previous.pagination_failure
            # No reader chain or closed credential-bearing clients needed after
            # the identity-bound evidence has been transferred.
            self.risk_reads = None

    async def market(self, slug: str):
        if self.account_policy is None:
            return await super().market(slug)
        from polymaker.rdp_sm_entry import exact_market

        async with GammaClient(self.cfg.wallet.gamma_host) as gamma:
            return await exact_market(gamma, slug=slug)

    async def book(self, token: str):
        # SDK normally caches tick size indefinitely. SM needs an official read
        # at every submission, rather than a permanently cached M1 snapshot.
        self.gateway._client._ClobClient__tick_sizes.pop(token, None)
        tick = await self.gateway._io(self.gateway._client.get_tick_size, token)
        raw = await self.gateway._io(self.gateway._client.get_order_book, token)
        return raw, tick


def _execution_identity(reads: _ExecutionReads) -> str:
    """Verify local SDK/config identity; Safe live permissions remain unverified."""
    from eth_account import Account
    from eth_utils import to_checksum_address

    cfg, gateway = reads.cfg, reads.gateway
    client = gateway._client
    signer = Account.from_key(cfg.secrets.pk).address
    funder = to_checksum_address(cfg.secrets.browser_address)
    mode = cfg.wallet.signature_type
    _require(
        cfg.wallet.chain_id == 137
        and client.get_address() == gateway.address == signer
        and client.builder.funder == gateway.funder == funder
        and client.builder.signature_type == mode,
        "ACCOUNT_IDENTITY_MISMATCH",
    )
    if mode == 0:
        _require(signer == funder, "EOA_FUNDER_IDENTITY_MISMATCH")
        return "LOCAL_EOA_KEY_VERIFIED"
    if mode == 2:
        from py_builder_relayer_client.builder.derive import derive
        from py_builder_relayer_client.config import get_contract_config

        expected = derive(signer, get_contract_config(cfg.wallet.chain_id).safe_factory)
        _require(
            signer != funder and funder == expected,
            "M2_REQUIRES_PROVEN_EOA_OR_DERIVED_SAFE",
        )
        return "NOT_VERIFIED_FOR_LIVE"
    _require(False, "M2_ACCOUNT_SIGNATURE_TYPE_UNSUPPORTED")


class _SMProcessor(UserEventProcessor):
    """Intercept proof invalidation; all economic/lifecycle processing stays FL."""

    def __init__(self, executor: SMWindowExecutor, store: StateStore):
        super().__init__(store)
        self.executor = executor

    def _external_commitment_changed(self, oid):
        observed = self.executor.gateway._client.observed_open_rows
        if oid in observed and self.owned_record(oid) is None:
            # External WS facts cannot be silently discarded while an old
            # no-fill proof is being used to admit a new SM order.
            observed[oid]["_sm_external_event"] = True
            self.executor.invalidate("EXTERNAL_COMMITMENT_EVIDENCE_CHANGED")

    def on_trade(self, ev: TradeEvent, condition_id: str, **kwargs):
        self._external_commitment_changed(ev.maker_order_id)
        before = (
            dict(self.fill_records),
            set(self.applied_fill_keys),
            set(self.quarantined_fill_keys),
        )
        super().on_trade(ev, condition_id, **kwargs)
        after = (self.fill_records, self.applied_fill_keys, self.quarantined_fill_keys)
        if before != after:
            if kwargs.get("source", "WS") == "WS":
                self.executor.external_revision += 1
            self.executor.invalidate("TRADE_EVIDENCE_CHANGED", ev.maker_order_id)
            with suppress(Exception):
                self.executor.record("sm_trade", asdict(ev))

    def on_order(self, ev: OrderEvent, condition_id: str, **kwargs):
        self._external_commitment_changed(ev.order_id)
        if self.owned_record(ev.order_id) is not None:
            self.executor.external_revision += 1
            previous = self.executor.terminal.get(ev.order_id)
            if previous is not None and not _terminal(ev):
                self.executor.fatal = "TERMINAL_LIVE_CONTRADICTION"
            self.executor.invalidate("ORDER_EVIDENCE_CHANGED", ev.order_id)
            with suppress(Exception):
                self.executor.record("sm_order", asdict(ev))
        super().on_order(ev, condition_id, **kwargs)

    def quarantine_order(self, order_id, reason, **kwargs):
        if self.owned_record(order_id) is not None:
            self.executor.invalidate("ORDER_QUARANTINED", order_id)
        super().quarantine_order(order_id, reason, **kwargs)


def _terminal(event: OrderEvent) -> bool:
    if event.status in {"CANCELED", "CANCELLED"}:
        return True
    if event.status != "MATCHED":
        return False
    original, matched = Decimal(str(event.original_size)), Decimal(str(event.size_matched))
    if not original.is_finite() or not matched.is_finite():
        return False
    # Decimal subtraction keeps exactly 0.1 outside the strict '< 0.1' rule.
    # This is an order-close tolerance; inventory still uses actual confirmed fills.
    return 0 <= original - matched < MATCHED_REMAINING_TOLERANCE_SHARES


class SMWindowExecutor:
    """Opt-in production API for M3: open -> execute_window -> close.

    No loop calls execute_window automatically. BLOCKED requires read-only
    verification or manual intervention; UNKNOWN/contradictions never auto-clear.
    The caller must not share this account with another trading writer.
    """

    def __init__(
        self,
        cfg: Config,
        slug: str,
        session_id: str,
        reads: _ExecutionReads,
        journal: RiskJournal,
        *,
        monotonic=time.monotonic,
        sleep=asyncio.sleep,
        display_events=None,
    ):
        self.cfg, self.slug, self.session_id = cfg, slug, session_id
        self.reads, self.gateway, self.journal = reads, reads.gateway, journal
        self.monotonic, self.sleep = monotonic, sleep
        self.store = StateStore(":memory:")
        self.processor = _SMProcessor(self, self.store)
        self.attempts: list[Placement] = []
        self.known: set[str] = set()
        self.terminal: dict[str, OrderEvent] = {}
        self.proofs: dict[str, dict[str, Any]] = {}
        self.pending_confirmations: set[str] = set()
        self.revision = 0
        self.external_revision = 0
        self.proof_revision = -1
        self.fatal: str | None = None
        self.reason: str | None = "NO_CLOSE_EVIDENCE"
        self.used_windows: set[str] = set()
        self.lock = asyncio.Lock()
        self.closed = False
        self.official_time = 0.0
        self.time_anchor = self.monotonic()
        self.meta = None
        self._cancel_jobs: dict[frozenset[str], asyncio.Task] = {}
        # Optional CLI-only references to already-created Journal payloads.
        # No formatting, I/O, callbacks or queue writes inside execution.
        self.display_events = display_events

    @classmethod
    async def open(
        cls,
        cfg: Config,
        slug: str,
        session_id: str,
        *,
        risk_reads: _ExecutionReads | None = None,
        **clock,
    ):
        _require(isinstance(session_id, str) and bool(session_id.strip()), "SESSION_ID_INVALID")
        # M1 READY is a required startup snapshot, never a cached write permit.
        # Preserve even the startup account observation; READY does not prove
        # that a liability omitted by the next replica read has been released.
        startup_reads = _ExecutionReads(cfg, risk_reads=risk_reads)
        preflight = await run_preflight(cfg, slug, _reads=startup_reads)
        if preflight.status is not SessionStatus.READY:
            raise _Issue(SessionStatus.BLOCKED, "M1_SESSION_NOT_READY",
                         context={"preflight": preflight.to_dict()})
        reads = _ExecutionReads(cfg, risk_reads=startup_reads)
        journal = None
        try:
            await reads.connect_account()
            authority = _execution_identity(reads)
            journal = RiskJournal(
                cfg.paths.journal_dir, reads.gateway.funder, current_slug=slug,
                current_market_id=preflight.market["condition_id"],
                official_time=preflight.market["start"] + preflight.market["elapsed_s"])
            _require(session_id not in journal.historical_session_ids, "HISTORICAL_SM_SESSION_ID_REUSED")
            journal.verify_retained_reserve(reads, preflight.account)
            executor = cls(cfg, slug, session_id, reads, journal, **clock)
            executor.record(
                "sm_session",
                {
                    "session_id": session_id,
                    "slug": slug,
                    "signer": reads.gateway.address,
                    "funder": reads.gateway.funder,
                    "signature_type": cfg.wallet.signature_type,
                    "live_authority": authority,
                    "starting_spendable_usdc": min(preflight.account['available_balance_usd'],
                        preflight.account['applicable_allowance_usd'] -
                        preflight.account['existing_buy_commitment_usd']),
                    "starting_official_balance_usdc": preflight.account['official_balance_usd'],
                    "test_capital_limit_usdc": str(CAPITAL_LIMIT),
                    "historical_pending_cost_reserved_usd": preflight.account.get(
                        'historical_pending_cost_reserved_usd', 0),
                    "historical_matched": (reads.account_policy.historical
                                           if reads.account_policy is not None else {}),
                    **({"capital_basis": "OFFICIAL_BALANCE_AT_SESSION_START",
                        "historical_pending_cost_observed_usd": float(sum(
                            (Decimal(e["pending_cost_reserved_usd"])
                             for e in reads.account_policy.historical.values()), Decimal(0)))}
                       if reads.account_policy is not None and reads.account_policy.use_starting_balance else {}),
                    "preflight_observed_order_ids": sorted(
                        reads.gateway._client.observed_open_rows
                    ),
                    "preflight_observed_trade_ids": sorted(
                        {tid for tid, _ in reads.gateway._client.observed_trade_legs}
                    ),
                    "preflight_account_incomplete": reads.gateway._client.pagination_failure,
                },
            )
            return executor
        except BaseException:
            reads.close()
            if journal is not None:
                journal.close()
            raise

    def record(self, kind: str, payload: Any) -> None:
        try:
            self.journal.write(
                kind, {"session_id": self.session_id, "payload": payload}, time.time()
            )
        except Exception:
            self.fatal = "JOURNAL_WRITE_FAILED"
            self.proof_revision = -1
            raise
        if self.display_events is not None:
            with suppress(Exception):
                self.display_events.append((kind, payload))

    def invalidate(self, reason: str, order_id: str | None = None) -> None:
        self.revision += 1
        self.proof_revision = -1
        self.reason = reason
        if order_id is not None:
            self.proofs.pop(order_id, None)

    @property
    def safe_to_continue(self) -> bool:
        return (
            not self.closed
            and self.fatal is None
            and self.gateway._client.pagination_failure is None
            and self.reason is None
            and self.proof_revision == self.revision
            and all(a.outcome in {"ACK", "REJECTED", "ABORTED"} for a in self.attempts)
            and set(self.proofs) == self.known
            and set(self.terminal) == self.known
        )

    def confirmed(self) -> list[TradeEvent]:
        return sorted(
            (
                ev
                for ev in self.processor.economic_fills_for_orders(self.known)
                if ev.status is TradeState.CONFIRMED
            ),
            key=lambda ev: (ev.trade_id, ev.maker_order_id),
        )

    def committed(self) -> Decimal:
        """One exposure per placement; terminal proof releases only unfilled size."""
        total = Decimal(0)
        for attempt in self.attempts:
            if attempt.outcome in {"REJECTED", "ABORTED"}:
                continue
            oid = attempt.order_id
            proof = self.proofs.get(oid)
            fills = self.processor.economic_fills_for_order(oid) if oid else []
            if proof is not None and oid in self.terminal:
                # Only verified confirmed fills remain after a complete barrier.
                total += sum(
                    (Decimal(str(ev.price)) * Decimal(str(ev.size)) for ev in fills), Decimal(0)
                )
            else:
                # Full limit-price exposure covers fills + remaining + uncertainty
                # once, even if cancellation returned ACK or a WS fill was seen.
                total += attempt.maximum
        return total

    async def _read_books(self, meta):
        books = {}
        for token in (meta.yes.token_id, meta.no.token_id):
            raw_book, tick = await self.reads.book(token)
            books[token] = _book(raw_book, tick, token, meta)
        now = self.official_time + self.monotonic() - self.time_anchor
        _require(all(-2 <= now - b["timestamp"] <= self.cfg.risk.ws_stale_halt_s
                     for b in books.values()), "EXECUTION_DATA_STALE")
        return books

    async def _snapshot(self, *, include_books=True):
        began = self.monotonic()
        _require(
            self.gateway._client.pagination_failure is None,
            self.gateway._client.pagination_failure or "ACCOUNT_PAGINATION_UNRESOLVED",
        )
        _require(
            all(
                getattr(self.cfg.wallet, key).rstrip("/") == host
                for key, host in OFFICIAL_HOSTS.items()
            )
            and self.cfg.wallet.chain_id == 137,
            "OFFICIAL_ENDPOINT_CONFIGURATION_REQUIRED",
        )
        for oid in sorted(self.known - set(self.proofs)):
            _require(
                await reconcile_owned_order(oid, self.gateway, self.processor, retry_delays=())
                is ReconciliationOutcome.COMPLETE,
                "PRE_SUBMIT_FILL_FACTS_UNRESOLVED",
            )
        raw = await self.reads.market(self.slug)
        meta, start = _market(raw, self.slug)
        if self.meta is not None:
            _require(
                meta.condition_id == self.meta.condition_id and meta.tokens == self.meta.tokens,
                "SESSION_MARKET_CHANGED",
            )
        self.meta = meta
        tokens = {meta.yes.token_id, meta.no.token_id}
        # Account admission precedes the final book reads. This reference tick
        # is only for _account's descriptive lower bound, never a quote tick.
        market = {side: {"tick": meta.tick_size} for side in ("yes", "no")}
        capital_revision = self.revision
        account = await _account(self.reads, meta, market)
        client = self.gateway._client
        if self.known:
            # The close proof is reusable, but newly observed trade facts are
            # not. Production account policy already reads complete trade pages.
            if self.reads.account_policy is None:
                await self.gateway.get_trades(market=meta.condition_id)
            if self._official_fills(client.last_trades, self.known) != self._local_fills(self.known):
                self.invalidate("PREVIOUS_WINDOW_TRADE_EVIDENCE_CHANGED")
                _require(False, "PREVIOUS_WINDOW_TRADE_EVIDENCE_CHANGED")
        first_open_rows = list(client.last_open_rows)
        _execution_identity(self.reads)
        # Raw SDK orders already pass F01 identity validation; read full IDs too.
        orders = await self.gateway.open_orders()
        for order in orders:
            if order.order_id in self.terminal:
                self.fatal = "TERMINAL_LIVE_CONTRADICTION"
                self.invalidate("OFFICIAL_OPEN_ORDER_CONTRADICTS_TERMINAL", order.order_id)
                self.record("sm_terminal_open_order_contradiction", asdict(order))
                _require(False, self.fatal)
            if order.order_id in self.known:
                owned = self.processor.owned_record(order.order_id)
                _require(
                    order.token_id == owned["token_id"]
                    and order.side is Side.BUY
                    and order.price == float(owned["submitted_price"])
                    and order.size <= float(owned["submitted_qty"]),
                    "OPEN_ORDER_ECONOMICS_CONTRADICT_OWNERSHIP",
                )
        _require(
            all(o.order_id in self.known for o in orders if o.token_id in tokens),
            "EXTERNAL_TARGET_ORDER",
        )
        # Balance/allowance are collateral readings, not evidence that a local
        # ACK has already appeared in the open-order replica. Merge both lists
        # and local unresolved placements by identity, reserving each once.
        reservations: dict[str, Decimal] = {}
        identities = {}
        for historical, rows in (
            (True, list(client.observed_open_rows.values())),
            (False, first_open_rows),
            (False, client.last_open_rows),
        ):
            seen = set()
            for row in rows:
                oid = next(
                    row[k].strip()
                    for k in ("id", "orderID", "order_id")
                    if isinstance(row.get(k), str) and row[k].strip()
                )
                if historical and oid in self.known:
                    continue  # Own attempts already have fresh terminal/fill proofs.
                _require(oid not in seen, "OPEN_ORDER_IDENTITY_CONFLICT")
                seen.add(oid)
                price = Decimal(str(strict_number(row["price"])))
                size = Decimal(str(strict_number(row["original_size"])))
                side = Side(row["side"].upper())
                identity = (_token_id(row["asset_id"]), side, price, size)
                _require(
                    oid not in identities or identities[oid] == identity,
                    "OPEN_ORDER_SNAPSHOTS_CONFLICT",
                )
                identities[oid] = identity
                _require(0 < price < 1 and size >= 0, "OPEN_ORDER_COMMITMENT_UNKNOWN")
                _require(identity[0] not in tokens or oid in self.known, "EXTERNAL_TARGET_ORDER")
                if oid in self.known:
                    owned = self.processor.owned_record(oid)
                    _require(
                        identity
                        == (
                            owned["token_id"],
                            Side.BUY,
                            Decimal(str(owned["submitted_price"])),
                            Decimal(str(owned["submitted_qty"])),
                        ),
                        "OPEN_ORDER_ECONOMICS_CONTRADICT_OWNERSHIP",
                    )
                if oid in self.terminal:
                    self.fatal = "TERMINAL_LIVE_CONTRADICTION"
                    self.invalidate("OFFICIAL_OPEN_ORDER_CONTRADICTS_TERMINAL", oid)
                    _require(False, self.fatal)
                policy = self.reads.account_policy
                if (policy is not None and policy.use_starting_balance
                        and oid in policy.starting_order_ids and oid not in self.known):
                    continue  # The operator manages orders present at session startup.
                if side is Side.BUY:
                    reservations[oid] = price * size
        listed = {
            next(
                row[k].strip()
                for k in ("id", "orderID", "order_id")
                if isinstance(row.get(k), str) and row[k].strip()
            )
            for row in first_open_rows + client.last_open_rows
        }
        for oid in sorted(reservations.keys() - self.known - listed):
            if await self._external_no_fill_terminal(oid, identities[oid]):
                reservations[oid] = Decimal(0)
        for attempt in self.attempts:
            if attempt.outcome in {"REJECTED", "ABORTED"}:
                continue
            oid = attempt.order_id
            if oid in self.proofs and oid in self.terminal:
                continue  # Reconciled confirmed inventory is already spent collateral.
            key = oid or f"unknown:{attempt.attempt_id}"
            reservations[key] = max(reservations.get(key, Decimal(0)), attempt.maximum)
        reserved = sum(reservations.values(), Decimal(0)) + Decimal(str(
            account.get('historical_pending_cost_reserved_usd', 0)))
        # Conservative library funding also reserves expired BUY envelopes.
        # The operator-managed CLI does not charge pre-session obligations.
        pending_costs = {}
        policy = self.reads.account_policy
        balance_basis = policy is not None and policy.use_starting_balance
        for row in (policy.pending.values() if policy is not None else ()):
            trade = parse_trade(row)
            if row.get("trader_side") == "MAKER":
                for leg in trade.maker_legs:
                    if leg.maker_address and leg.maker_address.lower() == self.gateway.funder.lower():
                        pending_costs[leg.order_id] = pending_costs.get(leg.order_id, Decimal(0)) + (
                            Decimal(str(leg.price)) * Decimal(str(leg.matched_amount)))
            elif row.get("trader_side") == "TAKER":
                pending_costs[trade.taker_order_id] = pending_costs.get(trade.taker_order_id, Decimal(0)) + (
                    Decimal(str(strict_number(row.get("price"))))
                    * Decimal(str(strict_number(row.get("size")))))
        journal_reserved = Decimal(0)
        for oid, quote in (self.journal.historical_orders.items() if not balance_basis else ()):
            maximum = Decimal(quote["price"]) * Decimal(quote["size"])
            journal_reserved += max(Decimal(0), maximum - pending_costs.get(oid, Decimal(0))
                                    - reservations.get(oid, Decimal(0)))
        reserved += journal_reserved
        account["historical_order_cost_reserved_usd"] = float(journal_reserved)
        account["execution_reserved_usd"] = float(reserved)
        account["basic_spendable_budget_usd"] = float(
            max(
                Decimal(0),
                min(
                    CAPITAL_LIMIT,
                    Decimal(str(account["official_balance_usd"])) - reserved,
                    Decimal(str(account["applicable_allowance_usd"])) - reserved,
                ),
            )
        )
        for token, name in ((meta.yes.token_id, "yes"), (meta.no.token_id, "no")):
            owned_qty = sum(
                ev.size
                for ev in self.processor.economic_fills_for_orders(self.known)
                if ev.token_id == token
            )
            _require(
                abs(account[f"{name}_inventory"] - owned_qty) <= 1e-6,
                "ACCOUNT_INVENTORY_NOT_EXPLAINED_BY_SM",
            )
        _require(client._ClobClient__resolve_version() == 2, "M2_ORDER_VERSION_UNSUPPORTED")
        now = await self.reads.server_time()
        self.official_time, self.time_anchor = now, self.monotonic()
        # No order reconciliation, account or market requests after these books.
        books = await self._read_books(meta) if include_books else {}
        if books:
            account["minimum_pair_commitment_usd"] = meta.min_order_size * sum(
                b["tick"] for b in books.values())
        _require(
            client.pagination_failure is None,
            client.pagination_failure or "ACCOUNT_PAGINATION_UNRESOLVED",
        )
        _require(self.revision == capital_revision, "CAPITAL_EVIDENCE_CHANGED_DURING_READ")
        account["capital_evidence_revision"] = capital_revision
        age = self.cfg.risk.ws_stale_halt_s
        current_time = now + self.monotonic() - self.time_anchor
        _require(
            self.monotonic() - began <= age
            and all(-2 <= current_time - b["timestamp"] <= age for b in books.values()),
            "EXECUTION_DATA_STALE",
        )
        return meta, start, books, account

    async def _external_no_fill_terminal(self, oid: str, identity) -> bool:
        """Fresh no-fill cancellation proof only; history itself is never erased.

        Recheck on every snapshot. LIVE, failed/ambiguous reads, any fill or
        pending trade restores the full envelope. No external order is owned,
        cancelled or projected into SM inventory by this read-only check.
        """

        observed = self.gateway._client.observed_open_rows[oid]
        if self.gateway._client.pagination_failure is not None:
            return False
        market = observed.get("market")
        if observed.get("_sm_fill_observed") or observed.get("_sm_external_event"):
            return False  # A later zero-fill replica cannot erase a seen fill.

        def no_fill(row):
            ev = normalize_order(row)
            if ev is not None and (ev.size_matched > 0 or ev.associate_trades):
                observed["_sm_fill_observed"] = True
            return (
                ev is not None
                and (ev.token_id, ev.side, Decimal(str(ev.price)), Decimal(str(ev.original_size)))
                == identity
                and ev.status in {"CANCELED", "CANCELLED"}
                and ev.size_matched == 0
                and isinstance(row.get("associate_trades"), list)
                and not row["associate_trades"]
                and isinstance(market, str)
                and bool(market)
                and ev.market_id == market
            )

        try:
            row = await self.gateway.get_order(oid)
            if not no_fill(row):
                return False
            trades = await self.gateway.get_trades(market=normalize_order(row).market_id)
            if any(leg.order_id == oid for raw in trades for leg in parse_trade(raw).maker_legs):
                observed["_sm_fill_observed"] = True
                return False
            final = await self.gateway.get_order(oid)
            if (
                not no_fill(final)
                or normalize_order(final).market_id != normalize_order(row).market_id
            ):
                return False
            self.record("sm_external_no_fill_terminal", {"order": final, "trades": trades})
            return True
        except Exception:
            return False  # Absence, errors and incomplete facts cannot release collateral.

    def _validate(self, quote: WindowQuote, window_id: str, books, meta):
        _require(
            quote.market_id == meta.condition_id and quote.window_id == window_id,
            "QUOTE_SESSION_IDENTITY_INVALID",
        )
        token = _token_id(quote.token_id)
        expected = {"YES": meta.yes.token_id, "NO": meta.no.token_id}
        _require(
            quote.outcome in expected
            and expected[quote.outcome] == token
            and quote.side is Side.BUY,
            "QUOTE_BUY_TOKEN_INVALID",
        )
        _require(
            type(quote.price) in (int, float)
            and type(quote.size) in (int, float)
            and math.isfinite(quote.price)
            and math.isfinite(quote.size),
            "QUOTE_NUMBER_INVALID",
        )
        price, size, tick = map(
            lambda x: Decimal(str(x)), (quote.price, quote.size, books[token]["tick"])
        )
        _require(tick <= price <= 1 - tick and price % tick == 0, "QUOTE_PRICE_TICK_INVALID")
        _require(
            size >= Decimal(str(meta.min_order_size)) and size % Decimal(".01") == 0,
            "QUOTE_SIZE_INVALID",
        )
        _require(price < Decimal(str(books[token]["best_ask"])), "QUOTE_WOULD_TAKE")

    async def _verify_order(self, oid: str) -> None:
        outcome = await reconcile_owned_order(oid, self.gateway, self.processor, retry_delays=())
        trades = list(getattr(self.gateway._client, "last_trades", []))
        row = await self.gateway.get_order(oid)
        with suppress(Exception):
            self.record(
                "sm_official_reconciliation",
                {"order_id": oid, "order": row, "trades": trades, "filltruth": outcome.value},
            )
        ev = normalize_order(row)
        owned = self.processor.owned_record(oid)
        _require(
            ev is not None
            and ev.token_id == owned["token_id"]
            and ev.side.value == owned["side"]
            and ev.market_id == self.meta.condition_id
            and ev.original_size == float(owned["submitted_qty"])
            and ev.price == float(owned["submitted_price"]),
            "FINAL_ORDER_IDENTITY_UNKNOWN",
        )
        UserEventProcessor.on_order(self.processor, ev, self.meta.condition_id)
        if oid in self.terminal and not _terminal(ev):
            self.fatal = "TERMINAL_LIVE_CONTRADICTION"
        _require(_terminal(ev), "ORDER_NOT_TERMINAL")
        _require(outcome is ReconciliationOutcome.COMPLETE, "FILLTRUTH_NOT_COMPLETE")
        fills = self.processor.economic_fills_for_order(oid)
        official = self._official_fills(trades, {oid})
        local = self._local_fills({oid})
        _require(
            all(
                key in official
                and official[key][1:] == (leg.asset_id, leg.side, leg.price, leg.matched_amount)
                for key, (_, leg) in self.gateway._client.observed_trade_legs.items()
                if key[1] == oid
            ),
            "OBSERVED_PAGE_FILL_NOT_RECONCILED",
        )
        _require(official == local, "TRADE_CONFIRMATION_UNRESOLVED")
        if not all(v[0] is TradeState.CONFIRMED for v in official.values()):
            # Only complete, consistent economics on verified terminal orders
            # qualify for waiting. Unknown placement/terminal/fill facts do not.
            if (all(v[0] in {TradeState.MATCHED, TradeState.MINED, TradeState.CONFIRMED}
                    for v in official.values())
                    and "associate_trades" in row
                    and set(ev.associate_trades) == {key[0] for key in official}
                    and abs(ev.size_matched - sum(f.size for f in fills)) <= 1e-6
                    and not self.processor.has_unresolved_fill_key(oid)
                    and oid not in self.processor.quarantined_orders):
                self.pending_confirmations.add(oid)
            _require(False, "TRADE_CONFIRMATION_UNRESOLVED")
        _require(
            not self.processor.has_unresolved_fill_key(oid)
            and oid not in self.processor.quarantined_orders
            and all(
                key in self.processor.applied_fill_keys
                for key in self.processor.fill_records
                if key[1] == oid
            ),
            "TRACKER_UNRESOLVED",
        )
        _require(
            "associate_trades" in row
            and set(ev.associate_trades) == {key[0] for key in official}
            and abs(ev.size_matched - sum(f.size for f in fills)) <= 1e-6,
            "FINAL_FILL_SET_NOT_PROVEN",
        )
        self.terminal[oid] = ev
        self.proofs[oid] = {"order": row, "trades": trades, "outcome": outcome.value}

    def _official_fills(self, trades, ids):
        official = {}
        for raw_trade in trades:
            parsed = parse_trade(raw_trade)
            for leg in parsed.maker_legs:
                if leg.order_id in ids:
                    _token_id(leg.asset_id)
                    _require(
                        parsed.market == self.meta.condition_id, "OFFICIAL_TRADE_MARKET_UNKNOWN"
                    )
                    key = (parsed.trade_id, leg.order_id)
                    value = (parsed.status, leg.asset_id, leg.side, leg.price, leg.matched_amount)
                    _require(
                        key not in official or official[key] == value, "OFFICIAL_TRADE_CONFLICT"
                    )
                    official[key] = value
        return official

    def _local_fills(self, ids):
        return {
            (f.trade_id, f.maker_order_id): (f.status, f.token_id, f.our_side, f.price, f.size)
            for f in self.processor.economic_fills_for_orders(ids)
        }

    async def verify_close(self, *, cancel: bool = False) -> bool:
        """Explicit read/cleanup request; serialize with any active window."""
        async with self.lock:
            if cancel:
                self._cancel_jobs = {}  # An explicit later cleanup may retry cancellation.
            return await self._await_owned(self._verify_close(cancel=cancel))

    async def _await_owned(self, operation, *, propagate_cancel: bool = True):
        """Drain the same coroutine/SDK reads even when its caller is cancelled.

        Cancelling run_in_executor's awaiter does not stop its SDK thread. Keep
        the operation owned until completion; the independent timer still
        prioritizes known-ID cancellation while we wait.
        """
        task = asyncio.create_task(operation)
        interrupted = False
        while True:
            try:
                result = await asyncio.shield(task)
                break
            except asyncio.CancelledError:
                interrupted = True
                self.fatal = "ABNORMAL_EXIT"
                if task.cancelled():
                    raise RuntimeError("EXECUTION_READ_TASK_CANCELLED") from None
        if interrupted and propagate_cancel:
            raise asyncio.CancelledError()
        return result

    async def _cancel_known(self) -> None:
        """One managed cancellation per known-ID set, shared by timer/closeout.

        The SDK read worker is not cancelled by a timer. Its owner still awaits
        it; this independent known-ID cancellation can run on the existing pool.
        Final reconciliation starts only after that owner regains control.
        """
        scope = frozenset(self.known)
        if not scope:
            return
        job = self._cancel_jobs.get(scope)
        if job is None:

            async def send():
                began, cancel_ts = self.monotonic(), time.time()
                ack = await self.gateway.cancel(sorted(scope))
                with suppress(Exception):
                    self.record("sm_cancel_ack", {"ids": sorted(scope), "ack": ack,
                        "request_ts": cancel_ts, "duration_s": self.monotonic() - began})

            job = asyncio.create_task(send())
            self._cancel_jobs[scope] = job
        while True:
            try:
                await asyncio.shield(job)
                return
            except asyncio.CancelledError:
                self.fatal = "ABNORMAL_EXIT"
                if job.cancelled():
                    raise RuntimeError("CANCELLATION_TASK_CANCELLED") from None

    async def _verify_close(self, *, cancel: bool = False) -> bool:
        """Fresh official order/trade facts; no old COMPLETE or terminal cache."""
        self.proof_revision = -1
        self.proofs = {}
        self.pending_confirmations = set()
        try:
            if cancel and self.known:
                await self._cancel_known()
            if any(a.outcome in {"UNKNOWN", "INTENT"} for a in self.attempts):
                try:
                    candidates = await self.gateway.open_orders()
                    lookup = {"open_orders": [asdict(order) for order in candidates]}
                except Exception as exc:
                    lookup = {"read_error": type(exc).__name__}
                with suppress(Exception):
                    self.record(
                        "sm_unknown_official_lookup",
                        {
                            **lookup,
                            "resolution": "NO_RELIABLE_REQUEST_TO_ORDER_CORRELATION; risk retained",
                        },
                    )
            external_revision = self.external_revision
            errors = []
            for oid in sorted(self.known):
                try:
                    await self._verify_order(oid)
                except Exception as exc:
                    errors.append(f"{oid}:{exc}")
            _require(not errors, ";".join(errors))
            _require(
                self.external_revision == external_revision, "EVENT_DURING_CLOSE_REQUIRES_RECHECK"
            )
            _require(
                set(self.proofs) == self.known and set(self.terminal) == self.known,
                "CLOSE_EVIDENCE_INVALIDATED",
            )
            # The last complete official trade page set must still explain every
            # earlier order proof; a later read cannot silently introduce a leg.
            _require(
                self._official_fills(getattr(self.gateway._client, "last_trades", []), self.known)
                == self._local_fills(self.known),
                "CROSS_ORDER_TRADE_SET_CHANGED",
            )
            _require(
                all(a.outcome in {"ACK", "REJECTED", "ABORTED"} for a in self.attempts),
                "UNKNOWN_PLACEMENT_EXPOSURE",
            )
            _require(self.fatal is None, self.fatal or "FATAL_EXECUTION_STATE")
            _require(
                self.gateway._client.pagination_failure is None,
                self.gateway._client.pagination_failure or "ACCOUNT_PAGINATION_UNRESOLVED",
            )
            self.reason = None
            self.proof_revision = self.revision
            self.record(
                "sm_close_barrier",
                {
                    "result": "SAFE_TO_CONTINUE",
                    "proofs": self.proofs,
                    "confirmed_fills": [asdict(f) for f in self.confirmed()],
                },
            )
            return True
        except Exception as exc:
            self.reason = self.fatal or str(exc)
            self.proof_revision = -1
            # Old terminal is diagnostic evidence, never a continuation permit.
            with suppress(Exception):
                self.record(
                    "sm_close_barrier",
                    {
                        "result": "BLOCKED",
                        "reason": self.reason,
                        "attempts": [asdict(a) for a in self.attempts],
                        "capital_committed": str(self.committed()),
                    },
                )
            return False

    async def _wait_for_confirmation(self) -> bool:
        """After cancellation, keep the barrier closed while settlement catches up.

        No new BUY or repricing here. Stop at this market's trading cutoff plus
        the existing close buffer, or immediately on any other safety failure.
        """
        start = int(self.slug.rsplit("-", 1)[1])
        deadline = self.time_anchor + start + TRADING_END_S + CANCEL_BUFFER_S - self.official_time
        while (self.fatal is None and self.gateway._client.pagination_failure is None
               and self.pending_confirmations
               and all(a.outcome in {"ACK", "REJECTED", "ABORTED"} for a in self.attempts)
               and self.reason == ";".join(
                   f"{oid}:TRADE_CONFIRMATION_UNRESOLVED" for oid in sorted(self.pending_confirmations))
               and self.monotonic() < deadline):
            await self.sleep(min(2.0, deadline - self.monotonic()))
            if self.fatal is not None:
                return False
            if await self._verify_close():
                return True
        return self.safe_to_continue

    def _placement_outcomes(self, raw, quotes):
        """One result per submitted quote; unknown legs never erase known ACKs."""
        if len(quotes) == 1 and isinstance(raw, dict):
            raw = [raw]
        if not isinstance(raw, list) or len(raw) != len(quotes):
            return [("UNKNOWN", None) for _ in quotes]
        outcomes = []
        for item, quote in zip(raw, quotes, strict=True):
            outcome, order = "UNKNOWN", None
            if isinstance(item, dict):
                if _explicit_maker_rejection(item, Decimal(str(quote.price)) * Decimal(str(quote.size))):
                    outcome = "REJECTED"
                elif item.get("success") is False:
                    if not any(item.get(k) not in (None, "") for k in ("orderID", "order_id", "orderId", "id")):
                        outcome = "REJECTED"
                elif item.get("success") is True and item.get("errorMsg") in (None, ""):
                    try:
                        placed = self.gateway._parse_place_response(item, [quote])
                        if len(placed) == 1:
                            outcome, order = "ACK", placed[0]
                    except PlacementUnknown:
                        pass
            outcomes.append((outcome, order))
        ids = [order.order_id for _, order in outcomes if order is not None]
        # A duplicated ID cannot identify both signed orders.
        return [("UNKNOWN", None) if order is not None and ids.count(order.order_id) > 1
                else (outcome, order) for outcome, order in outcomes]

    def _accept_placement(self, attempt, order, meta):
        quote, client = attempt.quote, self.gateway._client
        _require(re.fullmatch(r"[A-Za-z0-9_-]+", order.order_id) is not None, "ORDER_ID_NOT_OPAQUE")
        _require(order.order_id not in self.known and order.order_id not in client.observed_open_rows,
                 "DUPLICATE_ORDER_ID")
        _require(order.token_id == quote.token_id and order.side is Side.BUY
                 and order.price == quote.price and order.size == quote.size,
                 "PLACEMENT_QUOTE_IDENTITY_CHANGED")
        attempt.outcome, attempt.order_id = "ACK", order.order_id
        self.known.add(order.order_id)
        client.known_ids.add(order.order_id)
        self.processor.register_order(order.order_id, meta.condition_id, quote.token_id,
                                      Side.BUY, quote.size, quote.price, "rdp_sm")
        self.store.upsert_order(order)
        self.record("sm_placement_ack", asdict(attempt))

    async def execute_window(
        self,
        window_id: str,
        quotes: list[WindowQuote],
        *,
        fill_window_sec: float = 3.0,
        quote_revision: int | None = None,
        quote_factory=None,
    ) -> WindowResult:
        async with self.lock:
            result = WindowResult(
                self.session_id,
                window_id,
                quotes[0].market_id if quotes else "",
                time.time(),
                requested_quotes=list(quotes),
            )
            first_attempt = len(self.attempts)
            previous_ids = frozenset(self.known)
            deadline_handle = None
            deadline_job = None
            skipped_quote_reason = None
            self._cancel_jobs = {}

            def close_at_deadline():
                nonlocal deadline_job
                deadline_job = asyncio.create_task(self._cancel_known())

            def refresh_quotes(meta, books):
                nonlocal quotes
                if quote_factory is None:
                    return False
                _require(quote_revision is not None, "STRATEGY_QUOTE_REVISION_REQUIRED")
                fresh = quote_factory(meta, books, window_id)
                # Keep the revision-bound side and inventory decision. A single
                # REBALANCE leg may resize only by the minimum-notional rule.
                def identity(q):
                    return q.market_id, q.token_id, q.outcome, q.side, q.window_id
                _require([identity(q) for q in fresh] == [identity(q) for q in quotes],
                         "STRATEGY_QUOTE_IDENTITY_CHANGED")
                for old, new in zip(quotes, fresh, strict=True):
                    if old.size != new.size:
                        fills = self.confirmed()
                        gap = abs(sum((Decimal(str(f.size)) * (1 if f.token_id == meta.yes.token_id else -1)
                                       for f in fills), Decimal(0)))
                        _require(len(quotes) == 1 and gap >= 5
                                 and Decimal(str(old.size)) == maker_buy_size(Decimal(str(old.price)), gap)
                                 and Decimal(str(new.size)) == maker_buy_size(Decimal(str(new.price)), gap),
                                 "STRATEGY_QUOTE_SIZE_CHANGED")
                changed = quotes != fresh
                quotes = list(fresh)
                result.requested_quotes = list(quotes)
                return changed

            try:
                _require(not self.closed and self.fatal is None, self.fatal or "SESSION_CLOSED")
                _require(window_id not in self.used_windows, "WINDOW_ID_ALREADY_USED")
                _require(
                    isinstance(window_id, str) and bool(window_id.strip()), "WINDOW_ID_INVALID"
                )
                _require(
                    1 <= len(quotes) <= 2 and len({q.outcome for q in quotes}) == len(quotes),
                    "WINDOW_QUOTE_COUNT_INVALID",
                )
                _require(
                    math.isfinite(fill_window_sec) and fill_window_sec >= 3, "FILL_WINDOW_INVALID"
                )
                if self.attempts:
                    _require(self.safe_to_continue, "PREVIOUS_WINDOW_UNRESOLVED")
                self.used_windows.add(window_id)
                # One fresh admission for the entire request. There is no
                # account/market roundtrip between YES and NO submissions.
                meta, start, books, account = await self._await_owned(self._snapshot())
                if self.attempts:
                    _require(self.safe_to_continue, "PREVIOUS_WINDOW_EVIDENCE_CHANGED")
                refresh_quotes(meta, books)
                for quote in quotes:
                    self._validate(quote, window_id, books, meta)
                if quote_revision is not None:
                    _require(all(Decimal(str(q.price)) * Decimal(str(q.size)) >= MIN_MAKER_BUY_NOTIONAL
                                 for q in quotes), "MAKER_BUY_NOTIONAL_BELOW_MINIMUM")
                new = sum((Decimal(str(q.price)) * Decimal(str(q.size)) for q in quotes), Decimal(0))
                _require(self.committed() + new <= CAPITAL_LIMIT, "TEST_CAPITAL_LIMIT_EXCEEDED")
                _require(new <= Decimal(str(account["basic_spendable_budget_usd"])),
                         "OFFICIAL_CAPITAL_INSUFFICIENT")
                elapsed = self.official_time + self.monotonic() - self.time_anchor - start
                delta = sum((Decimal(str(f.size)) * (1 if f.token_id == meta.yes.token_id else -1)
                             for f in self.confirmed()), Decimal(0))
                cutoff = TRADING_END_S if abs(delta) >= 5 else NORMAL_END_S
                _require(TRADING_START_S <= elapsed < cutoff,
                         "MARKET_SUBMISSION_CUTOFF")
                if elapsed >= NORMAL_END_S:
                    _require(len(quotes) == 1
                             and quotes[0].outcome == ("NO" if delta > 0 else "YES")
                             and Decimal(str(quotes[0].size)) == maker_buy_size(
                                 Decimal(str(quotes[0].price)), abs(delta)),
                             "LATE_REBALANCE_QUOTE_INVALID")
                _require(account["applicable_spenders"], "SPENDER_UNKNOWN")
                _require(self.revision == account["capital_evidence_revision"],
                         "CAPITAL_EVIDENCE_CHANGED_BEFORE_SUBMIT")
                _require(self.gateway._client.pagination_failure is None,
                         self.gateway._client.pagination_failure or "ACCOUNT_PAGINATION_UNRESOLVED")
                _require(quote_revision is None or self.revision == quote_revision,
                         "STRATEGY_QUOTE_EVIDENCE_CHANGED")
                if quote_factory is not None:
                    self.record("sm_quote_refreshed", {
                        "window_id": window_id, "quotes": [asdict(q) for q in quotes],
                        "books": {"YES": books[meta.yes.token_id], "NO": books[meta.no.token_id]}})
                batch = [Placement(uuid.uuid4().hex, quote) for quote in quotes]
                self.attempts.extend(batch)
                self.invalidate("PLACEMENT_INTENT")
                # Both full commitments and both durable intents precede POST.
                for attempt in batch:
                    self.record("sm_placement_intent", {**asdict(attempt),
                        "capital_committed_usd": str(self.committed())})
                self.processor.arm_pending_many([(q.token_id, Side.BUY) for q in quotes])
                result.t0_monotonic = self.monotonic()
                deadline = result.t0_monotonic + fill_window_sec
                result.fill_deadline_monotonic = deadline
                revision = self.revision

                def guard():
                    elapsed = self.official_time + self.monotonic() - self.time_anchor - start
                    return (
                        self.fatal is None and self.revision == revision
                        and previous_ids <= self.proofs.keys()
                        and self.monotonic() < deadline
                        and self.monotonic() - self.time_anchor <= self.cfg.risk.ws_stale_halt_s
                        and TRADING_START_S <= elapsed < cutoff
                    )

                client = self.gateway._client
                client.attempt, client.batch_attempts, client.submit_guard = batch[0], batch, guard
                gateway_quotes = [Quote(q.token_id, Side.BUY, q.price, q.size) for q in quotes]

                def parse_response(raw, submitted):
                    return [order for _, order in self._placement_outcomes(raw, submitted)
                            if order is not None]

                task = asyncio.create_task(self.gateway.place(
                    gateway_quotes, replace(meta, tick_size=books[quotes[0].token_id]["tick"]),
                    submit_guard=guard, force_post_only=True, strict_placement=True,
                    tick_sizes={q.token_id: books[q.token_id]["tick"] for q in quotes},
                    placement_parser=parse_response,
                ))
                try:
                    while True:
                        try:
                            await asyncio.shield(task)
                            break
                        except asyncio.CancelledError:
                            # Drain the same request, then close every known leg.
                            self.fatal = "ABNORMAL_EXIT"
                            if task.cancelled():
                                raise RuntimeError("SUBMISSION_TASK_CANCELLED") from None
                except Exception:
                    # The actual transport observer determines whether a POST
                    # began. A timeout after POST retains every unknown leg.
                    pass
                finally:
                    client.attempt, client.batch_attempts = None, None
                outcomes = (self._placement_outcomes(batch[0].raw_response, gateway_quotes)
                            if batch[0].post_started else [("ABORTED", None) for _ in batch])
                for attempt, (outcome, order) in zip(batch, outcomes, strict=True):
                    try:
                        attempt.outcome = outcome
                        if outcome == "ACK":
                            self._accept_placement(attempt, order, meta)
                        elif outcome == "UNKNOWN":
                            self.fatal = self.fatal or "UNKNOWN_PLACEMENT_EXPOSURE"
                            self.record("sm_placement_unknown", asdict(attempt))
                        self.record("sm_placement_result", asdict(attempt))
                    except Exception:
                        if attempt.outcome != "ACK" or attempt.order_id is None:
                            attempt.outcome = "UNKNOWN"
                        self.fatal = self.fatal or "UNKNOWN_PLACEMENT_EXPOSURE"
                        with suppress(Exception):
                            self.record("sm_placement_unknown" if attempt.outcome == "UNKNOWN"
                                        else "sm_placement_record_failed", asdict(attempt))
                if all(a.outcome in {"ACK", "REJECTED", "ABORTED"} for a in batch):
                    self.processor.discard_pending()
                if self.known:
                    deadline_handle = asyncio.get_running_loop().call_later(
                        max(0, deadline - self.monotonic()), close_at_deadline)
                while (self.monotonic() < deadline and self.fatal is None
                       and all(a.outcome == "ACK" for a in batch)):
                    await self.sleep(min(0.25, max(0, deadline - self.monotonic())))
                safe = await self._await_owned(self._verify_close(cancel=True))
                if not safe and self.pending_confirmations:
                    await self._await_owned(self._wait_for_confirmation())
            except asyncio.CancelledError:
                self.fatal = "ABNORMAL_EXIT"
                await self._await_owned(self._verify_close(cancel=True), propagate_cancel=False)
            except Exception as exc:
                self.reason = self.fatal or str(exc)
                if self.display_events is not None and getattr(exc, "context", None):
                    self.display_events.append(("sm_diagnostic", exc.context))
                if any(a.outcome == "INTENT" for a in self.attempts):
                    self.fatal = self.fatal or "UNRESOLVED_PLACEMENT_INTENT"
                quote_moved = (
                    quote_revision is not None
                    and len(self.attempts) == first_attempt
                    and self.fatal is None
                    and self.reason in SAFE_QUOTE_SKIP_REASONS)
                if self.known or quote_moved:
                    # Close known exposure even when second pre-submit validation fails.
                    original_reason = self.reason
                    safe = await self._await_owned(self._verify_close(cancel=True), propagate_cancel=False)
                    if quote_moved and safe and self.safe_to_continue:
                        skipped_quote_reason = original_reason
                        self.record("sm_quote_skipped", {
                            "window_id": window_id,
                            "elapsed_s": self.official_time - int(self.slug.rsplit("-", 1)[1]),
                            "reason": original_reason})
                    elif not quote_moved:
                        self.reason = original_reason
            finally:
                if deadline_handle is not None:
                    deadline_handle.cancel()
                if deadline_job is not None:
                    # No timer cancellation job may outlive this result/store.
                    while True:
                        try:
                            await asyncio.shield(deadline_job)
                            break
                        except asyncio.CancelledError:
                            self.fatal = "ABNORMAL_EXIT"
                            if deadline_job.cancelled():
                                break
            result.end_time = time.time()
            result.close_monotonic = self.monotonic()
            result.placement_results = [asdict(a) for a in self.attempts[first_attempt:]]
            result.known_order_ids = sorted(self.known)
            result.confirmed_fills = [asdict(f) for f in self.confirmed()]
            if self.meta is not None:
                result.yes_inventory = sum(
                    f.size for f in self.confirmed() if f.token_id == self.meta.yes.token_id
                )
                result.no_inventory = sum(
                    f.size for f in self.confirmed() if f.token_id == self.meta.no.token_id
                )
            result.delta = result.yes_inventory - result.no_inventory
            result.capital_committed = float(self.committed())
            result.close_barrier = "SAFE_TO_CONTINUE" if self.safe_to_continue else "BLOCKED"
            result.block_reason = (skipped_quote_reason if self.safe_to_continue
                                   else self.reason or self.fatal)
            result.journal_references = [f"{self.journal.path}#{self.journal.sequence}"]
            try:
                self.record("sm_window_result", result.to_dict())
            except Exception:
                result.close_barrier, result.block_reason = "BLOCKED", "JOURNAL_WRITE_FAILED"
            return result

    def close(self) -> None:
        """Release resources; never automatically trade or erase the risk ledger."""
        if self.lock.locked():
            raise RuntimeError("EXECUTION_ACTIVE; cancel and await the window before teardown")
        self.closed = True
        self.reads.close()
        self.store.close()
        self.journal.close()

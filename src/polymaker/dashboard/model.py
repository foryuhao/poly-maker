"""Confirmed-fill FIFO accounting, deliberately independent of the executor."""

from __future__ import annotations

from collections import Counter, deque
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any, cast

Record = dict[str, Any]

ZERO = Decimal(0)
MICRO = Decimal("0.000001")
TRADE_KINDS = {"sm_trade", "sm_ba_taker_trade", "sm_network_exit_trade"}


def number(value: Any) -> Decimal | None:
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def compact(kind: str, payload: Record) -> Record:
    """Keep useful evidence, never copy raw API responses or credentials."""
    if kind == "sm_session":
        fields = {"session_id", "slug", "normal_entry_mode", "runtime_source_sha256",
                  "repair", "ca_observation", "ba", "ba_flow", "cooldown",
                  "normal_max_entry_price", "test_capital_limit_usdc"}
        payload = {k: v for k, v in payload.items() if k in fields}
    elif kind == "sm_run_result":
        payload = {k: v for k, v in payload.items() if k not in {"windows", "skipped_quotes"}}
    elif kind in {"sm_ca_wait", "sm_obi_wait", "sm_quote_skipped"}:
        payload = {k: v for k, v in payload.items()
                   if k in {"reason", "state", "elapsed_s", "ws_diagnostics"}}
        if isinstance(payload.get("ws_diagnostics"), dict):
            payload["ws_diagnostics"] = {
                k: v for k, v in payload["ws_diagnostics"].items()
                if k in {"reason", "generation", "sequence", "failure_count", "last_failure"}}
    elif kind == "sm_close_barrier":
        payload = {k: v for k, v in payload.items() if k in {"result", "reason"}}

    def clean(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: clean(v) for k, v in value.items()
                    if not any(s in k.lower() for s in (
                        "secret", "password", "passphrase", "private_key", "authorization",
                        "api_key", "signature", "raw_response"))
                    and k not in {"response", "signer", "funder", "wallet", "live_authority"}}
        if isinstance(value, list):
            return [clean(v) for v in value]
        return value

    return cast(Record, clean(payload))


def summarize(session_id: str, events: list[Record], sources: list[Record]) -> Record:
    with localcontext() as ctx:
        ctx.prec = 50
        return _summarize(session_id, events, sources)


def _summarize(session_id: str, events: list[Record], sources: list[Record]) -> Record:
    risk = sorted((e for e in events if e["source_kind"] == "risk"), key=lambda e: e["offset"])
    ca = sorted((e for e in events if e["source_kind"] == "ca"), key=lambda e: e["offset"])
    header: Record = next((e["payload"] for e in risk if e["kind"] == "sm_session"), {})
    final: Record = next((e["payload"] for e in reversed(risk) if e["kind"] == "sm_run_result"), {})
    slug = header.get("slug", "UNKNOWN")
    try:
        start = int(slug.rsplit("-", 1)[1])
    except (ValueError, IndexError):
        start = None
    problems = []
    if not header:
        problems.append("SESSION_HEADER_PENDING")
    for s in sources:
        for issue_field in ("errors", "gaps", "replaced"):
            if s.get(issue_field):
                problems.append(f"{s['kind'].upper()}_{issue_field.upper()}:{s[issue_field]}")
        if s.get("missing"):
            problems.append(f"{s['kind'].upper()}_SOURCE_MISSING")
        if not s.get("caught_up"):
            problems.append(f"{s['kind'].upper()}_CATCHING_UP")
        if s.get("partial"):
            problems.append(f"{s['kind'].upper()}_PARTIAL_LINE")
        if s.get("diagnostics_dropped"):
            problems.append(f"{s['kind'].upper()}_DIAGNOSTICS_DROPPED")
    ca_runtime = header.get("ca_observation", {}).get("runtime", {})
    outcomes = ca_runtime.get("snapshot", {}).get("outcomes", {})
    tokens = {str(p["token_id"]): side for side, p in outcomes.items() if p.get("token_id")}
    roles, orders, pending_orders = {}, {}, set()
    resolved_taker = set()
    posts = set()
    first: dict[tuple[str, str], Record] = {}
    confirmed: dict[tuple[str, str], Record] = {}
    status: dict[tuple[str, str], str] = {}
    phase = "NORMAL / WAIT_CA" if header.get("normal_entry_mode") == "ca_v1" else "PREFLIGHT"
    reason, network = None, None
    last_signal: dict[str, Record] = {"CA": {}, "BA": {}}
    ca_confirmed, ba_confirmed = set(), set()
    merged = {}
    recent = []
    for e in risk:
        k, p = e["kind"], e["payload"]
        if k == "sm_placement_result":
            quote = p.get("quote", {})
            oid = p.get("order_id") or p.get("expected_order_id")
            wid = quote.get("window_id", "")
            role = ("TP" if wid.endswith((":ca-tp", ":restored-tp")) else
                    "MAKER" if ":ba:" in wid else "NORMAL")
            if oid:
                roles[oid] = role
                orders[oid] = {"order_id": oid, "role": role, "quote": quote,
                               "status": p.get("outcome", "UNKNOWN")}
                if p.get("outcome") in {"UNKNOWN", "INTENT"}:
                    pending_orders.add(oid)
            if quote.get("token_id") and quote.get("outcome") in {"YES", "NO"}:
                tokens[quote["token_id"]] = quote["outcome"]
        elif k in {"sm_ba_taker_post_returned", "sm_network_exit_post_returned"}:
            oid = p.get("order_id")
            if oid and p.get("post_started"):
                roles[oid] = "TAKER" if k.startswith("sm_ba_") else "EXIT"
                orders[oid] = {"order_id": oid, "role": roles[oid],
                               "quote": p.get("plan", {}), "status": "PENDING_CONFIRMATION"}
                pending_orders.add(oid)
        elif k == "sm_network_request" and p.get("method") == "POST" and p.get("endpoint") == "/orders":
            posts.add(p.get("operation_id") or f"offset:{e['offset']}")
        elif k in TRADE_KINDS:
            key = (p.get("trade_id"), p.get("maker_order_id"))
            if not all(key):
                problems.append("FILL_ID_MISSING")
                continue
            first.setdefault(key, e)
            status[key] = p.get("status")
            if p.get("status") == "CONFIRMED":
                old = confirmed.get(key)
                if (old and all(field in old["payload"] and field in p for field in ("principal_usdc", "fee_usdc"))
                        and any(number(old["payload"][field]) != number(p[field])
                                for field in ("size", "principal_usdc", "fee_usdc"))):
                    problems.append("CONFLICTING_CONFIRMED_FILL")
                if old is None or k != "sm_trade":
                    confirmed[key] = e
        elif k == "sm_strategy_window":
            phase = p.get("state", phase)
            for quote in p.get("quotes", []):
                if quote.get("outcome") in {"YES", "NO"}:
                    tokens[quote["token_id"]] = quote["outcome"]
        elif k == "sm_ba_execution_state":
            phase = "REPAIR / " + p.get("state", "UNKNOWN")
            reason = p.get("reason") or p.get("fallback_reason")
        elif k == "sm_repair_episode":
            if p.get("event") == "ACTUAL_OPENING_QUANTITY":
                phase = "REPAIR / WAIT_BA"
            elif p.get("event") == "COMPLETE_WITH_RESIDUAL_TOLERANCE":
                phase = "NORMAL / RESIDUAL_RETAINED"
        elif k in {"sm_ba_taker_result", "sm_network_exit_result"}:
            for order in p.get("orders", []):
                if order.get("complete"):
                    resolved_taker.add(order.get("order_id"))
                    pending_orders.discard(order.get("order_id"))
        elif k in {"sm_ba_taker_state", "sm_network_exit_state"}:
            if p.get("state") in {"明确拒绝", "未发送"}:
                pending_orders.discard(p.get("order_id"))
                resolved_taker.add(p.get("order_id"))
        elif k in {"sm_ca_wait", "sm_obi_wait", "sm_quote_skipped", "sm_close_barrier"}:
            reason = p.get("reason") or reason
        elif k == "sm_cooldown":
            phase = "COOLDOWN"
        elif k == "sm_network_transition":
            network = p
        elif k == "sm_cancel_ack":
            result = p.get("result", {})
            if isinstance(result, dict):
                for oid in result.get("canceled", []):
                    if oid in orders:
                        orders[oid]["status"] = "CANCEL_ACK / FILL_RECONCILE"
        elif k == "sm_merge_result" and p.get("status") == "CONFIRMED":
            merged[p.get("merge_id") or p.get("tx_hash")] = Decimal(str(p["amount_raw"])) / 1_000_000
            tokens.update({p["yes_token_id"]: "YES", p["no_token_id"]: "NO"})
        if k == "sm_opposite_ba_execution" and p.get("event") == "CONFIRMED":
            ba_confirmed.add(p.get("episode_id"))
        if k == "sm_opposite_ba_episode" or (k == "sm_ba_episode" and header.get("repair", {}).get("policy") != "opposite_ba_v1"):
            deadline = p.get("order_deadline")
            mono = e.get("monotonic")
            last_signal["BA"][p.get("outcome", "?")] = {
                **p, "ts": e["ts"],
                "wall_deadline": e["ts"] + deadline - mono if deadline is not None and mono is not None else None}
        if k not in {"sm_ba_episode", "sm_opposite_ba_episode", "sm_session", "sm_network_request"}:
            recent.append({"ts": e["ts"], "kind": k, "reason": p.get("reason") or p.get("state")
                           or p.get("status") or p.get("event") or ""})
    for e in ca:
        p = e["payload"]
        ep = p.get("episode", {})
        if ep.get("outcome") in {"YES", "NO"}:
            tokens[ep["token_id"]] = ep["outcome"]
            deadline, mono = ep.get("deadline"), e.get("monotonic")
            last_signal["CA"][ep["outcome"]] = {
                **ep, "event": e["kind"], "ts": e["ts"],
                "wall_deadline": e["ts"] + deadline - mono if deadline is not None and mono is not None else None}
        if e["kind"] == "CA_CONFIRMED":
            ca_confirmed.add(ep.get("episode_id"))
    # This count is sourced from the dedicated detector, not trading opportunities.
    if not ba_confirmed:
        ba_confirmed = {e["payload"].get("episode_id") for e in risk
                        if e["kind"] == "sm_ba_episode" and e["payload"].get("event") == "CONFIRMED"}
    fills = []
    for key, e in confirmed.items():
        p = e["payload"]
        role = roles.get(key[1], "UNKNOWN")
        qty, price = number(p.get("size")), number(p.get("price"))
        side = tokens.get(p.get("token_id"))
        if qty is None or price is None or qty <= 0 or not ZERO < price < 1 or side is None or p.get("our_side") != "BUY":
            problems.append("UNSUPPORTED_OR_INVALID_FILL")
            continue
        if status[key] == "FAILED":
            problems.append("CONFIRMED_THEN_FAILED")
        if ((role in {"TAKER", "EXIT"} or p.get("liquidity_role") == "TAKER")
                and any(number(p.get(field)) is None for field in ("principal_usdc", "fee_usdc"))):
            problems.append("TAKER_EXACT_COST_PENDING")
            continue
        principal = number(p.get("principal_usdc", qty * price))
        fee = number(p.get("fee_usdc", 0))
        if principal is None or fee is None or principal < 0 or fee < 0:
            problems.append("INVALID_FILL_COST")
            continue
        if role == "UNKNOWN":
            problems.append("FILL_ROLE_PENDING")
        fills.append({"order_id": key[1], "trade_id": key[0], "outcome": side, "role": role,
                      "quantity": qty, "cost": principal + fee, "fee": fee,
                      "unit_cost": (principal + fee) / qty,
                      "offset": first[key]["offset"], "ts": first[key]["ts"]})
        if role not in {"TAKER", "EXIT"} or key[1] in resolved_taker:
            pending_orders.discard(key[1])
    fills.sort(key=lambda f: f["offset"])
    queues: dict[str, deque[Record]] = {"YES": deque(), "NO": deque()}
    pairs = []
    for fill in fills:
        remaining = fill["quantity"]
        opposite = queues["NO" if fill["outcome"] == "YES" else "YES"]
        while remaining > 0 and opposite:
            old = opposite[0]
            qty = min(remaining, old["remaining"])
            category = fill["role"] if fill["role"] in {"MAKER", "TAKER"} else "OTHER"
            pairs.append({"category": category, "exit_role": fill["role"], "quantity": str(qty),
                          "pnl": str(qty * (1 - old["fill"]["unit_cost"] - fill["unit_cost"])),
                          "opening_order_id": old["fill"]["order_id"], "closing_order_id": fill["order_id"],
                          "ts": max(old["fill"]["ts"], fill["ts"])})
            remaining -= qty
            old["remaining"] -= qty
            if old["remaining"] == 0:
                opposite.popleft()
        if remaining:
            queues[fill["outcome"]].append({"fill": fill, "remaining": remaining})
    acquired = {side: sum((f["quantity"] for f in fills if f["outcome"] == side), ZERO)
                for side in ("YES", "NO")}
    merged_qty = sum(merged.values(), ZERO)
    residual = [{"outcome": side, "quantity": str(lot["remaining"]),
                 "cost": str(lot["remaining"] * lot["fill"]["unit_cost"])}
                for side in queues for lot in queues[side]]
    by_role = {role: sum((Decimal(p["pnl"]) for p in pairs if p["category"] == role), ZERO)
               for role in ("MAKER", "TAKER", "OTHER")}
    pair_pnl = sum(by_role.values(), ZERO)
    paired_qty = sum((Decimal(p["quantity"]) for p in pairs), ZERO)
    if merged_qty > paired_qty + MICRO:
        problems.append("MERGE_EXCEEDS_CONFIRMED_PAIRS")
    if final:
        phase = "ENDED / " + final.get("execution_status", "UNKNOWN")
        reason = final.get("stop_reason")
        if (final.get("execution_status") == "COMPLETED" and final.get("final_close_barrier")
                in {"SAFE", "SAFE_TO_CONTINUE", "SAFE_TO_EXIT"}):
            pending_orders.clear()
        if final.get("final_close_barrier") == "BLOCKED" or final.get("execution_status") != "COMPLETED":
            problems.append("TERMINAL_EXECUTION_UNRESOLVED")
        for side in ("YES", "NO"):
            expected = number(final.get("confirmed_" + side.lower()))
            final_merged = number(final.get("merged_pairs"))
            if expected is not None and final_merged is not None and abs(acquired[side] - expected - final_merged) > MICRO:
                problems.append("FINAL_INVENTORY_MISMATCH")
    pending_fills = sum(key not in confirmed and state in {"MATCHED", "MINED", "RETRYING"}
                        for key, state in status.items())
    if pending_fills or pending_orders:
        problems.append("EXECUTION_CONFIRMATION_PENDING")
    ca_report = final.get("ca_observation") or ca_runtime
    ca_writer = ca_report.get("writer", ca_report.get("snapshot", {}).get("writer", {}))
    if ca_writer.get("failure") or ca_writer.get("dropped"):
        problems.append("CA_WRITER_INCOMPLETE")
    ca_sequence = max((s.get("seq", 0) for s in sources if s["kind"] == "ca"), default=0)
    if ca_writer.get("accepted", 0) > ca_sequence:
        problems.append("CA_RECORDS_PENDING")
    residual_pnl: Decimal | None = ZERO if not residual else None
    sizing = final.get("sizing_report", {})
    winner = sizing.get("final_winning_outcome")
    if residual and winner in {"YES", "NO"}:
        reported = [number(sizing.get(k)) for k in ("intentional_residual_pnl", "unintentional_residual_pnl")]
        computed = sum((Decimal(lot["quantity"]) * (1 if lot["outcome"] == winner else 0)
                        - Decimal(lot["cost"]) for lot in residual), ZERO)
        valid_reported = [v for v in reported if v is not None]
        if len(valid_reported) == 2 and abs(sum(valid_reported, ZERO) - computed) <= MICRO:
            residual_pnl = computed
        else:
            problems.append("SETTLEMENT_EVIDENCE_MISMATCH")
    for oid, order in orders.items():
        order["confirmed_quantity"] = str(sum((f["quantity"] for f in fills if f["order_id"] == oid), ZERO))
        quote_qty = number(order["quote"].get("size", order["quote"].get("quantity")))
        if quote_qty is not None and Decimal(order["confirmed_quantity"]) >= quote_qty:
            order["status"] = "FILLED_CONFIRMED"
        if final:
            order["status"] = "SESSION_ENDED / " + order["status"]
    last_ts = max((s.get("last_ts", 0) for s in sources if s["kind"] == "risk"), default=0)
    total_pnl = pair_pnl + residual_pnl if residual_pnl is not None and not problems else None
    return {
        "session_id": session_id, "slug": slug, "market_start": start,
        "market_end": start + 300 if start is not None else None,
        "header": header, "phase": phase, "reason": reason, "network": network,
        "ended": bool(final), "execution_status": final.get("execution_status"),
        "last_event_at": last_ts, "sources": sources, "issues": sorted(set(problems)),
        "signals": last_signal, "signal_counts": {"CA": len(ca_confirmed), "BA": len(ba_confirmed)},
        "submitted_orders": len(posts), "filled_orders": len({f["order_id"] for f in fills}),
        "pending_fills": pending_fills, "pending_orders": len(pending_orders),
        "orders": list(orders.values()), "recent_events": recent[-8:],
        "inventory": {side: str(acquired[side] - merged_qty) for side in acquired},
        "delta": str(acquired["YES"] - acquired["NO"]),
        "paired_quantity": str(paired_qty), "merged_pairs": str(merged_qty),
        "unmerged_pairs": str(max(ZERO, paired_qty - merged_qty)), "residual": residual,
        "pair_pnl": str(pair_pnl), "pnl_by_role": {k: str(v) for k, v in by_role.items()},
        "pairs": pairs, "fees": str(sum((f["fee"] for f in fills), ZERO)),
        "residual_pnl": str(residual_pnl) if residual_pnl is not None else None,
        "trading_pnl": str(total_pnl) if total_pnl is not None else None,
        "rebates": sizing.get("actual_maker_rebate", "UNKNOWN"), "gas": None,
        "signal_event_counts": dict(Counter(e["kind"] for e in ca)),
    }

"""Read-only restart verification for an interrupted, expired SM session.

The original Journal is append-only. A distinct archive event never asserts
that the interrupted run completed, settled, or reached a safe close barrier.
"""

import hashlib
import json
import re
from datetime import datetime
from decimal import Decimal

from polymaker.domain import Side, TradeState
from polymaker.rdp_sm import _require
from polymaker.userstream.parse import normalize_order, parse_trade, strict_number

ARCHIVE_EVENT = "sm_session_interrupted_reconciled"


def validate_bound_events(group, check):
    """Execution evidence must refer to an earlier durable placement ACK."""
    from polymaker.rdp_sm_execution import Placement, WindowResult

    known, attempts = {}, {}
    for row in group:
        kind, p = row["kind"], row["data"]["payload"]
        if kind in {"sm_placement_intent", "sm_placement_ack", "sm_placement_result", "sm_placement_unknown"}:
            attempts[p["attempt_id"]] = {k: p[k] for k in Placement.__dataclass_fields__}
        if kind == "sm_placement_ack":
            known[p["order_id"]] = p["quote"]
        elif kind in {"sm_trade", "sm_order", "sm_official_reconciliation"}:
            oid = p["maker_order_id" if kind == "sm_trade" else "order_id"]
            check(oid in known)
            if kind == "sm_official_reconciliation":
                ev = normalize_order(p["order"])
                check(ev is not None and ev.order_id == oid)
            else:
                check(p["market_id"] == known[oid]["market_id"]
                      and p["token_id"] == known[oid]["token_id"])
        elif kind == "sm_cancel_ack":
            check(isinstance(p["ids"], list) and bool(p["ids"]) and set(p["ids"]) <= set(known))
        elif kind in {"sm_close_barrier", "sm_window_result"}:
            check(isinstance(p.get("confirmed_fills", []), list))
            check(all(f["maker_order_id"] in known for f in p.get("confirmed_fills", [])))
            if kind == "sm_window_result":
                check(set(p) == set(WindowResult.__dataclass_fields__))
                check(set(p["known_order_ids"]) <= set(known))
                nested = p["placement_results"]
                check(bool(known) or all(Decimal(str(p[k])) == 0
                      for k in ("yes_inventory", "no_inventory", "delta")))
            else:
                check(set(p.get("proofs", {})) <= set(known))
                nested = p.get("attempts", [])
            check(isinstance(nested, list))
            for attempt in nested:
                check(attempt == attempts.get(attempt["attempt_id"]))
                check(attempt["order_id"] is None or attempt["order_id"] in known)
            check(bool(known) or Decimal(str(p.get("capital_committed", 0))) == 0)


def pending_trades(payload, session, check):
    """Retain raw observations for the existing AccountRiskPolicy, never fills."""
    start = int(session["slug"].rsplit("-", 1)[1])
    known = set(payload["orders"])
    rows = []
    for raw in payload["trades"]:
        trade = parse_trade(raw)
        if trade.status in {TradeState.CONFIRMED, TradeState.FAILED}:
            continue
        # The marker is bound to this expired session, not arbitrary account
        # exposure. Live admission additionally reuses the full account policy
        # (official Gamma mapping, tokens, role, cost and current commitments).
        check(trade.market == payload["summary"]["market_id"]
              and start <= strict_number(raw.get("match_time", raw.get("timestamp"))) <= start + 300
              and raw.get("trader_side") == "MAKER")
        ours = [leg for leg in trade.maker_legs if leg.maker_address
                and leg.maker_address.lower() == session["funder"].lower()]
        check(bool(ours) and all(leg.side is Side.BUY for leg in ours))
        check(all(leg in ours for leg in trade.maker_legs if leg.order_id in known))
        rows.append(raw)
    return rows


async def refresh_pending_account(journal, reads, market, rows):
    """Reapply the existing risk policy, including when official pages omit rows."""
    from polymaker.rdp_sm import _account, _market
    from polymaker.rdp_sm_entry import AccountRiskPolicy

    retained = journal.historical_pending_rows + rows
    if not retained:
        return None
    if reads.account_policy is None:
        reads.account_policy = AccountRiskPolicy()
    for raw in retained:
        trade = parse_trade(raw)
        prior = reads.account_policy.pending.setdefault(trade.trade_id, raw)
        _require(prior == raw, "PENDING_TRADE_EVIDENCE_CHANGED")
    meta, _ = _market(await reads.market(market["slug"]), market["slug"])
    _require(meta.condition_id == market["condition_id"]
             and meta.yes.token_id == market["yes"]["token_id"]
             and meta.no.token_id == market["no"]["token_id"], "RESTART_CURRENT_MARKET_CONFLICT")
    return await _account(reads, meta, market)


def pending_archive(lines, official_time):
    """Preview only; the existing history reader subsequently validates all rows."""
    if not lines:
        return None
    last = json.loads(lines[-1])
    if last["kind"] in {"sm_run_result", ARCHIVE_EVENT}:
        return None
    tail = []
    for line in reversed(lines):
        row = json.loads(line)
        tail.append(row)
        if row["kind"] == "sm_session":
            break
    _require(tail[-1]["kind"] == "sm_session", "INTERRUPTED_SESSION_HEADER_MISSING")
    tail.reverse()
    session = tail[0]["data"]["payload"]
    cids = {r["data"]["payload"]["quote"]["market_id"] for r in tail
            if r["kind"] == "sm_placement_intent"}
    _require(len(cids) <= 1, "INTERRUPTED_MARKET_CONFLICT")
    summary = {"session_id": session["session_id"], "market_id": next(iter(cids), None),
               "start_time": tail[0]["ts"], "stop_time": last["ts"],
               "execution_status": "INCOMPLETE", "final_close_barrier": "BLOCKED",
               "live_authority": session["live_authority"], "stop_reason": "PROCESS_INTERRUPTED"}
    # These amounts are only a projection of recorded results. The existing
    # validate_merge_history must still prove every receipt/token/cash delta.
    from polymaker.rdp_sm_merge import SCALE

    merged = sum((r["data"]["payload"]["amount_raw"] for r in tail
                  if r["kind"] == "sm_merge_result" and r["data"]["payload"].get("status") == "CONFIRMED"), 0)
    summary.update(merged_pairs=float(Decimal(merged) / SCALE),
                   recovered_collateral_usdc=float(Decimal(merged) / SCALE))
    return {"summary": summary,
            "prefix_sha256": hashlib.sha256("".join(lines).encode()).hexdigest(),
            "official_time": official_time, "orders": {}, "trades": [], "open_orders": [],
            "metadata": None, "matched_remaining_tolerance_shares": "0"}


def validate_archive(payload, group, prefix_hash, check):
    """Verify stored official evidence as rigorously as fresh startup evidence."""
    from polymaker.rdp_sm_execution import RDP_TOLERANCE, _same_fill_economics, _terminal
    from polymaker.state.tracker import within_tolerance

    validate_bound_events(group, check)
    check(set(payload) == {"summary", "prefix_sha256", "official_time", "orders", "trades",
                           "open_orders", "metadata", "matched_remaining_tolerance_shares"})
    summary, session = payload["summary"], group[0]["data"]["payload"]
    keys = {"session_id", "market_id", "start_time", "stop_time",
            "execution_status", "final_close_barrier", "live_authority", "stop_reason"}
    check(set(summary) in (keys, keys | {"merged_pairs", "recovered_collateral_usdc"}))
    check(payload["prefix_sha256"] == prefix_hash
          and summary["execution_status"] == "INCOMPLETE"
          and summary["final_close_barrier"] == "BLOCKED"
          and summary["stop_reason"] == "PROCESS_INTERRUPTED")
    start = int(session["slug"].rsplit("-", 1)[1])
    stamp = payload["official_time"]
    check(type(stamp) in (int, float) and start + 300 <= stamp)
    cid = summary["market_id"]
    check(isinstance(cid, str) and re.fullmatch(r"0x[0-9a-fA-F]{64}", cid) is not None)
    quotes = {r["data"]["payload"]["order_id"]: r["data"]["payload"]["quote"]
              for r in group if r["kind"] == "sm_placement_ack"}
    check(isinstance(payload["orders"], dict) and set(payload["orders"]) == set(quotes))
    check(isinstance(payload["open_orders"], list))
    check(not any(r.get("id", r.get("orderID", r.get("order_id"))) in quotes
                  for r in payload["open_orders"]))
    if not quotes:
        raw = payload["metadata"]
        check(isinstance(raw, dict) and raw.get("slug") == session["slug"]
              and raw.get("conditionId") == cid)
        end = datetime.fromisoformat(raw["endDate"].replace("Z", "+00:00"))
        check(end.tzinfo is not None and end.timestamp() == start + 300)
    tolerance = Decimal(payload["matched_remaining_tolerance_shares"])
    check(tolerance.is_finite() and tolerance >= 0)
    # Reuse the executor's exact official trade identity/economics projection.
    # Its only dependency here is the bound historical condition ID.
    from types import SimpleNamespace

    from polymaker.rdp_sm_execution import SMWindowExecutor

    bound = SimpleNamespace(meta=SimpleNamespace(condition_id=cid))
    official = SMWindowExecutor._official_fills(bound, payload["trades"], set(quotes))
    for oid, quote in quotes.items():
        raw = payload["orders"][oid]
        order = normalize_order(raw)
        check(order is not None and order.order_id == oid and order.market_id == cid
              and order.token_id == quote["token_id"] and order.side is Side.BUY
              and within_tolerance(order.price, quote["price"], RDP_TOLERANCE)
              and within_tolerance(order.original_size, quote["size"], RDP_TOLERANCE)
              and _terminal(order, tolerance) and "associate_trades" in raw)
        legs = {key: value for key, value in official.items() if key[1] == oid}
        check(set(order.associate_trades) == {key[0] for key in legs})
        check(all(value[0] in {TradeState.CONFIRMED, TradeState.MATCHED, TradeState.MINED,
                               TradeState.RETRYING} and value[1] == quote["token_id"]
                  and value[2] is Side.BUY
                  and within_tolerance(value[3], quote["price"], RDP_TOLERANCE)
                  for value in legs.values()))
        check(within_tolerance(order.size_matched,
              sum((Decimal(str(v[4])) for v in legs.values()), Decimal(0)), RDP_TOLERANCE))
    # Never discard a previously observed fill merely because a later page is empty.
    for row in group:
        p = row["data"]["payload"]
        observed = p.get("trades", []) if row["kind"] == "sm_official_reconciliation" else []
        for trade in observed:
            parsed = parse_trade(trade)
            for leg in parsed.maker_legs:
                if leg.order_id in quotes:
                    key = (parsed.trade_id, leg.order_id)
                    check(key in official and _same_fill_economics(official[key][1:],
                          (leg.asset_id, leg.side, leg.price, leg.matched_amount)))
                    check(parsed.status is not TradeState.CONFIRMED
                          or official[key][0] is TradeState.CONFIRMED)
        if row["kind"] == "sm_trade" and p.get("maker_order_id") in quotes:
            key = (p["trade_id"], p["maker_order_id"])
            check(key in official and _same_fill_economics(official[key][1:],
                  (p["token_id"], Side(p["our_side"]), p["price"], p["size"])))
            check(p["status"] != "CONFIRMED" or official[key][0] is TradeState.CONFIRMED)
    pending_trades(payload, session, check)


async def reconcile_interrupted(journal, reads, market):
    """No placement, cancellation, settlement or mutation ownership is granted."""
    pending = journal.interrupted_session
    if pending is None:
        return await refresh_pending_account(journal, reads, market, [])
    payload, group = pending["payload"], pending["group"]
    client, gateway = reads.gateway._client, reads.gateway
    _require(journal.historical_identity == (gateway.address.lower(), reads.cfg.wallet.signature_type),
             "HISTORICAL_SM_ACCOUNT_IDENTITY_CONFLICT")
    quotes = {r["data"]["payload"]["order_id"]: r["data"]["payload"]["quote"]
              for r in group if r["kind"] == "sm_placement_ack"}
    client.historical_read_ids = set(quotes)  # GET permission only; never known_ids/DELETE.
    try:
        await gateway.open_orders()  # SDK enforces complete pagination.
        payload["open_orders"] = list(client.last_open_rows)
        payload["orders"] = {oid: await gateway.get_order(oid) for oid in sorted(quotes)}
        cid = payload["summary"]["market_id"]
        if not quotes:
            from polymaker.catalog.gamma import GammaClient
            from polymaker.rdp_sm import _Issue
            from polymaker.rdp_sm_entry import exact_market

            slug = group[0]["data"]["payload"]["slug"]
            async with GammaClient(reads.cfg.wallet.gamma_host) as gamma:
                if cid is not None:
                    raw = await exact_market(gamma, condition_ids=cid)
                else:
                    try:
                        raw = await exact_market(gamma, slug=slug, closed="true")
                    except _Issue as exc:
                        if str(exc) != "MARKET_NOT_AVAILABLE":
                            raise
                        raw = await exact_market(gamma, slug=slug, closed="false")
            _require(cid is None or cid == raw["conditionId"], "INTERRUPTED_MARKET_CONFLICT")
            payload["metadata"] = raw
            cid = raw["conditionId"]
            payload["summary"]["market_id"] = cid
        payload["trades"] = await gateway.get_trades(market=cid)
        _require(client.pagination_failure is None, client.pagination_failure or "ACCOUNT_PAGINATION_UNRESOLVED")
        payload["official_time"] = await reads.server_time()
        payload["matched_remaining_tolerance_shares"] = str(reads.cfg.rdp_sm.matched_remaining_tolerance_shares)
        def check(ok):
            _require(ok, "INTERRUPTED_SESSION_OFFICIAL_EVIDENCE_UNRESOLVED")
        validate_archive(payload, group, payload["prefix_sha256"], check)
        account = await refresh_pending_account(journal, reads, market,
            pending_trades(payload, group[0]["data"]["payload"], check))
        # Also preserve sticky open observations from the actual startup reads.
        _require(not (set(quotes) & set(client.observed_open_rows)), "INTERRUPTED_ORDER_STILL_OPEN")
        import time
        stamp = max(time.time(), payload["summary"]["stop_time"])
        payload["summary"]["stop_time"] = stamp
        journal.write(ARCHIVE_EVENT, {"session_id": payload["summary"]["session_id"],
                                     "payload": payload}, stamp)
        journal.interrupted_session = None
        return account
    finally:
        client.historical_read_ids = set()

"""Bounded exact-owned-ID reconciliation, including complete FAK POST receipts."""

import json
import re
from dataclasses import asdict
from types import SimpleNamespace

from py_clob_client_v2.clob_types import TradeParams
from py_clob_client_v2.constants import END_CURSOR, INITIAL_CURSOR
from py_clob_client_v2.exceptions import PolyApiException

from polymaker.domain import Side
from polymaker.m4_live_numeric import MICRO, ZERO, fill_event, maker_fill_event, units, validate_cash_fills
from polymaker.m4_live_pricing import GTD_CHASE_STAGE, follower_order
from polymaker.m4_live_task import (
    ORDER_TERMINAL,
    POST_TRADE_ID_FIELDS,
    POST_TX_HASH_FIELDS,
    require,
    taker_exception_rejection,
    zero_rejection,
)
from polymaker.m4_state import decimal
from polymaker.rdp_sm_execution import Placement, WindowQuote, _immediate_buy_not_filled_response
from polymaker.userstream.parse import maker_order_id, parse_trade, strict_number

GTD_TAIL_LIMIT = decimal("0.10")
GTD_TAIL_REASON = "M12_GTD_EXPIRED_TAIL_RECONCILED"


def saved_post_response(ledger, oid):
    records = [p for kind in ("owned_post_receipt", "owned_post_error_receipt")
               for p in ledger.facts(kind) if p["order_id"] == oid]
    require(len(records) <= 1, "M4_POST_RESPONSE_CONFLICT")
    record = records[0] if records else {}
    raw = record.get("response")
    if isinstance(raw, list):
        # One signed order is sent per request. Never erase an ambiguous batch
        # (which can contain a positive match) by treating it as no receipt.
        require(len(raw) <= 1, "M4_POST_RESPONSE_SHAPE_INVALID")
        raw = raw[0] if raw else None
    return raw, record.get("http_status")


def normalized_receipt(response):
    """Normalize supported field aliases without changing the durable facts."""
    if not isinstance(response, dict):
        return None
    receipt = dict(response)
    status = receipt.get("status")
    require(status is None or isinstance(status, str), "M4_RECEIPT_STATUS_INVALID")
    if status is not None:
        receipt["status"] = status.lower()
    for fields in (POST_TRADE_ID_FIELDS, POST_TX_HASH_FIELDS):
        values = [receipt[k] for k in fields if receipt.get(k) is not None]
        if not values:
            continue
        for refs in values:
            require(isinstance(refs, list)
                    and all(isinstance(ref, str) and ref for ref in refs),
                    "M4_RECEIPT_REFERENCES_INVALID")
            if fields == POST_TRADE_ID_FIELDS:
                trade_ids({"associate_trades": refs})
        require(all(set(refs) == set(values[0]) for refs in values),
                "M4_RECEIPT_REFERENCE_CONFLICT")
        receipt[fields[0]] = values[0]
    return receipt


def zero_receipt_proof(a, response, http_status=None):
    """Replay the same zero-fill classifiers as the live POST path after a crash."""
    plan, quote = json.loads(a["plan"]), json.loads(a["quote"])
    order_type = plan.get("order_type", "FAK")
    if http_status is not None:
        return taker_exception_rejection(
            SimpleNamespace(status_code=http_status, error_msg=response),
            decimal(plan["principal_usdc"]), order_type=order_type,
        )
    proof = zero_rejection(response, decimal(plan["principal_usdc"]), order_type=order_type)
    if proof:
        return proof
    if order_type != "FAK":
        return None
    quote["side"] = Side(quote["side"])
    attempt = Placement(a["task_id"], WindowQuote(**quote),
                        expected_order_id=a["order_id"], order_id=a["order_id"],
                        post_started=bool(a["started"]), entry_execution="taker",
                        order_type="FAK", raw_response=response)
    if _immediate_buy_not_filled_response(attempt):
        return {"kind": "EXACT_FAK_KILLED", "response": response}
    return None


def order_from_receipt(receipt, a, meta, funder):
    # A successful, final FAK matched receipt is positive evidence, unlike a 404.
    require(
        json.loads(a["plan"]).get("order_type", "FAK") == "FAK"
        and receipt
        and receipt.get("success") is True
        and receipt.get("status") == "matched"
        and not receipt.get("errorMsg")
        and not receipt.get("error")
        and all(receipt.get(k) in (None, "", a["order_id"])
                for k in ("orderID", "order_id", "orderId", "id"))
        and receipt.get("tradeIDs"),
        "M4_NO_COMPLETE_FAK_RECEIPT",
    )
    plan, quote = json.loads(a["plan"]), json.loads(a["quote"])
    require(
        decimal(receipt["takingAmount"]) > 0
        and 0 < decimal(receipt["makingAmount"]) <= decimal(plan["principal_usdc"]) + MICRO,
        "M4_RECEIPT_ECONOMICS_INVALID",
    )
    return {
        "id": a["order_id"],
        "market": meta.condition_id,
        "asset_id": quote["token_id"],
        "side": "BUY",
        "order_type": "FAK",
        "price": plan["limit_price"],
        "original_size": plan["quantity"],
        "maker_address": funder,
        "size_matched": receipt["takingAmount"],
        "associate_trades": receipt["tradeIDs"],
        "status": "MATCHED",
        "evidence_source": "OWNED_FINAL_FAK_RECEIPT",
    }


def trade_ids(raw):
    ids = raw.get("associate_trades")
    require(
        isinstance(ids, list)
        and all(isinstance(t, str) and 0 < len(t) < 200
                and re.fullmatch(r"[A-Za-z0-9_-]+", t) for t in ids)
        and len(set(ids)) == len(ids),
        "M4_ORDER_TRADE_LIST_INVALID",
    )
    return ids


def linked_trade_ids(ledger, oid):
    with ledger.lock:
        return {row[0] for row in ledger.db.execute(
            "SELECT trade_id FROM taker_trade_links WHERE order_id=?", (oid,))}


async def gtd_account_rows(gateway, oid, path, market_id):
    """Only the terminal cursor proves the account collection is complete."""
    rows = []
    cursor, seen = INITIAL_CURSOR, set()
    for _ in range(10):
        require(cursor not in seen, "M12_GTD_ACCOUNT_PAGE_INCOMPLETE")
        seen.add(cursor)
        page = await gateway._io(gateway._client.gtd_reconciliation_page, oid, path, cursor)
        require(isinstance(page, dict) and isinstance(page.get("data"), list), "M12_GTD_ACCOUNT_PAGE_INVALID")
        data = page["data"]
        require(page.get("count", len(data)) == len(data) and all(
            isinstance(row, dict) and isinstance(row.get("id"), str) and row["id"]
            and row.get("market") == market_id for row in data), "M12_GTD_ACCOUNT_PAGE_INVALID")
        rows.extend(data)
        cursor = page.get("next_cursor")
        require(isinstance(cursor, str) and cursor, "M12_GTD_ACCOUNT_PAGE_INCOMPLETE")
        if cursor == END_CURSOR:
            return rows, len(seen)
    raise ValueError("M12_GTD_ACCOUNT_PAGE_LIMIT")


def remember_gtd_tail_fill(ledger, oid, event, tolerance):
    with ledger.lock:
        saved = ledger.db.execute("SELECT economics FROM taker_trade_evidence WHERE trade_id=?",
                                  (event.trade_id,)).fetchone()
        require(saved is None or decimal(json.loads(saved[0])["size"]) == decimal(event.size),
                "M4_ORDER_TRADE_QUANTITY_CONFLICT")
        return ledger.remember_fill(oid, event, tolerance)


async def expired_gtd_tail(gateway, ledger, oid, plan, order, spec, matched, ids):
    require(follower_order(plan)
            and 0 <= spec["quantity"] - matched < GTD_TAIL_LIMIT and ids
            and decimal(ledger.get("matched_lower_bound:" + oid, "0")) == matched,
            "M12_GTD_UNPROVEN_REMAINDER")
    expiration = decimal(plan["expiration"])
    official = decimal(strict_number(await gateway._io(gateway._client.get_server_time)))
    require(0 < expiration < official
            and (order.get("expiration") is None or decimal(order["expiration"]) == expiration),
            "M12_GTD_UNPROVEN_REMAINDER")
    funder, market_id = gateway.funder.lower(), spec["market_id"]
    orders, order_pages = await gtd_account_rows(gateway, oid, "/data/orders", market_id)
    require(all(row["id"] != oid for row in orders), "M12_GTD_STILL_OPEN")
    trades, trade_pages = await gtd_account_rows(gateway, oid, "/data/trades", market_id)
    owned = {}
    for row in trades:
        legs = row.get("maker_orders")
        require(isinstance(legs, list) and all(isinstance(leg, dict) for leg in legs),
                "M12_GTD_ACCOUNT_TRADE_INVALID")
        maker_legs = [leg for leg in legs if maker_order_id(leg) == oid]
        is_taker = row.get("taker_order_id") == oid
        require(not (is_taker and maker_legs), "M12_GTD_ACCOUNT_TRADE_CONFLICT")
        if not is_taker and not maker_legs:
            require(row["id"] not in ids, "M12_GTD_ACCOUNT_TRADE_CONFLICT")
            continue
        require(owned.setdefault(row["id"], row) == row, "M12_GTD_ACCOUNT_TRADE_CONFLICT")
    require(set(owned) == set(ids) == linked_trade_ids(ledger, oid), "M12_GTD_ACCOUNT_TRADE_IDS_CONFLICT")
    events = []
    for tid, row in owned.items():
        trade = parse_trade(row)
        require(trade.trade_id == tid and trade.status.value in {"CONFIRMED", "FAILED"},
                "M12_GTD_ACCOUNT_TRADE_NOT_FINAL")
        if trade.taker_order_id == oid:
            require(str(row.get("maker_address", "")).lower() == funder, "M4_ORDER_OWNER_CONFLICT")
            event, tolerance, _ = fill_event(trade, spec)
        else:
            require(all(str(leg.get("maker_address", "")).lower() == funder
                        for leg in row["maker_orders"] if maker_order_id(leg) == oid), "M4_ORDER_OWNER_CONFLICT")
            event, tolerance, _ = maker_fill_event(trade, spec, oid, funder)
        events.append((event, tolerance))
    fills = [event for event, _ in events]
    require(sum((units(f.size) for f in fills), ZERO) == matched, "M4_ORDER_TRADE_QUANTITY_CONFLICT")
    confirmed = sum((units(f.size) for f in fills if f.status.value == "CONFIRMED"), ZERO)
    tail = spec["quantity"] - confirmed
    require(0 < tail < GTD_TAIL_LIMIT, "M12_GTD_UNPROVEN_REMAINDER")
    validate_cash_fills(fills, spec)
    fills = [remember_gtd_tail_fill(ledger, oid, event, tolerance) for event, tolerance in events]
    return fills, {"expiry": str(expiration), "exchange_time": str(official), "remainder": str(tail),
                   "funder": funder, "open_order_absent": True, "open_order_pages": order_pages,
                   "trade_pages": trade_pages}


async def reconcile_owned(market, oid):
    e, ledger = market.e, market.ledger
    client = e.gateway._client
    a = ledger.attempt(oid)
    if a["state"] in {"TERMINAL", "UNSENT"}:
        return
    try:
        require(a["started"] and ledger.owns_order(oid), "M4_FOREIGN_ORDER")
        response, http_status = saved_post_response(ledger, oid)
        if proof := zero_receipt_proof(a, response, http_status):
            with ledger.lock:
                require(
                    a["state"] != "SETTLING"
                    and decimal(ledger.get("matched_lower_bound:" + oid, "0")) == 0
                    and not ledger.db.execute(
                        "SELECT 1 FROM taker_trade_links WHERE order_id=?", (oid,)
                    ).fetchone(),
                    "M4_ZERO_RECEIPT_PRIOR_MATCH_CONFLICT",
                )
                ledger.terminal(oid, 0, 0, proof)
            ledger.set("owned_orders_need_reconciliation", any(
                p["started"] and p["state"] not in {"TERMINAL", "UNSENT"}
                for p in ledger.attempts()))
            e.record("sm_m4_owned_zero_receipt_recovered", {"order_id": oid, "proof": proof})
            return
        # Each durable response is bound to this single signed POST. Missing an
        # optional response ID must not discard positive amounts; a foreign ID
        # is contradictory evidence, not a reason to ignore the whole receipt.
        receipt = normalized_receipt(response)
        if receipt:
            require(all(receipt.get(k) in (None, "", oid)
                        for k in ("orderID", "order_id", "orderId", "id")),
                    "M4_RECEIPT_ORDER_CONFLICT")
        plan, quote = json.loads(a["plan"]), json.loads(a["quote"])
        resting = plan.get("order_type") == "GTD"
        detail_status = 200
        try:
            raw = await e.gateway._io(client.get_order, oid)
        except Exception as exc:
            if resting:
                if not isinstance(exc, PolyApiException) or exc.status_code != 404:
                    raise
                raw = None
                detail_status = 404
            else:
                raw = order_from_receipt(receipt, a, e.meta, e.gateway.funder)
        detail_missing = resting and (raw is None or raw == {} or (isinstance(raw, str) and not raw.strip()))
        if detail_missing or raw is None or raw == {}:
            if detail_missing:
                raw = {"id": oid, "market": e.meta.condition_id, "asset_id": quote["token_id"],
                       "maker_address": e.gateway.funder, "side": "BUY", "order_type": "GTD",
                       "price": plan["limit_price"], "original_size": plan["quantity"],
                       "status": "DETAIL_UNAVAILABLE", "evidence_source": "LOCAL_SIGNED_PLAN_AND_MATCH",
                       "detail_http_status": detail_status,
                       "size_matched": ledger.get("matched_lower_bound:" + oid, "0"),
                       "associate_trades": sorted(linked_trade_ids(ledger, oid))}
            else:
                raw = order_from_receipt(receipt, a, e.meta, e.gateway.funder)
        quote_price = decimal(plan["limit_price"])
        reported_price = decimal(raw["price"]) if isinstance(raw, dict) and "price" in raw else None
        price_matches = reported_price == quote_price
        if plan.get("taker_buy_amount_policy") and reported_price is not None:
            # Owned order responses can report the quote or round the signed
            # amount/share ratio to collateral precision.
            price_matches |= abs(reported_price - decimal(plan["signed_limit_price"])) <= MICRO
        require(
            isinstance(raw, dict)
            and raw.get("id") == oid
            and all(raw.get(k) in (None, oid) for k in ("order_id", "orderID", "orderId"))
            and raw.get("market") == e.meta.condition_id
            and raw.get("asset_id") == quote["token_id"]
            and raw.get("side") == "BUY"
            and raw.get("order_type") == plan.get("order_type", "FAK")
            and price_matches
            and units(raw["original_size"]) == units(plan["quantity"]),
            "M4_ORDER_IDENTITY_CONFLICT",
        )
        require(
            isinstance(raw.get("maker_address"), str)
            and e.gateway.funder
            and raw["maker_address"].lower() == e.gateway.funder.lower(),
            "M4_ORDER_OWNER_CONFLICT",
        )
        matched = units(raw["size_matched"])
        require(matched >= 0, "M4_ORDER_MATCHED_INVALID")
        ids = trade_ids(raw)
        lagging_order = None
        complete_receipt = None
        if (not resting and receipt and receipt.get("success") is True and receipt.get("status") == "matched"
                and receipt.get("tradeIDs")
                and all(receipt.get(k) not in (None, "")
                        for k in ("makingAmount", "takingAmount"))):
            complete_receipt = order_from_receipt(receipt, a, e.meta, e.gateway.funder)
            receipt_ids = trade_ids(complete_receipt)
            receipt_qty = units(complete_receipt["size_matched"])
            # Identity/ownership above must agree first. A nonterminal replica
            # may lag a final positive POST receipt; contradictory terminal or
            # extra-match evidence must still block. No 404/empty body is proof.
            if raw.get("status") in {"LIVE", "DELAYED"}:
                require(matched <= receipt_qty + MICRO and set(ids) <= set(receipt_ids),
                        "M4_RECEIPT_ORDER_CONFLICT")
                lagging_order = raw
                raw, matched, ids = complete_receipt, receipt_qty, receipt_ids
            else:
                require(abs(matched - receipt_qty) <= MICRO and set(ids) == set(receipt_ids),
                        "M4_RECEIPT_ORDER_CONFLICT")
        # Even without numeric amounts, matched status or a transaction/trade
        # reference contradicts a stale zero. Require owned match evidence;
        # FAILED linked trades may still prove zero eventual spend below.
        if receipt and (receipt.get("status") == "matched" or receipt.get("tradeIDs")
                        or receipt.get("transactionsHashes")):
            require(matched > 0, "M4_POST_MATCH_EVIDENCE_MISSING")
        previous = decimal(ledger.get("matched_lower_bound:" + oid, "0"))
        require(matched >= 0 and matched + MICRO >= previous, "M4_ORDER_MATCHED_REGRESSED")
        if matched > previous:
            ledger.set("matched_lower_bound:" + oid, str(matched))
        ledger.link_trades(oid, ids)
        require(linked_trade_ids(ledger, oid) == set(ids), "M4_ORDER_TRADE_LIST_REGRESSED")
        if receipt:
            reported = receipt.get("tradeIDs") or []
            require(
                isinstance(reported, list) and set(reported) <= set(ids),
                "M4_POST_TRADE_REFERENCES_MISSING",
            )
            if receipt.get("takingAmount") not in (None, ""):
                require(
                    matched + MICRO >= units(receipt["takingAmount"]),
                    "M4_POST_FILL_QUANTITY_MISSING",
                )
        spec = {
            "market_id": e.meta.condition_id,
            "token_id": quote["token_id"],
            "opposite_token": e.meta.no.token_id
            if quote["token_id"] == e.meta.yes.token_id
            else e.meta.yes.token_id,
            "price": decimal(plan["limit_price"]),
            "quantity": decimal(plan["quantity"]),
            "fees": plan["fees"],
            **({"buy_amount_usdc": decimal(plan["principal_usdc"])}
               if plan.get("taker_buy_amount_policy") else {}),
        }
        tail_candidate = resting and (detail_missing or
                                      (raw.get("status") == "MATCHED" and matched < spec["quantity"]))
        fills = []
        pending_margin = ZERO
        for tid in (() if detail_missing else ids):
            cursor = None
            seen = set()
            fetched = []
            for _ in range(10):
                page = await e.gateway._io(
                    client.get_trades_paginated,
                    TradeParams(id=tid, market=e.meta.condition_id, asset_id=None if resting else quote["token_id"]),
                    cursor,
                )
                require(
                    isinstance(page, dict) and isinstance(page.get("trades"), list),
                    "M4_TRADE_PAGE_INVALID",
                )
                fetched.extend(page["trades"])
                cursor = page.get("next_cursor")
                if cursor == "LTE=":
                    break
                require(
                    isinstance(cursor, str) and cursor and cursor not in seen,
                    "M4_TRADE_PAGE_INCOMPLETE",
                )
                seen.add(cursor)
            else:
                raise ValueError("M4_TRADE_PAGE_LIMIT")
            require(len(fetched) == 1 and fetched[0].get("id") == tid, "M4_TRADE_QUERY_INCOMPLETE")
            trade = parse_trade(fetched[0])
            require(trade.trade_id == tid, "M4_FOREIGN_TRADE")
            if resting and trade.raw.get("trader_side") == "MAKER":
                event, tolerance, fee_ceiling = maker_fill_event(trade, spec, oid, e.gateway.funder)
            else:
                require(trade.taker_order_id == oid, "M4_FOREIGN_TRADE")
                event, tolerance, fee_ceiling = fill_event(trade, spec)
            if tail_candidate:
                event = remember_gtd_tail_fill(ledger, oid, event, tolerance)
            else:
                event = ledger.remember_fill(oid, event, tolerance)
            fills.append(event)
            if event.status.value not in {"CONFIRMED", "FAILED"}:
                # A stable formula-based bound, independent of which fee estimate
                # arrived first. Later allowed updates cannot consume extra budget.
                pending_margin += fee_ceiling - decimal(event.fee_usdc) + MICRO
        tail_evidence = None
        if tail_candidate:
            fills, tail_evidence = await expired_gtd_tail(e.gateway, ledger, oid, plan, raw, spec, matched, ids)
        validate_cash_fills(fills, spec)
        require(
            abs(sum((units(f.size) for f in fills), ZERO) - matched) <= MICRO,
            "M4_ORDER_TRADE_QUANTITY_CONFLICT",
        )
        if resting:
            require(matched <= units(plan["quantity"]), "M12_GTD_QUANTITY_EXCEEDED")
            if raw.get("status") in {"LIVE", "DELAYED", "UNMATCHED"}:
                # A confirmed partial fill still leaves an executable remainder.
                if raw["status"] == "LIVE" and matched == previous == 0 and not ids:
                    paid = (receipt or {}).get("makingAmount")
                    try:
                        if paid in (None, "") or decimal(paid) == 0:
                            return raw
                    except (TypeError, ValueError, ArithmeticError):
                        pass  # Inconclusive receipt cannot authorize a chase.
                return None
        require(tail_evidence is not None or raw.get("status") in ORDER_TERMINAL
                or (resting and raw.get("status") == "EXPIRED"),
                "M4_ORDER_NOT_TERMINAL")
        require(raw["status"] != "MATCHED" or matched > 0, "M4_MATCHED_ORDER_WITHOUT_MATCH")
        # Even an incomplete POST receipt may carry positive paid principal.
        # A lagging zero query cannot erase it merely because quantity or IDs
        # are missing. A final matched receipt's money must agree exactly.
        if receipt and receipt.get("makingAmount") not in (None, ""):
            reported_principal = decimal(receipt["makingAmount"])
            observed_principal = sum((decimal(f.principal_usdc) for f in fills), ZERO)
            require(
                0 <= reported_principal <= decimal(plan["principal_usdc"]) + MICRO
                and observed_principal + MICRO >= reported_principal
                and (resting or receipt.get("status") != "matched"
                     or abs(observed_principal - reported_principal) <= MICRO),
                "M4_RECEIPT_TRADE_PRINCIPAL_CONFLICT",
            )
        proof = {
            "kind": "OWNED_TERMINAL_ORDER_AND_TRADES",
            "order": raw,
            "trades": [asdict(f) for f in fills],
        }
        if lagging_order:
            proof["lagging_order_observation"] = lagging_order
        if tail_evidence is not None:
            proof.update(reason=GTD_TAIL_REASON, gtd_tail=tail_evidence)
        if all(f.status.value in {"CONFIRMED", "FAILED"} for f in fills):
            confirmed = [f for f in fills if f.status.value == "CONFIRMED"]
            qty = sum((units(f.size) for f in confirmed), ZERO)
            cost = sum((decimal(f.principal_usdc) + decimal(f.fee_usdc) for f in confirmed), ZERO)
            ledger.terminal(oid, qty, cost, proof)
            e.record(
                "sm_m4_owned_order_resolved",
                {
                    "order_id": oid,
                    "confirmed_quantity": str(qty),
                    "principal_and_fee_usdc": str(cost),
                    **({"reason": GTD_TAIL_REASON} if tail_evidence is not None else {}),
                    **({"pricing_stage": GTD_CHASE_STAGE, "old_order_id": plan["pricing"]["old_order_id"]}
                       if plan.get("pricing", {}).get("stage") == GTD_CHASE_STAGE else {}),
                },
            )
        else:
            possible = [f for f in fills if f.status.value != "FAILED"]
            cost = (
                sum((decimal(f.principal_usdc) + decimal(f.fee_usdc) for f in possible), ZERO)
                + pending_margin
            )
            # The original gross reservation already bounds permitted fills and fee rounding.
            cost = min(cost, decimal(plan["commitment_usdc"]))
            qty = sum((units(f.size) for f in possible), ZERO)
            ledger.settling(oid, proof, cost, qty)
            e.record(
                "sm_m4_owned_order_settling",
                {
                    "order_id": oid,
                    "matched_quantity": str(qty),
                    "cost_bound_usdc": str(cost),
                    "unmatched_residual_proven": True,
                },
            )
        ledger.set(
            "owned_orders_need_reconciliation",
            any(
                a["started"] and a["state"] not in {"TERMINAL", "UNSENT"} for a in ledger.attempts()
            ),
        )
    except Exception as exc:
        if ledger.attempt(oid)["state"] not in {"TERMINAL", "UNSENT"}:
            reason = {"reason": str(exc), "error_type": type(exc).__name__}
            ledger.block_reconciliation(oid, reason)
            e.record("sm_m4_owned_order_wait", {"order_id": oid, **reason})
        else:
            e.fatal = "M4_TASK_RECONCILIATION_FOLLOWUP_FAILED"
            raise

"""Merge only this session's confirmed pairs through the official settlement SDK."""

from __future__ import annotations

import math
import re
import uuid
from contextlib import suppress
from dataclasses import replace
from decimal import Decimal

from polymaker.rdp_sm import DUST_SHARES, _require
from polymaker.rdp_sm_execution import RDP_TOLERANCE
from polymaker.settlement_sweep import (
    SCALE,
    PolymarketSettlementBackend,
    Position,
    resolve_settlement_context,
)
from polymaker.state.tracker import exceeds_tolerance, fill_cost, fill_record_cost

MERGE_START_S = 240


class MergeRateLimited(RuntimeError):
    """An explicit SDK 429 before a transaction handle exists; safe to defer."""

    def __init__(self, retry_after=None):
        super().__init__("MERGE_RATE_LIMITED")
        self.retry_after = (float(retry_after) if type(retry_after) in (int, float)
                            and math.isfinite(retry_after) and retry_after >= 0 else None)


def _progress(callback):
    # Presentation must not interrupt durable transaction/accounting updates.
    if callback is not None:
        with suppress(Exception):
            callback()


def holdings(executor):
    fills = executor.economic_fills()
    return {
        token: sum((Decimal(str(f.size)) for f in fills if f.token_id == token), Decimal(0))
        - executor.merged_pairs
        for token in (executor.meta.yes.token_id, executor.meta.no.token_id)
    }


def raw(qty):
    units = qty * SCALE
    _require(units.is_finite() and units >= 0 and units == units.to_integral_value(),
             "MERGE_QUANTITY_PRECISION_INVALID")
    return int(units)


class SMMerger:
    def __init__(self, cfg):
        self.backend = PolymarketSettlementBackend(cfg)

    def prepare(self, meta, expected):
        backend = self.backend
        backend.connect()
        market = backend.client.get_market(slug=meta.slug)
        context = resolve_settlement_context(market, backend.environment)
        _require(context.condition_id.lower() == meta.condition_id.lower()
                 and (context.yes_id, context.no_id) == (meta.yes.token_id, meta.no.token_id)
                 and context.negative_risk == meta.neg_risk,
                 "MERGE_MARKET_IDENTITY_CHANGED")
        # The pinned SDK resolves metadata again at dispatch. Bind every such
        # read to this exact route, including the just-ended/closed market.
        native_resolve = backend.client._resolve_market_position_context

        def bound_resolve(*, condition_id=None, market_id=None, closed=None):
            from polymarket.errors import UserInputError

            _require(condition_id == context.condition_id and market_id is None,
                     "MERGE_SDK_MARKET_CHANGED")
            try:
                resolved = native_resolve(condition_id=condition_id, closed=closed)
            except UserInputError as exc:
                if closed is not None or str(exc) != f"No market found for condition {condition_id}":
                    raise
                resolved = native_resolve(condition_id=condition_id, closed=True)
            _require(str(resolved.condition_id).lower() == context.condition_id.lower()
                     and resolved.protocol == context.protocol
                     and tuple(map(str, resolved.token_ids)) == (context.yes_id, context.no_id)
                     and str(resolved.position_erc1155_address).lower() == context.position_erc1155_address.lower()
                     and str(resolved.adapter_address).lower() == str(context.adapter_address).lower()
                     and (context.protocol != "ctf" or resolved.neg_risk == context.negative_risk),
                     "MERGE_SDK_CONTEXT_CHANGED")
            return resolved

        backend.client._resolve_market_position_context = bound_resolve
        position = Position(context, meta.slug, 0, 0, False, False)
        yes = backend._balance(context, context.yes_id)
        no = backend._balance(context, context.no_id)
        _require(all(abs(actual - raw(expected[token])) < DUST_SHARES * SCALE
                     for actual, token in ((yes, context.yes_id), (no, context.no_id))),
                 "MERGE_BALANCE_NOT_EXPLAINED_BY_SESSION")
        # Physical balances include ignored dust; merge at most confirmed session pairs.
        self.merge_amount_raw = min(yes, no, *(raw(qty) for qty in expected.values()))
        return replace(position, yes_raw=yes, no_raw=no), backend.collateral_raw()

    def settle(self, handle, position, collateral_before):
        # Bound SDK polling; a timeout is UNKNOWN and must never cause a retry.
        bounded = replace(handle, _max_polls=min(handle._max_polls, 12),
                          _poll_delay_s=min(handle._poll_delay_s, 2))
        tx_id, tx_hash, confirmed = self.backend.wait(bounded)
        _require(isinstance(tx_hash, str) and re.fullmatch(r"0x[0-9a-fA-F]{64}", tx_hash),
                 "MERGE_TRANSACTION_HASH_UNKNOWN")
        receipt = self.backend.w3.eth.get_transaction_receipt(tx_hash)
        status = receipt["status"]
        _require(type(status) is int and status in (0, 1), "MERGE_RECEIPT_UNKNOWN")
        if not confirmed or status != 1:
            _require(status == 0, "MERGE_TERMINAL_STATUS_CONFLICT")
            return {"status": "FAILED", "receipt_status": 0, "tx_id": tx_id, "tx_hash": tx_hash}
        yes = self.backend._balance(position.context, position.yes_token)
        no = self.backend._balance(position.context, position.no_token)
        collateral = self.backend.collateral_raw()
        amount = self.merge_amount_raw
        _require((yes, no) == (position.yes_raw - amount, position.no_raw - amount)
                 and collateral - collateral_before == amount,
                 "MERGE_POST_STATE_NOT_VERIFIED")
        return {"status": "CONFIRMED", "receipt_status": 1, "tx_id": tx_id, "tx_hash": tx_hash,
                "after_yes_raw": yes, "after_no_raw": no, "collateral_after_raw": collateral}

    def close(self):
        self.backend.close()


async def merge_confirmed_pairs(executor, *, elapsed_s=None, on_progress=None):
    if executor.cfg.rdp_sm.normal_entry_mode == "m1_v1":
        return False  # M1 retains both outcomes for platform settlement.
    async with executor.lock:
        if not executor.known or executor.meta is None:
            return False
        _require(executor.safe_to_continue, "MERGE_CLOSE_BARRIER_REQUIRED")
        elapsed = elapsed_s
        if elapsed is None:
            now = await executor._await_owned(executor.reads.server_time())
            elapsed = now - int(executor.slug.rsplit("-", 1)[1])
        if elapsed < MERGE_START_S or min(holdings(executor).values()) < DUST_SHARES:
            return False
        _require(await executor._await_owned(executor._verify_close(cancel=True)),
                 "MERGE_CLOSE_BARRIER_REQUIRED")
        before = holdings(executor)
        if min(before.values()) < DUST_SHARES:
            return False
        merger = SMMerger(executor.cfg)
        revision = executor.revision
        try:
            try:
                position, collateral = await executor._await_owned(
                    executor.gateway._io(merger.prepare, executor.meta, before))
            except Exception as exc:
                from polymarket.errors import RateLimitError

                if isinstance(exc, RateLimitError):
                    raise MergeRateLimited(exc.retry_after) from None
                reason = "BUILDER_AUTH_MISSING" if str(exc) == "BUILDER_AUTH_MISSING" else type(exc).__name__
                raise RuntimeError(f"MERGE_PREPARE_FAILED:{reason}") from None
            _require(executor.safe_to_continue and executor.revision == revision,
                     "MERGE_EVIDENCE_CHANGED_BEFORE_SUBMIT")
            _require(merger.backend.wallet.lower() == executor.gateway.funder.lower()
                     and merger.backend.signer.lower() == executor.gateway.address.lower(),
                     "MERGE_ACCOUNT_IDENTITY_CHANGED")
            if merger.merge_amount_raw < DUST_SHARES * SCALE:
                return False
            intent = {"merge_id": uuid.uuid4().hex, "condition_id": executor.meta.condition_id,
                      "wallet": executor.gateway.funder, "yes_token_id": position.yes_token,
                      "no_token_id": position.no_token, "amount_raw": merger.merge_amount_raw,
                      "dust_threshold_shares": str(DUST_SHARES),
                      "before_yes_raw": position.yes_raw, "before_no_raw": position.no_raw,
                      "collateral_before_raw": collateral, "elapsed_s": elapsed}
            _require(intent["amount_raw"] > 0, "MERGE_NO_CONFIRMED_PAIRS")
            executor.record("sm_merge_intent", intent)
            executor.merge_pending = True
            cache_write = (executor.shared_reads.begin_write("MERGE")
                           if executor.shared_reads is not None else None)
            _progress(on_progress)
            submitted = False
            try:
                handle = await executor._await_owned(executor.gateway._io(
                    merger.backend.submit_merge, position, intent["amount_raw"]))
                submitted = True
                executor.record("sm_merge_submitted", {**intent,
                    "tx_id": str(getattr(handle, "transaction_id", None) or "") or None,
                    "tx_hash": str(getattr(handle, "transaction_hash", None) or "") or None})
                _progress(on_progress)
                result = await executor._await_owned(executor.gateway._io(
                    merger.settle, handle, position, collateral))
            except Exception as exc:
                from polymarket.errors import RateLimitError, UserInputError
                if isinstance(exc, UserInputError) and not submitted:
                    result = {"status": "NOT_SUBMITTED", "error_type": "UserInputError"}
                elif isinstance(exc, RateLimitError) and not submitted:
                    # The pinned SDK returns a handle immediately after acceptance.
                    # An explicit 429 before that is rejection, unlike a transport
                    # timeout or a rate limit while polling an accepted transaction.
                    limited = MergeRateLimited(exc.retry_after)
                    result = {"status": "NOT_SUBMITTED", "error_type": "RateLimitError",
                              "http_status": 429, "retry_after_s": limited.retry_after}
                else:
                    executor.fatal = "MERGE_UNKNOWN"
                    executor.record("sm_merge_result", {**intent, "status": "UNKNOWN",
                                                         "error_type": type(exc).__name__})
                    raise RuntimeError("MERGE_UNKNOWN") from None
            finally:
                if cache_write is not None:
                    executor.shared_reads.end_write(cache_write)
            qty = Decimal(intent["amount_raw"]) / SCALE
            released = {}
            if result["status"] == "CONFIRMED":
                for token, size in before.items():
                    cost = sum((fill_cost(f) for f in executor.economic_fills()
                                if f.token_id == token), Decimal(0)) - executor.merged_cost.get(token, Decimal(0))
                    released[token] = cost * qty / size
                result["released_cost_usdc"] = str(sum(released.values(), Decimal(0)))
            executor.record("sm_merge_result", {**intent, **result})
            _progress(on_progress)
            executor.merge_pending = False
            if result.get("error_type") == "RateLimitError":
                raise MergeRateLimited(result["retry_after_s"])
            _require(result["status"] == "CONFIRMED", f"MERGE_{result['status']}")
            for token, cost in released.items():
                executor.merged_cost[token] = executor.merged_cost.get(token, Decimal(0)) + cost
            executor.merged_pairs += qty
            _require(await executor._await_owned(executor._verify_close()), "MERGE_CLOSE_RECHECK_BLOCKED")
            return True
        finally:
            await executor._await_owned(executor.gateway._io(merger.close), propagate_cancel=False)


def validate_merge_history(group, session, summary, check):
    """Require paired durable intents and complete token/collateral proofs."""
    intents, submitted, done = {}, set(), set()
    merged = 0
    retired = {}
    close = None
    owned = {e["data"]["payload"]["order_id"]: e["data"]["payload"]["quote"]
             for e in group if e["kind"] == "sm_placement_ack"}
    for event in group:
        kind, p = event["kind"], event["data"]["payload"]
        if kind == "sm_close_barrier":
            close = p
        if not kind.startswith("sm_merge_"):
            continue
        mid = p["merge_id"]
        if kind == "sm_merge_intent":
            check(isinstance(mid, str) and bool(mid) and mid not in intents)
            check(close is not None and close["result"] == "SAFE_TO_CONTINUE")
            check(p["condition_id"] == summary["market_id"]
                  and p["wallet"].lower() == session["funder"].lower()
                  and type(p["elapsed_s"]) in (int, float) and p["elapsed_s"] >= MERGE_START_S)
            check(all(type(p[k]) is int and p[k] >= 0 for k in (
                "amount_raw", "before_yes_raw", "before_no_raw", "collateral_before_raw")))
            dust_policy = p.get("dust_threshold_shares") == str(DUST_SHARES)
            check(p["amount_raw"] > 0 and p["amount_raw"] <= min(p["before_yes_raw"], p["before_no_raw"]))
            if not dust_policy:
                check(p["amount_raw"] == min(p["before_yes_raw"], p["before_no_raw"]))
            check(p["yes_token_id"] != p["no_token_id"])
            session_remaining = []
            for token, field in ((p["yes_token_id"], "before_yes_raw"), (p["no_token_id"], "before_no_raw")):
                fills = [f for f in close["confirmed_fills"] if f["token_id"] == token]
                check(all(f["status"] == "CONFIRMED" and f["market_id"] == p["condition_id"] for f in fills))
                check(all(type(f["size"]) in (int, float) and type(f["price"]) in (int, float)
                          and f["size"] > 0 and 0 < f["price"] < 1 for f in fills))
                check(all(f["maker_order_id"] in owned and f["our_side"] == "BUY"
                          and owned[f["maker_order_id"]]["token_id"] == token
                          and owned[f["maker_order_id"]]["outcome"] == (
                              "YES" if field == "before_yes_raw" else "NO")
                          # Same RDP price tolerance as live fill admission.
                          and not exceeds_tolerance(f["price"], owned[f["maker_order_id"]]["price"],
                                                    RDP_TOLERANCE) for f in fills))
                qty = sum((Decimal(str(f["size"])) for f in fills), Decimal(0))
                check(qty.is_finite() and qty * SCALE == (qty * SCALE).to_integral_value())
                remaining = int(qty * SCALE) - merged
                session_remaining.append(remaining)
                check(abs(remaining - p[field]) < DUST_SHARES * SCALE if dust_policy
                      else remaining == p[field])
            if dust_policy:
                check(p["amount_raw"] >= DUST_SHARES * SCALE
                      and p["amount_raw"] == min(*session_remaining, p["before_yes_raw"], p["before_no_raw"]))
            check(not (set(intents) - done))
            intents[mid] = p
        else:
            check(mid in intents and mid not in done)
            check(all(p.get(k) == v for k, v in intents[mid].items()))
            if kind == "sm_merge_submitted":
                check(mid not in submitted)
                submitted.add(mid)
                continue
            check(kind == "sm_merge_result")
            if p["status"] == "NOT_SUBMITTED":
                check(mid not in submitted and (
                    p.get("error_type") == "UserInputError"
                    or p.get("error_type") == "RateLimitError" and p.get("http_status") == 429))
            else:
                check(mid in submitted and isinstance(p["tx_hash"], str)
                      and re.fullmatch(r"0x[0-9a-fA-F]{64}", p["tx_hash"]))
                if p["status"] == "FAILED":
                    check(type(p["receipt_status"]) is int and p["receipt_status"] == 0)
                else:
                    check(p["status"] == "CONFIRMED" and type(p["receipt_status"]) is int
                          and p["receipt_status"] == 1)
                    amount = p["amount_raw"]
                    check(all(type(p[k]) is int and p[k] >= 0 for k in (
                        "after_yes_raw", "after_no_raw", "collateral_after_raw")))
                    check(p["after_yes_raw"] == p["before_yes_raw"] - amount
                          and p["after_no_raw"] == p["before_no_raw"] - amount
                          and p["collateral_after_raw"] == p["collateral_before_raw"] + amount)
                    released = Decimal(0)
                    for token, _field in ((p["yes_token_id"], "before_yes_raw"),
                                         (p["no_token_id"], "before_no_raw")):
                        gross_cost = sum((fill_record_cost(f)
                                          for f in close["confirmed_fills"] if f["token_id"] == token), Decimal(0))
                        owned_qty_raw = sum((Decimal(str(f["size"])) * SCALE
                                             for f in close["confirmed_fills"] if f["token_id"] == token), Decimal(0)) - merged
                        cost = (gross_cost - retired.get(token, Decimal(0))) * Decimal(amount) / owned_qty_raw
                        released += cost
                        retired[token] = retired.get(token, Decimal(0)) + cost
                    check(Decimal(p["released_cost_usdc"]) == released)
                    merged += amount
            done.add(mid)
    check(set(intents) == done)
    for field in ("merged_pairs", "recovered_collateral_usdc"):
        value = summary.get(field, 0)
        check(type(value) in (int, float) and Decimal(str(value)) == Decimal(merged) / SCALE)

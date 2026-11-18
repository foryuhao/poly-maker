"""Live-only, durable buy tasks. Never imported by the baseline runner.

Each signed FAK is sent at most once. Only exact owned-order evidence can
release its reservation and authorize another signed FAK for the residual.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sqlite3
import time
from dataclasses import asdict
from decimal import ROUND_FLOOR, Decimal
from pathlib import Path
from threading import RLock

from py_clob_client_v2.clob_types import TradeParams

from polymaker.domain import Quote, Side
from polymaker.m4_execution import SUBMIT_PENDING_TOLERANCE_SEC
from polymaker.m4_live_config import all_in_cost, gross_risk_cost, planned_fee
from polymaker.m4_live_http import NativePostNotSent
from polymaker.m4_live_taker import SinglePostM4LiveLedger, SinglePostM4TakerMarket
from polymaker.m4_state import decimal
from polymaker.maker_retry import post_exception_rejection, rejection
from polymaker.rdp_sm_ca_taker import taker_event, validate_buy_fills
from polymaker.rdp_sm_execution import Placement, WindowQuote, _immediate_buy_not_filled_response
from polymaker.userstream.parse import parse_trade

ZERO, STEP, MICRO = Decimal(0), Decimal(".1"), Decimal(".000001")
TERMINAL_TASKS = {"COMPLETE", "DUST", "BUDGET_EXHAUSTED", "CUTOFF", "SKIPPED"}
ORDER_TERMINAL = {"MATCHED", "CANCELED", "CANCELLED", "CANCELED_MARKET_RESOLVED", "INVALID"}
TRACKING = "OWNED_TAKER_ORDERS_ONLY"
POST_TRADE_ID_FIELDS = ("tradeIDs", "tradeIds", "trade_ids")
POST_TX_HASH_FIELDS = ("transactionsHashes", "transactionHashes", "transactions_hashes")
FAK_NO_MATCH = (
    "no orders found to match with fak order. "
    "fak orders are partially filled or killed if no match is found."
)
FAK_AMOUNT_PRECISION_ERROR = (
    "invalid amounts, the market buy orders maker amount supports a max accuracy "
    "of 2 decimals, taker amount a max of 4 decimals"
)


def encode(value):
    return json.dumps(value, sort_keys=True, default=str, allow_nan=False)


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def minimum_order_ok(price, quantity):
    # User contract: >= 5 shares OR strictly > 1 USDC signed notional.
    # Never round a residual upward to satisfy either branch.
    return quantity >= 5 or price * quantity > 1


def zero_rejection(raw, principal):
    item = raw[0] if isinstance(raw, list) and len(raw) == 1 else raw
    if not isinstance(item, dict):
        return None
    # Every supported positive reference vetoes zero-fill classification,
    # including the legacy shared classifier's validation-error fallback.
    if any(item.get(k) not in (None, []) for k in POST_TRADE_ID_FIELDS + POST_TX_HASH_FIELDS):
        return None
    try:
        if any(
            item.get(k) not in (None, "") and decimal(item[k]) != 0
            for k in ("makingAmount", "takingAmount")
        ):
            return None
    except (ValueError, TypeError, ArithmeticError):
        return None
    # Exact documented no-match rejection is affirmative zero-fill evidence.
    # Never broaden this to timeouts, generic 'killed', or a conflicting receipt.
    message = item.get("errorMsg") or item.get("error")
    # The actual batch API can set success=true alongside a validation error.
    # Match only this exact pre-acceptance error, empty identity/status, no
    # positive economic/reference evidence, and a locally incompatible amount.
    # Generic success=true errors, mixed results and positive evidence stay UNKNOWN.
    if (isinstance(item.get("success"), bool)
            and isinstance(message, str) and message.strip().lower() == FAK_AMOUNT_PRECISION_ERROR
            and decimal(principal) % Decimal('.01') != 0
            and all(item.get(k) in (None, "") for k in ("orderID", "order_id", "orderId", "id"))
            and item.get("status") in (None, "")):
        return {"kind": "FAK_AMOUNT_PRECISION_REJECTED", "message": message,
                "retryable": False}
    if (
        item.get("success") is False
        and isinstance(message, str)
        and message.strip().lower() == FAK_NO_MATCH
        and all(item.get(k) in (None, "") for k in ("orderID", "order_id", "orderId", "id"))
        and item.get("status") in (None, "")
    ):
        return {"kind": "FAK_NO_MATCH", "message": message, "retryable": True}
    return rejection(raw, principal)


def taker_exception_rejection(exc, principal):
    if getattr(exc, "status_code", None) in {425, 429}:
        # A status code cannot override contradictory acceptance evidence in
        # the same response (including a proxy's unexpected mixed response).
        payload = getattr(exc, "error_msg", None)
        if isinstance(payload, list) and len(payload) == 1:
            payload = payload[0]
        if isinstance(payload, dict):
            if (payload.get("success") is True
                    or payload.get("status") not in (None, "")
                    or any(payload.get(k) not in (None, "") for k in
                           ("orderID", "order_id", "orderId", "id"))
                    or any(payload.get(k) not in (None, []) for k in
                           POST_TRADE_ID_FIELDS + POST_TX_HASH_FIELDS)):
                return None
            try:
                if any(payload.get(k) not in (None, "") and decimal(payload[k]) != 0
                       for k in ("makingAmount", "takingAmount")):
                    return None
            except (ValueError, TypeError, ArithmeticError):
                return None
            message = str(payload.get("errorMsg") or payload.get("error") or "")
        elif isinstance(payload, str) or payload is None:
            message = payload or ""
        else:
            return None
        if any(word in message.lower() for word in ("duplicat", "already exists", "context canceled")):
            return None
    if getattr(exc, "status_code", None) != 400:
        return post_exception_rejection(exc, principal)
    payload = getattr(exc, "error_msg", None)
    if isinstance(payload, str):
        payload = {"success": False, "errorMsg": payload}
    elif isinstance(payload, dict):
        payload = {"success": False, **payload}
    # The same economic/conflicting-evidence checks apply to HTTP 400 bodies
    # and decoded batch results. An error string cannot erase a positive fill.
    return zero_rejection(payload, principal)


class TaskLedger(SinglePostM4LiveLedger):
    continuous = True
    revision = "m4-taker-task-v1"
    task_policy = "PROVEN_RESIDUAL_UNTIL_T240"

    @property
    def sizing_policy(self):
        return "M4_CONTINUOUS_FAK_PLANNED_MAX5_GROSS_MAX10_V1"

    def __init__(
        self,
        path,
        identity,
        *,
        settings,
        fees=None,
        limit=Decimal(10),
        order_budget=Decimal(5),
        wall=None,
    ):
        require(
            identity.get("mode") == "LIVE"
            and settings.execution_revision == self.revision
            and identity.get("settings_hash") == settings.fingerprint
            and fees is not None,
            "M4_TASK_IDENTITY_INVALID",
        )
        self.settings = settings
        snapshot = settings.taker_strategy
        if snapshot is not None:
            self.ledger_strategy = snapshot.ledger
            order_budget, limit = self.risk_limits
        require(identity.get("strategy") == self.ledger_strategy, "M4_STRATEGY_IDENTITY_REQUIRED")
        previous = self.metadata(path, strategy=self.ledger_strategy)
        if not previous:
            # Reuse the stable base schema, but never run its no-fill recovery
            # validator against an existing task ledger with confirmed fills.
            super().__init__(
                path,
                identity,
                settings=settings,
                fees=fees,
                limit=limit,
                order_budget=order_budget,
                wall=wall,
            )
        else:
            self.settings, self.fees = settings, fees
            self.path, self.wall, self.lock = Path(path).absolute(), wall or time.time, RLock()
            self.limit, self.order_budget = decimal(limit), decimal(order_budget)
            expected = {
                **identity,
                "limit": str(self.limit),
                "order_budget": str(self.order_budget),
                "prepost_policy": self.sizing_policy,
            }
            require(
                (self.order_budget, self.limit) == self.risk_limits
                and previous.get("identity") == expected
                and previous.get("taker_fees") == fees,
                "M4_TASK_RECOVERY_IDENTITY_CONFLICT",
            )
            self.db = sqlite3.connect(self.path, check_same_thread=False)
            self.db.row_factory = sqlite3.Row
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
        try:
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS taker_attempts (
                    seq INTEGER PRIMARY KEY, order_id TEXT UNIQUE NOT NULL,
                    task_id TEXT NOT NULL, plan TEXT NOT NULL, quote TEXT NOT NULL,
                    state TEXT NOT NULL, started INTEGER NOT NULL DEFAULT 0,
                    filled TEXT NOT NULL DEFAULT '0', spent TEXT NOT NULL DEFAULT '0',
                    proof TEXT);
                CREATE TABLE IF NOT EXISTS taker_trade_links (
                    trade_id TEXT PRIMARY KEY, order_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS taker_trade_evidence (
                    trade_id TEXT PRIMARY KEY, order_id TEXT NOT NULL,
                    status TEXT NOT NULL, economics TEXT NOT NULL);
            """)
            self.db.commit()
            self.set("fill_tracking", TRACKING)
            self.validate_recovery()
        except BaseException:
            self.close()
            raise

    @property
    def risk_limits(self):
        policy = self.settings.taker_strategy
        return (policy.signal_budget_usdc, policy.market_budget_usdc) if policy else super().risk_limits

    @property
    def direction_limit(self):
        policy = self.settings.taker_strategy
        return policy.direction_limit if policy else self.settings.max_opportunities_per_direction

    def attempts(self, key=None):
        with self.lock:
            return [
                dict(r)
                for r in self.db.execute(
                    "SELECT * FROM taker_attempts "
                    + ("WHERE task_id=? " if key is not None else "")
                    + "ORDER BY seq",
                    (key,) if key is not None else (),
                )
            ]

    def attempt(self, oid):
        with self.lock:
            row = self.db.execute(
                "SELECT * FROM taker_attempts WHERE order_id=?", (oid,)
            ).fetchone()
            require(row is not None, "M4_FOREIGN_ORDER")
            return dict(row)

    def root_plan(self, key):
        return super().plan(key)

    def plan(self, key):
        row = self.row(key)
        if row["order_id"] and row["state"] in {"PREPARED", "UNKNOWN", "ACK"}:
            return json.loads(self.attempt(row["order_id"])["plan"])
        plans = self.get("next_plan:" + key)
        return plans if plans is not None else self.root_plan(key)

    def initial_plan_price_allowed(self, price):
        return self.minimum_price <= price < 1

    def root_policy_evidence(self, row):
        return {}

    def plan_signal(self, key, tick, minimum, *, pricing_origin=None):
        row = self.row(key)
        anchor, tick = decimal(row["price"]), decimal(tick)
        price = self.initial_plan_price(anchor, tick)
        require(
            tick > 0 and anchor % tick == 0 and self.initial_plan_price_allowed(price),
            "M4_TAKER_ANCHOR_PLUS_TICK_INVALID",
        )
        quantity = self.order_quantity(price, minimum)
        plan = {
            "opportunity_id": key,
            "anchor_price": str(anchor),
            "tick_at_plan": str(tick),
            "limit_price": str(price),
            "quantity": str(quantity),
            "minimum": str(minimum),
            "principal_usdc": str(price * quantity),
            "planned_fee_usdc": str(planned_fee(price, quantity, self.fees)),
            "planned_cost_usdc": str(all_in_cost(price, quantity, self.fees)),
            "commitment_usdc": str(gross_risk_cost(price, quantity, self.fees)),
            "budget_policy": self.settings.execution.taker.budget_policy,
            "fees": dict(self.fees),
            "order_type": "FAK",
            "task_policy": self.task_policy,
            "max_posts_per_signed_order": 1,
            **self.root_policy_evidence(row),
        }
        if pricing_origin is not None:
            plan["pricing_origin"] = pricing_origin
        self.fact("taker_plan", key, plan)
        return plan

    def initial_plan_price(self, anchor, tick):
        return anchor + tick

    def committed(self):
        return sum((decimal(r["spent"]) + decimal(r["reserved"]) for r in self.rows()), ZERO)

    def instruction_slots(self):
        return sum(
            bool(r["posts"]) or r["state"] in {"RESERVED", "PREPARED", "UNKNOWN", "ACK"}
            for r in self.rows()
        )

    def reserve(self, key, *, spendable, direction_limit=1):
        with self.lock, self.db:
            row = self.row(key)
            if row["state"] != "OBSERVED":
                return False
            reason = (
                "SESSION_INSTRUCTION_LIMIT"
                if self.instruction_slots() >= 2 * self.direction_limit
                else "M4_DIRECTION_OPPORTUNITY_LIMIT"
                if direction_limit is not None and self.direction_slots(key) >= direction_limit
                else None
            )
            amount = decimal(self.root_plan(key)["commitment_usdc"])
            if amount > min(self.limit - self.committed(), decimal(spendable)):
                amount = ZERO  # Retain intent; next_plan sizes within available cash/risk.
            self.db.execute(
                "UPDATE opportunities SET state=?,reason=?,reserved=? WHERE id=?",
                ("SKIPPED" if reason else "RESERVED", reason, str(ZERO if reason else amount), key),
            )
            return reason is None

    def validate_recovery(self):
        for row in self.rows():
            attempts = self.attempts(row["id"])
            open_attempts = [a for a in attempts if a["state"] not in {"TERMINAL", "UNSENT"}]
            require(
                len(open_attempts) <= 1
                and row["posts"] == sum(a["started"] for a in attempts)
                and decimal(row["filled"]) == sum((decimal(a["filled"]) for a in attempts), ZERO)
                and decimal(row["spent"]) == sum((decimal(a["spent"]) for a in attempts), ZERO)
                and decimal(row["reserved"]) >= 0,
                "M4_TASK_RECOVERY_ACCOUNTING_CONFLICT",
            )
            prior_qty, prior_cost = ZERO, ZERO
            for a in attempts:
                require(
                    a["state"] in {"PREPARED", "UNKNOWN", "ACK", "TERMINAL", "UNSENT"}
                    and a["started"] in {0, 1}
                    and decimal(a["filled"]) >= 0
                    and decimal(a["spent"]) >= 0,
                    "M4_TASK_ATTEMPT_INVALID",
                )
                self.validate_attempt(a)
                p = json.loads(a["plan"])
                require(
                    decimal(p["prior_filled"]) == prior_qty
                    and decimal(p["prior_spent_usdc"]) == prior_cost,
                    "M4_TASK_RECOVERY_ATTEMPT_SEQUENCE",
                )
                if a["state"] in {"TERMINAL", "UNSENT"}:
                    proof = json.loads(a["proof"])
                    if proof.get("kind") == "OWNED_TERMINAL_ORDER_AND_TRADES":
                        trades = proof["trades"]
                        require(
                            proof["order"]["id"] == a["order_id"]
                            and all(
                                f["maker_order_id"] == a["order_id"]
                                and f["status"] in {"CONFIRMED", "FAILED"}
                                for f in trades
                            ),
                            "M4_TASK_RECOVERY_FILL_PROOF",
                        )
                        confirmed = [f for f in trades if f["status"] == "CONFIRMED"]
                        require(
                            decimal(a["filled"])
                            == sum((decimal(f["size"]) for f in confirmed), ZERO)
                            and decimal(a["spent"])
                            == sum(
                                (
                                    decimal(f["principal_usdc"]) + decimal(f["fee_usdc"])
                                    for f in confirmed
                                ),
                                ZERO,
                            ),
                            "M4_TASK_RECOVERY_FILL_PROOF",
                        )
                    else:
                        require(
                            decimal(a["filled"]) == 0 and decimal(a["spent"]) == 0,
                            "M4_TASK_RECOVERY_FILL_PROOF",
                        )
                else:
                    require(
                        decimal(a["filled"]) == 0 and decimal(a["spent"]) == 0,
                        "M4_TASK_RECOVERY_UNFINAL_DEBIT",
                    )
                prior_qty += decimal(a["filled"])
                prior_cost += decimal(a["spent"])
            if open_attempts:
                a = open_attempts[0]
                require(
                    row["order_id"] == a["order_id"]
                    and row["state"] == a["state"]
                    and row["started"] == a["started"]
                    and decimal(row["reserved"])
                    == decimal(json.loads(a["plan"])["commitment_usdc"]),
                    "M4_TASK_RECOVERY_RESERVATION_CONFLICT",
                )
            elif row["order_id"]:
                raise ValueError("M4_TASK_RECOVERY_DANGLING_ORDER")
        require(self.committed() <= self.limit, "M4_TASK_RECOVERY_OVER_LIMIT")

    def validate_attempt(self, attempt):
        row, plan, quote = (
            self.row(attempt["task_id"]),
            json.loads(attempt["plan"]),
            json.loads(attempt["quote"]),
        )
        root = self.root_plan(row["id"])
        price, qty = decimal(quote["price"]), decimal(quote["size"])
        require(
            re.fullmatch(r"0x[0-9a-fA-F]{64}", attempt["order_id"]) is not None
            and quote["window_id"] == row["id"]
            and quote["market_id"] == self.get("market")["condition_id"]
            and quote["token_id"] == row["token"]
            and quote["side"] == "BUY"
            and price
            == decimal(root["limit_price"])
            == decimal(row["price"]) + decimal(root["tick_at_plan"])
            and 0 < qty <= decimal(root["quantity"])
            and qty % STEP == 0
            and minimum_order_ok(price, qty)
            and plan["fees"] == self.fees
            and plan["limit_price"] == root["limit_price"]
            and decimal(plan["quantity"]) == qty
            and decimal(plan["principal_usdc"]) == price * qty
            and decimal(plan["planned_cost_usdc"]) == all_in_cost(price, qty, self.fees) <= 5
            and decimal(plan["prior_spent_usdc"]) + decimal(plan["planned_cost_usdc"]) <= 5
            and decimal(plan["prior_filled"]) + qty <= decimal(root["quantity"])
            and decimal(plan["commitment_usdc"]) == gross_risk_cost(price, qty, self.fees),
            "M4_TASK_ATTEMPT_ECONOMICS_CONFLICT",
        )

    def next_plan(self, key, *, minimum, spendable):
        """Round down residual; never increase quantity to satisfy a minimum."""
        with self.lock, self.db:
            row, root = self.row(key), self.root_plan(key)
            require(row["state"] == "RESERVED" and not row["order_id"], "M4_TASK_NOT_READY")
            remaining = max(ZERO, decimal(root["quantity"]) - decimal(row["filled"]))
            qty = (remaining / STEP).to_integral_value(rounding=ROUND_FLOOR) * STEP
            price = decimal(root["limit_price"])
            budget = self.order_budget - decimal(row["spent"])
            own = decimal(row["reserved"])
            available = min(self.limit - self.committed() + own, decimal(spendable) + own)
            while qty > 0 and all_in_cost(price, qty, self.fees) > budget:
                qty -= STEP
            if remaining < MICRO:
                self.finish(key, "COMPLETE", "M4_TARGET_FILLED")
                return None
            if qty <= 0 or not minimum_order_ok(price, qty):
                possible = (remaining / STEP).to_integral_value(rounding=ROUND_FLOOR) * STEP
                state = "DUST" if not minimum_order_ok(price, possible) else "BUDGET_EXHAUSTED"
                self.finish(key, state, "M4_MINIMUM_OR_BUDGET_REMAINDER")
                return None
            while qty > 0 and gross_risk_cost(price, qty, self.fees) > available:
                qty -= STEP
            if qty <= 0 or not minimum_order_ok(price, qty):
                self.db.execute(
                    "UPDATE opportunities SET reserved='0',reason='M4_WAIT_REMAINDER_FUNDS' WHERE id=?",
                    (key,),
                )
                self.db.execute("DELETE FROM metadata WHERE key=?", ("next_plan:" + key,))
                return None
            plan = {
                **root,
                "quantity": str(qty),
                "principal_usdc": str(price * qty),
                "planned_fee_usdc": str(planned_fee(price, qty, self.fees)),
                "planned_cost_usdc": str(all_in_cost(price, qty, self.fees)),
                "commitment_usdc": str(gross_risk_cost(price, qty, self.fees)),
                "prior_spent_usdc": row["spent"],
                "prior_filled": row["filled"],
                "minimum": str(minimum),
                "minimum_policy": "SHARES_GE_5_OR_NOTIONAL_GT_1",
                "max_native_posts": 1,
            }
            self.db.execute(
                "UPDATE opportunities SET reserved=? WHERE id=?", (plan["commitment_usdc"], key)
            )
            self.db.execute(
                "INSERT OR REPLACE INTO metadata VALUES (?,?)", ("next_plan:" + key, encode(plan))
            )
            return plan

    def prepare(self, key, oid, quote):
        with self.lock, self.db:
            row, plan = self.row(key), self.plan(key)
            require(
                row["state"] == "RESERVED" and not row["order_id"] and not row["started"],
                "M4_TASK_PREPARE_STATE",
            )
            a = {"task_id": key, "order_id": oid, "plan": encode(plan), "quote": encode(quote)}
            self.validate_attempt(a)
            require(
                decimal(row["spent"]) + decimal(plan["planned_cost_usdc"]) <= 5
                and decimal(row["filled"]) + decimal(plan["quantity"])
                <= decimal(self.root_plan(key)["quantity"])
                and self.committed() <= 10,
                "M4_TASK_RESIDUAL_BUDGET_CHANGED",
            )
            self.db.execute(
                "INSERT INTO taker_attempts(order_id,task_id,plan,quote,state) VALUES (?,?,?,?,?)",
                (oid, key, a["plan"], a["quote"], "PREPARED"),
            )
            self.db.execute(
                "UPDATE opportunities SET state='PREPARED',order_id=?,quote=? WHERE id=?",
                (oid, a["quote"], key),
            )

    def post_start(self, key, **kwargs):
        with self.lock, self.db:
            row = self.row(key)
            require(row["state"] == "PREPARED" and not row["started"], "M4_DUPLICATE_POST")
            a = self.attempt(row["order_id"])
            require(a["state"] == "PREPARED" and not a["started"], "M4_DUPLICATE_POST")
            self.validate_attempt(a)
            self.db.execute(
                "UPDATE taker_attempts SET state='UNKNOWN',started=1 WHERE order_id=?",
                (a["order_id"],),
            )
            self.db.execute(
                "UPDATE opportunities SET state='UNKNOWN',started=1,posts=posts+1 WHERE id=?",
                (key,),
            )

    def response(self, oid, state, proof=None):
        require(state in {"ACK", "UNKNOWN"}, "M4_TASK_RESPONSE_INVALID")
        with self.lock, self.db:
            a = self.attempt(oid)
            require(a["started"] and a["state"] in {"UNKNOWN", "ACK"}, "M4_TASK_RESPONSE_STATE")
            self.db.execute(
                "UPDATE taker_attempts SET state=?,proof=? WHERE order_id=?",
                (state, encode(proof), oid),
            )
            self.db.execute(
                "UPDATE opportunities SET state=?,reason=? WHERE id=?", (state, state, a["task_id"])
            )

    def post_receipt(self, oid, raw):
        # Keep the original response even when later reads remain UNKNOWN.
        self.fact("owned_post_receipt", oid, {"order_id": oid, "response": raw})

    def post_error_receipt(self, oid, exc):
        # SDK errors carry the HTTP response too. Preserve its positive evidence
        # before any subsequent GET can return a lagging zero. Keep the status:
        # e.g. a 500 containing a no-match message is still not zero-fill proof.
        status = getattr(exc, "status_code", None)
        if isinstance(status, int) and getattr(exc, "error_msg", None) is not None:
            self.fact("owned_post_error_receipt", oid, {
                "order_id": oid, "http_status": status, "response": exc.error_msg,
            })

    def terminal(self, oid, filled, spent, proof, *, unsent=False):
        """Atomic final proof + debit + release; repeated observation is idempotent."""
        filled, spent = decimal(filled), decimal(spent)
        with self.lock, self.db:
            a = self.attempt(oid)
            require(
                filled >= 0
                and spent >= 0
                and spent <= decimal(json.loads(a["plan"])["commitment_usdc"]),
                "M4_TASK_FILL_EXCEEDS_RESERVATION",
            )
            if a["state"] in {"TERMINAL", "UNSENT"}:
                require(
                    decimal(a["filled"]) == filled and decimal(a["spent"]) == spent,
                    "M4_TASK_TERMINAL_CONFLICT",
                )
                return
            require(bool(a["started"]) != unsent, "M4_TASK_TERMINAL_WITHOUT_START")
            row = self.row(a["task_id"])
            require(row["order_id"] == oid, "M4_TASK_CURRENT_ORDER_CONFLICT")
            self.db.execute(
                "UPDATE taker_attempts SET state=?,filled=?,spent=?,proof=? WHERE order_id=?",
                ("UNSENT" if unsent else "TERMINAL", str(filled), str(spent), encode(proof), oid),
            )
            total_qty, total_cost = decimal(row["filled"]) + filled, decimal(row["spent"]) + spent
            root = self.root_plan(row["id"])
            residual = max(ZERO, decimal(root["quantity"]) - total_qty)
            rounded = (residual / STEP).to_integral_value(rounding=ROUND_FLOOR) * STEP
            state = (
                "COMPLETE"
                if residual < MICRO
                else "DUST"
                if not minimum_order_ok(decimal(root["limit_price"]), rounded)
                else "BUDGET_EXHAUSTED"
                if total_cost >= 5
                else "RESERVED"
            )
            self.db.execute(
                "UPDATE opportunities SET state=?,filled=?,spent=?,reserved='0',"
                "order_id=NULL,quote=NULL,started=0,reason=? WHERE id=?",
                (
                    state,
                    str(total_qty),
                    str(total_cost),
                    "M4_" + state
                    if state != "RESERVED"
                    else "M4_PROVEN_RESIDUAL"
                    if not unsent
                    else "M4_PROVEN_UNSENT",
                    row["id"],
                ),
            )
            self.db.execute("DELETE FROM metadata WHERE key=?", ("next_plan:" + row["id"],))

    def finish(self, key, state, reason):
        with self.lock, self.db:
            row = self.row(key)
            require(not row["order_id"] and not row["started"], "M4_UNRESOLVED_ORDER_CANNOT_FINISH")
            self.db.execute(
                "UPDATE opportunities SET state=?,reason=?,reserved=0 WHERE id=?",
                (state, reason, key),
            )

    def owns_order(self, oid):
        return any(a["order_id"] == oid and a["started"] for a in self.attempts())

    def owns_trade(self, tid):
        with self.lock:
            r = self.db.execute(
                "SELECT order_id FROM taker_trade_links WHERE trade_id=?", (tid,)
            ).fetchone()
            return bool(r and self.owns_order(r[0]))

    def link_trades(self, oid, ids):
        with self.lock, self.db:
            require(self.owns_order(oid), "M4_FOREIGN_ORDER")
            for tid in ids:
                prior = self.db.execute(
                    "SELECT order_id FROM taker_trade_links WHERE trade_id=?", (tid,)
                ).fetchone()
                require(prior is None or prior[0] == oid, "M4_TRADE_LINK_CONFLICT")
                self.db.execute("INSERT OR IGNORE INTO taker_trade_links VALUES (?,?)", (tid, oid))

    def remember_trade(self, oid, event):
        data = asdict(event)
        status = event.status.value
        data.pop("status")
        # Transport timestamps can vary between updates; economics cannot.
        data.pop("ts", None)
        data.pop("timestamp", None)
        economics = encode(data)
        with self.lock, self.db:
            prior = self.db.execute(
                "SELECT * FROM taker_trade_evidence WHERE trade_id=?", (event.trade_id,)
            ).fetchone()
            require(
                prior is None
                or (
                    prior["order_id"] == oid
                    and prior["economics"] == economics
                    and (
                        prior["status"] not in {"CONFIRMED", "FAILED"} or prior["status"] == status
                    )
                ),
                "M4_TRADE_EVIDENCE_CONFLICT",
            )
            self.db.execute(
                "INSERT OR REPLACE INTO taker_trade_evidence VALUES (?,?,?,?)",
                (event.trade_id, oid, status, economics),
            )

    def metric(self, *, winner=None):
        rows = [r for r in self.rows() if winner is None or r["outcome"] == winner]
        plans = {p["opportunity_id"]: p for p in self.facts("taker_plan")}
        return {
            "fill_tracking": TRACKING,
            "signals": len(rows),
            "native_posts": sum(r["posts"] for r in rows),
            "submitted_unverified": sum(r["state"] in {"UNKNOWN", "ACK"} for r in rows),
            "pending": sum(r["state"] not in TERMINAL_TASKS for r in rows),
            "uncertain_retries": 0,
            "committed_usdc": str(self.committed()),
            "positions_verified": False,
            "by_signal": [
                {
                    **{
                        k: r[k]
                        for k in (
                            "id",
                            "outcome",
                            "state",
                            "reason",
                            "posts",
                            "filled",
                            "spent",
                            "reserved",
                        )
                    },
                    "spent_usdc": r["spent"],
                    "target_quantity": plans.get(r["id"], {}).get("quantity"),
                    "remaining_quantity": str(
                        max(ZERO, decimal(plans[r["id"]]["quantity"]) - decimal(r["filled"]))
                    )
                    if r["id"] in plans
                    else None,
                    "remaining_planned_budget_usdc": str(
                        max(ZERO, self.order_budget - decimal(r["spent"]))
                    ),
                }
                for r in rows
            ],
        }


class TaskMarket(SinglePostM4TakerMarket):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ledger.set("fill_tracking", TRACKING)
        self.e.record(
            "sm_m4_owned_fill_tracking",
            {
                "scope": TRACKING,
                "positions_verified": False,
                "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            },
        )

    def restore(self):
        for row in self.ledger.rows():
            if row["state"] in TERMINAL_TASKS or row["state"] == "ABORTED":
                continue
            try:
                self.ledger.root_plan(row["id"])
            except ValueError:
                require(not row["posts"] and not row["order_id"], "M4_POSTED_TASK_WITHOUT_PLAN")
                self.ledger.finish(row["id"], "SKIPPED", "M4_INCOMPLETE_UNSENT_INTENT")
                continue
            if row["order_id"]:
                a = self.ledger.attempt(row["order_id"])
                if not a["started"]:
                    self.ledger.terminal(
                        a["order_id"], 0, 0, {"kind": "RESTART_UNSENT"}, unsent=True
                    )
                else:
                    q, plan = json.loads(a["quote"]), json.loads(a["plan"])
                    q["side"] = Side(q["side"])
                    self.register(
                        Placement(
                            row["id"],
                            WindowQuote(**q),
                            outcome="UNKNOWN",
                            expected_order_id=a["order_id"],
                            order_id=a["order_id"],
                            post_started=True,
                            entry_execution="taker",
                            order_type="FAK",
                            fee_reserve_usdc=str(
                                decimal(plan["commitment_usdc"]) - decimal(plan["principal_usdc"])
                            ),
                        )
                    )
            self.pending[row["id"]] = self.restored_opportunity(row)

    def schedule(self, opportunity):
        row = self.ledger.row(opportunity.episode_id)
        if row["state"] in TERMINAL_TASKS or self.stop.is_set() or self.e.inhibited():
            return
        if not row["order_id"] and not self.entry_window_open():
            self.ledger.finish(row["id"], "CUTOFF", "M4_T240_RESIDUAL")
            return
        delay = min(
            self.settings.retry_max_sec,
            self.settings.retry_initial_sec * 2 ** min(10, max(0, row["posts"] - 1)),
        )
        # Unknown / ACK reconciliation is read-only and bounded, also at T240+.
        if row["order_id"]:
            delay = max(0.5, delay)
        self.pending[row["id"]] = opportunity
        self.retry_after[row["id"]] = self.e.monotonic() + delay

    def guard(self, opportunity, quote=None):
        e, key = self.e, opportunity.episode_id
        with self.feed.lock:
            row, plan = self.ledger.row(key), self.ledger.plan(key)
            book, now = self.feed.books[opportunity.token_id], e.monotonic()
            pending = list(self.feed.m1_pending_received)
            if self.feed.m1_processing_received is not None:
                pending.append(self.feed.m1_processing_received)
            age = now - min(pending) if pending else 0.0
            health = e.reads.network.snapshot()
            price = decimal(plan["limit_price"])
            cash = (
                min(
                    decimal(self.account[k])
                    for k in ("official_balance_usd", "applicable_allowance_usd")
                )
                if self.account_current()
                else ZERO
            )
            checks = [
                ("M4_STOPPING", not self.stop.is_set() and not e.closed and not e.inhibited()),
                (e.fatal or "M4_FATAL_EXECUTION_STATE", e.fatal is None),
                ("M4_JOURNAL_UNAVAILABLE", e.journal.ready),
                (
                    "M4_UNRESOLVED_PRIOR_ATTEMPT",
                    not row["started"] and row["state"] in {"OBSERVED", "RESERVED", "PREPARED"},
                ),
                (self.account_reason or "M4_ACCOUNT_SNAPSHOT_STALE", self.account_current()),
                ("M4_ACCOUNT_BUDGET", self.ledger.committed() <= cash),
                ("M4_ENTRY_WINDOW_ENDED", self.entry_window_open()),
                (
                    "M4_POST_LATENCY_BUDGET",
                    quote is None or self.initial_post_budget_available(opportunity),
                ),
                ("M4_SUBMIT_INPUT_BACKLOG", -1e-9 <= age <= SUBMIT_PENDING_TOLERANCE_SEC + 1e-9),
                ("M4_BOOK_UNAVAILABLE", self.feed.m1.legal(book, now)),
                ("M4_TAKER_TICK_CHANGED", book.tick == decimal(plan["tick_at_plan"])),
                ("M4_TAKER_PRICE_CAP", book.best_ask is not None and book.best_ask <= price),
                ("M4_PREPARED_PRICE_CHANGED", quote is None or decimal(quote.price) == price),
                (
                    "M4_TASK_BUDGET",
                    quote is None
                    or decimal(row["spent"]) + all_in_cost(price, quote.size, plan["fees"]) <= 5,
                ),
                ("M4_SESSION_BUDGET", self.ledger.committed() <= 10),
                (health.reason, health.allowed),
            ]
            self.guard_reason = next((reason for reason, valid in checks if not valid), None)
            return self.guard_reason is None

    def guard_failure(self, key):
        return self.guard_reason

    async def _submit(self, opportunity):
        e, key = self.e, opportunity.episode_id
        row = self.ledger.row(key)
        if row["state"] in TERMINAL_TASKS or self.stop.is_set() or e.inhibited():
            return
        if row["started"]:
            await self.reconcile(row["order_id"])
            self.schedule(opportunity)
            return
        if not self.guard(opportunity):
            self.schedule(opportunity)
            return
        if row["state"] == "OBSERVED" and not self.ledger.reserve(
            key,
            spendable=self.spendable(),
            direction_limit=self.ledger.direction_limit,
        ):
            return
        plan = self.ledger.next_plan(key, minimum=e.meta.min_order_size, spendable=self.spendable())
        if plan is None:
            e.record("sm_m4_task_remainder", self.ledger.row(key))
            self.schedule(opportunity)
            return
        quote = WindowQuote(
            e.meta.condition_id,
            row["token"],
            row["outcome"],
            Side.BUY,
            float(decimal(plan["limit_price"])),
            float(decimal(plan["quantity"])),
            key,
        )
        attempt, oid = None, None
        try:
            ticks = {token: float(book.tick) for token, book in self.feed.books.items()}
            prepared = await e.gateway.prepare(
                [Quote(quote.token_id, Side.BUY, quote.price, quote.size)], e.meta, ticks
            )
            oid = prepared.order_ids[0]
            self.ledger.prepare(key, oid, asdict(quote))
            e.record(
                "sm_prepared",
                {
                    "order_id": oid,
                    "opportunity_id": key,
                    "quote": asdict(quote),
                    "body_hash": prepared.body_hash,
                },
            )
            await e.journal.flush()
            attempt = Placement(
                f"{key}:post{row['posts'] + 1}", quote, expected_order_id=oid, order_id=oid
            )
            self.register(attempt)

            def post_start():
                # FULL-sync SQLite commit precedes any native HTTP write.
                self.ledger.post_start(key)
                e.record(
                    "sm_m4_native_post",
                    {
                        "opportunity_id": key,
                        "order_id": oid,
                        "post_number": self.ledger.row(key)["posts"],
                        "quote": asdict(quote),
                        "elapsed_s": self.elapsed(),
                        "execution_mode": "taker",
                    },
                )

            raw = await e.gateway.send_prepared(
                prepared,
                submit_guard=lambda: self.guard(opportunity, quote),
                operation_context={
                    "attempt": attempt,
                    "batch_attempts": [attempt],
                    "network_batch_id": None,
                    "on_post_start": post_start,
                },
            )
            if self.ledger.attempt(oid)["started"]:
                self.ledger.post_receipt(oid, raw)
            if not self.ledger.attempt(oid)["started"]:
                self.ledger.terminal(
                    oid, 0, 0, {"kind": "NOT_SENT", "reason": self.guard_failure(key)}, unsent=True
                )
            elif _immediate_buy_not_filled_response(attempt):
                self.ledger.terminal(oid, 0, 0, {"kind": "EXACT_FAK_KILLED", "response": raw})
            elif proof := zero_rejection(raw, attempt.principal_limit):
                self.ledger.terminal(oid, 0, 0, proof)
            else:
                outcome, order = e._placement_outcomes(raw, [quote])[0]
                state = (
                    "ACK"
                    if outcome == "ACK" and order is not None and order.order_id == oid
                    else "UNKNOWN"
                )
                self.ledger.response(oid, state, raw)
            e.record(
                "sm_m4_live_post",
                {
                    "order_id": oid,
                    "opportunity_id": key,
                    "outcome": self.ledger.row(key)["state"],
                    "quote": asdict(quote),
                },
            )
        except Exception as exc:
            if oid is not None and any(a["order_id"] == oid for a in self.ledger.attempts(key)):
                a = self.ledger.attempt(oid)
                if (isinstance(exc, NativePostNotSent) and exc.order_id == oid
                        and getattr(self.ledger, "cash_target", False)):
                    self.ledger.native_not_sent(oid, str(exc))
                    attempt.post_started = False
                    e.record("sm_m4_native_post_blocked", {
                        "order_id": oid, "opportunity_id": key, "reason": str(exc),
                        "elapsed_s": self.elapsed(), "native_post_sent": False,
                    })
                elif a["state"] in {"TERMINAL", "UNSENT"}:
                    e.fatal = "M4_TASK_OUTCOME_FOLLOWUP_FAILED"
                    raise
                elif not a["started"]:
                    self.ledger.terminal(
                        oid, 0, 0, {"kind": "NOT_SENT", "error": type(exc).__name__}, unsent=True
                    )
                else:
                    self.ledger.post_error_receipt(oid, exc)
                    if attempt and (proof := taker_exception_rejection(exc, attempt.principal_limit)):
                        self.ledger.terminal(oid, 0, 0, proof)
                    else:
                        self.ledger.response(oid, "UNKNOWN", {"error": type(exc).__name__})
            e.record(
                "sm_m4_live_post_error",
                {
                    "opportunity_id": key,
                    "order_id": oid,
                    "error_type": type(exc).__name__,
                    "reason": str(exc),
                },
            )
        finally:
            self.schedule(opportunity)

    async def reconcile(self, oid):
        """Read one owned order and only its explicitly associated trade IDs."""
        e, ledger, client = self.e, self.ledger, self.e.gateway._client
        a = ledger.attempt(oid)
        if a["state"] in {"TERMINAL", "UNSENT"}:
            return
        try:
            require(a["started"] and ledger.owns_order(oid), "M4_FOREIGN_ORDER")
            raw = await e.gateway._io(client.get_order, oid)
            plan, quote = json.loads(a["plan"]), json.loads(a["quote"])
            require(
                isinstance(raw, dict)
                and raw.get("id") == oid
                and all(raw.get(k) in (None, oid) for k in ("order_id", "orderID", "orderId"))
                and raw.get("market") == e.meta.condition_id
                and raw.get("asset_id") == quote["token_id"]
                and raw.get("side") == "BUY"
                and raw.get("order_type") == "FAK"
                and decimal(raw["price"]) == decimal(plan["limit_price"])
                and decimal(raw["original_size"]) == decimal(plan["quantity"]),
                "M4_ORDER_IDENTITY_CONFLICT",
            )
            expected_funder = e.gateway.funder
            require(
                isinstance(raw.get("maker_address"), str)
                and expected_funder
                and raw["maker_address"].lower() == expected_funder.lower(),
                "M4_ORDER_OWNER_CONFLICT",
            )
            matched = decimal(raw["size_matched"])
            require(matched >= 0, "M4_ORDER_MATCHED_INVALID")
            previous_matched = decimal(ledger.get("matched_lower_bound:" + oid, "0"))
            require(matched >= previous_matched, "M4_ORDER_MATCHED_REGRESSED")
            ledger.set("matched_lower_bound:" + oid, str(matched))
            ids = raw.get("associate_trades")
            require(
                isinstance(ids, list)
                and all(
                    isinstance(t, str) and 0 < len(t) < 200 and re.fullmatch(r"[A-Za-z0-9_-]+", t)
                    for t in ids
                )
                and len(set(ids)) == len(ids),
                "M4_ORDER_TRADE_LIST_INVALID",
            )
            ledger.link_trades(oid, ids)
            with ledger.lock:
                known = {
                    r[0]
                    for r in ledger.db.execute(
                        "SELECT trade_id FROM taker_trade_links WHERE order_id=?", (oid,)
                    )
                }
            require(known == set(ids), "M4_ORDER_TRADE_LIST_REGRESSED")
            receipt = next(
                (x["response"] for x in ledger.facts("owned_post_receipt") if x["order_id"] == oid),
                None,
            )
            if isinstance(receipt, list) and len(receipt) == 1:
                receipt = receipt[0]
            if isinstance(receipt, dict) and receipt.get("orderID") == oid:
                reported_ids = receipt.get("tradeIDs") or []
                require(
                    isinstance(reported_ids, list) and set(reported_ids) <= set(ids),
                    "M4_POST_TRADE_REFERENCES_MISSING",
                )
                if receipt.get("takingAmount") not in (None, ""):
                    require(
                        matched + MICRO >= decimal(receipt["takingAmount"]),
                        "M4_POST_FILL_QUANTITY_MISSING",
                    )
            fills = []
            opposite = (
                e.meta.no.token_id
                if quote["token_id"] == e.meta.yes.token_id
                else e.meta.yes.token_id
            )
            spec = {
                "market_id": e.meta.condition_id,
                "token_id": quote["token_id"],
                "opposite_token": opposite,
                "price": decimal(plan["limit_price"]),
                "quantity": decimal(plan["quantity"]),
                "fees": plan["fees"],
            }
            for tid in ids:
                cursor, seen, fetched = None, set(), []
                for _ in range(10):
                    page = await e.gateway._io(
                        client.get_trades_paginated,
                        TradeParams(id=tid, market=e.meta.condition_id, asset_id=quote["token_id"]),
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
                require(
                    len(fetched) == 1 and fetched[0].get("id") == tid, "M4_TRADE_QUERY_INCOMPLETE"
                )
                trade = parse_trade(fetched[0])
                require(trade.trade_id == tid and trade.taker_order_id == oid, "M4_FOREIGN_TRADE")
                event = taker_event(trade, spec)
                ledger.remember_trade(oid, event)
                fills.append(event)
            validate_buy_fills(fills, spec)
            require(
                abs(sum((decimal(f.size) for f in fills), ZERO) - matched) <= MICRO,
                "M4_ORDER_TRADE_QUANTITY_CONFLICT",
            )
            require(raw.get("status") in ORDER_TERMINAL, "M4_ORDER_NOT_TERMINAL")
            require(raw["status"] != "MATCHED" or matched > 0, "M4_MATCHED_ORDER_WITHOUT_MATCH")
            require(
                all(f.status.value in {"CONFIRMED", "FAILED"} for f in fills), "M4_TRADES_NOT_FINAL"
            )
            confirmed = [f for f in fills if f.status.value == "CONFIRMED"]
            quantity = sum((decimal(f.size) for f in confirmed), ZERO)
            cost = sum((decimal(f.principal_usdc) + decimal(f.fee_usdc) for f in confirmed), ZERO)
            ledger.terminal(
                oid,
                quantity,
                cost,
                {
                    "kind": "OWNED_TERMINAL_ORDER_AND_TRADES",
                    "order": raw,
                    "trades": [asdict(f) for f in fills],
                },
            )
            e.record(
                "sm_m4_owned_order_resolved",
                {
                    "order_id": oid,
                    "confirmed_quantity": str(quantity),
                    "principal_and_fee_usdc": str(cost),
                },
            )
        except Exception as exc:
            # Not-found, empty/incomplete history and transient settlement are
            # not zero-fill evidence. Retain the full reservation and query again.
            if ledger.attempt(oid)["state"] not in {"TERMINAL", "UNSENT"}:
                ledger.response(
                    oid, "UNKNOWN", {"reason": str(exc), "error_type": type(exc).__name__}
                )
                e.record("sm_m4_owned_order_wait", {"order_id": oid, "reason": str(exc)})
            else:
                e.fatal = "M4_TASK_RECONCILIATION_FOLLOWUP_FAILED"
                raise

    async def advance(self):
        await super().advance()
        if (
            self.elapsed() >= self.settings.cutoff_sec
            and self.post_task is None
            and not self.stop.is_set()
        ):
            now = self.e.monotonic()
            ready = [
                r
                for r in self.ledger.rows()
                if r["started"] and r["order_id"] and self.retry_after.get(r["id"], 0) <= now
            ]
            if ready:
                r = ready[0]
                self.retry_after[r["id"]] = now + 2
                self.post_task = asyncio.create_task(self.submit(self.restored_opportunity(r)))

    def finish_pending(self):
        # Keep exact owned IDs, unresolved reservations and confirmed debits.
        for row in self.ledger.rows():
            if row["order_id"] and not row["started"]:
                self.ledger.terminal(
                    row["order_id"], 0, 0, {"kind": "UNSENT_WINDOW_END"}, unsent=True
                )
                row = self.ledger.row(row["id"])
            if (
                not row["order_id"]
                and row["state"] not in TERMINAL_TASKS
                and not self.stop.is_set()
            ):
                self.ledger.finish(row["id"], "CUTOFF", "M4_T240_RESIDUAL")
        self.pending.clear()

    def complete_window(self):
        if self.ledger.get("complete"):
            return
        self.ledger.set("complete", True)
        unresolved = self.ledger.metric()["submitted_unverified"]
        self.ledger.set("owned_orders_need_reconciliation", bool(unresolved))
        self.ledger.set("settlement_policy", "PLATFORM_AUTO_SETTLEMENT")
        self.ledger.set("settlement_status", "AWAITING_PLATFORM_SETTLEMENT_UNVERIFIED")
        self.ledger.set("remaining_positions", None)
        self.transition("COMPLETE")
        self.heartbeat()
        self.e.record(
            "sm_run_result",
            {
                "market_id": self.e.meta.condition_id,
                "execution_status": "TASK_WINDOW_COMPLETE_WITH_UNRESOLVED"
                if unresolved
                else "TASK_WINDOW_COMPLETE",
                "fill_tracking": TRACKING,
                "positions_verified": False,
                "settlement_status": "AWAITING_PLATFORM_SETTLEMENT_UNVERIFIED",
                "m4_submissions": self.ledger.metric(),
            },
        )

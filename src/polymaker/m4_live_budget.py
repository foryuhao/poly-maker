"""Live-only cash-target tasks with separately reserved unsettled matches."""

import json
import re
from dataclasses import asdict

from polymaker.domain import Side, TradeState
from polymaker.m4_live_config import all_in_cost, gross_risk_cost, planned_fee
from polymaker.m4_live_numeric import (
    MICRO,
    STEP,
    ZERO,
    budget_quantity,
    compatible_economics,
    fak_amounts_compatible,
    units,
)
from polymaker.m4_live_pricing import DYNAMIC, quote_valid
from polymaker.m4_live_task import TaskLedger, encode, minimum_order_ok, require
from polymaker.m4_state import decimal
from polymaker.state.tracker import TradeEvent


class BudgetLedger(TaskLedger):
    revision = "m4-taker-budget-v1"
    cash_target = True
    task_policy = "REMAINING_ALL_IN_CASH_UNTIL_T240"

    @property
    def sizing_policy(self):
        return "M4_CASH_TARGET_PLANNED_MAX5_GROSS_MAX10_V1"

    def order_quantity(self, price, minimum):
        # observe() first sizes the signal's anchor, which is not its POST cap.
        # Keep the reference quantity distinct from the executable next_plan.
        quantity = budget_quantity(price, self.order_budget, self.fees, executable=False)
        require(
            minimum_order_ok(decimal(price), quantity), "M4_TAKER_MINIMUM_EXCEEDS_ALL_IN_BUDGET"
        )
        return quantity

    def plan_signal(self, key, tick, minimum, *, pricing_origin=None):
        require((pricing_origin is not None) == self.dynamic_pricing, "M4_PRICING_ORIGIN_MISSING")
        plan = super().plan_signal(key, tick, minimum, pricing_origin=pricing_origin)
        self.set("budget_target:" + key, "5")
        return plan

    @property
    def dynamic_pricing(self):
        return self.settings.execution.taker.pricing_policy == DYNAMIC

    def pricing_evidence(self, identity):
        with self.lock:
            found = self.db.execute(
                "SELECT payload FROM facts WHERE kind='taker_pricing_quote' AND identity=?",
                (identity,),
            ).fetchone()
            return json.loads(found[0]) if found else None

    def select_pricing(self, key, quote):
        """No active/unresolved order can be repriced or lose its reservation."""
        with self.lock:
            row, root = self.row(key), self.root_plan(key)
            require(self.dynamic_pricing and not row["order_id"] and not self.unresolved(key),
                    "M4_PRICING_ORDER_UNRESOLVED")
            plan = {**root, "pricing": quote, "limit_price": quote["limit_price"]}
            require(quote_valid(root, plan, row["token"], quote), "M4_PRICING_QUOTE_INVALID")
            self.fact("taker_pricing_quote", quote["id"], quote)
            with self.db:
                self.db.execute("INSERT OR REPLACE INTO metadata VALUES (?,?)",
                                ("pricing_selection:" + key, encode(quote)))
                self.db.execute("DELETE FROM metadata WHERE key=?", ("next_plan:" + key,))

    def selected_price(self, key):
        selected = self.get("pricing_selection:" + key) if self.dynamic_pricing else None
        return decimal(selected["limit_price"] if selected else self.root_plan(key)["limit_price"])

    def pending_matches(self, key=None):
        return [a for a in self.attempts(key) if a["state"] == "SETTLING"]

    def pending_cost(self, key):
        return sum(
            (
                decimal(self.get("settling:" + a["order_id"])["cost_bound"])
                for a in self.pending_matches(key)
            ),
            ZERO,
        )

    def budget_used(self, key):
        return decimal(self.row(key)["spent"]) + self.pending_cost(key)

    def unresolved(self, key):
        return any(
            a["state"] in {"UNKNOWN", "ACK"} or self.get("blocked:" + a["order_id"])
            for a in self.attempts(key)
        )

    def budget_basis(self, key):
        return [
            {
                "order_id": a["order_id"],
                "spent": a["spent"],
                "pending": str(
                    decimal(self.get("settling:" + a["order_id"])["cost_bound"])
                    if a["state"] == "SETTLING"
                    else ZERO
                ),
            }
            for a in self.attempts(key)
            if a["started"]
        ]

    def refresh_totals(self, key):
        """Called inside the same SQLite transaction as each evidence transition."""
        row = self.row(key)
        attempts = self.attempts(key)
        filled = sum((decimal(a["filled"]) for a in attempts), ZERO)
        spent = sum((decimal(a["spent"]) for a in attempts), ZERO)
        pending = self.pending_cost(key)
        active = self.attempt(row["order_id"]) if row["order_id"] else None
        reserve = pending + (
            decimal(json.loads(active["plan"])["commitment_usdc"]) if active else ZERO
        )
        if active:
            state, reason = active["state"], active["state"]
        elif self.get("buy_closed") or self.get("complete"):
            state, reason = "CUTOFF", self.get("market_halt_reason") or "M4_T240_ENTRY_ENDED"
        elif self.unresolved(key):
            state, reason = "WAIT_SETTLEMENT", "M4_UNKNOWN_PRIOR_ORDER"
        else:
            price = self.selected_price(key)
            qty = budget_quantity(price, self.order_budget - spent - pending, self.fees)
            if minimum_order_ok(price, qty):
                state, reason = "RESERVED", "M4_REMAINING_CASH"
            elif self.pending_matches(key):
                state, reason = "WAIT_SETTLEMENT", "M4_PENDING_MATCHES_RESERVE_BUDGET"
            elif spent >= self.order_budget - MICRO:
                state, reason = "BUDGET_EXHAUSTED", "M4_BUDGET_TARGET_REACHED"
            elif self.dynamic_pricing and self.order_budget - spent < decimal(".01"):
                # Any positive signed BUY principal must be an integral cent.
                # No future ask can make this residue executable. Pending fills
                # were handled above, so a FAILED match can still release cash.
                state, reason = "DUST", "M4_REMAINING_CASH_BELOW_USDC_CENT"
            elif self.dynamic_pricing:
                # A different ask/grid may make a previously illegal residual
                # executable. Retain the intent until T240, without rounding up.
                state, reason = "RESERVED", "M4_REMAINDER_WAIT_LEGAL_REPRICE"
            else:
                state, reason = "DUST", "M4_REMAINING_CASH_BELOW_MINIMUM"
        self.db.execute(
            "UPDATE opportunities SET filled=?,spent=?,reserved=?,state=?,reason=? WHERE id=?",
            (str(filled), str(spent), str(reserve), state, reason, key),
        )

    def next_plan(self, key, *, minimum, spendable):
        with self.lock, self.db:
            row, root = self.row(key), self.root_plan(key)
            require(row["state"] == "RESERVED" and not row["order_id"], "M4_TASK_NOT_READY")
            if self.unresolved(key):
                return None
            selected = self.get("pricing_selection:" + key) if self.dynamic_pricing else None
            require(not self.dynamic_pricing or selected is not None, "M4_PRICING_NOT_SELECTED")
            price = self.selected_price(key)
            used = self.budget_used(key)
            pending = self.pending_cost(key)
            qty = budget_quantity(price, self.order_budget - used, self.fees)
            if not minimum_order_ok(price, qty):
                self.refresh_totals(key)
                return None
            # pending matches stay reserved; only an unsent preparation may be replaced.
            own = decimal(row["reserved"]) - pending
            available = min(self.limit - self.committed() + own, decimal(spendable) + own)
            while qty > 0 and (gross_risk_cost(price, qty, self.fees) > available
                               or not fak_amounts_compatible(price, qty)):
                qty -= STEP
            if not minimum_order_ok(price, qty):
                self.db.execute(
                    "UPDATE opportunities SET reserved=?,reason='M4_WAIT_REMAINDER_FUNDS' WHERE id=?",
                    (str(pending), key),
                )
                self.db.execute("DELETE FROM metadata WHERE key=?", ("next_plan:" + key,))
                return None
            plan = {
                **root,
                "limit_price": str(price),
                **({"pricing": selected} if selected else {}),
                "quantity": str(qty),
                "principal_usdc": str(price * qty),
                "planned_fee_usdc": str(planned_fee(price, qty, self.fees)),
                "planned_cost_usdc": str(all_in_cost(price, qty, self.fees)),
                "commitment_usdc": str(gross_risk_cost(price, qty, self.fees)),
                "prior_spent_usdc": str(used),
                "prior_filled": row["filled"],
                "budget_basis": self.budget_basis(key),
                "minimum": str(minimum),
                "minimum_policy": "SHARES_GE_5_OR_NOTIONAL_GT_1",
                "amount_precision": "FAK_BUY_CENTS_SHARES_0_1_V1",
                "task_policy": "REMAINING_ALL_IN_CASH_UNTIL_T240",
                "max_native_posts": 1,
            }
            self.db.execute(
                "UPDATE opportunities SET reserved=? WHERE id=?",
                (str(pending + decimal(plan["commitment_usdc"])), key),
            )
            self.db.execute(
                "INSERT OR REPLACE INTO metadata VALUES (?,?)", ("next_plan:" + key, encode(plan))
            )
            return plan

    def validate_attempt(self, attempt):
        row = self.row(attempt["task_id"])
        plan, quote = json.loads(attempt["plan"]), json.loads(attempt["quote"])
        root = self.root_plan(row["id"])
        price, qty = decimal(quote["price"]), decimal(quote["size"])
        basis = plan.get("budget_basis", [])
        prior = sum((decimal(p["spent"]) + decimal(p["pending"]) for p in basis), ZERO)
        pricing = plan.get("pricing", {})
        require(
            ("pricing_origin" in root) == self.dynamic_pricing
            and quote_valid(root, plan, row["token"], self.pricing_evidence(pricing.get("id")))
            and price == decimal(plan["limit_price"])
            and decimal(root["limit_price"]) == decimal(row["price"]) + decimal(root["tick_at_plan"]),
            "M4_TASK_ATTEMPT_PRICING_CONFLICT",
        )
        require(
            re.fullmatch(r"0x[0-9a-fA-F]{64}", attempt["order_id"]) is not None
            and quote["window_id"] == row["id"]
            and quote["market_id"] == self.get("market")["condition_id"]
            and quote["token_id"] == row["token"]
            and quote["side"] == "BUY"
            and 0 < price < 1
            and qty > 0
            and qty % STEP == 0
            and minimum_order_ok(price, qty)
            and plan["fees"] == self.fees
            and decimal(plan["quantity"]) == qty
            and decimal(plan["principal_usdc"]) == price * qty
            and decimal(plan["planned_cost_usdc"]) == all_in_cost(price, qty, self.fees)
            and prior == decimal(plan["prior_spent_usdc"])
            and prior >= 0
            and prior + all_in_cost(price, qty, self.fees) <= 5
            and decimal(plan["commitment_usdc"]) == gross_risk_cost(price, qty, self.fees),
            "M4_TASK_ATTEMPT_ECONOMICS_CONFLICT",
        )
        seen = set()
        for p in basis:
            earlier = self.attempt(p["order_id"])
            require(
                p["order_id"] not in seen
                and earlier["task_id"] == row["id"]
                and earlier["started"]
                and (not attempt.get("seq") or earlier["seq"] < attempt["seq"])
                and decimal(p["spent"]) >= 0
                and decimal(p["pending"]) >= 0,
                "M4_BUDGET_BASIS_INVALID",
            )
            seen.add(p["order_id"])
        expected = {
            a["order_id"]
            for a in self.attempts(row["id"])
            if a["started"]
            and (not attempt.get("seq") or a["seq"] < attempt["seq"])
            and a["order_id"] != attempt["order_id"]
        }
        require(seen == expected, "M4_BUDGET_BASIS_INCOMPLETE")

    def prepare(self, key, oid, quote):
        with self.lock, self.db:
            row, plan = self.row(key), self.plan(key)
            require(
                row["state"] == "RESERVED"
                and not row["order_id"]
                and not row["started"]
                and not self.unresolved(key),
                "M4_TASK_PREPARE_STATE",
            )
            a = {"task_id": key, "order_id": oid, "plan": encode(plan), "quote": encode(quote)}
            self.validate_attempt(a)
            # Signing yields to old-order reconciliation, which may refresh
            # totals and release this not-yet-prepared plan's reservation.
            # Reacquire it atomically with PREPARED, or leave the task unsent.
            reserved = self.pending_cost(key) + decimal(plan["commitment_usdc"])
            committed = self.committed() - decimal(row["reserved"]) + reserved
            require(
                self.budget_used(key) + decimal(plan["planned_cost_usdc"]) <= 5
                and committed <= self.limit,
                "M4_TASK_RESIDUAL_BUDGET_CHANGED",
            )
            self.db.execute(
                "INSERT INTO taker_attempts(order_id,task_id,plan,quote,state) VALUES (?,?,?,?,?)",
                (oid, key, a["plan"], a["quote"], "PREPARED"),
            )
            self.db.execute(
                "UPDATE opportunities SET state='PREPARED',order_id=?,quote=?,reserved=? WHERE id=?",
                (oid, a["quote"], str(reserved), key),
            )

    def terminal(self, oid, filled, spent, proof, *, unsent=False):
        filled, spent = units(filled), units(spent)
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
            if a["state"] == "SETTLING":
                require(
                    spent <= decimal(self.get("settling:" + oid)["cost_bound"]),
                    "M4_SETTLED_COST_EXCEEDS_PENDING_BOUND",
                )
            row = self.row(a["task_id"])
            require(
                row["order_id"] == oid or a["state"] == "SETTLING", "M4_TASK_CURRENT_ORDER_CONFLICT"
            )
            self.db.execute(
                "UPDATE taker_attempts SET state=?,filled=?,spent=?,proof=? WHERE order_id=?",
                ("UNSENT" if unsent else "TERMINAL", str(filled), str(spent), encode(proof), oid),
            )
            if row["order_id"] == oid:
                self.db.execute(
                    "UPDATE opportunities SET order_id=NULL,quote=NULL,started=0 WHERE id=?",
                    (row["id"],),
                )
                self.db.execute("DELETE FROM metadata WHERE key=?", ("next_plan:" + row["id"],))
            self.db.execute("DELETE FROM metadata WHERE key=?", ("blocked:" + oid,))
            self.refresh_totals(row["id"])

    def post_start(self, key, **kwargs):
        # Last durable gate, under the same lock that updates reconciliation facts.
        # A newly detected UNKNOWN between signing and native POST forbids the buy.
        with self.lock, self.db:
            plan = self.plan(key)
            require(not self.unresolved(key), "M4_UNRESOLVED_PRIOR_ATTEMPT")
            require(
                self.budget_used(key) + decimal(plan["planned_cost_usdc"]) <= 5
                and self.committed() <= 10,
                "M4_TASK_RESIDUAL_BUDGET_CHANGED",
            )
            return super().post_start(key, **kwargs)

    def native_ready(self, key, oid):
        """Only this newly committed intent may bypass its own UNKNOWN state."""
        with self.lock:
            row, a = self.row(key), self.attempt(oid)
            self.validate_attempt(a)
            return (
                row["order_id"] == oid and row["started"] and row["state"] == "UNKNOWN"
                and a["task_id"] == key and a["started"] and a["state"] == "UNKNOWN"
                and all(
                    not self.get("blocked:" + p["order_id"])
                    and (p["order_id"] == oid or p["state"] not in {"ACK", "UNKNOWN"})
                    for p in self.attempts(key)
                )
            )

    def native_not_sent(self, oid, reason):
        """Durable intent was armed, but the native transport was never invoked.

        Keep started/posts as intent history; never infer this proof from a timeout.
        A crash before this commit conservatively leaves UNKNOWN for reconciliation.
        """
        with self.lock, self.db:
            a = self.attempt(oid)
            row = self.row(a["task_id"])
            require(a["started"] and a["state"] == "UNKNOWN" and row["order_id"] == oid,
                    "M4_LOCAL_NOT_SENT_STATE_CONFLICT")
            proof = {"kind": "LOCAL_NATIVE_POST_BLOCKED", "order_id": oid,
                     "native_post_sent": False, "reason": reason}
            self.db.execute(
                "UPDATE taker_attempts SET state='UNSENT',proof=? WHERE order_id=?",
                (encode(proof), oid),
            )
            self.db.execute(
                "UPDATE opportunities SET order_id=NULL,quote=NULL,started=0 WHERE id=?",
                (row["id"],),
            )
            self.db.execute("DELETE FROM metadata WHERE key=?", ("next_plan:" + row["id"],))
            self.refresh_totals(row["id"])

    def settling(self, oid, proof, cost_bound, quantity):
        with self.lock, self.db:
            a = self.attempt(oid)
            row = self.row(a["task_id"])
            require(
                a["started"] and a["state"] not in {"TERMINAL", "UNSENT"}, "M4_SETTLEMENT_STATE"
            )
            require(
                0 <= cost_bound <= decimal(json.loads(a["plan"])["commitment_usdc"]),
                "M4_MATCH_COST_BOUND",
            )
            self.db.execute(
                "UPDATE taker_attempts SET state='SETTLING',proof=? WHERE order_id=?",
                (encode(proof), oid),
            )
            self.db.execute(
                "INSERT OR REPLACE INTO metadata VALUES (?,?)",
                (
                    "settling:" + oid,
                    encode({"cost_bound": str(cost_bound), "quantity": str(quantity)}),
                ),
            )
            self.db.execute("DELETE FROM metadata WHERE key=?", ("blocked:" + oid,))
            if row["order_id"] == oid:
                self.db.execute(
                    "UPDATE opportunities SET order_id=NULL,quote=NULL,started=0 WHERE id=?",
                    (row["id"],),
                )
                self.db.execute("DELETE FROM metadata WHERE key=?", ("next_plan:" + row["id"],))
            self.refresh_totals(row["id"])

    def block_reconciliation(self, oid, reason):
        with self.lock, self.db:
            a = self.attempt(oid)
            if a["state"] == "SETTLING":
                self.db.execute(
                    "INSERT OR REPLACE INTO metadata VALUES (?,?)",
                    ("blocked:" + oid, encode(reason)),
                )
                self.refresh_totals(a["task_id"])
            else:
                self.response(oid, "UNKNOWN", reason)

    def finish(self, key, state, reason):
        with self.lock, self.db:
            row = self.row(key)
            require(not row["order_id"] and not row["started"], "M4_UNRESOLVED_ORDER_CANNOT_FINISH")
            self.db.execute(
                "UPDATE opportunities SET state=?,reason=?,reserved=? WHERE id=?",
                (state, reason, str(self.pending_cost(key)), key),
            )

    def remember_fill(self, oid, event, fee_tolerance):
        data = asdict(event)
        status = event.status.value
        data.pop("status")
        data.pop("ts", None)
        with self.lock, self.db:
            prior = self.db.execute(
                "SELECT * FROM taker_trade_evidence WHERE trade_id=?", (event.trade_id,)
            ).fetchone()
            if prior:
                old = json.loads(prior["economics"])
                require(
                    # Each observation is checked against the fee formula above.
                    # Two valid observations can occupy opposite ends of its tolerance.
                    prior["order_id"] == oid and compatible_economics(old, data, 2 * fee_tolerance),
                    "M4_TRADE_EVIDENCE_CONFLICT",
                )
                if prior["status"] in {"CONFIRMED", "FAILED"}:
                    require(
                        status == prior["status"] or status not in {"CONFIRMED", "FAILED"},
                        "M4_TRADE_FINALITY_CONFLICT",
                    )
                    # A lagging nonfinal replica must not undo confirmed economic facts.
                    return TradeEvent(
                        **{
                            **old,
                            "our_side": Side(old["our_side"]),
                            "status": TradeState(prior["status"]),
                            "ts": event.ts,
                        }
                    )
            self.db.execute(
                "INSERT OR REPLACE INTO taker_trade_evidence VALUES (?,?,?,?)",
                (event.trade_id, oid, status, encode(data)),
            )
            return event

    def validate_recovery(self):
        for row in self.rows():
            attempts = self.attempts(row["id"])
            active = [a for a in attempts if a["state"] in {"PREPARED", "ACK", "UNKNOWN"}]
            require(
                len(active) <= 1
                and row["posts"] == sum(a["started"] for a in attempts)
                and decimal(row["filled"]) == sum((decimal(a["filled"]) for a in attempts), ZERO)
                and decimal(row["spent"]) == sum((decimal(a["spent"]) for a in attempts), ZERO),
                "M4_TASK_RECOVERY_ACCOUNTING_CONFLICT",
            )
            for a in attempts:
                self.validate_attempt(a)
                if a["state"] == "UNSENT" and a["started"]:
                    proof = json.loads(a["proof"])
                    require(
                        proof.get("kind") == "LOCAL_NATIVE_POST_BLOCKED"
                        and proof.get("order_id") == a["order_id"]
                        and proof.get("native_post_sent") is False,
                        "M4_TASK_RECOVERY_UNSENT_PROOF",
                    )
                require(
                    a["state"] in {"PREPARED", "ACK", "UNKNOWN", "SETTLING", "TERMINAL", "UNSENT"}
                    and a["started"] in {0, 1},
                    "M4_TASK_ATTEMPT_INVALID",
                )
                if a["state"] in {"SETTLING", "TERMINAL"}:
                    proof = json.loads(a["proof"])
                    if proof.get("kind") == "OWNED_TERMINAL_ORDER_AND_TRADES":
                        trades = proof["trades"]
                        require(
                            proof["order"]["id"] == a["order_id"]
                            and proof["order"]["market"] == self.get("market")["condition_id"]
                            and proof["order"]["asset_id"] == row["token"]
                            and all(
                                f["maker_order_id"] == a["order_id"]
                                and f["token_id"] == row["token"]
                                and f["market_id"] == self.get("market")["condition_id"]
                                for f in trades
                            )
                            and abs(
                                sum((units(f["size"]) for f in trades), ZERO)
                                - units(proof["order"]["size_matched"])
                            )
                            <= MICRO,
                            "M4_TASK_RECOVERY_FILL_PROOF",
                        )
                        if a["state"] == "TERMINAL":
                            require(
                                all(f["status"] in {"CONFIRMED", "FAILED"} for f in trades),
                                "M4_TASK_RECOVERY_UNFINAL_DEBIT",
                            )
                            fs = [f for f in trades if f["status"] == "CONFIRMED"]
                            require(
                                decimal(a["filled"]) == sum((decimal(f["size"]) for f in fs), ZERO)
                                and decimal(a["spent"])
                                == units(
                                    sum(
                                        (
                                            decimal(f["principal_usdc"]) + decimal(f["fee_usdc"])
                                            for f in fs
                                        ),
                                        ZERO,
                                    )
                                ),
                                "M4_TASK_RECOVERY_FILL_PROOF",
                            )
                        else:
                            possible = [f for f in trades if f["status"] != "FAILED"]
                            known_cost = sum(
                                (
                                    decimal(f["principal_usdc"]) + decimal(f["fee_usdc"])
                                    for f in possible
                                ),
                                ZERO,
                            )
                            require(
                                decimal(a["filled"]) == 0
                                and decimal(a["spent"]) == 0
                                and known_cost
                                <= decimal(self.get("settling:" + a["order_id"])["cost_bound"])
                                <= decimal(json.loads(a["plan"])["commitment_usdc"])
                                and decimal(self.get("settling:" + a["order_id"])["quantity"])
                                == sum((units(f["size"]) for f in possible), ZERO),
                                "M4_TASK_RECOVERY_PENDING_COST",
                            )
                    else:
                        require(
                            a["state"] == "TERMINAL"
                            and decimal(a["filled"]) == 0
                            and decimal(a["spent"]) == 0,
                            "M4_TASK_RECOVERY_FILL_PROOF",
                        )
                else:
                    require(
                        decimal(a["filled"]) == 0 and decimal(a["spent"]) == 0,
                        "M4_TASK_RECOVERY_UNFINAL_DEBIT",
                    )
            if active:
                a = active[0]
                require(
                    row["order_id"] == a["order_id"]
                    and row["state"] == a["state"]
                    and row["started"] == a["started"]
                    and decimal(row["reserved"])
                    == self.pending_cost(row["id"])
                    + decimal(json.loads(a["plan"])["commitment_usdc"]),
                    "M4_TASK_RECOVERY_RESERVATION_CONFLICT",
                )
            else:
                require(
                    not row["order_id"]
                    and not row["started"]
                    and decimal(row["reserved"]) >= self.pending_cost(row["id"]),
                    "M4_TASK_RECOVERY_DANGLING_ORDER",
                )
        require(self.committed() <= self.limit, "M4_TASK_RECOVERY_OVER_LIMIT")

    def metric(self, *, winner=None):
        value = super().metric(winner=winner)
        value["submitted_unverified"] = sum(
            a["state"] in {"ACK", "UNKNOWN", "SETTLING"} for a in self.attempts()
        )
        value["target_kind"] = "ALL_IN_CASH"
        for item in value["by_signal"]:
            key = item["id"]
            item["initial_reference_quantity"] = item.pop("target_quantity")
            item.pop("remaining_quantity")
            item["target_budget_usdc"] = "5"
            item["pending_match_cost_bound_usdc"] = str(self.pending_cost(key))
            item["pending_matched_quantity"] = str(
                sum(
                    (
                        decimal(self.get("settling:" + a["order_id"])["quantity"])
                        for a in self.pending_matches(key)
                    ),
                    ZERO,
                )
            )
            item["requires_reconciliation"] = self.unresolved(key)
            item["remaining_planned_budget_usdc"] = str(max(ZERO, 5 - self.budget_used(key)))
        return value

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
    age_us,
    budget_quantity,
    compatible_economics,
    fak_amounts_compatible,
    units,
)
from polymaker.m4_live_price_band import (
    ABANDON_BEST_ASK_BAND, ABANDON_FIRST_SIGNED_BAND, BEST_ASK_BAND_ABANDONED,
    SIGNED_LIMIT_BAND_ABANDONED, POST_PRICE_OUTSIDE_BAND,
    WAIT_FOR_PRICE_BAND, price_in_band,
)
from polymaker.m4_live_pricing import DYNAMIC, quote_valid, rejected_price_band_valid
from polymaker.m4_live_task import TaskLedger, encode, minimum_order_ok, require
from polymaker.m4_state import decimal
from polymaker.m4_types import BEST_ASK_SIGNAL, M21_LEDGER, TakerPriceRisk, TakerStrategy, signal_lifetime_limit_us
from polymaker.state.tracker import TradeEvent

FAK_BUY_MIN_PRINCIPAL = decimal("1")
FAK_BUY_MINIMUM_POLICY = "EXCHANGE_PRINCIPAL_GE_1_USDC_V1"


def minimum_fak_buy_ok(price, quantity):
    # Keep the strategy's OR rule, and also satisfy the exchange's independent
    # marketable BUY principal floor. Shares >=5 cannot waive that cash floor.
    return minimum_order_ok(price, quantity) and price * quantity >= FAK_BUY_MIN_PRINCIPAL


class BudgetLedger(TaskLedger):
    revision = "m4-taker-budget-v1"
    cash_target = True
    @property
    def task_policy(self):
        return (f"{self.signal_strategy}_ZERO_FILL_ONLY_UNTIL_SIGNAL_DEADLINE_V1" if self.signal_limited
                else "REMAINING_ALL_IN_CASH_UNTIL_T240")

    @property
    def signal_strategy(self):
        return "M21" if self.ledger_strategy == M21_LEDGER else "M6"

    @property
    def sizing_policy(self):
        return ("STRATEGY_CASH_TARGET_POLICY_V1" if self.settings.taker_strategy else
                "M4_CASH_TARGET_PLANNED_MAX5_GROSS_MAX10_V1")

    @property
    def signal_limited(self):
        return self.settings.execution.taker.pricing_policy == BEST_ASK_SIGNAL

    def initial_plan_price(self, anchor, tick):
        # For pure Best ask execution this is an unsigned reference only.
        return anchor if self.signal_limited else super().initial_plan_price(anchor, tick)

    @property
    def post_price_band(self):
        return self.settings.post_price_band

    def signal_price_allowed(self, price):
        return 0 < price < 1 if self.post_price_band else super().signal_price_allowed(price)

    def initial_plan_price_allowed(self, price):
        # This is an unsigned pricing reference, never permission to POST.
        # Keep a plan so even a rejected initial cap has immutable evidence.
        return 0 < price <= 1 if self.post_price_band else super().initial_plan_price_allowed(price)

    @property
    def price_band_stop_reason(self):
        return (SIGNED_LIMIT_BAND_ABANDONED
                if self.settings.post_price_band_retry_policy == ABANDON_FIRST_SIGNED_BAND
                else BEST_ASK_BAND_ABANDONED)

    def observe(self, opportunity, *, minimum, elapsed):
        require(opportunity.price_risk == self.settings.live_taker_price_risk,
                "M4_OPPORTUNITY_PRICE_RISK_CONFLICT")
        require(opportunity.taker_strategy == self.settings.taker_strategy,
                "TAKER_OPPORTUNITY_STRATEGY_CONFLICT")
        if opportunity.taker_strategy:
            self.validate_opportunity_market(opportunity.market_id, opportunity.token_id, opportunity.outcome)
        return super().observe(opportunity, minimum=minimum, elapsed=elapsed)

    def validate_opportunity_market(self, market_id, token, outcome):
        market = self.get("market")
        require(market_id == market["condition_id"] and outcome in {"YES", "NO"}
                and market["tokens"][int(outcome == "NO")]["token_id"] == token,
            "TAKER_OPPORTUNITY_MARKET_CONFLICT")

    def root_policy_evidence(self, row):
        evidence = json.loads(row["evidence"])
        saved = evidence.get("price_risk")
        snapshot = evidence.get("taker_strategy")
        policy = TakerStrategy(**snapshot) if snapshot is not None else None
        require(policy == self.settings.taker_strategy, "TAKER_OPPORTUNITY_STRATEGY_CONFLICT")
        if policy:
            self.validate_opportunity_market(evidence.get("market_id"), row["token"], row["outcome"])
        risk = TakerPriceRisk(**saved) if saved is not None else None
        require(risk == self.settings.live_taker_price_risk, "M4_OPPORTUNITY_PRICE_RISK_CONFLICT")
        if risk is not None:
            return {**risk.plan_fields(), **({"taker_strategy": snapshot, "market_id": evidence["market_id"]}
                                           if policy else {})}

        # Historical tasks and signals in recovered legacy markets have no
        # handoff snapshot. Their original market settings remain authoritative.
        evidence = {"post_price_band": self.post_price_band} if self.post_price_band else {}
        policy = self.settings.post_price_band_retry_policy
        if policy in {ABANDON_BEST_ASK_BAND, ABANDON_FIRST_SIGNED_BAND}:
            evidence["post_price_band_retry_policy"] = policy
        return evidence

    def validate_root_policy(self, row, root):
        expected = self.root_policy_evidence(row)
        require(root.get("post_price_band") == expected.get("post_price_band")
                and root.get("taker_strategy") == expected.get("taker_strategy")
                and root.get("market_id") == expected.get("market_id")
                and root.get("post_price_band_source") == expected.get("post_price_band_source")
                and root.get("post_price_band_retry_policy", WAIT_FOR_PRICE_BAND)
                    == expected.get("post_price_band_retry_policy", WAIT_FOR_PRICE_BAND),
                "M4_TASK_ATTEMPT_PRICING_CONFLICT")
        if self.signal_limited:
            evidence = json.loads(row["evidence"])
            clock = root.get("pricing_origin", {})
            require(clock.get("policy") == BEST_ASK_SIGNAL
                    and clock.get("signal_monotonic") == evidence["signal_start_mono"]
                    and clock.get("lifetime_us") == age_us(evidence["order_deadline"], evidence["signal_start_mono"])
                    and 0 < clock["lifetime_us"] <= signal_lifetime_limit_us(self.ledger_strategy),
                    f"{self.signal_strategy}_SIGNAL_CLOCK_EVIDENCE_CONFLICT")

    def price_band_abandonment(self, key):
        with self.lock:
            row = self.db.execute(
                "SELECT payload FROM facts WHERE kind='taker_price_band_abandonment' AND identity=?",
                (key,),
            ).fetchone()
            return json.loads(row[0]) if row else None

    def abandon_price_band(self, key, quote):
        """Latch entry termination; confirmed/pending/UNKNOWN funds stay intact."""
        with self.lock, self.db:
            root, row = self.root_plan(key), self.row(key)
            self.validate_root_policy(row, root)
            policy = root.get("post_price_band_retry_policy")
            require(policy in {ABANDON_BEST_ASK_BAND, ABANDON_FIRST_SIGNED_BAND}
                    and rejected_price_band_valid(root, quote, row["token"])
                    and (quote["stage"] != "INITIAL" or not self.attempts(key)),
                    "M4_BAND_ABANDONMENT_EVIDENCE_INVALID")
            prior = self.price_band_abandonment(key)
            if prior is not None:
                return prior
            proof = {"policy": policy, "reason": self.price_band_stop_reason,
                     "opportunity_id": key, "post_price_band": root["post_price_band"],
                     "pricing_origin": root["pricing_origin"], "pricing": quote}
            self.db.execute("INSERT INTO facts VALUES ('taker_price_band_abandonment',?,?)",
                            (key, encode(proof)))
            self.refresh_totals(key)
            return proof

    @staticmethod
    def post_price_allowed(price, root):
        band = root.get("post_price_band")
        return price_in_band(price, band) if band else 0 < decimal(price) < 1

    def record_price_band_wait(self, key, price, stage, root):
        name = "post_price_band_wait:" + key
        if root.get("post_price_band") and self.get(name) is None:
            self.set(name, {"price": str(price), "stage": stage, **root["post_price_band"]})

    def order_quantity(self, price, minimum):
        # observe() first sizes the signal's anchor, which is not its POST cap.
        # Keep the reference quantity distinct from the executable next_plan.
        quantity = budget_quantity(price, self.order_budget, self.fees, executable=False)
        require(
            quantity > 0 and (self.signal_limited or minimum_order_ok(decimal(price), quantity)),
            "M4_TAKER_MINIMUM_EXCEEDS_ALL_IN_BUDGET"
        )
        return quantity

    def plan_signal(self, key, tick, minimum, *, pricing_origin=None):
        require((pricing_origin is not None) == self.dynamic_pricing, "M4_PRICING_ORIGIN_MISSING")
        plan = super().plan_signal(key, tick, minimum, pricing_origin=pricing_origin)
        self.set("budget_target:" + key, str(self.order_budget))
        return plan

    @property
    def dynamic_pricing(self):
        return self.settings.execution.taker.pricing_policy in {DYNAMIC, BEST_ASK_SIGNAL}

    def single_signal_gross_limit(self, used):
        return self.order_budget - used if self.signal_limited else self.limit

    def filled_or_matched(self, key):
        return self.signal_limited and (decimal(self.row(key)["filled"]) > 0
                                        or bool(self.pending_matches(key)))

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
            require(not self.price_band_abandonment(key), self.price_band_stop_reason)
            row, root = self.row(key), self.root_plan(key)
            require(self.dynamic_pricing and not row["order_id"] and not self.unresolved(key),
                    "M4_PRICING_ORDER_UNRESOLVED")
            plan = {**root, "pricing": quote, "limit_price": quote["limit_price"]}
            require(quote_valid(root, plan, row["token"], quote), "M4_PRICING_QUOTE_INVALID")
            self.fact("taker_pricing_quote", quote["id"], quote)
            with self.db:
                if root.get("post_price_band"):
                    self.db.execute("INSERT OR REPLACE INTO metadata VALUES (?,?)",
                                    ("post_price_band_eligible:" + key, encode(True)))
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
        elif self.price_band_abandonment(key) and not self.unresolved(key):
            # Stop new buys immediately; independent owned-order reads continue
            # until every pending match is CONFIRMED/FAILED. Never resurrect entry.
            state, reason = ("WAIT_SETTLEMENT" if self.pending_matches(key) else "CUTOFF"), self.price_band_stop_reason
            if (not attempts and not self.get("post_price_band_eligible:" + key)
                    and not self.pending_matches(key)):
                state, reason = "SKIPPED", POST_PRICE_OUTSIDE_BAND
        elif self.signal_limited and (filled > 0 or self.pending_matches(key)):
            state, reason = (("WAIT_SETTLEMENT", f"{self.signal_strategy}_PENDING_MATCHES_NO_TOP_UP")
                             if self.pending_matches(key)
                             else ("CUTOFF", f"{self.signal_strategy}_CONFIRMED_FILL_NO_TOP_UP"))
        elif self.get("buy_closed") or self.get("complete"):
            state, reason = "CUTOFF", self.get("market_halt_reason") or "M4_T240_ENTRY_ENDED"
        elif self.unresolved(key):
            state, reason = "WAIT_SETTLEMENT", "M4_UNKNOWN_PRIOR_ORDER"
        else:
            price = self.selected_price(key)
            qty = budget_quantity(price, self.order_budget - spent - pending, self.fees)
            if minimum_fak_buy_ok(price, qty):
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
            elif self.order_budget - spent < FAK_BUY_MIN_PRINCIPAL:
                # No future price can turn this confirmed cash remainder into
                # a BUY principal >=1. Uncertain/pending matches were handled
                # above: a failed match must still be able to release budget.
                state, reason = "DUST", "M4_REMAINING_CASH_BELOW_FAK_BUY_MINIMUM"
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
            require(not self.price_band_abandonment(key), self.price_band_stop_reason)
            row, root = self.row(key), self.root_plan(key)
            require(row["state"] == "RESERVED" and not row["order_id"], "M4_TASK_NOT_READY")
            if self.unresolved(key):
                return None
            selected = self.get("pricing_selection:" + key) if self.dynamic_pricing else None
            require(not self.dynamic_pricing or selected is not None, "M4_PRICING_NOT_SELECTED")
            price = self.selected_price(key)
            require(self.post_price_allowed(price, root), POST_PRICE_OUTSIDE_BAND)
            used = self.budget_used(key)
            pending = self.pending_cost(key)
            qty = budget_quantity(price, self.order_budget - used, self.fees)
            if not minimum_fak_buy_ok(price, qty):
                self.refresh_totals(key)
                return None
            # pending matches stay reserved; only an unsent preparation may be replaced.
            own = decimal(row["reserved"]) - pending
            available = min(self.limit - self.committed() + own, decimal(spendable) + own)
            available = min(available, self.single_signal_gross_limit(used))
            while qty > 0 and (gross_risk_cost(price, qty, self.fees) > available
                               or not fak_amounts_compatible(price, qty)):
                qty -= STEP
            if not minimum_fak_buy_ok(price, qty):
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
                "fak_buy_minimum_policy": FAK_BUY_MINIMUM_POLICY,
                "fak_buy_min_principal_usdc": str(FAK_BUY_MIN_PRINCIPAL),
                "amount_precision": "FAK_BUY_CENTS_SHARES_0_1_V1",
                "task_policy": self.task_policy,
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
        self.validate_root_policy(row, root)
        require(
            ("pricing_origin" in root) == self.dynamic_pricing
            and quote_valid(root, plan, row["token"], self.pricing_evidence(pricing.get("id")))
            and price == decimal(plan["limit_price"])
            and plan.get("taker_strategy") == root.get("taker_strategy")
            and plan.get("market_id") == root.get("market_id")
            and decimal(root["limit_price"]) == self.initial_plan_price(
                decimal(row["price"]), decimal(root["tick_at_plan"])),
            "M4_TASK_ATTEMPT_PRICING_CONFLICT",
        )
        require(self.post_price_allowed(price, root), POST_PRICE_OUTSIDE_BAND)
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
            and prior + all_in_cost(price, qty, self.fees) <= self.order_budget
            and decimal(plan["commitment_usdc"]) == gross_risk_cost(price, qty, self.fees),
            "M4_TASK_ATTEMPT_ECONOMICS_CONFLICT",
        )
        if self.signal_limited:
            require(prior == 0 and decimal(plan["commitment_usdc"]) <= self.order_budget
                    and minimum_fak_buy_ok(price, qty) and fak_amounts_compatible(price, qty),
                    f"{self.signal_strategy}_ATTEMPT_BUDGET_CONFLICT")
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
            require(not self.price_band_abandonment(key), self.price_band_stop_reason)
            row, plan = self.row(key), self.plan(key)
            require(
                row["state"] == "RESERVED"
                and not row["order_id"]
                and not row["started"]
                and not self.unresolved(key),
                "M4_TASK_PREPARE_STATE",
            )
            a = {"task_id": key, "order_id": oid, "plan": encode(plan), "quote": encode(quote)}
            require(minimum_fak_buy_ok(decimal(quote["price"]), decimal(quote["size"])),
                    "M4_FAK_BUY_MINIMUM")
            # validate_attempt also restores immutable historical orders. Keep
            # its legacy evidence rules so old rejected receipts remain readable;
            # enforce the new exchange floor only on new preparations/POSTs.
            self.validate_attempt(a)
            # Signing yields to old-order reconciliation, which may refresh
            # totals and release this not-yet-prepared plan's reservation.
            # Reacquire it atomically with PREPARED, or leave the task unsent.
            reserved = self.pending_cost(key) + decimal(plan["commitment_usdc"])
            committed = self.committed() - decimal(row["reserved"]) + reserved
            require(
                self.budget_used(key) + decimal(plan["planned_cost_usdc"]) <= self.order_budget
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
            require(not self.price_band_abandonment(key), self.price_band_stop_reason)
            plan = self.plan(key)
            require(self.post_price_allowed(decimal(plan["limit_price"]), self.root_plan(key)),
                    POST_PRICE_OUTSIDE_BAND)
            require(not self.unresolved(key), "M4_UNRESOLVED_PRIOR_ATTEMPT")
            require(
                self.budget_used(key) + decimal(plan["planned_cost_usdc"]) <= self.order_budget
                and self.committed() <= self.limit and not self.filled_or_matched(key),
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
            if self.price_band_abandonment(key):
                self.refresh_totals(key)
                return
            if (self.post_price_band and state == "CUTOFF" and reason == "M4_T240_RESIDUAL"
                    and self.get("post_price_band_wait:" + key)
                    and not self.get("post_price_band_eligible:" + key)
                    and not self.attempts(key)):
                # A durable observation never reached executable price admission.
                # Preserve it as a normal price filter, not a lost/failed POST.
                state, reason = "SKIPPED", POST_PRICE_OUTSIDE_BAND
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
        roots = {root["opportunity_id"]: root for root in self.facts("taker_plan")}
        for row in self.rows():
            root = roots.get(row["id"])
            if root is not None:
                self.validate_root_policy(row, root)
            abandoned = self.price_band_abandonment(row["id"])
            if abandoned:
                root = self.root_plan(row["id"])
                require(
                    abandoned["policy"] in {ABANDON_BEST_ASK_BAND, ABANDON_FIRST_SIGNED_BAND}
                    and root.get("post_price_band_retry_policy") == abandoned["policy"]
                    and abandoned["reason"] == self.price_band_stop_reason
                    and abandoned["opportunity_id"] == row["id"]
                    and abandoned["post_price_band"] == root.get("post_price_band")
                    and abandoned["pricing_origin"] == root["pricing_origin"]
                    and rejected_price_band_valid(root, abandoned["pricing"], row["token"])
                    and (abandoned["pricing"]["stage"] != "INITIAL" or not self.attempts(row["id"])),
                    "M4_BAND_ABANDONMENT_EVIDENCE_INVALID",
                )
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
            item["target_budget_usdc"] = str(self.order_budget)
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
            item["remaining_planned_budget_usdc"] = str(max(ZERO, self.order_budget - self.budget_used(key)))
        return value

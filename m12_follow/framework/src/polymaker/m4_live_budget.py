"""Live-only cash-target tasks with separately reserved unsettled matches."""

import json
import re
from dataclasses import asdict
from decimal import ROUND_CEILING, ROUND_FLOOR
from math import gcd

from polymaker.domain import Side, TradeState
from polymaker.execution.amounts import market_buy_quantity
from polymaker.execution.taker_fees import buy_amount_fee_reserve
from polymaker.m4_live_config import M42112_PRICE_BAND_SOURCE, gross_risk_cost, planned_fee
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
    WAIT_FOR_PRICE_BAND,
)
from polymaker.m4_live_pricing import DYNAMIC, order_price_allowed, quote_valid, rejected_price_band_valid
from polymaker.m4_live_task import TaskLedger, encode, minimum_order_ok, require
from polymaker.m4_state import decimal
from polymaker.m4_types import BASELINE_FOLLOW_STRATEGIES, BEST_ASK_SIGNAL, M21_LEDGER, M421_LEDGER, TakerPriceRisk, TakerStrategy, signal_lifetime_limit_us
from polymaker.market_protocol import market_protocol
from polymaker.state.tracker import TradeEvent

FAK_BUY_MIN_PRINCIPAL = decimal("1")
FAK_BUY_MINIMUM_POLICY = "EXCHANGE_PRINCIPAL_GE_1_USDC_V1"
AMOUNT_PRECISION = "FAK_BUY_CENTS_SHARES_4DP_V1"
GTD_PRECISION = "LIMIT_BUY_SHARES_2DP_V1"
CENT = decimal(".01")
M12_ANCHOR_INVALIDATED = "M12_BEST_ASK_BELOW_SIGNAL_ANCHOR"
M12_FROZEN_CAP_INVALIDATED = "M12_BEST_ASK_BELOW_FROZEN_CAP"


def follow_fak_amount(principal, price, *, ask_at_cap=False):
    # Preserve cash cents. Ceiling the shares keeps the signed ratio at or
    # below the frozen cap without discarding spendable principal.
    amount = principal.quantize(CENT, rounding=ROUND_FLOOR)
    step = decimal(".0001")
    shares = (amount / price).quantize(step, rounding=ROUND_CEILING)
    if not ask_at_cap or shares * price == amount:
        return amount, shares
    # A ratio just below a *visible ask at the cap* cannot take that tick.
    # Only here prefer the largest exact executable lot to a known no-match.
    numerator, denominator = price.as_integer_ratio()
    cents_per_lot = numerator // gcd(numerator, 100 * denominator)
    exact = (amount // (CENT * cents_per_lot)) * (CENT * cents_per_lot)
    if exact >= FAK_BUY_MIN_PRINCIPAL and (exact > 1 or exact / price >= 5):
        return exact, (exact / price).quantize(step)
    return amount, shares


def minimum_fak_buy_ok(price, quantity):
    # Keep the strategy's OR rule, and also satisfy the exchange's independent
    # marketable BUY principal floor. Shares >=5 cannot waive that cash floor.
    return minimum_order_ok(price, quantity) and price * quantity >= FAK_BUY_MIN_PRINCIPAL


class BudgetLedger(TaskLedger):
    revision = "m4-taker-budget-v1"
    cash_target = True
    @property
    def task_policy(self):
        if self.amount_buy:
            return "M421_REMAINING_PRINCIPAL_UNTIL_SIGNAL_DEADLINE_V1"
        return (f"{self.signal_strategy}_ZERO_FILL_ONLY_UNTIL_SIGNAL_DEADLINE_V1" if self.signal_limited
                else "REMAINING_ALL_IN_CASH_UNTIL_T240")

    @property
    def amount_buy(self):
        return self.settings.taker_buy_amount_policy is not None

    @property
    def signal_strategy(self):
        if self.ledger_strategy == M421_LEDGER:
            return "M421"
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

    def observe(self, opportunity, *, minimum, elapsed, detector_source=None):
        require(opportunity.price_risk == self.settings.signal_price_risk(detector_source),
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
        return market_protocol(market)

    def root_policy_evidence(self, row):
        evidence = json.loads(row["evidence"])
        saved = evidence.get("price_risk")
        snapshot = evidence.get("taker_strategy")
        policy = TakerStrategy(**snapshot) if snapshot is not None else None
        require(policy == self.settings.taker_strategy, "TAKER_OPPORTUNITY_STRATEGY_CONFLICT")
        protocol = (self.validate_opportunity_market(evidence.get("market_id"), row["token"], row["outcome"])
                    if policy else "v1")
        risk = TakerPriceRisk(**saved) if saved is not None else None
        source = None
        if self.settings.source_price_bands is not None:
            source = self.m421_signal_source(row["id"])
        require(risk == self.settings.signal_price_risk(source), "M4_OPPORTUNITY_PRICE_RISK_CONFLICT")
        if risk is not None:
            return {**risk.plan_fields(),
                    **({"protocol_version": "v2"} if protocol == "v2" else {}),
                    **({"detector_source": source, "post_price_band_source": M42112_PRICE_BAND_SOURCE}
                       if source is not None else {}),
                    **({"taker_strategy": snapshot, "market_id": evidence["market_id"]}
                                           if policy else {}),
                    **({"taker_buy_amount_policy": self.settings.taker_buy_amount_policy}
                       if self.amount_buy else {})}

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
                and root.get("protocol_version") == expected.get("protocol_version")
                and root.get("detector_source") == expected.get("detector_source")
                and root.get("post_price_band_source") == expected.get("post_price_band_source")
                and root.get("taker_buy_amount_policy") == expected.get("taker_buy_amount_policy")
                and root.get("post_price_band_retry_policy", WAIT_FOR_PRICE_BAND)
                    == expected.get("post_price_band_retry_policy", WAIT_FOR_PRICE_BAND),
                "M4_TASK_ATTEMPT_PRICING_CONFLICT")
        if self.signal_limited:
            evidence = json.loads(row["evidence"])
            clock = root.get("pricing_origin", {})
            scoped = evidence.get("support_id", "").startswith("sqlite:")
            if scoped:
                require(root.get("detector_source") in BASELINE_FOLLOW_STRATEGIES
                        and clock.get("market_cutoff") == self.settings.cutoff_sec
                        and type(clock.get("overpay_ticks")) is int and clock["overpay_ticks"] >= 0,
                        "M12_INTENT_MARKET_POLICY_INVALID")
            else:
                require("market_cutoff" not in clock, "M12_INTENT_MARKET_POLICY_UNEXPECTED")
            require(clock.get("policy") == BEST_ASK_SIGNAL
                    and clock.get("signal_monotonic") == evidence["signal_start_mono"]
                    and clock.get("lifetime_us") == age_us(evidence["order_deadline"], evidence["signal_start_mono"])
                    and 0 < clock["lifetime_us"] <= (300_000_000 if scoped else signal_lifetime_limit_us(self.ledger_strategy)),
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
        return order_price_allowed(price, root)

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
        return self.signal_limited and (bool(self.pending_matches(key))
                                        or (not self.amount_buy and decimal(self.row(key)["filled"]) > 0))

    @staticmethod
    def confirmed_principal(attempt):
        if not decimal(attempt["filled"]):
            return ZERO
        require(attempt["state"] == "TERMINAL", "M421_CONFIRMED_PRINCIPAL_PROOF_MISSING")
        return BudgetLedger.principal_from_proof(json.loads(attempt["proof"]))

    @staticmethod
    def principal_from_proof(proof):
        require(proof["kind"] == "OWNED_TERMINAL_ORDER_AND_TRADES",
                "M421_CONFIRMED_PRINCIPAL_PROOF_MISSING")
        return units(sum((decimal(fill["principal_usdc"]) for fill in proof["trades"]
                          if fill["status"] == "CONFIRMED"), ZERO))

    def principal_used(self, key):
        return sum((self.confirmed_principal(a) for a in self.attempts(key)), ZERO)

    @staticmethod
    def amount_minimum_ok(amount, quantity):
        return amount >= FAK_BUY_MIN_PRINCIPAL and (quantity >= 5 or amount > 1)

    def amount_price_allowed(self, price, amount, quantity, root):
        if quantity <= 0:
            return False
        signed = amount / quantity
        # Shares are rounded down. Preserve the intended executable ask and
        # exclude any higher tick, including after the market's tick tightens.
        if root.get("pricing_origin", {}).get("follow_limit"):
            return price - decimal(".0001") < signed <= price and self.post_price_allowed(signed, root)
        return price <= signed < price + decimal(".0001") and self.post_price_allowed(signed, root)

    def amount_order(self, key, price, available=None, *, order_type="FAK", ask_at_cap=False):
        principal = self.order_budget - self.principal_used(key)
        if available is not None:
            # Invert the same gross commitment used to reserve actual cash.
            reserve_per_dollar = buy_amount_fee_reserve(decimal(1), self.fees) - MICRO
            principal = min(principal, (available - MICRO) / (1 + reserve_per_dollar))
        amount = principal.quantize(CENT, rounding=ROUND_FLOOR)
        if amount < FAK_BUY_MIN_PRINCIPAL:
            return None
        if self.root_plan(key).get("pricing_origin", {}).get("follow_limit"):
            if order_type == "GTD":
                quantity = (principal / price).quantize(CENT, rounding=ROUND_FLOOR)
                amount = price * quantity
            else:
                amount, quantity = follow_fak_amount(amount, price, ask_at_cap=ask_at_cap)
            if not self.amount_minimum_ok(amount, quantity) or not self.post_price_allowed(amount / quantity, self.root_plan(key)):
                return None
            return amount, quantity
        quantity = market_buy_quantity(amount, price)
        if (not self.amount_minimum_ok(amount, quantity)
                or not self.amount_price_allowed(price, amount, quantity, self.root_plan(key))):
            return None
        return amount, quantity

    def valid_follow_terms(self, plan, principal, price, qty):
        if plan.get("order_type") == "GTD":
            cutoff = int(self.get("market")["slug"].rsplit("-", 1)[1]) + self.settings.cutoff_sec
            return (qty >= decimal(plan["minimum"]) and qty % CENT == 0 and principal == price * qty
                    and plan.get("expiration") == cutoff + 60)
        return (plan.get("order_type") == "FAK" and plan.get("expiration") == 0 and principal % CENT == 0
                and qty == (principal / price).quantize(decimal(".0001"), rounding=ROUND_CEILING))

    def plan_minimum_ok(self, plan, price, quantity):
        return (self.amount_minimum_ok(decimal(plan["principal_usdc"]), quantity)
                if self.amount_buy else minimum_fak_buy_ok(price, quantity))

    def signal_budget_allows(self, key, plan):
        if self.amount_buy:
            return (not self.pending_matches(key)
                    and self.principal_used(key) + decimal(plan["principal_usdc"]) <= self.order_budget)
        return self.budget_used(key) + decimal(plan["planned_cost_usdc"]) <= self.order_budget

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
                **({"principal": str(self.confirmed_principal(a))} if self.amount_buy else {}),
                "pending": str(
                    decimal(self.get("settling:" + a["order_id"])["cost_bound"])
                    if a["state"] == "SETTLING"
                    else ZERO
                ),
            }
            for a in self.attempts(key)
            if a["started"]
        ]

    def invalidate_follow_price(self, key, evidence):
        marker = "m12_anchor_invalidation:" + key
        with self.lock, self.db:
            if self.get(marker):
                return False
            self.db.execute("INSERT INTO metadata VALUES (?,?)", (marker, encode(evidence)))
            if not self.row(key)["order_id"]:
                self.refresh_totals(key)
        return True

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
        elif (self.ledger_strategy == M421_LEDGER and (invalidation := self.get("m12_anchor_invalidation:" + key))
              and not self.unresolved(key)):
            reason = invalidation.get("reason", M12_ANCHOR_INVALIDATED)
            if self.pending_matches(key):
                state = "WAIT_SETTLEMENT"
            else:
                state = "CUTOFF" if any(a["started"] and a["state"] != "UNSENT" for a in attempts) else "SKIPPED"
        elif self.signal_limited and (self.pending_matches(key) or (not self.amount_buy and filled > 0)):
            state, reason = (("WAIT_SETTLEMENT", f"{self.signal_strategy}_PENDING_MATCHES_NO_TOP_UP")
                             if self.pending_matches(key)
                             else ("CUTOFF", f"{self.signal_strategy}_CONFIRMED_FILL_NO_TOP_UP"))
        elif self.get("buy_closed") or self.get("complete"):
            state, reason = "CUTOFF", self.get("market_halt_reason") or "M4_T240_ENTRY_ENDED"
        elif self.unresolved(key):
            state, reason = "WAIT_SETTLEMENT", "M4_UNKNOWN_PRIOR_ORDER"
        elif self.amount_buy:
            remaining = self.order_budget - sum((self.confirmed_principal(a) for a in attempts), ZERO)
            if remaining == 0:
                state, reason = "BUDGET_EXHAUSTED", "M421_PRINCIPAL_TARGET_REACHED"
            elif remaining.quantize(CENT, rounding=ROUND_FLOOR) <= FAK_BUY_MIN_PRINCIPAL:
                # At exactly $1, the band cannot supply the five shares needed
                # by the strategy's strictly-greater-than-$1 alternative.
                state, reason = "DUST", "M421_REMAINING_PRINCIPAL_BELOW_FAK_MINIMUM"
            else:
                state, reason = "RESERVED", "M421_REMAINING_PRINCIPAL"
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

    def next_plan(self, key, *, minimum, spendable, remaining_sec=None):
        with self.lock, self.db:
            require(not self.price_band_abandonment(key), self.price_band_stop_reason)
            row, root = self.row(key), self.root_plan(key)
            require(row["state"] == "RESERVED" and not row["order_id"], "M4_TASK_NOT_READY")
            if self.unresolved(key) or (self.amount_buy and self.pending_matches(key)):
                return None
            selected = self.get("pricing_selection:" + key) if self.dynamic_pricing else None
            require(not self.dynamic_pricing or selected is not None, "M4_PRICING_NOT_SELECTED")
            price = self.selected_price(key)
            require(self.post_price_allowed(price, root), POST_PRICE_OUTSIDE_BAND)
            used = self.budget_used(key)
            pending = self.pending_cost(key)
            # pending matches stay reserved; only an unsent preparation may be replaced.
            own = decimal(row["reserved"]) - pending
            available = min(self.limit - self.committed() + own, decimal(spendable) + own)
            follow = root.get("pricing_origin", {}).get("follow_limit")
            order_type = "FAK"
            expiration = 0
            if follow and remaining_sec is not None and remaining_sec > 120:
                rest = self.amount_order(key, price, available, order_type="GTD")
                if rest and rest[1] >= decimal(minimum):
                    order_type = "GTD"
                    expiration = int(self.get("market")["slug"].rsplit("-", 1)[1]) + self.settings.cutoff_sec + 60
            if self.amount_buy:
                amount_order = self.amount_order(key, price, available, order_type=order_type,
                                                 ask_at_cap=bool(follow and selected.get("ask_at_cap")))
            else:
                qty = budget_quantity(price, self.order_budget - used, self.fees)
                if not minimum_fak_buy_ok(price, qty):
                    self.refresh_totals(key)
                    return None
                available = min(available, self.single_signal_gross_limit(used))
                while qty > 0 and (gross_risk_cost(price, qty, self.fees) > available
                                   or not fak_amounts_compatible(price, qty)):
                    qty -= STEP
                amount_order = (price * qty, qty) if minimum_fak_buy_ok(price, qty) else None
            if amount_order is None:
                self.db.execute(
                    "UPDATE opportunities SET reserved=?,reason='M4_WAIT_REMAINDER_FUNDS' WHERE id=?",
                    (str(pending), key),
                )
                self.db.execute("DELETE FROM metadata WHERE key=?", ("next_plan:" + key,))
                return None
            amount, qty = amount_order
            fee = planned_fee(price, qty, self.fees)
            plan = {
                **root,
                **({"order_type": order_type, "expiration": expiration} if follow else {}),
                "limit_price": str(price),
                **({"pricing": selected} if selected else {}),
                "quantity": str(qty),
                "principal_usdc": str(amount),
                "planned_fee_usdc": str(fee),
                "planned_cost_usdc": str(amount + fee),
                "commitment_usdc": str(amount + buy_amount_fee_reserve(amount, self.fees)),
                "prior_spent_usdc": str(used),
                "prior_filled": row["filled"],
                "budget_basis": self.budget_basis(key),
                "minimum": str(minimum),
                "minimum_policy": "SHARES_GE_5_OR_NOTIONAL_GT_1",
                "fak_buy_minimum_policy": FAK_BUY_MINIMUM_POLICY,
                "fak_buy_min_principal_usdc": str(FAK_BUY_MIN_PRINCIPAL),
                "amount_precision": (GTD_PRECISION if order_type == "GTD" else
                                     AMOUNT_PRECISION if self.amount_buy else "FAK_BUY_CENTS_SHARES_0_1_V1"),
                "task_policy": self.task_policy,
                "max_native_posts": 1,
            }
            if self.amount_buy:
                plan["signed_limit_price"] = str(amount / qty)
                plan["prior_principal_usdc"] = str(sum((decimal(p["principal"])
                                                       for p in plan["budget_basis"]), ZERO))
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
        principal = decimal(plan["principal_usdc"])
        pricing = plan.get("pricing", {})
        self.validate_root_policy(row, root)
        if self.settings.source_price_bands is not None:
            require(all(plan.get(field) == root[field] for field in (
                "detector_source", "post_price_band_source", "post_price_band", "post_price_band_retry_policy",
            )), "M42112_ATTEMPT_PRICE_BAND_CONFLICT")
        require(
            ("pricing_origin" in root) == self.dynamic_pricing
            and quote_valid(root, plan, row["token"], self.pricing_evidence(pricing.get("id")))
            and price == decimal(plan["limit_price"])
            and plan.get("taker_strategy") == root.get("taker_strategy")
            and plan.get("taker_buy_amount_policy") == root.get("taker_buy_amount_policy")
            and plan.get("market_id") == root.get("market_id")
            and plan.get("protocol_version") == root.get("protocol_version")
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
            and plan["fees"] == self.fees
            and decimal(plan["quantity"]) == qty
            and decimal(plan["planned_cost_usdc"]) == principal + planned_fee(price, qty, self.fees)
            and prior == decimal(plan["prior_spent_usdc"])
            and prior >= 0
            and decimal(plan["commitment_usdc"]) == principal + buy_amount_fee_reserve(principal, self.fees),
            "M4_TASK_ATTEMPT_ECONOMICS_CONFLICT",
        )
        if self.amount_buy:
            prior_principal = sum((decimal(p["principal"]) for p in basis), ZERO)
            require(plan.get("amount_precision") == (GTD_PRECISION if plan.get("order_type") == "GTD" else AMOUNT_PRECISION)
                    and plan["task_policy"] == self.task_policy
                    and self.amount_minimum_ok(principal, qty)
                    and (self.valid_follow_terms(plan, principal, price, qty) if root.get("pricing_origin", {}).get("follow_limit")
                         else qty == market_buy_quantity(principal, price))
                    and decimal(plan["signed_limit_price"]) == principal / qty
                    and self.amount_price_allowed(price, principal, qty, root)
                    and prior_principal == decimal(plan["prior_principal_usdc"])
                    and 0 <= prior_principal <= self.order_budget - principal,
                    "M421_ATTEMPT_PRINCIPAL_OR_PRICE_CONFLICT")
        else:
            require(qty % STEP == 0 and minimum_order_ok(price, qty) and principal == price * qty
                    and prior + decimal(plan["planned_cost_usdc"]) <= self.order_budget,
                    "M4_TASK_ATTEMPT_ECONOMICS_CONFLICT")
        if self.signal_limited and not self.amount_buy:
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
            if self.amount_buy:
                require(earlier["state"] in {"TERMINAL", "UNSENT"}
                        and decimal(p["spent"]) == decimal(earlier["spent"])
                        and decimal(p["pending"]) == 0
                        and decimal(p["principal"]) == self.confirmed_principal(earlier),
                        "M421_PRINCIPAL_BASIS_CONFLICT")
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
            require(self.plan_minimum_ok(plan, decimal(quote["price"]), decimal(quote["size"])),
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
                self.signal_budget_allows(key, plan)
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
            if self.amount_buy:
                principal = self.principal_from_proof(proof) if filled else ZERO
                require(0 <= principal <= min(spent, decimal(json.loads(a["plan"])["principal_usdc"]))
                        and self.principal_used(a["task_id"]) + principal <= self.order_budget,
                        "M421_CONFIRMED_PRINCIPAL_EXCEEDS_BUDGET")
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
                self.signal_budget_allows(key, plan)
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

    def _m421_claims(self):
        # Durable start markers include intents the native gate later proved
        # unsent. Only possibly sent attempts lock the slot after expiry.
        return self.db.execute(
            """SELECT id,outcome FROM opportunities
               WHERE state IN ('RESERVED','PREPARED','UNKNOWN','ACK')
                  OR EXISTS (SELECT 1 FROM taker_attempts
                             WHERE task_id=opportunities.id AND started=1 AND state!='UNSENT')"""
        ).fetchall()

    def m421_signal_source(self, key):
        with self.lock:
            fact = self.db.execute("SELECT payload FROM facts WHERE kind='m421_signal_order' AND identity=?",
                                   (key,)).fetchone()
        require(fact is not None, "M421_SIGNAL_SOURCE_MISSING")
        source = json.loads(fact[0])["source"]
        if self.settings.signal_slot_policy:
            require(source in self.settings.signal_output_sources, "M42112_SIGNAL_SOURCE_NOT_ENABLED")
        return source

    def m421_slot_group(self, row):
        source = self.m421_signal_source(row["id"]) if self.settings.signal_slot_policy else None
        return source, row["outcome"]

    @property
    def instruction_slot_limit(self):
        base = super().instruction_slot_limit
        return base * len(self.settings.signal_output_sources) if self.settings.signal_slot_policy else base

    def instruction_slots(self):
        if self.ledger_strategy != M421_LEDGER:
            return super().instruction_slots()
        with self.lock:
            return len(self._m421_claims())

    def direction_slots(self, key):
        if self.ledger_strategy != M421_LEDGER:
            return super().direction_slots(key)
        with self.lock:
            source, outcome = self.m421_slot_group(self.row(key))
            return sum(task != key and side == outcome and (source is None or self.m421_signal_source(task) == source)
                       for task, side in self._m421_claims())

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
            if (self.price_band_abandonment(key)
                    or (self.ledger_strategy == M421_LEDGER and self.get("m12_anchor_invalidation:" + key))):
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
        if self.settings.signal_slot_policy:
            with self.lock:
                groups = [(self.m421_signal_source(key), outcome) for key, outcome in self._m421_claims()]
            require(len(groups) == len(set(groups)), "M42112_RECOVERY_DUPLICATE_SIGNAL_SLOT")
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
                if self.amount_buy and a["state"] == "TERMINAL":
                    principal = self.confirmed_principal(a)
                    require(0 <= principal <= min(decimal(a["spent"]),
                                                  decimal(json.loads(a["plan"])["principal_usdc"])),
                            "M421_CONFIRMED_PRINCIPAL_EXCEEDS_BUDGET")
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
            if self.amount_buy:
                require(self.principal_used(row["id"]) <= self.order_budget,
                        "M421_CONFIRMED_PRINCIPAL_EXCEEDS_BUDGET")
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

    def follow_metrics(self, admissions):
        # An open GTD can already contain CONFIRMED fills. Include those exact
        # trade IDs once without releasing its remaining order reservation.
        with self.lock:
            fills = self.db.execute("""
                SELECT a.task_id, t.economics FROM taker_trade_evidence t
                JOIN taker_attempts a ON a.order_id=t.order_id WHERE t.status='CONFIRMED'
            """).fetchall()
            attempts = self.attempts()
            rows = {r["id"]: r for r in self.rows()}
        economics = {}
        for key, encoded in fills:
            fill = json.loads(encoded)
            principal, fee, qty = economics.get(key, (ZERO, ZERO, ZERO))
            economics[key] = (principal + decimal(fill["principal_usdc"]), fee + decimal(fill["fee_usdc"]),
                              qty + units(fill["size"]))
        result = []
        newest = max((i["audit_seq"] for i in admissions), default=None)
        for admission in sorted(admissions, key=lambda i: i["audit_seq"]):
            key = admission["id"]
            principal, fee, qty = economics.get(key, (ZERO, ZERO, ZERO))
            row = rows.get(key)
            posts = sum(a["task_id"] == key and a["started"] and a["state"] != "UNSENT" for a in attempts)
            state = row["state"] if row else ("SUPERSEDED_BEFORE_SUBMISSION"
                    if admission["audit_seq"] != newest else "NOT_SUBMITTED")
            cost = principal + fee
            target_qty = decimal(admission["baseline_quantity"])
            result.append({**admission, "state": state, "reason": row["reason"] if row else state,
                           "posts_started": posts, "target_principal_usdc": str(self.order_budget),
                           "confirmed_shares": str(qty), "confirmed_principal_usdc": str(principal),
                           "confirmed_fees_usdc": str(fee), "confirmed_all_in_usdc": str(cost),
                           "principal_completion_ratio": str(principal / self.order_budget),
                           "baseline_share_completion_ratio": str(qty / target_qty) if target_qty > 0 else None,
                           "all_in_usdc_per_share": str(cost / qty) if qty else None,
                           "cost_vs_anchor_usdc": str(cost - decimal(admission["anchor"]) * qty)})
        return result

    def metric(self, *, winner=None):
        value = super().metric(winner=winner)
        value["submitted_unverified"] = sum(
            a["state"] in {"ACK", "UNKNOWN", "SETTLING"} for a in self.attempts()
        )
        value["target_kind"] = "PRINCIPAL" if self.amount_buy else "ALL_IN_CASH"
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
            used = self.principal_used(key) if self.amount_buy else self.budget_used(key)
            item["remaining_planned_budget_usdc"] = str(max(ZERO, self.order_budget - used))
            if self.amount_buy:
                item["confirmed_principal_usdc"] = str(used)
        admissions = self.facts("m12_follow_admission")
        if admissions:
            intents = [i for i in self.follow_metrics(admissions) if winner is None or i["outcome"] == winner]
            value["by_follow_intent"] = intents
            value["baseline_intents"] = len(intents)
            value["intents_with_confirmed_fills"] = sum(decimal(i["confirmed_shares"]) > 0 for i in intents)
        return value

"""Independent read-only reconciliation; does not import the matching engine."""

from collections import defaultdict
from decimal import Decimal

from polymaker.m1234_sim.store import read_audit, read_transaction
from polymaker.m1234_sim.validity import database_validity


def reconcile(path):
    with read_transaction(path) as db:
        return _reconcile(path, db)


def _reconcile(path, db):
    orders, accounts, errors = {}, {}, []
    failures = []

    def account(scenario, profile):
        return accounts.setdefault(
            (scenario, profile),
            {
                "initial": Decimal(20),
                "cash": Decimal(20),
                "reserved": Decimal(0),
                "spent": Decimal(0),
                "payout": Decimal(0),
                "inventory": defaultdict(Decimal),
            },
        )

    for event in read_audit(path, connection=db):
        kind, data = event["kind"], event["payload"]
        scenario = data.get("scenario")
        if kind == "SIMULATION_INVALID":
            failures.append(data["conflict"])
        if kind == "ACCOUNT_OPEN":
            key = (scenario, data["profile"])
            if key in accounts:
                errors.append("duplicate account opening")
            acc = account(scenario, data["profile"])
            acc["initial"] = Decimal(data["initial_cash"])
            acc["cash"] = acc["initial"]
            if acc["initial"] < 0:
                errors.append("negative initial balance")
        elif kind == "SUBMIT":
            key = (scenario, data["order"])
            if key in orders:
                errors.append("duplicate physical submission")
            intent = data["intent"]
            cost = Decimal(intent["price"]) * Decimal(intent["quantity"])
            if cost != Decimal(data["reserve"]):
                errors.append("submission reserve mismatch")
            orders[key] = {"intent": intent, "filled": Decimal(0), "reserve": cost}
            account(scenario, intent["profile"])["reserved"] += cost
        elif kind in {"MODELLED_FILL", "RELEASE"}:
            order = orders[(scenario, data["order"])]
            acc = account(scenario, order["intent"]["profile"])
            if kind == "MODELLED_FILL":
                quantity = Decimal(data["quantity"])
                amount = quantity * Decimal(order["intent"]["price"])
                if amount != Decimal(data["cost"]) or data["price"] != order["intent"]["price"]:
                    errors.append("fill cost mismatch")
                order["filled"] += quantity
                if order["filled"] > Decimal(order["intent"]["quantity"]) or quantity <= 0:
                    errors.append("overfill or nonpositive fill")
                acc["cash"] -= amount
                acc["spent"] += amount
                acc["inventory"][order["intent"]["outcome"]] += quantity
            else:
                amount = Decimal(data["amount"])
            order["reserve"] -= amount
            acc["reserved"] -= amount
            if min(order["reserve"], acc["reserved"], acc["cash"] - acc["reserved"]) < 0:
                errors.append("over-release or insufficient cash")
        elif kind == "RISK_RERESERVE":
            order = orders[(scenario, data["order"])]
            amount = Decimal(data["amount"])
            acc = account(scenario, order["intent"]["profile"])
            order["reserve"] += amount
            acc["reserved"] += amount
            if amount <= 0 or acc["reserved"] > acc["cash"]:
                errors.append("invalid uncertainty re-reservation")
        elif kind == "SETTLE":
            acc = account(scenario, data["profile"])
            payout = acc["inventory"].get(data["winner"], Decimal(0))
            if payout != Decimal(data["payout"]) or acc["payout"]:
                errors.append("settlement mismatch or duplicate")
            acc["cash"] += payout
            acc["payout"] += payout
        elif kind == "TRADE_ALLOCATION":
            if not 0 <= Decimal(data["consumed"]) <= Decimal(data["available"]):
                errors.append("public volume over-allocation")
    # Compare against the independently persisted final state, including accounts
    # with no orders. This check catches snapshot/report divergence.
    from polymaker.m1234_sim.report import read_state

    state = read_state(path, connection=db)
    for scenario, broker in state["brokers"].items():
        for profile, actual in broker["accounts"].items():
            expected = account(scenario, profile)
            for field in ("initial", "cash", "reserved", "spent", "payout"):
                if Decimal(actual[field]) != expected[field]:
                    errors.append(f"{scenario}/{profile}/{field} differs")
            for outcome in ("YES", "NO"):
                if Decimal(actual["inventory"].get(outcome, 0)) != expected["inventory"].get(
                    outcome, 0
                ):
                    errors.append(f"{scenario}/{profile}/{outcome} differs")
    validity = database_validity(path, state, failures, connection=db)
    arithmetic = "PASS" if not errors else "FAIL"
    return {
        "status": arithmetic if validity["status"] in {"VALID", "UNVERIFIED_LEGACY"} else "FAIL",
        "arithmetic_status": arithmetic,
        "acceptance_status": ("PASS" if arithmetic == "PASS" and validity["status"] == "VALID"
                              else "FAIL" if validity["status"] != "UNVERIFIED_LEGACY" or errors
                              else "UNVERIFIED_LEGACY"),
        "validity": validity,
        "scope": "ARITHMETIC_RECONCILIATION_WITH_SEPARATE_SIMULATION_VALIDITY",
        "errors": errors,
        "physical_orders": len(orders),
        "accounts": {"/".join(k): v for k, v in accounts.items()},
    }

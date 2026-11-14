"""Read-only economic and legacy-candidate review of a completed shadow replay."""

import argparse
import json
import sqlite3
from decimal import Decimal
from pathlib import Path


def rows(path, query):
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        return db.execute(query).fetchall()


def validate(run):
    plan = json.loads((run / "plan.json").read_text())
    source = Path(plan["source_run"])
    checks = []
    candidate_query = """SELECT input_seq,payload FROM audit WHERE kind='SIGNAL'
        AND json_extract(payload,'$.strategy') IN ('M1','M2','M3','M4')
        AND json_extract(payload,'$.event') IN ('OPPORTUNITY_IDENTIFIED','M2_CANDIDATE','CANDIDATE') ORDER BY seq"""
    for proof in sorted(run.glob("btc-*/replay-validation.json")):
        name = proof.parent.name
        db = proof.parent / "simulation.sqlite3"
        old = source / name / "simulation.sqlite3"
        a = [(seq, json.loads(payload)) for seq, payload in rows(old, candidate_query)]
        b = [(seq, json.loads(payload)) for seq, payload in rows(db, candidate_query)]
        if a != b:
            raise ValueError("OLD_CANDIDATE_OUTPUT_CHANGED:" + name)
        intents = rows(
            db,
            "SELECT payload FROM audit WHERE kind='INTENT' AND json_extract(payload,'$.scenario') LIKE 'research/%'",
        )
        for (raw,) in intents:
            p = json.loads(raw)
            order = p["intent"]
            price = Decimal(order["price"])
            quantity = Decimal(order["quantity"])
            if (
                price != min(Decimal(p["signal_anchor"]), Decimal(p["submission_best_bid"]))
                or not 0 < price * quantity <= 5
            ):
                raise ValueError("RESEARCH_PRICE_OR_BUDGET_CHANGED:" + name)
            if order["lifetime"] != "GTC_UNTIL_T240":
                raise ValueError("RESEARCH_LIFETIME_CHANGED")
        cancels = rows(
            db,
            "SELECT payload FROM audit WHERE kind='MAIN_CANCEL_DECISION' AND json_extract(payload,'$.scenario') LIKE 'research/%'",
        )
        if any(json.loads(raw)["reason"] != "T240" for (raw,) in cancels):
            raise ValueError("EXTRA_POLICY_CANCEL")
        checks.append(
            {
                "market": name,
                "old_candidates_identical": True,
                "old_candidates": len(a),
                "research_intents_checked": len(intents),
                "T240_cancel_decisions": len(cancels),
            }
        )
    if not checks:
        raise ValueError("NO_COMPLETE_REPLAY")
    result = {
        "status": "PASS",
        "markets": checks,
        "scope": "Retained candidate identity, budget, quote, lifetime and cancellation policy",
    }
    (run / "launch/economic-validation.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run", type=Path, required=True)
    print(json.dumps(validate(p.parse_args().run.resolve()), indent=2))

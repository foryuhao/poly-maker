"""Reconcile production counts/assumed ledger with an independent replay audit."""

import argparse
import json
import sqlite3
from contextlib import closing
from decimal import Decimal as D
from pathlib import Path

from m510_next_review import GROUPS, review_run
from m511_sim_instant_fill import ResearchInstantFill
from m511_sim_metrics import collect


def validate(root, name):
    run = root / name
    terminal = json.loads((run / "completion.json").read_text())
    assert terminal["status"] == "PASS" and terminal["markets"] == 25
    members = [m["market"] for m in json.loads((root / "cohort25.json").read_text())["markets"]]
    independent = review_run(run, members)
    assert independent["status"] == "COMPLETE"
    output = root / ("assumed-"+name)
    ledger = ResearchInstantFill(run, output)
    try:
        first = ledger.refresh()
        with closing(sqlite3.connect(output / "ledger.sqlite3")) as db:
            before = db.execute("SELECT count(*) FROM audit").fetchone()[0]
        second = ledger.refresh()
        with closing(sqlite3.connect(output / "ledger.sqlite3")) as db:
            after = db.execute("SELECT count(*) FROM audit").fetchone()[0]
        assert first["strategies"] == second["strategies"] and before == after
    finally:
        ledger.close()
    metrics = collect(run, root / ("metrics-cache-"+name+".json"))
    (root / ("metrics-"+name+".json")).write_text(json.dumps(metrics, indent=2)+"\n")
    assert first["source_scenario"] == "research/trade-through"
    for report in (first, metrics):
        assert report["status"] == "OK" and report["counted_markets"] == 25
        assert not report["errors"] and not report["excluded_invalid_markets"]
    assert set(metrics["modes"]) == {"research"}
    checks = {}
    for strategy in GROUPS:
        expected = independent["results"][strategy+"/research/trade-through"]
        assumed = first["strategies"][strategy]
        matched = first["execution_comparison"][strategy]["research/trade-through"]
        actual = metrics["modes"]["research"]["strategies"][strategy]
        assert expected["signals"] == assumed["signals"] == actual["signals"]
        assert expected["submitted"] == assumed["submitted_orders"] == actual["submitted_orders"]
        assert expected["assumed_filled"] == assumed["assumed_filled_orders"]
        assert expected["modelled_filled"] == matched["filled_orders"] == actual["filled_orders"]
        assert expected["unresolved"] == matched["unresolved_orders"] == actual["unresolved_orders"]
        assert D(expected["assumed_pnl"]) == D(assumed["settled_gross_pnl_usdc"])
        assert expected["modelled_pnl"] == D(matched["conditional_known_settled_pnl_usdc"])
        assert assumed["pending_settlement_orders"] == 0
        if expected["unresolved"]:
            assert matched["settled_gross_pnl_usdc"] is None
        checks[strategy] = {k: expected[k] for k in (
            "signals", "submitted", "assumed_filled", "modelled_filled", "unresolved",
            "assumed_pnl", "modelled_pnl")}
    result = {"status": "PASS", "run": str(run), "counted_markets": 25,
        "scenario": "research/trade-through", "assumed_population": "same committed SUBMITs",
        "idempotent_assumed_ledger": before == after, "strategies": checks,
        "scope": "Production metrics and incremental assumed ledger agree with independent SQLite fill-quantity and terminal-account reconciliation"}
    (root / ("ledger-validation-"+name+".json")).write_text(json.dumps(result, indent=2, default=str)+"\n")
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--directory", type=Path, required=True)
    p.add_argument("--run", required=True)
    args = p.parse_args()
    print(json.dumps(validate(args.directory.resolve(), args.run), indent=2, default=str))

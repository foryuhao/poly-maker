#!/usr/bin/env python3
"""Independently reconcile the completed main-mode replay against audit and source signals."""
import argparse
import csv
import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from decimal import Decimal as D
from pathlib import Path


def read(path):
    return json.loads(Path(path).read_text())


def connect(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)


def signals(db, watermark):
    return {(strategy, event, payload) for strategy, event, payload in db.execute(
        "SELECT json_extract(payload,'$.strategy'),json_extract(payload,'$.event'),"
        "json_extract(payload,'$.payload') FROM audit WHERE kind='SIGNAL' AND input_seq<=? "
        "AND json_extract(payload,'$.event') IN ('OPPORTUNITY_IDENTIFIED','M2_CANDIDATE','CANDIDATE')",
        (watermark,))}


def validate(output):
    manifest, comparison = read(output / "manifest.json"), read(output / "comparison.json")
    assert comparison["status"] == "COMPLETE"
    assert comparison["markets"] == len(manifest["sources"])
    checks, pairs = [], {}
    totals = defaultdict(lambda: {"submitted": 0, "filled_orders": 0, "unresolved_orders": 0,
                                  "known_gross_pnl": D(0), "gross_pnl_lower": D(0),
                                  "gross_pnl_upper": D(0)})
    for source in manifest["sources"]:
        market = source["market"]
        directory = output / market
        result, orders = read(directory / "result.json"), read(directory / "orders.json")
        assert result["status"] == "PASS" and all(result["baseline_exact"].values())
        with connect(directory / "simulation.sqlite3") as db, connect(
            Path(source["input"]).with_name("simulation.sqlite3")
        ) as original:
            assert signals(db, source["input_seq"]) == signals(original, source["input_seq"])
            fills, submits, intents, cancels, main_cancel = {}, {}, {}, set(), set()
            for kind, raw in db.execute(
                "SELECT kind,payload FROM audit WHERE kind IN "
                "('SUBMIT','INTENT','MODELLED_FILL','CANCEL_REQUEST','MAIN_CANCEL_DECISION') ORDER BY seq"
            ):
                p = json.loads(raw)
                if kind == "INTENT":
                    i = p["intent"]
                    intents[(p["scenario"], i["strategy"], i["opportunity"])] = p
                    continue
                key = (p["scenario"], p["order"])
                if kind == "SUBMIT":
                    assert key not in submits
                    submits[key] = p
                elif kind == "MODELLED_FILL":
                    assert key in submits and D(p["quantity"]) > 0
                    f = fills.setdefault(key, [D(0), D(0)])
                    f[0] += D(p["quantity"])
                    f[1] += D(p["cost"])
                elif p["scenario"].startswith("main/"):
                    if kind == "CANCEL_REQUEST":
                        cancels.add(key)
                    else:
                        assert p["reason"] == "T240"
                        assert p["elapsed"] + p["clock_uncertainty"] >= 240
                        main_cancel.add(key)
            assert cancels == main_cancel
            counts = Counter()
            for o in orders:
                scenario = o["scenario"] if o["variant"] == "baseline" else "main/" + o["scenario"]
                key = (scenario, o["order"])
                submit = submits[key]
                intent = submit["intent"]
                qty, cost = fills.get(key, (D(0), D(0)))
                assert qty == D(o["filled"])
                pnl = qty * (int(o["outcome"] == source["winner"]) - D(o["price"]))
                assert pnl == D(o["known_gross_pnl"])
                assert cost == qty * D(o["price"])
                if o["variant"] == "main":
                    evidence = intents[(scenario, o["strategy"], o["candidate"])]
                    assert D(o["price"]) == min(D(evidence["signal_anchor"]), D(evidence["submission_best_bid"]))
                    assert D(o["price"]) * D(o["quantity"]) <= 5
                    assert intent["lifetime"] == "GTC_UNTIL_T240"
                    counts[(o["strategy"], o["scenario"], o["outcome"])] += 1
                    assert counts[(o["strategy"], o["scenario"], o["outcome"])] <= 1
                t = totals[(o["variant"], o["scenario"], o["strategy"])]
                t["submitted"] += 1
                t["filled_orders"] += qty > 0
                t["unresolved_orders"] += bool(o["uncertain"])
                t["known_gross_pnl"] += pnl
                for field in ("gross_pnl_lower", "gross_pnl_upper"):
                    t[field] += D(o[field])
                pair_key = (market, o["strategy"], o["scenario"], o["outcome"])
                pair = pairs.setdefault(pair_key, dict(zip(("market", "strategy", "scenario", "outcome"), pair_key, strict=True)))
                for field in ("candidate", "price", "quantity", "filled", "known_gross_pnl", "uncertain", "state"):
                    pair[o["variant"] + "_" + field] = o[field]
        checks.append({"market": market, "baseline_exact": True, "signals_exact": True,
                       "main_cancel_only_t240": True, "prices_and_budget_exact": True,
                       "audit_fill_pnl_reconciled": True})
    for r in comparison["rows"]:
        t = totals[(r["variant"], r["scenario"], r["strategy"])]
        for field, actual in t.items():
            assert D(r[field]) == actual, (r["strategy"], field)
    pair_rows = list(pairs.values())
    fields = sorted({key for row in pair_rows for key in row})
    with (output / "paired-orders.csv").open("w") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(pair_rows)
    validation = {"status": "PASS", "markets": len(checks), "checks": checks,
                  "comparison_sha256": hashlib.sha256((output / "comparison.json").read_bytes()).hexdigest(),
                  "validator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  "scope": "all frozen markets; rates and PnL are independent scenario accounts"}
    (output / "validation.json").write_text(json.dumps(validation, indent=2) + "\n")
    return validation


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = validate(args.output)
    print(json.dumps({k: v for k, v in result.items() if k != "checks"}, indent=2))

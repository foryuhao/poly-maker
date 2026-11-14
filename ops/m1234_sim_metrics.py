"""Incremental, read-only operational counts from the current run's committed audit."""

import json
import sqlite3
import time
from contextlib import closing
from decimal import Decimal
from pathlib import Path

STRATEGIES = ("M1", "M2", "M3", "M4")
EVENTS = {"M1": "OPPORTUNITY_IDENTIFIED", "M2": "M2_CANDIDATE",
          "M3": "CANDIDATE", "M4": "CANDIDATE"}
QUERY = """
SELECT seq, kind, payload FROM audit WHERE seq > ? AND seq <= ? AND
(kind IN ('SUBMIT', 'MODELLED_FILL', 'SIMULATION_INVALID', 'ORDER_UNCERTAIN', 'UNKNOWN_TAIL') OR
 (kind = 'SIGNAL' AND json_extract(payload, '$.event') IN
 ('OPPORTUNITY_IDENTIFIED', 'M2_CANDIDATE', 'CANDIDATE'))) ORDER BY seq
"""


def empty_counts():
    return {"signals": 0, "submitted_orders": 0, "filled_orders": 0,
            "fill_events": 0, "filled_shares": "0", "unresolved_orders": 0}


def scan_market(path, owner, previous):
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True,
                                timeout=2)) as db:
        db.execute("PRAGMA query_only = ON")
        db.execute("BEGIN")
        metadata = dict(db.execute("SELECT name, payload FROM metadata"))
        binding = json.loads(metadata["identity"])
        if json.loads(metadata["run_owner"]) != owner:
            raise ValueError("METRICS_RUN_IDENTITY_MISMATCH")
        tracker = json.loads((path.parent / (binding["database_id"] + ".validity.json")).read_text())
        if tracker["binding"] != binding or tracker.get("evidence_schema") != 1:
            raise ValueError("METRICS_TRACKER_IDENTITY_MISMATCH")
        status = tracker["validity"]["status"]
        if status not in {"TRACKING", "INVALID"}:
            raise ValueError("METRICS_TRACKER_UNVERIFIED")
        last = db.execute("SELECT seq, input_seq FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
        high, input_seq = last or (0, 0)
        quantity_cache_complete = all(
            order.get("scenario") != "research/trade-through"
            or order.get("submitted_quantity") is not None
            for order in previous.get("orders", {}).values())
        if (previous.get("binding") == binding and previous.get("seq", 0) <= high
                and quantity_cache_complete):
            # A failed scan must not modify the last usable cache.
            state = json.loads(json.dumps(previous))
        else:
            state = {"binding": binding, "seq": 0, "signals": {s: [] for s in STRATEGIES},
                     "orders": {}, "invalid": False}
        signals = {s: set(state["signals"][s]) for s in STRATEGIES}
        for _, kind, raw in db.execute(QUERY, (state["seq"], high)):
            payload = json.loads(raw)
            if kind == "SIGNAL":
                strategy = payload["strategy"]
                if payload["event"] != EVENTS[strategy]:
                    continue
                detail = payload["payload"]
                candidate = (detail["opportunity"]["episode_id"] if strategy == "M1" else
                             detail["candidate_id"] if strategy == "M2" else detail["id"])
                signals[strategy].add(json.dumps([detail.get("generation"), candidate]))
            elif kind == "SIMULATION_INVALID":
                state["invalid"] = True
            else:
                key = json.dumps([payload["scenario"], payload["order"]])
                if kind == "SUBMIT":
                    intent = payload["intent"]
                    if intent["strategy"] not in STRATEGIES:
                        raise ValueError("METRICS_UNKNOWN_STRATEGY")
                    state["orders"].setdefault(key, {"strategy": intent["strategy"],
                        "profile": intent["profile"], "scenario": payload["scenario"],
                        "submitted_quantity": intent.get("quantity"),
                        "fill_events": 0, "filled_shares": "0", "unresolved": False})
                elif kind in {"ORDER_UNCERTAIN", "UNKNOWN_TAIL"}:
                    state["orders"][key]["unresolved"] = True
                else:
                    order = state["orders"][key]  # An unjoined fill is an error, not zero.
                    quantity = Decimal(payload["quantity"])
                    if not quantity.is_finite() or quantity <= 0:
                        raise ValueError("METRICS_BAD_FILL_QUANTITY")
                    order["fill_events"] += 1
                    order["filled_shares"] = str(Decimal(order["filled_shares"]) + quantity)
                    if (order["scenario"] == "research/trade-through"
                            and Decimal(order["filled_shares"]) == Decimal(order["submitted_quantity"])):
                        order["unresolved"] = False
        state.update(seq=high, input_seq=input_seq,
                     signals={s: sorted(ids) for s, ids in signals.items()},
                     invalid=state["invalid"] or status == "INVALID")
        return state


def collect(run, cache_path):
    run = Path(run).resolve()
    owner = json.loads((run / "plan.json").read_text())["run_identity"]
    try:
        cache = json.loads(cache_path.read_text())
    except (OSError, ValueError):
        cache = {}
    if cache.get("schema") != 2 or cache.get("run_directory") != str(run):
        cache = {"schema": 2, "run_directory": str(run), "markets": {}}
    totals = {s: empty_counts() for s in STRATEGIES}
    breakdown, sources, errors, invalid = {}, [], [], []
    paths = sorted(run.glob("*/simulation.sqlite3"))  # Excludes prepost strategy databases.
    for path in paths:
        market = path.parent.name
        try:
            state = scan_market(path, owner, cache["markets"].get(market, {}))
            cache["markets"][market] = state
            sources.append({"market": market, "database_id": state["binding"]["database_id"],
                            "audit_seq": state["seq"], "input_seq": state["input_seq"],
                            "invalid": state["invalid"]})
            if state["invalid"]:
                invalid.append(market)
                continue
            for strategy in STRATEGIES:
                totals[strategy]["signals"] += len(state["signals"][strategy])
            for order in state["orders"].values():
                group = "/".join([order["strategy"], order["profile"], order["scenario"]])
                detail = breakdown.setdefault(group, {k: v for k, v in empty_counts().items()
                                                       if k != "signals"})
                for counts in (totals[order["strategy"]], detail):
                    counts["submitted_orders"] += 1
                    counts["filled_orders"] += int(order["fill_events"] > 0)
                    counts["unresolved_orders"] += int(order["unresolved"])
                    counts["fill_events"] += order["fill_events"]
                    counts["filled_shares"] = str(Decimal(counts["filled_shares"]) +
                                                  Decimal(order["filled_shares"]))
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
            errors.append({"market": market, "error": type(exc).__name__ + ":" + str(exc)})
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    temp = cache_path.with_suffix(".tmp")
    temp.write_text(json.dumps(cache, ensure_ascii=False) + "\n")
    temp.replace(cache_path)
    return {"status": "PARTIAL" if errors or invalid else "OK", "observed_at": time.time(),
            "scope": "CURRENT_RUN_COMMITTED_AUDIT_EXCLUDING_INVALID_MARKETS",
            "market_databases": len(paths), "counted_markets": len(sources) - len(invalid),
            "strategies": totals, "by_profile_scenario": breakdown, "sources": sources,
            "excluded_invalid_markets": invalid, "errors": errors,
            "definitions": {"signals": "unique candidate per market and strategy; not multiplied by scenarios",
                "filled_orders": "orders with positive MODELLED_FILL; partial fills count once per scenario/order",
                "submitted_orders": "SUBMIT orders, across both queue scenarios and M1 profiles",
                "unresolved_orders": "orders with ORDER_UNCERTAIN or UNKNOWN_TAIL; known fills may be incomplete",
                "freshness": "committed SQLite audit only; active transaction is not yet counted"}}

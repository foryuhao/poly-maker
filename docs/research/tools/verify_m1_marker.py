"""Verify a completed public M1 marker, including raw fixed-queue hold history.

This reads only local observation files. It never imports strategy/execution
code, so the hold-state bug cannot verify itself through the same implementation.
"""

import argparse
import gzip
import hashlib
import json
import tomllib
from decimal import Decimal as D
from pathlib import Path
from statistics import median


def rows(path):
    with (gzip.open(path, "rt") if path.suffix == ".gz" else path.open()) as stream:
        for line in stream:
            yield json.loads(line)


def raw_hold_checks(path, rounds, token, anchor):
    start = min(r["evidence_start"] for r in rounds)
    end = max(r["evidence_end"] for r in rounds)
    updates, blocked = [], set()
    for event in rows(path):
        if (event["event"] == "EVENT_DECISION" and event.get("token_id") == token
                and event.get("kind") in {"price_change", "book"}
                and event.get("reason") in {"M1_SOURCE_REGRESSION", "M1_SOURCE_AGE",
                    "M1_PROCESSING_GAP", "M1_EVENT_OUT_OF_ORDER_IGNORED"}):
            blocked.add(event["frame"])
        if event["event"] != "RAW_FRAME" or not start <= event["received_monotonic"] < end:
            continue
        if event["raw"] in ("PING", "PONG"):
            continue
        raw = json.loads(event["raw"]) if isinstance(event["raw"], str) else event["raw"]
        for row in raw if isinstance(raw, list) else [raw]:
            quantities = []
            if row.get("event_type") == "price_change":
                quantities = [D(ch["size"]) for ch in row["price_changes"]
                    if ch["asset_id"] == token and ch["side"] == "BUY" and D(ch["price"]) == anchor]
            elif row.get("event_type") == "book" and row["asset_id"] == token:
                quantities = [next((D(ch["size"]) for ch in row["bids"]
                                   if D(ch["price"]) == anchor), D(0))]
            for quantity in quantities:
                updates.append((event["processed_monotonic"], event["received_monotonic"],
                                event["frame"], quantity))
    checks = []
    for r in rounds:
        initial = low = D(r["initial"])
        hold, proven = None, 0.
        for processed, received, frame, quantity in updates:
            if frame in blocked or not r["evidence_start"] <= received < r["evidence_end"]:
                continue
            if quantity < low:
                low, hold, proven = quantity, None, 0.
            loss = initial-low
            recovery = min(D(1), max(D(0), quantity-low)/loss) if loss else D(0)
            if recovery < D(".2"):
                proven = 0.
            if recovery < D(".4"):
                hold = None
            elif hold is None and recovery >= D(".5"):
                hold = processed
            if hold is not None:
                proven = max(proven, min(processed, r["evidence_end"])-hold)
        if hold is not None:
            proven = max(proven, r["evidence_end"]-hold)
        checks.append({"round_id": r["round_id"], "trough_matches": low == D(r["trough"]),
                       "hold_after_last_failure_ms": proven*1000,
                       "passed": low == D(r["trough"]) and proven >= .100})
    return checks


def verify(session):
    result = json.loads((session/"result.json").read_text())
    config = tomllib.loads((session/"config.toml").read_text())["rdp_sm"]["m1"]
    path = session/("events.jsonl.gz" if (session/"events.jsonl.gz").exists() else "events.jsonl")
    facts = [e for e in rows(path) if e["event"] in {"WOULD_POST", "RF_CONFIRMED", "EX_CONFIRMED",
             "AP_CONFIRMED", "ROUND_CLOSED", "SIGNAL_INVALIDATED", "RF_INVALIDATED", "EX_ENDED"}]
    proofs = []
    for marker in (e for e in facts if e["event"] == "WOULD_POST"):
        ep = marker["opportunity"]
        rf = next(e for e in facts if e["event"] == "RF_CONFIRMED" and e["rf_id"] == ep["rf_id"])
        ex = next(e for e in facts if e["event"] == "EX_CONFIRMED" and e["ex_id"] == ep["ex_id"])
        ap = next(e for e in facts if e["event"] == "AP_CONFIRMED" and e["ap_id"] == ep["ap_id"])
        rounds = [next(e for e in facts if e["event"] == "ROUND_CLOSED" and e["round_id"] == r[1]) for r in rf["rounds"]]
        holds = raw_hold_checks(path, rounds, ep["token_id"], D(marker["price"]))
        now = marker["available_monotonic"]
        interruption = any(e.get("token_id") in {None, ep["token_id"]}
            and min(rf["available_monotonic"], ex["available_monotonic"]) < e["available_monotonic"] < now
            and (e["event"] in {"SIGNAL_INVALIDATED", "RF_INVALIDATED"}
                 or e["event"] == "EX_ENDED" and e["available_monotonic"] > ex["available_monotonic"]
                 or e["event"] == "ROUND_CLOSED" and not e["stable"]) for e in facts)
        reference = D(ex["reference_comparison_volume"])
        volumes = list(map(D, ex["subwindow_volumes"]))
        signal = config["signal"]
        checks = {
            "writer_complete": not result["writer"]["failure"] and not result["writer"]["dropped"]
                and result["writer"]["accepted"] == result["writer"]["written"],
            "detector_healthy": not result["m1_observation"]["failure"],
            "no_post": marker["post_started"] is False and marker["observation_only"] is True,
            "buy_maker": marker["side"] == "BUY" and marker["post_only"] is True,
            "same_token": rf["token_id"] == ex["token_id"] == ap["token_id"] == ep["token_id"],
            "independent_rounds": len(rounds) >= signal["rf_rounds"] and all(
                a["evidence_end"] <= b["evidence_start"] for a, b in zip(rounds, rounds[1:], strict=False)),
            "refills_qualified": all(D(r["hit"]) >= max(D(signal["min_round_shares"]),
                D(r["initial"])*D(signal["min_round_depth_fraction"])) and D(r["initial"])-D(r["trough"])
                >= max(D(signal["min_round_shares"]), D(r["initial"])*D(signal["min_round_depth_fraction"]))
                and r["stable"] and r["held"] and D(r["recovery"]) >= D(".5") for r in rounds),
            "median_recovery": median(D(r["recovery"]) for r in rounds) >= D(signal["rf_min_median_recovery"]),
            "raw_holds": all(c["passed"] for c in holds),
            "sell_attack": ap["side"] == "SELL" and D(ap["volume"]) >= D(ap["minimum"])
                and D(ap["share"]) >= D(signal["ap_min_share"]) and ap["records"] >= signal["ap_min_records"]
                and len(ap["occupied_subwindows"]) >= 2 and 2 in ap["occupied_subwindows"]
                and D(ap["concentration"]) <= D(signal["ap_max_concentration"]),
            "sell_exhaustion": all(v <= reference/6 for v in volumes) and sum(volumes) <= reference*D(".25"),
            "rf_ex_overlap": now < min(rf["deadline"], ex["deadline"]),
            "no_interruption": not interruption,
            "execution_budget": marker["remaining_ms"] >= config["post_budget_floor_ms"]+config["min_live_budget_ms"],
            "observation_window": config["observation_start_sec"] <= marker["elapsed_lower"]
                and marker["elapsed_upper"] < config["cutoff_sec"],
            "order_budget": D(marker["price"])*D(marker["size"]) <= D(config["order_budget_usdc"]),
            "parameter_identity": marker["parameter_hash"] == result["m1_observation"]["parameter_hash"],
        }
        proofs.append({"passed": all(checks.values()), "checks": checks, "raw_hold_checks": holds,
                       "marker": marker, "rf": rf, "rounds": rounds, "ap": ap, "ex": ex})
    with path.open("rb") as file:
        digest = hashlib.file_digest(file, "sha256").hexdigest()
    return {"status": "VERIFIED_LIVE_PUBLIC_WOULD_POST" if any(p["passed"] for p in proofs) else "NO_VERIFIED_MARKER",
            "account_admission": "NOT_CHECKED", "profile": config["signal"]["profile"],
            "algorithm_revision": result["m1_observation"].get("algorithm_revision"),
            "event_file": str(path.resolve()),
            "event_file_sha256": digest, "proofs": proofs}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", type=Path)
    args = parser.parse_args()
    result = verify(args.session)
    path = args.session/"marker_audit.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({"status": result["status"], "audit": str(path),
                      "failed_checks": [[k for k, v in p["checks"].items() if not v] for p in result["proofs"]]}))
    return 0 if result["status"] == "VERIFIED_LIVE_PUBLIC_WOULD_POST" else 1


if __name__ == "__main__":
    raise SystemExit(main())

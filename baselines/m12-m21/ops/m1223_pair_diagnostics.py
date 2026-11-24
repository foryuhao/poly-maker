"""Explain recorded M12 pair gates at atomic input boundaries, without PnL."""

import argparse
import json
import sqlite3
from collections import Counter
from decimal import Decimal as D
from itertools import groupby
from pathlib import Path


def diagnose(database):
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=1) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        row = db.execute("SELECT payload FROM audit WHERE kind='DEFINITION'").fetchone()
        definition = json.loads(row[0])["definition"]
        params = definition["settings"]["sop"]["parameters"]
        overlap = definition["settings"]["sop"].get("variant_id") == "M12_VALIDITY_OVERLAP"
        records = [
            (seq, json.loads(raw))
            for seq, raw in db.execute("""
            SELECT input_seq,payload FROM audit WHERE kind='SOP_EVIDENCE'
            AND json_extract(payload,'$.event') IN
            ('COMPONENT_CONFIRMED','COMPONENT_ENDED','SIGNAL_CONFIRMED') ORDER BY seq""")
        ]
        watermark = db.execute("SELECT MAX(input_seq) FROM audit").fetchone()[0]
    active, roots = {}, {}
    for _, batch in groupby(records, key=lambda r: r[0]):
        for _seq, packet in batch:
            event, fact = packet["event"], packet["payload"]
            if event == "COMPONENT_CONFIRMED":
                active[fact["id"]] = fact
                if fact["side"] == "ASK":
                    roots[fact["id"]] = {
                        "token": fact["token"],
                        "published_boundary": False,
                        "same_token_bid": False,
                        "same_generation_bid": False,
                        "anchor_ok": False,
                        "gap_ok": False,
                        "ratios_ok": False,
                        "selected": False,
                        "end_reason": None,
                    }
            elif event == "COMPONENT_ENDED":
                active.pop(fact["id"], None)
                if fact["id"] in roots:
                    roots[fact["id"]]["end_reason"] = fact["reason"]
            elif event == "SIGNAL_CONFIRMED" and fact["root_id"] in roots:
                roots[fact["root_id"]]["selected"] = True
        # Never evaluate a transient intermediate book as a publication boundary.
        for aid, ask in active.items():
            if ask["side"] != "ASK":
                continue
            status = roots[aid]
            status["published_boundary"] = True
            for bid in active.values():
                if bid["side"] != "BID" or bid["token"] != ask["token"]:
                    continue
                status["same_token_bid"] = True
                if bid["generation"] != ask["generation"]:
                    continue
                status["same_generation_bid"] = True
                if D(bid["price"]) >= D(ask["price"]):
                    continue
                status["anchor_ok"] = True
                if not overlap and abs(bid["key"][0] - ask["key"][0]) > params["pair_gap"] + 1e-9:
                    continue
                status["gap_ok"] = True
                if all(
                    D(params["ratio_min"]) <= D(ask[name]) / D(bid[name]) <= D(params["ratio_max"])
                    for name in (("u",) if overlap else ("u", "d"))
                ):
                    status["ratios_ok"] = True
    counts = Counter()
    for r in roots.values():
        if r["selected"]:
            reason = "RECORDED_SIGNAL_SELECTED"
        elif not r["published_boundary"]:
            reason = "ENDED_BEFORE_ATOMIC_PUBLICATION_BOUNDARY"
        elif not r["same_token_bid"]:
            reason = "NO_OVERLAPPING_LIVE_BID_FOR_SAME_TOKEN"
        elif not r["same_generation_bid"]:
            reason = "NO_BID_IN_SAME_RECONSTRUCTION_GENERATION"
        elif not r["anchor_ok"]:
            reason = "ORIGINAL_ANCHORS_NOT_ORDERED"
        elif not r["gap_ok"]:
            reason = "CONFIRMATION_DISTANCE_EXCEEDS_FROZEN_BOUND"
        elif not r["ratios_ok"]:
            reason = ("NORMALIZED_HIT_RATIO_OUTSIDE_BOUND" if overlap
                      else "NORMALIZED_HIT_OR_LOSS_RATIO_OUTSIDE_BOUND")
        else:
            reason = "ELIGIBLE_PAIR_WITHOUT_RECORDED_SIGNAL_REQUIRES_REVIEW"
        r["pair_outcome"] = reason
        counts[reason] += 1
    return {
        "market": definition["market"]["slug"],
        "source_db": str(database),
        "input_seq": watermark,
        "parameters": params,
        "variant_id": definition["settings"]["sop"].get("variant_id"),
        "pair_gap_filter_enabled": not overlap,
        "cross_side_loss_ratio_filter_enabled": not overlap,
        "ask_roots": len(roots),
        "outcomes": dict(counts),
        "root_details": roots,
        "selection_uses_pnl": False,
        "scope": "Recorded baseline components only; no inferred missing roots or counterfactual signals",
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--database", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    args = p.parse_args()
    result = diagnose(args.database)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "root_details"}, ensure_ascii=False))

"""Read-only reconstruction of account checks from pinned cache versions."""

import hashlib
import json
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent
SESSIONS = (
    "5c3bdb0c62fe45ca907799546b13ef9a",
    "9d33197d4ddc4c6f809a6155a312d02a",
    "0d356940bbe64fe5bd4df3f08d4718f9",
)


def main():
    summaries = []
    for sid in SESSIONS:
        source = next((ROOT / "journal").glob(f"*_{sid}.jsonl"))
        raw = source.read_bytes()
        rows = [json.loads(line) for line in raw.splitlines()]
        cache_root = ROOT / "research_outputs/periodic_sync" / sid
        cache_source = cache_root / "snapshots.jsonl"
        cache_raw = cache_source.read_bytes()
        snapshots = {r["version"]: r for r in map(json.loads, cache_raw.splitlines())}
        get = lambda row: row["data"]["payload"]
        session = next(get(r) for r in rows if r["kind"] == "sm_session")
        result = next(get(r) for r in rows if r["kind"] == "sm_run_result")
        meta = json.loads((cache_root / "manifest.json").read_text())["market"]
        start, tokens = meta["start"], meta["tokens"]
        fills, eligible, merged, mismatches, merges, decisions = {}, set(), Decimal(0), [], [], []
        for line, row in enumerate(rows, 1):
            d, kind = get(row), row["kind"]
            if kind == "sm_trade":
                key = (d["trade_id"], d["maker_order_id"])
                fills[key] = d
                if d["status"] == "CONFIRMED":
                    eligible.add(key)
            elif kind == "sm_position_reconciled":
                for fill in d["included"]:
                    key = (fill["trade_id"], fill["maker_order_id"])
                    fills.setdefault(key, fill)
                    eligible.add(key)
            elif kind == "sm_merge_result" and d["status"] == "CONFIRMED":
                merged += Decimal(d["amount_raw"])/1000000
                merges.append({"journal_line": line, "elapsed_wall": row["ts"]-start, **d})
            elif kind == "sm_repair_decision" and d.get("action") in {
                    "BUY", "CANCEL", "REPLACE_AFTER_CANCEL"}:
                decisions.append({"journal_line": line, "elapsed_wall": row["ts"]-start,
                                  **{k: d.get(k) for k in
                                     ("mode", "action", "reason", "side", "price", "quantity")}})
            elif kind == "sm_sync_cache_read" and d["purpose"] == "account":
                snap = snapshots[d["snapshot_version"]]
                pos = snap["last_good"]["positions"]
                qty = {r["token_id"]: Decimal(str(r["size"])) for r in pos["data"]}
                inventory = {}
                for token, side in zip(tokens, ("YES", "NO"), strict=True):
                    expected = sum((Decimal(str(fills[k]["size"])) for k in eligible
                                    if fills[k]["token_id"] == token), Decimal(0))-merged
                    actual = qty.get(token, Decimal(0))
                    inventory[side] = {"expected": str(expected), "actual": str(actual),
                                       "difference": str(actual-expected)}
                if any(abs(Decimal(v["difference"])) >= Decimal(".000001")
                       for v in inventory.values()):
                    mismatches.append({"journal_line": line, "elapsed_wall": row["ts"]-start,
                                       "cache_version": d["snapshot_version"],
                                       "positions_read_id": pos["read_id"], "inventory": inventory})
        # Only target-market quantities are extracted; account history is left in place.
        changes, last = [], None
        for snap in snapshots.values():
            pos = snap["last_good"].get("positions")
            if not pos:
                continue
            qty = {r["token_id"]: str(r["size"]) for r in pos["data"]}
            value = [qty.get(token, "0") for token in tokens]
            if value != last:
                changes.append({"cache_version": snap["version"],
                                "elapsed_wall": snap["published_wall"]-start,
                                "yes_no": value, "read_id": pos["read_id"]})
                last = value
        health = json.loads((cache_root / "summary.json").read_text())
        summaries.append({"market": session["slug"], "session_id": sid,
                          "source": str(source.resolve()),
                          "source_sha256": hashlib.sha256(raw).hexdigest(),
                          "cache_source": str(cache_source.resolve()),
                          "cache_sha256": hashlib.sha256(cache_raw).hexdigest(),
                          "result": {k: v for k, v in result.items() if k != "windows"},
                          "unique_fills": list(fills.values()), "merge_results": merges,
                          "repair_decisions": decisions, "positions_changes": changes,
                          "reconstructed_account_mismatches": mismatches,
                          "method": "Compare economic fill facts observed/proven before each account reference against that exact pinned cache version. Human inventory_check output is not persisted in the journal.",
                          "cache_health": {k: health.get(k) for k in
                                           ("failure", "observer_fatal_type", "dropped_events")}})
        assert source.read_bytes() == raw and cache_source.read_bytes() == cache_raw
    assert summaries[0]["result"]["delta"] == .01
    assert summaries[0]["merge_results"][0]["after_yes_raw"] == 10000
    assert all(s["result"]["execution_status"] == "COMPLETED" for s in summaries)
    latest = summaries[-1]["reconstructed_account_mismatches"]
    assert any(135 < m["elapsed_wall"] < 137
               and m["inventory"]["NO"]["difference"] == "-5.0" for m in latest)
    (OUT / "summary.json").write_text(json.dumps(summaries, indent=2)+"\n")
    print(json.dumps([{"market": s["market"], "mismatch_count": len(s["reconstructed_account_mismatches"]),
                       "first_mismatch": s["reconstructed_account_mismatches"][:1],
                       "last_mismatch": s["reconstructed_account_mismatches"][-1:]}
                      for s in summaries], indent=2))


if __name__ == "__main__":
    main()

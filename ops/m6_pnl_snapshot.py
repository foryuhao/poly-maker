"""Read-only M6 baseline snapshot and public official-resolution checks."""

import argparse
import asyncio
import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from polymaker.dashboard.settlement import validate_resolution
from polymaker.m1_virtual_api import VirtualPublicAPI

BJ = ZoneInfo("Asia/Shanghai")
MODEL = "M6_BEST_ASK_ANCHOR_MINUS_1TICK_UNTIL_T240_V1"
LEGACY_MODEL = "M6_BEST_ASK_ANCHOR_MINUS_1TICK_UNTIL_T240_V1"


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.project_dir.resolve() / "research_outputs/m6_virtual"
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    scope = json.loads((root / "statistics-scope.json").read_text())
    first_epoch = scope["first_market_epoch"]
    if scope["mode"] != "VIRTUAL" or scope["model"] != MODEL or first_epoch % 300:
        raise ValueError("M6_STATISTICS_SCOPE_INVALID")
    began = time.time()
    rows = []
    for epoch in range(first_epoch, int(began // 300) * 300 + 1, 300):
        path = root / "ledgers" / f"btc-updown-5m-{epoch}.sqlite3"
        row = {"epoch": epoch, "path": str(path), "metadata": {}, "facts": {}, "resolution": None}
        if path.exists():
            with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
                db.execute("PRAGMA query_only=ON")
                db.execute("BEGIN")
                row["metadata"] = {
                    k: json.loads(v) for k, v in db.execute("SELECT key,value FROM metadata")
                }
                assert row["metadata"]["identity"]["mode"] == "VIRTUAL"
                assert row["metadata"]["identity"]["strategy"] == "m6_withdrawal_supported_v1_1"
                assert row["metadata"]["baseline_fill_model"]["model"] in {MODEL, LEGACY_MODEL}
                for kind in ["virtual_post", "virtual_observation", "virtual_fill", "virtual_cancel"]:
                    row["facts"][kind] = [
                        json.loads(v)
                        for (v,) in db.execute("SELECT payload FROM facts WHERE kind=?", (kind,))
                    ]
        rows.append(row)
    api = VirtualPublicAPI()
    try:
        for row in rows:
            m = row["metadata"]
            if not m or row["epoch"] + 300 > began:
                continue
            market = m["market"]
            expected = {
                "slug": market["slug"],
                "condition_id": market["condition_id"],
                "token_ids": {
                    "YES": market["tokens"][0]["token_id"],
                    "NO": market["tokens"][1]["token_id"],
                },
            }
            saved = m.get("modeled_settlement")
            if saved:
                resolution = saved["resolution"]
                assert (
                    resolution["slug"] == expected["slug"]
                    and resolution["condition_id"] == expected["condition_id"]
                )
                assert resolution["token_ids"] == expected["token_ids"]
                assert resolution["closed"] and resolution["resolution_status"] == "resolved"
                assert sorted(map(float, resolution["outcome_prices"])) == [0, 1]
                row["resolution"] = resolution
                row["resolution_origin"] = "saved_official_settlement"
            else:
                try:
                    raw = await api.raw_market(market["slug"], purpose="settlement")
                    row["public_response"] = raw
                    row["public_checked_at"] = time.time()
                    row["resolution"] = validate_resolution(raw, expected)
                    row["resolution_origin"] = "read_only_official_check"
                except Exception as exc:
                    row["public_error"] = type(exc).__name__
    finally:
        await api.close()
    snapshot = {
        "snapshot_at": began,
        "snapshot_at_bj": datetime.fromtimestamp(began, BJ).isoformat(),
        "read_completed_at": time.time(),
        "first_epoch": first_epoch,
        "statistics_scope": scope,
        "records": rows,
        "scope": "M6 baseline only; same POST cohort; no account reads, writes or restarts.",
    }
    (output / "snapshot.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                "snapshot_at_bj": snapshot["snapshot_at_bj"],
                "markets": [
                    {
                        "epoch": r["epoch"],
                        "complete": r["metadata"].get("complete"),
                        "posts": len(r["facts"].get("virtual_post", [])),
                        "observations": [
                            o["status"] for o in r["facts"].get("virtual_observation", [])
                        ],
                        "official_winner": (r["resolution"] or {}).get("winner"),
                    }
                    for r in rows
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())

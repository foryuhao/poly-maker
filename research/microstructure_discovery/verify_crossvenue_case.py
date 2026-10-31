"""Recompute one historical executable-price case; never attributes personal fills."""

import argparse
import datetime as dt
import hashlib
import json
from decimal import ROUND_CEILING
from decimal import Decimal as D
from pathlib import Path

from seal_crossvenue_ws import seal_and_replay


def ceil_to(x, unit):
    return x.quantize(unit, rounding=ROUND_CEILING)


def price_case(cash=D("7.01"), p=D(".69"), rate=D(".05"), contracts=D(10), kp=D(".21")):
    gross = cash / p
    normal_fee = ceil_to(rate * cash * (1 - p), D(".00001"))
    normal_tokens = (cash - normal_fee) / p
    # Current July2026 Kalshi schedule rounds fee+position cost to0.0001USD.
    kcost = ceil_to(contracts * kp + D(".07") * contracts * kp * (1 - kp), D(".0001"))
    # Minimum0.01 contracts => at most1000 fills for10 contracts. Each
    # centicent rounding can add<0.0001USD. Bound both fragmented fee sides.
    fragmented_k = ceil_to(kcost + (contracts / D(".01")) * D(".0001"), D(".01"))
    # Polymarket round-to-five-decimal (including zero) fee<=2*unrounded fee.
    stress_tokens = gross * (1 - 2 * rate * (1 - p))
    return dict(
        poly_usdc_budget=str(cash),
        poly_price_cap=str(p),
        minimum_normal_no_tokens=str(normal_tokens),
        kalshi_contracts=str(contracts),
        kalshi_yes_price_cap=str(kp),
        kalshi_normal_all_in_cap=str(kcost),
        normal_total_cost=str(cash + kcost),
        normal_profit_after_002_reserve=str(
            min(contracts, normal_tokens) - cash - kcost - D(".02")
        ),
        poly_fragmented_token_floor=str(stress_tokens),
        kalshi_fragmented_all_in_bound=str(fragmented_k),
        fragmented_profit_after_002_reserve=str(
            min(contracts, stress_tokens) - cash - fragmented_k - D(".02")
        ),
    )


def main():
    p = argparse.ArgumentParser()
    p.add_argument("root", type=Path)
    p.add_argument("--case", type=Path, required=True)
    p.add_argument("--ws-root", type=Path, required=True)
    a = p.parse_args()
    folder = a.case
    trigger = json.loads((folder / "trigger.json").read_text())
    arrival = json.loads((folder / "poly_no_kalshi_yes_1.5.json").read_text())
    reg = json.loads((a.root / "registration.json").read_text())
    pair = next(
        v
        for v in json.loads((a.root / "mappings.json").read_text())
        if v["kalshi"]["ticker"] == trigger["ticker"]
    )
    fees = json.loads((a.root / "fees.json").read_text())[trigger["market"]]
    target = max(trigger[k]["received"] for k in ["yes", "no", "kalshi"])
    cases = sorted(
        f
        for f in (a.root / "automatic_confirmation").iterdir()
        if f.is_dir() and (f / "trigger.json").exists()
    )
    ws = json.loads((folder / "ws_audit.json").read_text())
    ledger = sum(
        (v["rows"] for v in json.loads((folder / "settled_trades_pages.json").read_text())), []
    )
    exact = []
    mismatch = []
    for v in ws["trades"]:
        t = v["event"]
        if t["asset_id"] != pair["poly_no"] or t["side"] != "BUY" or float(t["price"]) != 0.69:
            continue
        matches = [
            r
            for r in ledger
            if r["transaction_hash"] == t["transaction_hash"]
            and r["token_id"] == t["asset_id"]
            and r["side"] == "BUY"
        ]
        good = [
            r
            for r in matches
            if abs(float(r["price"]) - 0.69) <= 1e-6
            and abs(float(r["size"]) - float(t["size"])) <= 1e-6
        ]
        (exact if good else mismatch).append(dict(ws=t, ledger=matches))
    trades = json.loads((folder / "kalshi_trades.json").read_text())["data"]["trades"]
    # Official schema: YES/bid are the SAME directional bit. Bid does NOT
    # mean the aggressor hit a bid. YES includes buy-YES and sell-NO.
    kactual = [
        t
        for t in trades
        if t["taker_outcome_side"] == "yes"
        and float(t["yes_price_dollars"]) <= 0.21
        and target
        <= dt.datetime.fromisoformat(t["created_time"].replace("Z", "+00:00")).timestamp()
        <= target + 1.85
    ]
    no_qty = sum(float(r["ledger"][0]["size"]) for r in exact)
    kqty = sum(float(r["count_fp"]) for r in kactual)
    replay = seal_and_replay(a.ws_root, folder, target, pair["poly_no"])
    old_no_ends = min(
        (
            v["received"]
            for v in ws["trace"]
            if v["outcome"].endswith(":No") and v["ask"] is not None and v["ask"] > 0.69
        ),
        default=None,
    )
    checks = dict(
        first_fresh_automatic_signal=bool(cases and folder.resolve() == cases[0].resolve()),
        registered_before_signal=reg["created"] < target,
        market_and_no_asset_match=arrival["books"]["poly"]["data"]["market"]
        == pair["market"]["conditionId"]
        and arrival["books"]["poly"]["data"]["asset_id"] == pair["poly_no"],
        effective_fee_agreement=fees["parameters_verified"]
        and fees["clob"]["fd"]["r"] == 0.05
        and fees["clob"]["fd"]["e"] == 1,
        both_normal_rules_90_minutes="90 minutes" in pair["market"]["description"]
        and "90 minutes" in (pair["kalshi"]["rules_primary"] + pair["kalshi"]["rules_secondary"]),
        post_delay_both_fok_available=arrival["execution"]["poly_fok_available"]
        and arrival["execution"]["kalshi_fok_available"],
        public_settled_old_price_no_volume_exceeds_order=no_qty >= 10.16,
        public_kalshi_yes_direction_volume_exceeds_order=kqty >= 10,
        ws_cheap_quote_persists_after_15=old_no_ends is not None and old_no_ends > target + 1.5,
        sealed_raw_replay_at_decision_and_entry=len(replay["samples"]) == 2
        and not replay["errors"]
        and all(
            s["valid"]
            and not s["dirty"]
            and not s["source_regressed"]
            and s["cash_budget_available"]
            for s in replay["samples"]
        ),
    )
    settlement_file = folder / "settlement_verified.json"
    settlement = json.loads(settlement_file.read_text()) if settlement_file.exists() else None
    settlement_sources = []
    if settlement:
        ctf_file = Path(settlement["poly_ctf"]["source_file"])
        native_file = Path(settlement["poly_data_api"]["source_file"])
        k_file = Path(settlement["kalshi"]["source_file"])
        ctf_rows = json.loads(ctf_file.read_text())
        native_rows = json.loads(native_file.read_text())["resolution"]["data"]["data"]
        k_market = json.loads(k_file.read_text())["data"]["market"]
        checks["terminal_payouts_confirmed_from_sources"] = (
            any(
                r.get("denominator") == 1
                and r.get("numerators") == [0, 1]
                and r.get("chain") == "0x89"
                for r in ctf_rows
            )
            and any(
                r.get("status") == "resolved" and r.get("payouts") == [0, 1000000]
                for r in native_rows
            )
            and k_market.get("result") == "no"
            and k_market.get("status") in ("finalized", "settled")
        )
        settlement_sources = [settlement_file, ctf_file, native_file, k_file]
    result = dict(
        claim="Specific historical executable-price advantage under normal completed-game payouts, not proof of long-run strategy returns or personal fills",
        checks=checks,
        signal_time_utc=dt.datetime.fromtimestamp(target, dt.UTC).isoformat(),
        independent_capture_registered=reg["created"],
        arrival_actual_delay=max(v["received"] for v in arrival["books"].values()) - target,
        old_quote_removed_received=old_no_ends,
        latency_window_seconds=old_no_ends - target if old_no_ends else None,
        economics=price_case(),
        settlement=settlement,
        public_poly_exact_fills=exact,
        public_poly_exact_volume=no_qty,
        public_poly_vwap_mismatches=mismatch,
        public_kalshi_yes_direction_fills=kactual,
        public_kalshi_yes_direction_volume=kqty,
        ordinary_payoff_table=[
            dict(result=r, poly_no=p, kalshi_yes=k, total=p + k)
            for r, p, k in [("Chelsea wins", 1, 0), ("Draw", 1, 0), ("ManchesterUnited wins", 0, 1)]
        ],
        limitations=[
            "No live orders by this research; public executions belong to unidentified market participants",
            "A full hour has three separate signal episodes; do not count five adjacent snapshots as five independent trades",
            "The2.5-second hedge failed; fee-negative or single-leg episodes remain in the source results",
            "Cancellation/fair-price, >48h rescheduling and official-final-data rules are not identical across venues",
            "Funding/FX/redemption costs depend on prefunded accounts;0.02USD is a reserve scenario, not a measured universal expense",
            "One demonstrated price window does not establish long-run opportunity frequency, capacity or sustainable average returns",
        ],
        primary_sources=[
            "https://kalshi.com/docs/kalshi-fee-schedule.pdf",
            "https://docs.kalshi.com/api-reference/market/get-trades",
            "https://docs.kalshi.com/api-reference/market/get-market-orderbook",
            "https://docs.polymarket.com/trading/fees",
            "https://docs.polymarket.com/trading/place-orders",
        ],
    )
    (folder / "verified_case.json").write_text(json.dumps(result, indent=2))
    files = [
        folder / "trigger.json",
        folder / "poly_no_kalshi_yes_1.5.json",
        folder / "ws_audit.json",
        folder / "settled_trades_pages.json",
        folder / "kalshi_trades.json",
        folder / "verified_case.json",
        folder / "sealed_ws_replay.json",
        folder / "poly_ws_prefix.jsonl.gz",
        folder / "poly_ws_prefix_coverage.json",
        a.root / "registration.json",
        a.root / "mappings.json",
        a.root / "fees.json",
        Path(__file__),
        Path(__file__).with_name("seal_crossvenue_ws.py"),
        Path(__file__).with_name("live_gzip.py"),
        Path(__file__).with_name("study.py"),
    ]
    files.extend(settlement_sources)
    (folder / "verification_manifest.json").write_text(
        json.dumps({str(f): hashlib.sha256(f.read_bytes()).hexdigest() for f in files}, indent=2)
    )
    print(
        json.dumps(
            dict(
                checks=checks,
                economics=result["economics"],
                public_poly_no_volume=no_qty,
                public_kalshi_yes_direction_volume=kqty,
                quote_window=result["latency_window_seconds"],
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

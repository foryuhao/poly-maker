"""Supplement finished SOP reports using official public GETs; never rewrite audits."""

import argparse
import asyncio
import copy
import csv
import hashlib
import json
import sqlite3
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from m1223_report import summary


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_orders(path):
    with path.open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    for row in rows:
        for field in ("primary_eligible", "has_positive_modelled_fill", "unresolved"):
            if row[field] not in ("True", "False"):
                raise ValueError("EXPLICIT_ORDER_BOOLEAN_REQUIRED:" + field)
            row[field] = row[field] == "True"
        for field in ("official_winner", "assumed_gross_pnl", "known_simulated_gross_pnl"):
            if row[field] == "":
                row[field] = None
    return rows


def supplement_orders(orders, resolutions):
    """Apply verified official payouts only, preserving quantities and uncertainty."""
    result = copy.deepcopy(orders)
    for row in result:
        resolution = resolutions.get(row["market"])
        if resolution is None:
            continue
        if resolution["slug"] != row["market"]:
            raise ValueError("SETTLEMENT_MARKET_IDENTITY_MISMATCH")
        tokens = resolution["token_ids"]
        if (
            set(tokens) != {"YES", "NO"}
            or len(set(tokens.values())) != 2
            or row["token"] not in tokens.values()
            or resolution["winner"] not in tokens
        ):
            raise ValueError("SETTLEMENT_TOKEN_IDENTITY_MISMATCH")
        if row["official_winner"] not in (None, resolution["winner"]):
            raise ValueError("CONFLICT_WITH_RECORDED_OFFICIAL_RESULT")
        p, q, f = (Decimal(row[k]) for k in ("price", "quantity", "known_filled_quantity"))
        if not all(v.is_finite() for v in (p, q, f)) or not (
            0 < p < 1 and q > 0 and 0 <= f <= q and p * q <= 5
        ):
            raise ValueError("ORIGINAL_QUANTITY_OR_BUDGET_INVALID")
        if row["has_positive_modelled_fill"] != (f > 0):
            raise ValueError("POSITIVE_FILL_FLAG_DISAGREES_WITH_ORIGINAL_QUANTITY")
        payout = Decimal(int(row["token"] == tokens[resolution["winner"]]))
        assumed, simulated = str(q * (payout - p)), str(f * (payout - p))
        for field, value in (
            ("assumed_gross_pnl", assumed),
            ("known_simulated_gross_pnl", simulated),
        ):
            if row[field] is not None and Decimal(row[field]) != Decimal(value):
                raise ValueError("CONFLICT_WITH_RECORDED_SETTLEMENT_PNL")
            row[field] = value
        row["official_winner"] = resolution["winner"]
    return result


def statistics(report, orders):
    return {
        scope: {
            ledger: summary(
                orders if scope == "all_recorded" else [r for r in orders if r["primary_eligible"]],
                report["known_raw_signal_count" if scope == "all_recorded" else "known_primary_signal_count"],
                ledger,
            )
            for ledger in ("assumed", "simulated")
        }
        for scope in ("all_recorded", "primary")
    }


def canonical_identity(database):
    from polymaker.m1234_sim.engine import market_meta

    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        payload = db.execute("SELECT payload FROM audit WHERE kind='DEFINITION'").fetchone()
    if payload is None:
        raise ValueError("FROZEN_MARKET_IDENTITY_UNAVAILABLE")
    meta = market_meta(json.loads(payload[0])["definition"]["market"])
    return {
        "slug": meta.slug,
        "condition_id": meta.condition_id,
        "token_ids": {"YES": meta.yes.token_id, "NO": meta.no.token_id},
    }


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def write_csv(path, rows):
    with path.open("w", newline="") as stream:
        fields = list(rows[0]) if rows else ["market", "order_id"]
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


async def fetch_official(identities):
    from polymaker.dashboard.settlement import validate_resolution
    from polymaker.m1_virtual_api import VirtualPublicAPI

    observations, failovers = [], []
    api = VirtualPublicAPI(on_failover=lambda *args: failovers.append(args))
    try:
        for market, identity in identities.items():
            observed = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
            raw = None
            try:
                raw = await api.raw_market(market, purpose="settlement")
                resolution = validate_resolution(raw, identity)
                observations.append({
                    "market": market, "observed_beijing": observed,
                    "identity": identity, "raw": raw,
                    "status": "RESOLVED" if resolution else "PENDING",
                    "resolution": resolution,
                })
            except Exception as exc:
                observations.append({
                    "market": market, "observed_beijing": observed, "identity": identity,
                    "status": "UNAVAILABLE", "error_type": type(exc).__name__, "raw": raw,
                })
    finally:
        await api.close()
    return observations, failovers


def closeout(report_path, output):
    report = json.loads(report_path.read_text())
    if not report.get("phase_complete") or report.get("run_status") != "PASS":
        raise ValueError("FINISHED_SOURCE_PHASE_REQUIRED")
    run = Path(report["run"])
    terminal = json.loads((run / "completion.json").read_text())
    if terminal.get("status") != "PASS" or terminal.get("reason") != "PLANNED_COMPLETE":
        raise ValueError("PLANNED_COMPLETE_SOURCE_REQUIRED")
    if sha256(run / "completion.json") != report["evidence"]["terminal_sha256"]:
        raise ValueError("SOURCE_TERMINAL_CHANGED")
    plan = json.loads((run / "plan.json").read_text())
    if (
        sha256(run / "plan.json") != report["evidence"]["plan_sha256"]
        or plan["run_identity"] != report["run_identity"]
        or plan["settings"]["sop"] != report["sop_definition"]
        or plan.get("mode") != "SIMULATION_ONLY"
        or plan.get("real_order_transport") != "ABSENT"
    ):
        raise ValueError("SOURCE_PLAN_OR_SIMULATION_IDENTITY_CHANGED")
    orders_path = report_path.parent / "orders.csv"
    orders = read_orders(orders_path)
    original = statistics(report, orders)
    if any(original[scope] != report[scope] for scope in original):
        raise ValueError("ORIGINAL_CSV_AND_SOURCE_STATISTICS_DISAGREE")
    sources = [report_path, orders_path, run / "plan.json", run / "completion.json"]
    market_sources = {m["market"]: m for m in report["markets"]}
    pending = sorted({row["market"] for row in orders if row["official_winner"] is None})
    for market in pending:
        m = market_sources[market]
        database = Path(m["source_db"])
        if (
            not database.resolve().is_relative_to(run.resolve() / market)
            or sha256(database) != m["database_sha256"]
            or m["status"] != "SEALED"
            or m["audit_validity"] != "VALID"
        ):
            raise ValueError("ORIGINAL_PENDING_MARKET_DATABASE_BINDING_FAILED")
        sources.append(database)
    before = {str(p.resolve()): sha256(p) for p in sources}
    output.mkdir(parents=True, exist_ok=False)
    identities = {
        market: canonical_identity(Path(market_sources[market]["source_db"])) for market in pending
    }
    if any(identity["slug"] != market for market, identity in identities.items()):
        raise ValueError("PENDING_MARKET_IDENTITY_MISMATCH")
    observations, failovers = asyncio.run(fetch_official(identities))
    resolutions = {}
    for observation in observations:
        market = observation["market"]
        raw = observation.pop("raw")
        if raw is not None:
            raw_path = output / (market + "-official-response.json")
            write_json(raw_path, raw)
            observation.update(raw_response=str(raw_path.resolve()), raw_response_sha256=sha256(raw_path))
        if observation["status"] == "RESOLVED":
            resolutions[market] = observation["resolution"]
    updated = supplement_orders(orders, resolutions)
    values = statistics(report, updated)
    if any(sha256(Path(p)) != value for p, value in before.items()):
        raise ValueError("SOURCE_CHANGED_DURING_READ_ONLY_CLOSEOUT")
    write_csv(output / "orders.csv", updated)
    buckets = [
        {"scope": scope, "ledger": ledger, **bucket}
        for scope in values for ledger in values[scope]
        for bucket in values[scope][ledger]["price_buckets"]
    ]
    write_csv(output / "price-buckets.csv", buckets)
    receipt = {
        "schema": "m1223-official-settlement-supplement-v1",
        "scope": "PAYOUT_SUPPLEMENT_ONLY_NOT_SIGNAL_OR_EXECUTION_INPUT_OR_ADMISSION",
        "status": "PARTIAL" if any(r["official_winner"] is None for r in updated) else "SETTLEMENT_COMPLETE",
        "source_report": str(report_path.resolve()), "source_sha256": before,
        "closeout_tool_sha256": sha256(Path(__file__)),
        "original_statistics": original, "statistics": values,
        "official_observations": observations, "route_failovers": failovers,
        "resolved_markets_since_source": sorted(resolutions),
        "original_sources_unchanged": True,
        "execution_uncertainty_unchanged": True,
        "reported_beijing": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
    }
    write_json(output / "report.json", receipt)
    def display(value):
        return "—" if value is None else str(value)
    lines = ["# 官方结算补充", "", receipt["scope"], "", receipt["status"], "",
             "| 口径 | 假设交易场/笔 | 待结算 | 假设毛PnL | 去最高盈利1场 | 去最高盈利3场 | 已知模拟成交/未决 | 已知模拟毛PnL |",
             "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for scope, value in values.items():
        a, m = value["assumed"], value["simulated"]
        lines.append(f"| {scope} | {a['trading_markets']}/{a['orders']} | {a['pending_settlement_orders']} | {display(a['settled_gross_pnl'])} | {display(a['drop_best_profitable_markets']['1']['settled_gross_pnl'])} | {display(a['drop_best_profitable_markets']['3']['settled_gross_pnl'])} | {m['orders']}/{m['unresolved_orders']} | {display(m['settled_gross_pnl'])} |")
    lines += ["", "该补充只更新官方结算：信号、SUBMIT群体、价格、数量、已知成交及执行未决不变；原报告、原始行情和审计未改写。未决不等于未成交，未决仍存在时已知模拟PnL不是完整执行PnL。没有正数模拟成交样本时PnL不可评估，不补零。", "",
              "假设账本为同一SUBMIT群体全额成交，不是所有信号。去最高盈利1/3场保留全部亏损场；待结算存在时，PnL与稳健性视图只覆盖已结算部分。毛PnL不含未经核实的费用/返佣。完整主/全记录、价格分桶、0.01–0.02贡献及官方响应见report.json、orders.csv、price-buckets.csv。", "",
              "原频次与输入质量限制及资源见源阶段报告；本文件不能替代独立验收或24小时准入。", ""]
    (output / "report.md").write_text("\n".join(lines))
    print(json.dumps({
        "status": receipt["status"], "resolved_markets": sorted(resolutions),
        "output": str(output.resolve()),
        "all_assumed_pending_orders": values["all_recorded"]["assumed"]["pending_settlement_orders"],
        "all_assumed_gross_pnl": values["all_recorded"]["assumed"]["settled_gross_pnl"],
        "primary_assumed_gross_pnl": values["primary"]["assumed"]["settled_gross_pnl"],
        "execution_unresolved_orders": values["all_recorded"]["simulated"]["unresolved_orders"],
    }, ensure_ascii=False))
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    closeout(args.report.resolve(), args.output.resolve())

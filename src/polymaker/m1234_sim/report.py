"""Read-only reports; evidence categories and counterfactual accounts stay separate."""

import json
from collections import Counter
from contextlib import nullcontext
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

from polymaker.m1234_sim import MODEL
from polymaker.m1234_sim.events import digest, write_json
from polymaker.m1234_sim.store import read_transaction
from polymaker.m1234_sim.validity import database_validity, simulation_validity


def read_state(path, *, connection=None):
    with (read_transaction(path) if connection is None else nullcontext(connection)) as db:
        row = db.execute("SELECT payload FROM snapshots WHERE name='state'").fetchone()
        if row is None:
            raise ValueError("SIM_SNAPSHOT_UNAVAILABLE")
        return json.loads(row[0])


def summary(path, horizons=(1, 5, 30)):
    with read_transaction(path) as db:
        return _summary(path, horizons, db)


def execution_version(definition, scenario):
    settings = definition["settings"]
    research = scenario.startswith("research/") and settings.get("m5_m11")
    primary = scenario.startswith("main/") and settings.get("m234_execution_main")
    base = scenario.split("/", 1)[1] if primary or research else scenario
    identity = {
        "implementation": settings.get("implementation"),
        "scenario": settings["scenarios"][base],
        "seed": settings["seed"],
    }
    if primary:
        identity["execution_policy"] = settings["m234_execution_main"]
    if research:
        identity["execution_policy"] = settings["m5_m11"]["execution_override"]
        if "fill_model" in settings["m5_m11"]:
            identity["fill_model"] = settings["m5_m11"]["fill_model"]
    return digest(identity)


def _summary(path, horizons, db):
    state = read_state(path, connection=db)
    rows, signals, quality_counts = [], [], Counter()
    event_counts = Counter(dict(db.execute('SELECT kind, COUNT(*) FROM audit GROUP BY kind')))
    archived = db.execute(
        "SELECT payload FROM metadata WHERE name='diagnostic_archive'"
    ).fetchone()
    diagnostic_archive = json.loads(archived[0]) if archived else None
    if diagnostic_archive:
        event_counts["SIGNAL"] += diagnostic_archive.get("signal_rows", diagnostic_archive["rows"])
        if research_rows := diagnostic_archive.get("research_rows", 0):
            event_counts["RESEARCH_EVIDENCE"] += research_rows
        for row in diagnostic_archive["quality"]:
            quality_counts[(row["strategy"], row["reason"])] += row["count"]
    failures = []
    # Do not deserialize hundreds of thousands of diagnostic payloads in the
    # Python report thread: its GIL contention can starve the next market's
    # capture consumer. SQLite aggregates diagnostics while releasing the GIL.
    # Preserve malformed-payload failure behavior even for unselected kinds.
    if db.execute('SELECT 1 FROM audit WHERE NOT json_valid(payload) LIMIT 1').fetchone():
        raise ValueError('SIM_REPORT_INVALID_AUDIT_JSON')
    for strategy, reason, count in db.execute("""
        SELECT json_extract(payload, '$.strategy'),
               CASE WHEN json_type(payload, '$.payload.reason') IS NULL
                    THEN '"UNSPECIFIED"'
                    ELSE payload -> '$.payload.reason' END AS reason,
               COUNT(*) FROM audit
        WHERE kind = 'SIGNAL' AND json_extract(payload, '$.event') IN
              ('UNAVAILABLE', 'INPUT_UNAVAILABLE', 'EVENT_IGNORED')
        GROUP BY 1, 2 ORDER BY MIN(seq)
    """):
        quality_counts[(strategy, str(json.loads(reason)))] += count
    selected = db.execute("""
        SELECT seq, input_seq, kind, payload FROM audit WHERE
        kind IN ('SIMULATION_INVALID', 'DEFINITION', 'MODELLED_FILL', 'SKIP', 'M1_ADMISSION')
        OR (kind = 'M1_POLICY' AND json_extract(payload, '$.event') = 'sm_m1_candidate_skipped')
        OR (kind = 'SIGNAL' AND json_extract(payload, '$.event') IN
            ('OPPORTUNITY_IDENTIFIED', 'M2_CANDIDATE', 'CANDIDATE')) ORDER BY seq
    """)
    for seq, input_seq, kind, raw in selected:
        row = {'seq': seq, 'input_seq': input_seq, 'kind': kind, 'payload': json.loads(raw)}
        kind, payload = row["kind"], row["payload"]
        if kind == "SIMULATION_INVALID":
            failures.append(payload["conflict"])
        if kind == "SIGNAL":
            event = payload.get("event")
            if event in {"OPPORTUNITY_IDENTIFIED", "M2_CANDIDATE", "CANDIDATE"}:
                signals.append(row)
        elif kind in {"DEFINITION", "MODELLED_FILL", "SKIP", "M1_ADMISSION"} or (
            kind == "M1_POLICY" and payload.get("event") == "sm_m1_candidate_skipped"
        ):
            rows.append(row)
    fills = [r for r in rows if r["kind"] == "MODELLED_FILL"]
    definition = next(r["payload"]["definition"] for r in rows if r["kind"] == "DEFINITION")
    accounts = []
    for scenario, broker in state["brokers"].items():
        for profile, account in broker["accounts"].items():
            orders = [o for o in broker["orders"].values() if o["intent"]["profile"] == profile]
            selected_fills = [
                r
                for r in fills
                if r["payload"]["scenario"] == scenario
                and r["payload"]["order"] in {o["id"] for o in orders}
            ]
            submitted = sum((Decimal(o["intent"]["quantity"]) for o in orders), Decimal(0))
            filled = sum((Decimal(o["filled"]) for o in orders), Decimal(0))
            unresolved = [o for o in orders if o["uncertain"]]
            cost = Decimal(account["spent"])
            winner = account["winner"]
            known_payout = Decimal(account["payout"]) if winner else None
            lower = (known_payout or Decimal(0)) - cost
            upper = (
                known_payout if winner else sum(map(Decimal, account["inventory"].values()))
            ) - cost
            for order in unresolved:
                q = Decimal(order["intent"]["quantity"]) - Decimal(order["filled"])
                p = Decimal(order["intent"]["price"])
                if winner:
                    possible = q * ((1 if order["intent"]["outcome"] == winner else 0) - p)
                    lower += min(Decimal(0), possible)
                    upper += max(Decimal(0), possible)
                else:
                    lower -= q * p
                    upper += q * (1 - p)
            markouts = []
            for fill in selected_fills:
                data = fill["payload"]
                order = broker["orders"][data["order"]]
                changes = {}
                for horizon in horizons:
                    target = data["at"] + horizon
                    future = next(
                        (
                            m
                            for m in state["marks"]
                            if m["token"] == order["intent"]["token"]
                            and target <= m["at"] <= target + 0.25
                        ),
                        None,
                    )
                    changes[str(horizon)] = (
                        {
                            "status": "AVAILABLE",
                            "change": str(Decimal(future["mid"]) - Decimal(data["price"])),
                            "observed_at": future["at"],
                        }
                        if future
                        else {"status": "MISSING_FUTURE_OR_MARKET_ENDED", "change": None}
                    )
                markouts.append(
                    {
                        "order": order["id"],
                        "fill_input": data["input"],
                        "quantity": data["quantity"],
                        "horizons": changes,
                    }
                )
            admissions = {}
            admission_commitment = None
            m1_skips = set()
            for row in rows:
                payload = row["payload"]
                if payload.get("scenario") != scenario or payload.get("profile") != profile:
                    continue
                if row["kind"] == "M1_ADMISSION":
                    admissions[payload["row"]["id"]] = payload["row"]
                    admission_commitment = payload["committed_usdc"]
                elif row["kind"] == "M1_POLICY" and payload["event"] == "sm_m1_candidate_skipped":
                    m1_skips.add(payload["payload"]["opportunity"]["episode_id"])
            m1_skips.update(
                k for k, v in admissions.items() if v["state"] in {"SKIPPED", "ABORTED"}
            )
            strategy = "M1" if profile in definition["settings"]["profiles"] else profile
            opportunity_events = {
                "M1": {"OPPORTUNITY_IDENTIFIED"},
                "M2": {"M2_CANDIDATE"},
                "M3": {"CANDIDATE"},
                "M4": {"CANDIDATE"},
            }.get(strategy, {"CANDIDATE"})
            opportunities = sum(
                1
                for r in signals
                if r["payload"].get("strategy") == strategy
                and r["payload"].get("event") in opportunity_events
            )
            first_wait = [
                o["first_fill"] - o["intent"]["available"]
                for o in orders
                if o["first_fill"] is not None
            ]
            full_wait = [
                o["last_fill"] - o["intent"]["available"]
                for o in orders
                if Decimal(o["filled"]) == Decimal(o["intent"]["quantity"])
            ]
            strata = Counter()
            for order in orders:
                price = Decimal(order["intent"]["price"])
                band = (
                    "<=.09"
                    if price <= Decimal(".09")
                    else "<=.19"
                    if price <= Decimal(".19")
                    else ">.19"
                )
                elapsed = order["intent"]["available"] - float(
                    state["market"]["slug"].rsplit("-", 1)[1]
                )
                # Monotonic time is not an epoch; remaining-time bins are computed
                # from the frozen official anchor in DEFINITION below.
                definition = next(
                    r["payload"]["definition"] for r in rows if r["kind"] == "DEFINITION"
                )
                server, _, anchor = definition["official_clock"]
                elapsed = (
                    server
                    + order["intent"]["available"]
                    - anchor
                    - int(state["market"]["slug"].rsplit("-", 1)[1])
                )
                strata[
                    f"{band}|remaining_{int((300 - elapsed) // 60) * 60}s|{order['intent']['outcome']}"
                ] += 1
            accounts.append(
                {
                    "profile": profile,
                    "strategy": strategy,
                    "scenario": scenario,
                    "execution_version": execution_version(definition, scenario),
                    "signal_settings_hash": digest(
                        definition["settings"]["profiles"][profile]
                        if profile in definition["settings"]["profiles"]
                        else definition["settings"]["m5_m11"] if scenario.startswith("research/")
                        else definition["settings"]["m234"]
                    ),
                    "evidence": "MODELLED_FILL",
                    "opportunities": opportunities,
                    "intents": len(orders),
                    "submissions": len(orders),
                    "effective": sum(o["live_at"] is not None for o in orders),
                    "partial": sum(
                        0 < Decimal(o["filled"]) < Decimal(o["intent"]["quantity"]) for o in orders
                    ),
                    "full": sum(
                        Decimal(o["filled"]) == Decimal(o["intent"]["quantity"]) for o in orders
                    ),
                    "unknown": sum(o["uncertain"] or o["client"] == "UNKNOWN" for o in orders),
                    "client_unknown": sum(o["client"] == "UNKNOWN" for o in orders),
                    "exchange_tail_unknown": len(unresolved),
                    "strategy_admission_committed_usdc": admission_commitment,
                    "rejected": sum(o["state"] == "REJECTED" for o in orders),
                    "unfilled": sum(Decimal(o["filled"]) == 0 for o in orders),
                    "skips": len(m1_skips)
                    + sum(
                        r["kind"] == "SKIP"
                        and r["payload"].get("profile") == profile
                        and r["payload"].get("scenario") == scenario
                        for r in rows
                    ),
                    "submitted_shares": str(submitted),
                    "filled_shares": str(filled),
                    "share_fill_ratio": str(filled / submitted) if submitted else None,
                    "first_fill_wait_seconds": first_wait,
                    "full_fill_wait_seconds": full_wait,
                    "cost_usdc": str(cost),
                    "reserved_usdc": account["reserved"],
                    "inventory": account["inventory"],
                    "cash_usdc": account["cash"],
                    "gross_pnl_usdc": str(known_payout - cost)
                    if winner and not unresolved
                    else None,
                    "conditional_known_fill_pnl_usdc": str(known_payout - cost) if winner else None,
                    "possible_pnl_range_usdc": [str(lower), str(upper)],
                    "pnl_status": "UNRESOLVED_ORDER_RISK"
                    if unresolved
                    else "SETTLED"
                    if winner
                    else "PENDING_OFFICIAL_RESULT",
                    "fee_status": "NOT_INCLUDED_GROSS_ONLY",
                    "rebate_usdc": "0",
                    "assumed_fill_comparison": {
                        "evidence": "ASSUMED_FILL",
                        "cost_usdc": str(
                            sum(
                                (
                                    Decimal(o["intent"]["price"]) * Decimal(o["intent"]["quantity"])
                                    for o in orders
                                ),
                                Decimal(0),
                            )
                        ),
                        "gross_pnl_usdc": str(
                            sum(
                                (
                                    Decimal(o["intent"]["quantity"])
                                    * (
                                        (1 if o["intent"]["outcome"] == winner else 0)
                                        - Decimal(o["intent"]["price"])
                                    )
                                    for o in orders
                                ),
                                Decimal(0),
                            )
                        )
                        if winner
                        else None,
                    },
                    "markouts": markouts,
                    "order_strata": dict(strata),
                    "denominators": {
                        "fill_ratio": "submitted shares",
                        "partial/full/unknown": "physical orders",
                        "wait": "orders with first/full fill",
                        "opportunities": "candidate roots",
                        "strata": "physical submissions",
                        "independence": "market, not signal",
                    },
                    "order_ids": [o["id"] for o in orders],
                }
            )
    validity = database_validity(path, state, failures, connection=db)
    eligible = qualify_accounts(accounts, validity)
    fill_models = {name: broker["fill_model"] for name, broker in state["brokers"].items()
                   if "fill_model" in broker}
    return {
        "status": "PASS" if eligible else "FAIL",
        "validity": validity,
        "mode": "SIMULATION_ONLY",
        "model": state["model"],
        **({"fill_models": fill_models} if fill_models else {}),
        "market": state["market"],
        "coverage": state["coverage"],
        "official_result": state["resolution"],
        "accounts": accounts,
        "source_db": str(Path(path).resolve()),
        "input_seq": state["input_seq"],
        "definition_hash": state["definition_hash"],
        "implementation_hash": digest(definition["settings"].get("implementation")),
        "calibration": "UNCALIBRATED",
        "fee_status": "NOT_INCLUDED_GROSS_ONLY",
        "limitations": [
            "independent counterfactual accounts; do not sum as a portfolio",
            "fixed per-market capital; no continuous recycling",
            "direct public trades only; complementary matching not modelled",
            "unverified maker fees/rebates excluded; gross PnL only",
            "no market impact assumed",
            ("trade-through evidence does not guarantee real execution"
             if fill_models and len(fill_models) == len(state["brokers"])
             else "trades-only is not a mathematical lower bound on real PnL"),
        ],
        "event_counts": dict(event_counts),
        "input_quality_diagnostics": [
            {
                "strategy": k[0],
                "reason": k[1],
                "count": v,
                "denominator": "recorded diagnostics, not distinct public frames",
            }
            for k, v in quality_counts.items()
        ],
        "trace": {
            "table": "audit",
            "join": "MODELLED_FILL.order -> SUBMIT.intent; input_seq -> recorded frame; input frame:subevent identity",
            **({"diagnostic_archive": diagnostic_archive} if diagnostic_archive else {}),
        },
    }


def qualify_accounts(accounts, validity):
    eligible = validity["status"] == "VALID"
    for account in accounts:
        account["performance_eligible"] = eligible
        account["sample_eligible"] = eligible
        if not eligible:
            fields = (
                "filled_shares", "share_fill_ratio", "first_fill_wait_seconds",
                "full_fill_wait_seconds", "cost_usdc", "reserved_usdc", "inventory",
                "cash_usdc", "gross_pnl_usdc", "conditional_known_fill_pnl_usdc",
                "possible_pnl_range_usdc", "assumed_fill_comparison", "markouts",
            )
            account.setdefault("diagnostic_invalid_values", {key: account[key] for key in fields})
            for key in fields:
                account[key] = None
            account["pnl_status"] = validity["status"]
    return eligible


def invalidated_report(report, validity, *, basis="Committed prefix with current engine invalidity"):
    value = deepcopy(report)
    value.update(status="FAIL", validity=deepcopy(validity),
                 diagnostic_basis=basis, sample_eligible=False, performance_eligible=False)
    qualify_accounts(value["accounts"], validity)
    return value


def publish(directory, reports, *, run_failure=None):
    directory = Path(directory)
    if (
        (directory / "completion.json").exists()
        or list(directory.glob("*.run-failure.json"))
        or ((directory / "plan.json").exists() and (directory / "latest.json").exists())
    ):
        from polymaker.m1234_sim.runstate import inspect_run

        terminal = inspect_run(directory)
        if terminal["status"] == "FAIL":
            cause = terminal["terminal"] or {}
            run_failure = {"run_schema": 1, "run_identity": terminal["run_identity"],
                           "status": "FAIL", "primary_error": cause.get("primary_error"),
                           "reason": cause.get("reason"),
                           "cleanup_errors": cause.get("cleanup_errors", []),
                           "evidence_errors": terminal["errors"],
                           "invalid_markets": terminal["invalid_markets"]}
    reports = [{**report, "validity": report.get("validity", simulation_validity({}))}
               for report in reports]
    value = {
        "status": ("PASS" if all(r["validity"]["status"] == "VALID" for r in reports)
                   else "FAIL") if reports else "PENDING",
        "mode": "SIMULATION_ONLY",
        "model": MODEL,
        "calibration": "UNCALIBRATED",
        "complete_windows": sum(r["coverage"]["complete_window"] for r in reports),
        "valid_complete_windows": sum(r["coverage"]["complete_window"]
                                      and r["validity"]["status"] == "VALID" for r in reports),
        "invalid_markets": [r["market"]["slug"] for r in reports
                            if r["validity"]["status"] != "VALID"],
        "markets": reports,
        "aggregate_pnl": None,
        "notice": "Independent counterfactual accounts; no portfolio sum. Gross only. Not calibrated to actual fills.",
    }
    if run_failure is not None:
        value.update(status="FAIL", run_failure=run_failure,
                     sample_eligible=False, performance_eligible=False)
    write_json(directory / "latest.json", value)
    lines = [
        "# M1234 交易模拟研究",
        "",
        value["notice"],
        "",
        f"有效完整窗口 {value['valid_complete_windows']} / {len(reports)}，观察窗口标志数 {value['complete_windows']}。缺口单独记录。",
        "",
        "| 市场 | 有效性 | 完整窗口 | 缺口 | 官方结果 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for report in reports:
        c = report["coverage"]
        lines.append(
            f"| {report['market']['slug']} | {report['validity']['status']} | {c['complete_window']} | {len(c['gaps'])} | {(report['official_result'] or {}).get('winner', '待定')} |"
        )
        for failure in report["validity"]["failures"]:
            lines.append(f"失效原因 {failure['reason']}，输入 {failure.get('input_seq')}，订单 {failure.get('order')}。")
    lines += [
        "",
        "| 市场 / profile / 情景 | 机会 | 提交 | 部分/全部成交 | 未知 | 成本 | 预留 | 毛收益 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for report in reports:
        for a in report["accounts"]:
            lines.append(
                f"| {report['market']['slug']} / {a['profile']} / {a['scenario']} | {a['opportunities']} | {a['submissions']} | {a['partial']}/{a['full']} | {a['unknown']} | {a['cost_usdc']} | {a['reserved_usdc']} | {a['gross_pnl_usdc'] if a['gross_pnl_usdc'] is not None else a['pnl_status']} |"
            )
    lines += [
        "",
        "份数、等待时间、价格/剩余时间/方向分层和逐笔 markout 见 latest.json。未来行情缺失保留 null；markout 不计入最终收益。",
        "",
        "ASSUMED_FILL 对照、MODELLED_FILL 主账和真实成交校准数据保持独立。样本按市场聚类；本报告不做显著性或盈利结论。",
    ]
    (directory / "report.md").write_text("\n".join(lines) + "\n")
    return value

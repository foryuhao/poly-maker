"""Independent instant-fill signal-validation ledger over committed simulator SUBMITs.

No exchange connection, source writes, signal recomputation, or execution feedback.
Model time is the original SUBMIT time; publication waits for source audit commits.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import signal
import sqlite3
import subprocess
import time
from contextlib import closing
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from polymaker.dashboard import settlement
from polymaker.dashboard.settlement import validate_resolution

MODEL = "m234-instant-fill-main-v1"
STRATEGIES = ("M2", "M3", "M4")
SCENARIOS = ("main/trades-only", "main/cancel-half", "trades-only", "cancel-half")
ZERO = Decimal(0)
KINDS = (
    "DEFINITION",
    "ACCOUNT_OPEN",
    "SUBMIT",
    "MODELLED_FILL",
    "ORDER_UNCERTAIN",
    "UNKNOWN_TAIL",
    "OFFICIAL_RESULT",
    "SIMULATION_INVALID",
    "OBSERVATION_END",
)
QUERY = (
    "SELECT seq,input_seq,kind,payload FROM audit WHERE seq>? AND seq<=? AND "
    "(kind IN (" + ",".join("?" for _ in KINDS) + ") OR (kind='SIGNAL' AND "
    "json_extract(payload,'$.strategy') IN ('M2','M3','M4') AND "
    "json_extract(payload,'$.event') IN ('M2_CANDIDATE','CANDIDATE'))) ORDER BY seq"
)


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def atomic_json(path, value):
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(encode(value) + "\n")
    temp.replace(path)


def number(value):
    n = Decimal(str(value))
    if not n.is_finite():
        raise ValueError("NONFINITE_AMOUNT")
    return n


def readonly(path):
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
    db.execute("PRAGMA query_only=ON")
    db.execute("BEGIN")
    return db


def new_market(binding, strategies):
    return {
        "binding": binding,
        "seq": 0,
        "input_seq": 0,
        "invalid": False,
        "complete_window": False,
        "winner": None,
        "resolution_seq": None,
        "meta": None,
        "initial": {},
        "orders": {},
        "queue_orders": {},
        "ignored_orders": [],
        "signals": {s: [] for s in strategies},
    }


class InstantFill:
    report_heading = "M234 发单即成交：信号验证"
    execution_column = "排队情景"
    execution_description = "假设成交与排队模拟成交独立；仅为毛收益，尚未结算的订单不计入已结算收益。"

    def scenarios_for(self, plan):
        return SCENARIOS

    def strategies_for(self, plan):
        return STRATEGIES

    def __init__(self, source, output, scenario=None):
        self.source, self.output = Path(source).resolve(), Path(output).resolve()
        if (
            self.source == self.output
            or self.source in self.output.parents
            or self.output in self.source.parents
        ):
            raise ValueError("BASELINE_OUTPUT_MUST_BE_SEPARATE_FROM_SOURCE")
        self.source_plan = json.loads((self.source / "plan.json").read_text())
        if (
            self.source_plan.get("mode") != "SIMULATION_ONLY"
            or self.source_plan.get("real_order_transport") != "ABSENT"
        ):
            raise ValueError("BASELINE_REQUIRES_SIMULATION_ONLY_SOURCE")
        self.scenarios = self.scenarios_for(self.source_plan)
        scenario = scenario or self.scenarios[0]
        if scenario not in self.scenarios:
            raise ValueError("UNSUPPORTED_SUBMISSION_SOURCE")
        self.scenario = scenario
        self.strategies = self.strategies_for(self.source_plan)
        self.output.mkdir(parents=True, exist_ok=True)
        self.lock = (self.output / "writer.lock").open("a+")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock.close()
            raise ValueError("BASELINE_WRITER_ALREADY_RUNNING") from None
        self.identity = {
            "model": MODEL,
            "source_run": str(self.source),
            "source_run_identity": self.source_plan["run_identity"],
            "submission_source_scenario": scenario,
            "implementation_hash": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "settlement_validator_hash": hashlib.sha256(
                Path(settlement.__file__).read_bytes()
            ).hexdigest(),
        }
        try:
            path = self.output / "plan.json"
            if path.exists():
                if json.loads(path.read_text())["identity"] != self.identity:
                    raise ValueError("BASELINE_IDENTITY_MISMATCH_USE_NEW_OUTPUT")
            else:
                atomic_json(
                    path,
                    {
                        "identity": self.identity,
                        "created_at": time.time(),
                        "mode": "SIMULATION_ONLY",
                        "real_orders": 0,
                        "strategies": list(self.strategies),
                        "evidence": "ASSUMED_FILL",
                        "cohort": "ADMITTED_SUBMISSIONS_NOT_ALL_CANDIDATES",
                        "account_scope": "INDEPENDENT_PER_MARKET_AND_STRATEGY",
                        "fee_status": "NOT_INCLUDED_GROSS_ONLY",
                        "timing": "FILL_AT_SOURCE_SUBMIT_TIME_PUBLISH_AFTER_COMMIT",
                    },
                )
            self.db = sqlite3.connect(self.output / "ledger.sqlite3", timeout=2)
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.executescript("""
              CREATE TABLE IF NOT EXISTS markets(slug TEXT PRIMARY KEY,payload TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS audit(
                seq INTEGER PRIMARY KEY,market TEXT NOT NULL,source_seq INTEGER NOT NULL,
                input_seq INTEGER NOT NULL,kind TEXT NOT NULL,payload TEXT NOT NULL,
                UNIQUE(market,source_seq,kind));
            """)
        except Exception:
            self.lock.close()
            raise

    def close(self):
        self.db.close()
        self.lock.close()

    def emit(self, slug, row, kind, payload):
        seq, input_seq = row[:2]
        self.db.execute(
            "INSERT INTO audit(market,source_seq,input_seq,kind,payload) VALUES(?,?,?,?,?)",
            (slug, seq, input_seq, kind, encode(payload)),
        )

    def apply(self, slug, state, row):
        seq, _, kind, raw = row
        p = json.loads(raw)
        if kind == "DEFINITION":
            meta = p["definition"]["market"]
            if meta["slug"] != slug:
                raise ValueError("DEFINITION_MARKET_MISMATCH")
            state["meta"] = meta
        elif kind == "SIGNAL":
            s, event, detail = p["strategy"], p["event"], p["payload"]
            if s not in self.strategies or event != ("M2_CANDIDATE" if s == "M2" else "CANDIDATE"):
                return
            key = encode(
                [
                    detail.get("generation"),
                    detail.get("candidate_id") if s == "M2" else detail["id"],
                ]
            )
            if key not in state["signals"][s]:
                state["signals"][s].append(key)
        elif (
            kind == "ACCOUNT_OPEN"
            and p.get("scenario") == self.scenario
            and p["profile"] in self.strategies
        ):
            if p["profile"] in state["initial"] or number(p["initial_cash"]) < 0:
                raise ValueError("INVALID_ACCOUNT_OPEN")
            state["initial"][p["profile"]] = str(number(p["initial_cash"]))
            self.emit(
                slug,
                row,
                "ACCOUNT_OPEN",
                {"strategy": p["profile"], "initial_cash": p["initial_cash"], "at": p["at"]},
            )
        elif kind == "SUBMIT":
            intent = p["intent"]
            s, scenario = intent["strategy"], p["scenario"]
            key = encode([scenario, p["order"]])
            if s not in self.strategies or scenario not in self.scenarios:
                state["ignored_orders"].append(key)
                return
            if key in state["queue_orders"]:
                raise ValueError("DUPLICATE_SOURCE_SUBMIT")
            price, quantity = number(intent["price"]), number(intent["quantity"])
            if (
                intent["market"] != slug
                or intent["profile"] != s
                or intent["side"] != "BUY"
                or intent["outcome"] not in ("YES", "NO")
                or not 0 < price < 1
                or quantity <= 0
                or number(p["reserve"]) != price * quantity
            ):
                raise ValueError("INVALID_SOURCE_SUBMIT")
            tokens = state["meta"]["tokens"]
            if intent["token"] != tokens[0 if intent["outcome"] == "YES" else 1]["token_id"]:
                raise ValueError("SOURCE_ORDER_TOKEN_MISMATCH")
            state["queue_orders"][key] = {
                "strategy": s,
                "scenario": scenario,
                "order": p["order"],
                "price": str(price),
                "quantity": str(quantity),
                "outcome": intent["outcome"],
                "filled": "0",
                "unresolved": False,
            }
            if scenario != self.scenario:
                return
            if state["winner"] is not None:
                raise ValueError("SUBMIT_AFTER_OFFICIAL_SETTLEMENT")
            spent = sum(
                (number(o["cost"]) for o in state["orders"].values() if o["strategy"] == s), ZERO
            )
            if s not in state["initial"] or spent + price * quantity > number(state["initial"][s]):
                raise ValueError("BASELINE_BUDGET_EXCEEDED")
            order = {
                "order": p["order"],
                "strategy": s,
                "outcome": intent["outcome"],
                "token": intent["token"],
                "price": str(price),
                "quantity": str(quantity),
                "filled": str(quantity),
                "cost": str(price * quantity),
                "submit_at": p["at"],
                "fill_at": p["at"],
                "wait_seconds": 0,
                "evidence": "ASSUMED_FILL",
                "opportunity": intent["opportunity"],
                "source_seq": seq,
                "source_input_seq": row[1],
                "source_scenario": scenario,
            }
            state["orders"][p["order"]] = order
            self.emit(slug, row, "ASSUMED_FILL", order)
        elif kind in ("MODELLED_FILL", "ORDER_UNCERTAIN", "UNKNOWN_TAIL"):
            key = encode([p["scenario"], p["order"]])
            if key not in state["queue_orders"]:
                if key in state["ignored_orders"]:
                    return  # M1 orders are deliberately outside this mode.
                raise ValueError("UNJOINED_SOURCE_ORDER_EVENT")
            order = state["queue_orders"][key]
            if kind == "MODELLED_FILL":
                qty = number(p["quantity"])
                if qty <= 0 or number(order["filled"]) + qty > number(order["quantity"]):
                    raise ValueError("INVALID_SOURCE_MODELLED_FILL")
                order["filled"] = str(number(order["filled"]) + qty)
                if p["scenario"] == "research/trade-through" and number(order["filled"]) == number(order["quantity"]):
                    order["unresolved"] = False
            else:
                order["unresolved"] = True
        elif kind == "OFFICIAL_RESULT" and p.get("result") is not None:
            meta = state["meta"]
            if meta is None:
                raise ValueError("MISSING_MARKET_IDENTITY")
            identity = {
                "slug": slug,
                "condition_id": meta["condition_id"],
                "token_ids": {
                    "YES": meta["tokens"][0]["token_id"],
                    "NO": meta["tokens"][1]["token_id"],
                },
            }
            result = (
                validate_resolution(p["raw"], identity) if isinstance(p.get("raw"), dict) else None
            )
            if not result or result["winner"] != p["result"]["winner"]:
                raise ValueError("UNVERIFIED_OFFICIAL_SETTLEMENT")
            winner = result["winner"]
            if state["winner"] not in (None, winner):
                raise ValueError("OFFICIAL_SETTLEMENT_CONFLICT")
            if state["winner"] is None:
                state.update(winner=winner, resolution_seq=seq)
                result["observed_at"] = p["result"].get("observed_at")
                self.emit(
                    slug,
                    row,
                    "OFFICIAL_SETTLEMENT",
                    {
                        "winner": winner,
                        "result": result,
                        "raw_hash": hashlib.sha256(encode(p["raw"]).encode()).hexdigest(),
                    },
                )
        elif kind == "SIMULATION_INVALID":
            state["invalid"] = True
            self.emit(slug, row, "SOURCE_INVALID", p)
        elif kind == "OBSERVATION_END":
            state["complete_window"] = bool(p["coverage"]["complete_window"])

    def scan(self, path):
        slug = path.parent.name
        saved = self.db.execute("SELECT payload FROM markets WHERE slug=?", (slug,)).fetchone()
        with closing(readonly(path)) as source:
            meta = {
                k: json.loads(v) for k, v in source.execute("SELECT name,payload FROM metadata")
            }
            if meta["run_owner"] != self.identity["source_run_identity"]:
                raise ValueError("SOURCE_RUN_IDENTITY_MISMATCH")
            binding = meta["identity"]
            tracker = json.loads(
                (path.parent / (binding["database_id"] + ".validity.json")).read_text()
            )
            if (
                tracker.get("evidence_schema") != 1
                or tracker["binding"] != binding
                or tracker["validity"]["status"] not in ("TRACKING", "INVALID")
            ):
                raise ValueError("SOURCE_VALIDITY_UNVERIFIED")
            state = json.loads(saved[0]) if saved else new_market(binding, self.strategies)
            if state["binding"] != binding:
                raise ValueError("SOURCE_DATABASE_REPLACED")
            high, input_seq = source.execute(
                "SELECT seq,input_seq FROM audit ORDER BY seq DESC LIMIT 1"
            ).fetchone() or (0, 0)
            if high < state["seq"]:
                raise ValueError("SOURCE_AUDIT_REGRESSED")
            with self.db:
                for row in source.execute(QUERY, (state["seq"], high, *KINDS)):
                    self.apply(slug, state, row)
                state.update(
                    seq=high,
                    input_seq=input_seq,
                    invalid=state["invalid"] or tracker["validity"]["status"] == "INVALID",
                )
                self.db.execute("INSERT OR REPLACE INTO markets VALUES(?,?)", (slug, encode(state)))
        return state

    def refresh(self):
        if (
            hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
            != self.identity["implementation_hash"]
        ):
            raise ValueError("BASELINE_IMPLEMENTATION_CHANGED")
        current = json.loads((self.source / "plan.json").read_text())
        if current["run_identity"] != self.identity["source_run_identity"]:
            raise ValueError("SOURCE_RUN_IDENTITY_CHANGED")
        if (
            hashlib.sha256(Path(settlement.__file__).read_bytes()).hexdigest()
            != self.identity["settlement_validator_hash"]
        ):
            raise ValueError("SETTLEMENT_VALIDATOR_CHANGED")
        states, errors = {}, []
        paths = sorted(self.source.glob("*/simulation.sqlite3"))
        seen = {p.parent.name for p in paths}
        for (slug,) in self.db.execute("SELECT slug FROM markets"):
            if slug not in seen:
                errors.append({"market": slug, "error": "SOURCE_DATABASE_MISSING"})
        for path in paths:
            try:
                states[path.parent.name] = self.scan(path)
            except (
                OSError,
                ValueError,
                KeyError,
                TypeError,
                sqlite3.Error,
                ArithmeticError,
            ) as exc:
                errors.append(
                    {"market": path.parent.name, "error": type(exc).__name__ + ":" + str(exc)}
                )
        terminal = None
        if (self.source / "completion.json").exists():
            terminal = json.loads((self.source / "completion.json").read_text())
            if terminal.get("run_identity") != self.identity["source_run_identity"]:
                raise ValueError("SOURCE_TERMINAL_IDENTITY_MISMATCH")
            for slug in terminal.get("invalid_markets", []):
                if slug in states:
                    states[slug]["invalid"] = True
        report = self.report(states, errors, terminal)
        atomic_json(self.output / "latest.json", report)
        self.publish_text(report)
        return report

    def report(self, states, errors, terminal):
        excluded = [k for k, v in states.items() if v["invalid"]]
        good = {k: v for k, v in states.items() if not v["invalid"]}
        totals, comparisons, accounts = {}, {}, []
        for s in self.strategies:
            total = {
                "signals": 0,
                "submitted_orders": 0,
                "assumed_filled_orders": 0,
                "assumed_filled_shares": ZERO,
                "cost_usdc": ZERO,
                "settled_orders": 0,
                "pending_settlement_orders": 0,
                "settled_cost_usdc": ZERO,
                "settled_gross_pnl_usdc": ZERO,
                "settled_wins": 0,
            }
            for slug, state in good.items():
                orders = [o for o in state["orders"].values() if o["strategy"] == s]
                cost = sum((number(o["cost"]) for o in orders), ZERO)
                inventory = {
                    side: sum((number(o["quantity"]) for o in orders if o["outcome"] == side), ZERO)
                    for side in ("YES", "NO")
                }
                winner = state["winner"]
                payout = inventory[winner] if winner else ZERO
                initial = number(state["initial"].get(s, "0"))
                if cost > initial:
                    raise ValueError("BASELINE_ACCOUNT_NEGATIVE")
                total["signals"] += len(state["signals"][s])
                total["submitted_orders"] += len(orders)
                total["assumed_filled_orders"] += len(orders)
                total["assumed_filled_shares"] += sum(inventory.values(), ZERO)
                total["cost_usdc"] += cost
                if winner:
                    total["settled_orders"] += len(orders)
                    total["settled_cost_usdc"] += cost
                    total["settled_gross_pnl_usdc"] += payout - cost
                    total["settled_wins"] += sum(o["outcome"] == winner for o in orders)
                else:
                    total["pending_settlement_orders"] += len(orders)
                accounts.append(
                    {
                        "market": slug,
                        "strategy": s,
                        "evidence": "ASSUMED_FILL",
                        "initial_cash": str(initial),
                        "cash": str(initial - cost + payout),
                        "spent": str(cost),
                        "payout": str(payout),
                        "reserved": "0",
                        "inventory": {k: str(v) for k, v in inventory.items()},
                        "inventory_status": "SETTLED_HISTORICAL" if winner else "OPEN",
                        "winner": winner,
                        "gross_pnl": str(payout - cost) if winner else None,
                        "complete_window": state["complete_window"],
                        "orders": len(orders),
                    }
                )
            totals[s] = {k: str(v) if isinstance(v, Decimal) else v for k, v in total.items()}
            totals[s]["settled_win_rate"] = (
                str(Decimal(total["settled_wins"]) / total["settled_orders"])
                if total["settled_orders"]
                else None
            )
            totals[s]["settled_gross_roi"] = (
                str(total["settled_gross_pnl_usdc"] / total["settled_cost_usdc"])
                if total["settled_cost_usdc"]
                else None
            )
            comparisons[s] = {}
            for scenario in self.scenarios:
                rows = [
                    (st, o)
                    for st in good.values()
                    for o in st["queue_orders"].values()
                    if o["strategy"] == s and o["scenario"] == scenario
                ]
                pnl = sum(
                    (
                        number(o["filled"])
                        * ((1 if o["outcome"] == st["winner"] else 0) - number(o["price"]))
                        for st, o in rows
                        if st["winner"]
                    ),
                    ZERO,
                )
                unknown = sum(o["unresolved"] for _, o in rows)
                comparisons[s][scenario] = {
                    "submitted_orders": len(rows),
                    "filled_orders": sum(number(o["filled"]) > 0 for _, o in rows),
                    "filled_shares": str(sum((number(o["filled"]) for _, o in rows), ZERO)),
                    "unresolved_orders": unknown,
                    "conditional_known_settled_pnl_usdc": str(pnl),
                    "settled_gross_pnl_usdc": None if unknown else str(pnl),
                    "evidence": "MODELLED_FILL",
                }
        return {
            "model": MODEL,
            "mode": "SIGNAL_VALIDATION",
            "evidence": "ASSUMED_FILL",
            "status": "PARTIAL"
            if errors or excluded or (terminal and terminal.get("status") != "PASS")
            else "OK"
            if states
            else "UNAVAILABLE",
            "observed_at": time.time(),
            "source_run": str(self.source),
            "source_identity": self.identity["source_run_identity"],
            "source_scenario": self.scenario,
            "source_terminal": {k: terminal.get(k) for k in ("status", "reason")}
            if terminal
            else None,
            "scope": "CURRENT_RUN_COMMITTED_AUDIT_EXCLUDING_INVALID_MARKETS",
            "market_databases": len(states),
            "counted_markets": len(good),
            "excluded_invalid_markets": excluded,
            "errors": errors,
            "strategies": totals,
            "execution_comparison": comparisons,
            "accounts": accounts,
            "sources": [
                {
                    "market": slug,
                    "audit_seq": st["seq"],
                    "input_seq": st["input_seq"],
                    "database_id": st["binding"]["database_id"],
                    "invalid": st["invalid"],
                }
                for slug, st in states.items()
            ],
            "definitions": {
                "signals": "unique candidate per market and strategy; not multiplied by scenario",
                "assumed_filled_orders": "each admitted source SUBMIT filled once in full at its original time, price and quantity",
                "population": "admitted source submissions; not all candidate signals and not a replay of old study admission",
                "timing": "model fill is immediate; displayed data follows committed source audit",
                "fees": "NOT_INCLUDED_GROSS_ONLY",
                "settlement": "verified official result only; unresolved outcomes stay pending",
                "feedback": "fixed source submission cohort; assumed fills do not change source policy or future source orders",
                "independence": "per-market, per-strategy accounts; alternatives must not be added as one portfolio",
            },
            "real_orders": 0,
        }

    def publish_text(self, report):
        lines = [
            "# " + self.report_heading,
            "",
            "更新时间："
            + datetime.fromtimestamp(report["observed_at"], ZoneInfo("Asia/Shanghai")).isoformat(),
            f"状态：{report['status']}；本轮有效统计市场：{report['counted_markets']}。",
            "沿用通过准入的发单，在模型时间立即按原价和原数量全部假设成交，持有至官方结算。",
            self.execution_description,
            "",
            "| 策略 | 信号 | 发单 / 假设成交 | 已结算 | 待结算 | 已结算毛收益 USDC |",
            "|---|---:|---:|---:|---:|---:|",
        ]
        for s, row in report["strategies"].items():
            lines.append(
                f"| {s} | {row['signals']} | {row['submitted_orders']} / {row['assumed_filled_orders']} | {row['settled_orders']} | {row['pending_settlement_orders']} | {row['settled_gross_pnl_usdc']} |"
            )
        lines += [
            "",
            "| 策略 | " + self.execution_column + " | 发单 | 已确认模拟成交 | 未决 |",
            "|---|---|---:|---:|---:|",
        ]
        for s, scenarios in report["execution_comparison"].items():
            for name, row in scenarios.items():
                lines.append(
                    f"| {s} | {name} | {row['submitted_orders']} | {row['filled_orders']} | {row['unresolved_orders']} |"
                )
        if report["excluded_invalid_markets"] or report["errors"]:
            lines += ["", "存在排除市场或读取缺项，以上为可用部分，不能作为完整本轮结果。"]
        temp = self.output / "report.md.tmp"
        temp.write_text("\n".join(lines) + "\n")
        temp.replace(self.output / "report.md")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-scenario", choices=SCENARIOS)
    parser.add_argument("--follow", action="store_true")
    parser.add_argument("--interval", type=float, default=15)
    parser.add_argument("--source-unit")
    args = parser.parse_args()
    if not 1 <= args.interval <= 300:
        parser.error("interval must be between 1 and 300 seconds")
    stopped = False

    def stop(*_):
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    ledger = InstantFill(args.source_run, args.output, args.source_scenario)
    try:
        while not stopped:
            report = ledger.refresh()
            print(
                encode(
                    {
                        k: report[k]
                        for k in (
                            "model",
                            "status",
                            "observed_at",
                            "counted_markets",
                            "strategies",
                            "errors",
                        )
                    }
                ),
                flush=True,
            )
            if not args.follow or report["source_terminal"]:
                break
            if args.source_unit:
                check = subprocess.run(
                    ["systemctl", "--user", "is-active", args.source_unit],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                if check.stdout.strip() not in ("active", "activating"):
                    # The service can finish between refresh() and is-active.
                    report = ledger.refresh()
                    if report["source_terminal"]:
                        break
                    report["status"] = "PARTIAL"
                    report["errors"].append({"error": "SOURCE_SERVICE_STOPPED"})
                    atomic_json(ledger.output / "latest.json", report)
                    ledger.publish_text(report)
                    break
            if (
                time.time()
                > ledger.source_plan["started_utc_epoch"] + ledger.source_plan["max_seconds"] + 600
            ):
                report["status"] = "PARTIAL"
                report["errors"].append({"error": "SOURCE_RUN_DEADLINE_EXCEEDED"})
                atomic_json(ledger.output / "latest.json", report)
                ledger.publish_text(report)
                break
            for _ in range(max(1, int(args.interval * 5))):
                if stopped:
                    break
                time.sleep(0.2)
    finally:
        ledger.close()


if __name__ == "__main__":
    main()

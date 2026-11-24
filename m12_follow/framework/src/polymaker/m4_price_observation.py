"""Prospective signed-price cohort; owned saved evidence only, no network/orders."""
from __future__ import annotations

import fcntl
import json
import sqlite3
import time
from collections import defaultdict
from contextlib import closing
from datetime import datetime
from decimal import Decimal as D
from pathlib import Path
from zoneinfo import ZoneInfo

from polymaker.dashboard.settlement import validate_resolution
from polymaker.m4_control import atomic_json

STATE_KEY = "price_band_observation"
BJ = ZoneInfo("Asia/Shanghai")


def _read(path):
    return json.loads(Path(path).read_text())


def _number(value):
    value = D(str(value))
    if not value.is_finite():
        raise ValueError("OBSERVATION_NONFINITE_AMOUNT")
    return value


def _in_band(order, plan):
    return (_number(plan["minimum_inclusive"]) <= _number(order["proof"]["order"]["price"])
            < _number(plan["maximum_exclusive"]))


def read_signal_times(project, snapshot, plan):
    """Read only matching owned ledgers and bounded same-session journal bytes."""
    root = Path(project).resolve() / "research_outputs/m4_live"
    low = int(plan["started_at"] // 300) * 300
    times, gaps = {}, []
    for market in snapshot["markets"]:
        if market["epoch"] < low:
            continue
        orders = [o for o in market["confirmed_orders"] if _in_band(o, plan)]
        if not orders:
            continue
        path = root / "ledgers" / (market["expected"]["slug"] + ".sqlite3")
        if path.is_symlink() or path.resolve() != Path(market["ledger_path"]):
            raise ValueError("OBSERVATION_LEDGER_PATH_MISMATCH")
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
            meta = {r["key"]: json.loads(r["value"]) for r in db.execute("SELECT * FROM metadata")}
            if (meta["identity"]["mode"] != "LIVE"
                    or meta["identity"]["funder"].lower() != "0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d"
                    or meta["settings"]["execution_revision"] != "m4-taker-budget-v1"
                    or meta["market"]["slug"] != market["expected"]["slug"]
                    or meta["market"]["condition_id"].lower() != market["expected"]["condition_id"].lower()):
                raise ValueError("OBSERVATION_EXECUTOR_IDENTITY_MISMATCH")
            for order in orders:
                row = db.execute("SELECT * FROM taker_attempts WHERE order_id=?", (order["order_id"],)).fetchone()
                if (row is None or row["task_id"] != order["task_id"] or not row["started"]
                        or row["state"] != "TERMINAL"
                        or _number(json.loads(row["quote"])["price"]) != _number(order["proof"]["order"]["price"])
                        or _number(row["filled"]) != _number(order["quantity"])
                        or _number(row["spent"]) != _number(order["all_in_usdc"])):
                    raise ValueError("OBSERVATION_SIGNED_ORDER_EVIDENCE_MISMATCH")
        wanted = {o["task_id"] for o in orders}
        journals = sorted((root / "journal").glob("*_" + meta["session_id"] + ".jsonl"))
        if len(journals) != 1 or journals[0].is_symlink():
            gaps.append({"epoch": market["epoch"], "reason": "SIGNAL_JOURNAL_UNAVAILABLE"})
            continue
        with journals[0].open("rb") as stream:
            boundary = journals[0].stat().st_size
            data = stream.read(boundary)
        found = set()
        for line in data.splitlines(keepends=True):
            if not line.endswith(b"\n"):
                continue
            record = json.loads(line)
            payload = record.get("data", {}).get("payload", {})
            if record.get("kind") != "sm_m4_signal" or payload.get("event") != "OPPORTUNITY_IDENTIFIED":
                continue
            task = payload["opportunity"]["episode_id"]
            if task in wanted:
                key = (market["epoch"], task)
                wall = float(record["ts"])
                if key in times and times[key]["wall"] != wall:
                    raise ValueError("OBSERVATION_CONFLICTING_SIGNAL_CLOCK")
                times[key] = {"wall": wall, "journal": str(journals[0]), "journal_bytes": boundary}
                found.add(task)
        for task in wanted - found:
            gaps.append({"epoch": market["epoch"], "task_id": task, "reason": "SIGNAL_CLOCK_UNAVAILABLE"})
    return times, gaps


def build_observation(snapshot, official, plan, signal_times, *, cached_official=None, timing_gaps=()):
    """One sample per newly identified task; only qualifying fills contribute PnL."""
    if (snapshot["read_mode"] != "mode=ro,query_only,BEGIN"
            or snapshot["production_ledger_writes"] or snapshot["account_trade_or_position_queries"]
            or snapshot["service_actions"] or snapshot["order_actions"]):
        raise ValueError("OBSERVATION_SOURCE_SCOPE_INVALID")
    results = {r["epoch"]: r for r in official["markets"]}
    if len(results) != len(official["markets"]):
        raise ValueError("OBSERVATION_DUPLICATE_OFFICIAL_RESULT")
    cached_official = cached_official or {}
    groups, resolution_cache = {}, {}
    errors, reused, seen_orders, seen_trades = [], [], set(), set()
    low = int(plan["started_at"] // 300) * 300
    for market in snapshot["markets"]:
        epoch, expected = market["epoch"], market["expected"]
        if epoch < low:
            continue
        for order in market["confirmed_orders"]:
            if not _in_band(order, plan):
                continue
            key = (epoch, order["task_id"])
            timing = signal_times.get(key)
            if timing is None or timing["wall"] < plan["started_at"]:
                continue
            proof, quantity, principal, fee = order["proof"], D(0), D(0), D(0)
            if (proof["kind"] != "OWNED_TERMINAL_ORDER_AND_TRADES"
                    or proof["order"]["id"] != order["order_id"]
                    or proof["order"]["asset_id"] != order["token_id"]
                    or proof["order"]["side"] != "BUY"
                    or order["token_id"] not in expected["token_ids"].values()
                    or order["order_id"] in seen_orders):
                raise ValueError("OBSERVATION_OWNED_ORDER_IDENTITY_INVALID")
            seen_orders.add(order["order_id"])
            for trade in proof["trades"]:
                trade_key = (order["order_id"], trade["trade_id"])
                if (trade["status"] != "CONFIRMED" or trade["our_side"] != "BUY"
                        or trade["maker_order_id"] != order["order_id"]
                        or trade["token_id"] != order["token_id"]
                        or trade["market_id"].lower() != expected["condition_id"].lower()
                        or trade_key in seen_trades):
                    raise ValueError("OBSERVATION_FINAL_CONFIRMED_TRADE_REQUIRED")
                seen_trades.add(trade_key)
                quantity += _number(trade["size"])
                principal += _number(trade["principal_usdc"])
                fee += _number(trade["fee_usdc"])
            if (quantity <= 0 or quantity != _number(order["quantity"])
                    or principal != _number(order["principal_usdc"])
                    or fee != _number(order["fee_usdc"])
                    or principal + fee != _number(order["all_in_usdc"])):
                raise ValueError("OBSERVATION_FILL_COST_MISMATCH")
            if key not in groups:
                result = results.get(epoch, {})
                resolution = None
                try:
                    if result.get("raw") is not None:
                        resolution = validate_resolution(result["raw"], expected)
                except (ValueError, KeyError, TypeError) as exc:
                    errors.append({"epoch": epoch, "reason": str(exc)})
                if resolution is None and str(epoch) in cached_official:
                    prior = cached_official[str(epoch)]
                    resolution = validate_resolution(prior["raw"], expected)
                    if resolution:
                        result = prior
                        reused.append(epoch)
                if resolution:
                    resolution_cache[str(epoch)] = result
                elif result.get("error"):
                    errors.append({"epoch": epoch, "reason": result["error"]})
                groups[key] = {"epoch": epoch, "task_id": order["task_id"], "signal_wall": timing["wall"],
                               "token_id": order["token_id"], "signal_evidence": timing,
                               "quantity": D(0), "principal_usdc": D(0), "fee_usdc": D(0),
                               "order_ids": [], "signed_limits": [], "winner": resolution["winner"] if resolution else None,
                               "winning_token": resolution["token_ids"][resolution["winner"]] if resolution else None}
            group = groups[key]
            if group["token_id"] != order["token_id"]:
                raise ValueError("OBSERVATION_SIGNAL_TOKEN_CHANGED")
            group["quantity"] += quantity
            group["principal_usdc"] += principal
            group["fee_usdc"] += fee
            group["order_ids"].append(order["order_id"])
            group["signed_limits"].append(str(proof["order"]["price"]))
    signals = []
    for group in sorted(groups.values(), key=lambda x: (x["signal_wall"], x["epoch"], x["task_id"])):
        cost = group["principal_usdc"] + group["fee_usdc"]
        payout = (group["quantity"] if group["token_id"] == group["winning_token"] else D(0)) if group["winner"] else None
        signals.append({**group, "all_in_usdc": str(cost), "payout_usdc": str(payout) if payout is not None else None,
                        "pnl_usdc": str(payout - cost) if payout is not None else None})
    resolved = [s for s in signals if s["pnl_usdc"] is not None]
    pending = [s for s in signals if s["pnl_usdc"] is None]
    daily, markets = defaultdict(lambda: {"samples": 0, "pnl_usdc": D(0)}), defaultdict(int)
    for sample in resolved:
        date = datetime.fromtimestamp(sample["epoch"], BJ).date().isoformat()
        daily[date]["samples"] += 1
        daily[date]["pnl_usdc"] += D(sample["pnl_usdc"])
        markets[sample["epoch"]] += 1
    return {"observation_id": plan["id"], "started_at": plan["started_at"],
            "started_at_beijing": plan["started_at_beijing"], "snapshot_read_completed_at": snapshot["read_completed_at"],
            "status": "REVIEWABLE" if not timing_gaps and not errors else "PARTIAL_REVIEW_REQUIRED",
            "summary": {"effective_samples": len(resolved), "pending_resolution_signals": len(pending),
                        "winning_samples": sum(D(s["payout_usdc"]) > 0 for s in resolved),
                        "losing_samples": sum(D(s["payout_usdc"]) == 0 for s in resolved),
                        "resolved_all_in_usdc": str(sum((D(s["all_in_usdc"]) for s in resolved), D(0))),
                        "resolved_fee_usdc": str(sum((s["fee_usdc"] for s in resolved), D(0))),
                        "resolved_pnl_usdc": str(sum((D(s["pnl_usdc"]) for s in resolved), D(0))),
                        "pending_confirmed_cost_usdc": str(sum((D(s["all_in_usdc"]) for s in pending), D(0))),
                        "distinct_resolved_markets": len(markets), "multi_signal_markets": {str(k): v for k, v in markets.items() if v > 1},
                        "daily": dict(daily)},
            "signals": signals, "timing_gaps": list(timing_gaps), "resolution_errors": errors,
            "cached_final_result_epochs": sorted(set(reused)), "official_result_cache": resolution_cache,
            "definition": plan["sample_definition"], "live_post_maximum_price": "0.95",
            "automatic_trading_changes": False, "account_queries": [], "order_actions": [], "service_actions": []}


def milestone_due(plan, count):
    return [n for n in (plan["reminder_samples"], plan["evaluation_samples"])
            if count >= n and plan["milestones"][str(n)]["status"] not in {"NOTIFIED", "EVALUATED"}]


def run_observation(project, pnl_dir):
    project, pnl_dir = Path(project).resolve(), Path(pnl_dir).resolve()
    if not pnl_dir.is_relative_to(project / "research_outputs"):
        raise ValueError("OBSERVATION_PNL_DIRECTORY_OUTSIDE_PROJECT")
    state_dir = project / "research_outputs/m4_taker_watch"
    state_path = state_dir / "agent-state.json"
    plan = _read(state_path)[STATE_KEY]
    if not plan.get("enabled"):
        return {"status": "DISABLED"}
    snapshot = _read(pnl_dir / "snapshot.json")
    if snapshot.get("strategy", "M4") != "M4":
        raise ValueError("M4_PRICE_OBSERVATION_REQUIRES_M4_PNL")
    official = _read(pnl_dir / "official-resolutions.json")
    prior = _read(plan["latest_result"]) if plan.get("latest_result") else {}
    if prior and (prior["observation_id"] != plan["id"] or prior["started_at"] != plan["started_at"]):
        raise ValueError("OBSERVATION_CACHE_IDENTITY_MISMATCH")
    timing, gaps = read_signal_times(project, snapshot, plan)
    result = build_observation(snapshot, official, plan, timing,
                               cached_official=prior.get("official_result_cache"), timing_gaps=gaps)
    result["source_pnl_dir"] = str(pnl_dir)
    output = pnl_dir / "price-band-observation.json"
    with (state_dir / "agent-state.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = _read(state_path)
        current = state[STATE_KEY]
        if current["id"] != plan["id"] or current["started_at"] != plan["started_at"]:
            raise ValueError("OBSERVATION_PLAN_CHANGED_DURING_READ")
        due = milestone_due(current, result["summary"]["effective_samples"])
        result["milestones_due"] = due
        atomic_json(output, result)
        if snapshot["read_completed_at"] >= current.get("last_snapshot_read_completed_at", 0):
            current.update(latest_result=str(output), last_checked_at=time.time(),
                           last_snapshot_read_completed_at=snapshot["read_completed_at"], summary=result["summary"],
                           milestones_due=due)
            for number in due:
                milestone = current["milestones"][str(number)]
                milestone.update(status="DUE", first_reached_at=milestone.get("first_reached_at", time.time()))
            atomic_json(state_path, state)
    return result

"""Read-only local M4 Taker signal/execution audit and same-chat delivery.

Never imports a gateway, reads the trading API, writes a trading ledger, or
starts/stops a trading service. All fills come from owned-order ledger evidence.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import sqlite3
import subprocess
import time
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from polymaker.m4_control import atomic_json
from polymaker.m4_live_price_band import (
    ABANDON_BEST_ASK_BAND, ABANDON_FIRST_SIGNED_BAND, BEST_ASK_BAND_ABANDONED,
    SIGNED_LIMIT_BAND_ABANDONED, POST_SIGNED_LIMIT_BAND, price_in_band,
)
from polymaker.m4_live_pricing import DYNAMIC, quote_valid, rejected_price_band_valid
from polymaker.m4_types import BEST_ASK_SIGNAL, M6_LEDGER, TakerPriceRisk
from polymaker.m4_taker_coverage import rolling_coverage

PROJECT = Path(__file__).resolve().parents[1]
LIVE = "polymaker-m4-live.service"
TERMINAL = {"TERMINAL", "UNSENT"}


def read_json(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def service_status(unit=LIVE):
    reply = subprocess.run(
        ["systemctl", "--user", "show", unit, "-p", "ActiveState", "-p", "SubState",
         "-p", "MainPID", "-p", "NRestarts", "-p", "ExecMainStartTimestamp"],
        capture_output=True, text=True, timeout=10, check=True,
    )
    return dict(line.split("=", 1) for line in reply.stdout.splitlines() if "=" in line)


def band_abandonment_valid(proof, root, row, owned, band, policy):
    """Check the market's own versioned rejection proof without any live reads."""
    try:
        reason = (SIGNED_LIMIT_BAND_ABANDONED if policy == ABANDON_FIRST_SIGNED_BAND
                  else BEST_ASK_BAND_ABANDONED)
        return (policy in {ABANDON_BEST_ASK_BAND, ABANDON_FIRST_SIGNED_BAND}
                and proof["policy"] == root["post_price_band_retry_policy"] == policy
                and proof["reason"] == reason and proof["opportunity_id"] == row["id"]
                and proof["post_price_band"] == root["post_price_band"] == band
                and proof["pricing_origin"] == root["pricing_origin"]
                and rejected_price_band_valid(root, proof["pricing"], row["token"])
                and (proof["pricing"]["stage"] != "INITIAL" or not owned))
    except (KeyError, ValueError, TypeError, ArithmeticError):
        return False


def inspect_market(project, epoch, now=None, *, strategy="M4"):
    now = time.time() if now is None else now
    root = project / "research_outputs/m4_live"
    secondary = strategy == "M6"
    path = root / ("m6-ledgers" if secondary else "ledgers") / f"btc-updown-5m-{epoch}.sqlite3"
    if not path.exists():
        return {"epoch": epoch, "record_status": "NO_SESSION", "complete": False,
                "alerts": ["NO_SESSION_CHECK_STARTUP_AND_SERVICE"]}
    if path.is_symlink() or path.resolve() != path:
        raise ValueError("M4_LEDGER_PATH_INVALID")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        meta = {r["key"]: json.loads(r["value"]) for r in db.execute("SELECT * FROM metadata")}
        if meta.get("identity", {}).get("mode") != "LIVE":
            raise ValueError("M4_LIVE_IDENTITY_REQUIRED")
        if secondary and meta["identity"].get("strategy") != M6_LEDGER:
            raise ValueError("M6_LIVE_IDENTITY_REQUIRED")
        if meta.get("settings", {}).get("execution", {}).get("mode") != "taker":
            return {"epoch": epoch, "record_status": "LEGACY_NON_TAKER", "alerts": []}
        rows = [dict(r) for r in db.execute("SELECT * FROM opportunities ORDER BY rowid")]
        attempts = [dict(r) for r in db.execute("SELECT * FROM taker_attempts ORDER BY seq")]
        plans = {r["identity"]: json.loads(r["payload"]) for r in
                 db.execute("SELECT * FROM facts WHERE kind='taker_plan'")}
        pricing_quotes = {r["identity"]: json.loads(r["payload"]) for r in
                          db.execute("SELECT * FROM facts WHERE kind='taker_pricing_quote'")}
        band_abandonments = {r["identity"]: json.loads(r["payload"]) for r in
                             db.execute("SELECT * FROM facts WHERE kind='taker_price_band_abandonment'")}
        trades = [dict(r) for r in db.execute("SELECT * FROM taker_trade_evidence")]
    journals = sorted((root / ("m6-journal" if secondary else "journal")).glob("*_" + meta["session_id"] + ".jsonl"))
    if len(journals) != 1 or journals[0].is_symlink():
        raise ValueError("M4_JOURNAL_IDENTITY_UNAVAILABLE")
    candidates, opportunities, skipped = {}, {}, {}
    posts, responses, blocked, diagnostics, errors = [], [], [], Counter(), []
    network, latency = Counter(), []
    heartbeat, execution_policy, last_wall, parse_errors = {}, {}, None, 0
    # Freeze the journal read boundary; the live writer keeps running untouched.
    with journals[0].open("rb") as stream:
        data = stream.read(journals[0].stat().st_size)
    lines = data.splitlines(keepends=True)
    for line in lines:
        if not line.endswith(b"\n"):
            continue
        try:
            record = json.loads(line)
            kind, payload = record["kind"], record["data"]["payload"]
            wall = record["ts"]
        except (ValueError, KeyError, TypeError):
            parse_errors += 1
            continue
        last_wall = wall
        if kind == ("sm_m6_signal" if secondary else "sm_m4_signal"):
            event = payload.get("event")
            if event == "CANDIDATE":
                item = payload["evidence"]["payload"] if secondary else payload
                candidates[item["id"]] = {"wall": wall, **item}
            elif event == "OPPORTUNITY_IDENTIFIED":
                opp = payload["opportunity"]
                opportunities[opp["episode_id"]] = {"wall": wall, **opp}
            elif payload.get("reason"):
                diagnostics[payload["reason"]] += 1
        elif kind == "sm_m4_candidate_skipped":
            skipped[payload["opportunity"]["episode_id"]] = payload["reason"]
        elif kind == "sm_m4_native_post":
            posts.append({"wall": wall, **payload})
        elif kind == "sm_m4_native_post_blocked":
            blocked.append(payload)
        elif kind == "sm_m4_live_post":
            responses.append(payload)
        elif kind == "sm_m4_heartbeat":
            heartbeat = payload
        elif kind == "sm_m4_budget_execution_policy":
            execution_policy = payload
        elif kind == "sm_network_request":
            network[str(payload.get("outcome"))] += 1
            if payload.get("duration_ms") is not None:
                latency.append(payload["duration_ms"])
            if payload.get("outcome") != "SUCCESS":
                errors.append({"wall": wall, "kind": kind, **payload})
        elif any(word in kind for word in ("error", "fatal", "halt", "retry", "wait")):
            errors.append({"wall": wall, "kind": kind, **payload})
    by_id = {r["id"]: r for r in rows}
    by_task = defaultdict(list)
    for attempt in attempts:
        by_task[attempt["task_id"]].append(attempt)
    # Keep all signing/preparation failures, even when later wait events push
    # them out of the bounded diagnostics tail. These have no signed order ID
    # and must never be counted as POSTs or exchange zero-fill responses.
    unsigned_errors = defaultdict(list)
    for error in errors:
        if (error["kind"] == "sm_m4_live_post_error"
                and error.get("order_id") is None and error.get("opportunity_id")):
            unsigned_errors[error["opportunity_id"]].append(error)
    local_blocked_ids = {a["order_id"] for a in attempts
                         if a["state"] == "UNSENT" and a.get("proof")
                         and json.loads(a["proof"]).get("kind") == "LOCAL_NATIVE_POST_BLOCKED"}
    submitted_ids = {a["order_id"] for a in attempts if a["started"]} - local_blocked_ids
    alerts, observations = [], []
    complete = meta.get("complete") is True
    stable = complete and now >= epoch + 310
    if parse_errors:
        alerts.append("JOURNAL_PARSE_ERRORS")
    missing = []
    for key, opp in opportunities.items():
        if key not in by_id and key not in skipped:
            missing.append({**opp, "status": "NO_DURABLE_TASK_NEEDS_REVIEW"})
    if missing:
        observations.append("IDENTIFIED_SIGNAL_WITHOUT_TASK")
    # Inspect the market's saved price contract, never the current config.
    candidate_review = []
    roots = {r["root"] for r in rows} | {o["root_id"] for o in opportunities.values()}
    settings = meta["settings"]
    strategy_policy = settings.get("taker_strategy", {})
    signal_budget = Decimal(strategy_policy.get("signal_budget_usdc", 5))
    market_budget = Decimal(strategy_policy.get("market_budget_usdc", 10))
    taker = settings.get("execution", {}).get("taker", {})
    post_band = ({"policy": POST_SIGNED_LIMIT_BAND,
                  "minimum": taker["post_minimum_price"], "maximum": taker["post_maximum_price"]}
                 if taker.get("price_band_policy") == POST_SIGNED_LIMIT_BAND else None)
    retry_policy = taker.get("post_price_band_retry_policy")
    strategy_risk = settings.get("live_taker_price_risk")
    risk_fields = {}
    if strategy_risk is not None:
        risk_fields = TakerPriceRisk(**strategy_risk).plan_fields()
        post_band = risk_fields["post_price_band"]
        retry_policy = risk_fields["post_price_band_retry_policy"]
        if (stable or execution_policy) and (
            execution_policy.get("post_price_band_source") != risk_fields["post_price_band_source"]
            or execution_policy.get("post_price_band_policy") != post_band["policy"]
            or execution_policy.get("post_minimum_price") != post_band["minimum"]
            or execution_policy.get("post_maximum_price") != post_band["maximum"]
            or execution_policy.get("post_price_band_retry_policy") != retry_policy
            or execution_policy.get("signal_anchor_price_filter") is not False
        ):
            alerts.append("OPPORTUNITY_PRICE_RISK_RUNTIME_MISMATCH")
    for item in candidates.values():
        if item.get("root", item.get("root_id")) in roots:
            continue
        price = Decimal(str(item["price"]))
        elapsed = item["wall"] - epoch
        reason = ("PRICE_BELOW_FLOOR" if not post_band and price < Decimal(settings["minimum_price"])
                  else "OUTSIDE_ENTRY_WINDOW" if not settings["start_sec"] <= elapsed < settings["cutoff_sec"]
                  else "CANDIDATE_NOT_IDENTIFIED_REVIEW_EVIDENCE_LIFETIME")
        candidate_review.append({"id": item["id"], "price": str(price), "elapsed_s": elapsed, "reason": reason})
    signal_rows = []
    for row in rows:
        key, owned = row["id"], by_task[row["id"]]
        abandoned, root_plan = band_abandonments.get(key), plans.get(key, {})
        if strategy_risk is not None:
            recorded = opportunities.get(key)
            if (json.loads(row["evidence"]).get("price_risk") != strategy_risk
                    or (recorded is not None and recorded.get("price_risk") != strategy_risk)
                    or (root_plan and any(root_plan.get(k) != v for k, v in risk_fields.items()))):
                alerts.append("OPPORTUNITY_PRICE_RISK_EVIDENCE_MISMATCH")
        if abandoned and not band_abandonment_valid(
                abandoned, root_plan, row, owned, post_band, retry_policy):
            alerts.append("PRICE_BAND_ABANDONMENT_EVIDENCE_INVALID")
        if (stable and retry_policy == ABANDON_FIRST_SIGNED_BAND
                and root_plan and not price_in_band(root_plan["limit_price"], post_band)
                and not abandoned):
            alerts.append("INITIAL_PRICE_BAND_REJECTION_MISSING")
        local_errors = unsigned_errors[key]
        sent = [a for a in owned if a["order_id"] in submitted_ids]
        unresolved = [a for a in sent if a["state"] not in TERMINAL]
        filled, spent = Decimal(row["filled"]), Decimal(row["spent"])
        if filled != sum((Decimal(a["filled"]) for a in owned), Decimal(0)) or spent != sum((Decimal(a["spent"]) for a in owned), Decimal(0)):
            alerts.append("TASK_ATTEMPT_TOTAL_MISMATCH")
        if int(row["posts"]) != sum(a["started"] for a in owned):
            alerts.append("TASK_POST_COUNT_MISMATCH")
        if sum(a["state"] in {"UNKNOWN", "ACK"} for a in sent) > 1:
            alerts.append("MULTIPLE_UNRESOLVED_SUBMISSIONS_FOR_SIGNAL")
        if unresolved:
            observations.append("OWNED_ORDER_RECONCILIATION_PENDING")
        if not sent and row["state"] not in {"SKIPPED", "CUTOFF", "ABORTED"}:
            observations.append("SIGNAL_AWAITING_FIRST_POST")
        if complete and not sent and local_errors and row["state"] != "SKIPPED":
            alerts.append("SIGNAL_UNPOSTED_WITH_LOCAL_PREPARATION_ERRORS")
        signal_rows.append({k: row[k] for k in
                            ("id", "outcome", "price", "state", "reason", "posts", "filled", "spent", "reserved")} |
                           {"confirmed_fill": filled > 0, "additional_posts": max(0, len(sent)-1),
                            "price_band_abandonment": band_abandonments.get(key),
                            "remaining_cash_usdc": str(max(Decimal(0), signal_budget-spent)),
                            "unsigned_preparation_failures": len(local_errors),
                            "unsigned_preparation_error_reasons": dict(Counter(x.get("reason") for x in local_errors)),
                            "unresolved_order_ids": [a["order_id"] for a in unresolved],
                            "attempts": [{k: a[k] for k in ("order_id", "state", "started", "filled", "spent")} for a in owned]})
        for a in owned:
            if not a["started"]:
                continue
            quote, plan = json.loads(a["quote"]), json.loads(a["plan"])
            price, size = Decimal(str(quote["price"])), Decimal(str(quote["size"]))
            root_plan = plans.get(key, {})
            pricing = plan.get("pricing", {})
            dynamic = taker.get("pricing_policy") in {DYNAMIC, BEST_ASK_SIGNAL}
            cap = Decimal(root_plan.get("limit_price", "NaN"))
            valid_price = (quote_valid(root_plan, plan, row["token"], pricing_quotes.get(pricing.get("id")))
                           and price == Decimal(plan["limit_price"])) if dynamic else (
                               "pricing_origin" not in root_plan and cap.is_finite() and price <= cap)
            anchor = Decimal(row["price"])
            valid_anchor = 0 < anchor < 1 if post_band else Decimal(settings["minimum_price"]) <= anchor < 1
            if post_band:
                valid_price = (valid_price and root_plan.get("post_price_band") == post_band
                               and price_in_band(price, post_band))
            if (not valid_price or quote["side"] != "BUY" or not 0 < price < 1
                    or not valid_anchor
                    or size <= 0 or size % Decimal(".1") or not (size >= 5 or price * size > 1)):
                alerts.append("POST_PRICE_SIZE_RULE_VIOLATION")
            if Decimal(plan["planned_cost_usdc"]) + Decimal(plan.get("prior_spent_usdc", "0")) > signal_budget + Decimal(".000001"):
                alerts.append("PLANNED_ALL_IN_BUDGET_OVER_LIMIT_REVIEW")
            if secondary and (Decimal(plan["commitment_usdc"]) > signal_budget
                              or Decimal(plan.get("prior_spent_usdc", "0")) > 0
                              or plan.get("taker_strategy") != strategy_policy):
                alerts.append("M6_SEND_POLICY_MISMATCH")
    commitment = sum((Decimal(r["spent"]) + Decimal(r["reserved"]) for r in rows), Decimal(0))
    if commitment > market_budget:
        alerts.append("M6_MARKET_RISK_OVER_LIMIT" if secondary else "MARKET_RISK_OVER_10")
    directions = Counter(r["outcome"] for r in rows if r["posts"])
    if any(n > strategy_policy.get("direction_limit", 1) for n in directions.values()):
        alerts.append("DIRECTION_SLOT_OVER_LIMIT")
    post_ids = [p["order_id"] for p in posts]
    if len(post_ids) != len(set(post_ids)):
        alerts.append("REPEATED_NATIVE_POST_ID_REVIEW_RESTART_EVIDENCE")
    blocked_ids = {p["order_id"] for p in blocked} | local_blocked_ids
    if stable and set(post_ids) - blocked_ids != submitted_ids:
        alerts.append("NATIVE_POST_LEDGER_MISMATCH")
    if any(p.get("elapsed_s", 0) >= settings["cutoff_sec"] for p in posts if p["order_id"] not in blocked_ids):
        alerts.append("POST_AT_OR_AFTER_T240")
    if meta.get("market_halt_reason"):
        alerts.append("MARKET_BUY_HALTED")
    if now >= epoch + 330 and not complete:
        alerts.append("MARKET_CLOSE_NOT_COMPLETE")
    latency.sort()
    return {"epoch": epoch, "record_status": "AVAILABLE", "complete": complete,
            "settings": settings, "settings_hash": meta["identity"].get("settings_hash"),
            "ledger_path": str(path), "journal_path": str(journals[0]), "journal_bytes": len(data),
            "last_journal_wall": last_wall, "market_halt_reason": meta.get("market_halt_reason"),
            "candidate_count": len(candidates), "identified_signal_count": len(opportunities),
            "durable_task_count": len(rows),
            "posted_signal_count": sum(any(a["order_id"] in submitted_ids for a in by_task[r["id"]]) for r in rows),
            "skipped_task_count": sum(r["state"] == "SKIPPED" for r in rows),
            "confirmed_filled_signal_count": sum(Decimal(r["filled"]) > 0 for r in rows),
            "durable_post_intents": sum(a["started"] for a in attempts),
            "native_posts": len(submitted_ids),
            "additional_posts": sum(s["additional_posts"] for s in signal_rows),
            "responses": dict(Counter(r["outcome"] for r in responses if r["order_id"] in submitted_ids)),
            "unsent_response_events": sum(r["order_id"] not in submitted_ids for r in responses),
            "unsigned_preparation_failures": sum(len(v) for v in unsigned_errors.values()),
            "attempt_states": dict(Counter(a["state"] for a in attempts)),
            "trade_states": dict(Counter(t["status"] for t in trades)),
            "confirmed_spent_usdc": str(sum((Decimal(r["spent"]) for r in rows), Decimal(0))),
            "commitment_usdc": str(commitment), "signals": signal_rows,
            "signals_without_task": missing, "explicit_skips": skipped,
            "candidates_without_identified_signal": candidate_review,
            "network_outcomes": dict(network),
            "network_latency_ms": {"max": max(latency, default=None), "p95": latency[int(.95*(len(latency)-1))] if latency else None},
            "sampled_data_diagnostic_counts": dict(diagnostics), "errors_and_waits": errors[-100:],
            "last_heartbeat": heartbeat, "alerts": sorted(set(alerts)),
            "observations": sorted(set(observations)),
            "fill_definition": "Local exact-owned-order CONFIRMED evidence; ACK/SETTLING are not fills"}


def open_issue_epochs(state):
    return {issue["market_epoch"] for issue in state.get("open_issues", [])
            if issue.get("status") not in {"RESOLVED", "CLOSED", "COMPLETE"}
            and type(issue.get("market_epoch")) is int}


def pending_epochs(state, markets):
    # Only a successful ledger review can clear a previous order-related entry.
    # Open repair issues remain pending even after every order is terminal.
    available = {m["epoch"] for m in markets if m.get("record_status") == "AVAILABLE"}
    unresolved = {m["epoch"] for m in markets
                  if any(s.get("unresolved_order_ids") for s in m.get("signals", []))}
    return sorted((set(state.get("pending_market_epochs", [])) - available)
                  | open_issue_epochs(state) | unresolved)


def collect(project, state_dir, control, now=None):
    now = time.time() if now is None else now
    current = int(now // 300) * 300
    state = read_json(state_dir / "agent-state.json", {})
    start = control["start_epoch"]
    low = max(start, min(current-300, state.get("reviewed_through_epoch", start)))
    epochs = (set(range(low, current+1, 300)) | set(state.get("pending_market_epochs", []))
              | open_issue_epochs(state))
    markets = []
    for epoch in sorted(epochs):
        try:
            markets.append(inspect_market(project, epoch, now))
        except (ValueError, KeyError, TypeError, ArithmeticError, OSError, sqlite3.Error) as error:
            markets.append({"epoch": epoch, "record_status": "READ_ERROR", "error_type": type(error).__name__, "alerts": ["MARKET_AUDIT_READ_ERROR"]})
    root = project / "research_outputs/m4_live"
    latest = read_json(root / "latest.json", {})
    startup = read_json(root / "startup-status.json", {})
    balance = read_json(project / "runtime/trading-control/m4-balance-observation.json", {})
    service = service_status()
    alerts = []
    if service.get("ActiveState") != "active":
        alerts.append("LIVE_SERVICE_NOT_ACTIVE")
    heartbeat_age = now-(root / "latest.json").stat().st_mtime if (root / "latest.json").exists() else None
    if latest.get("slug") == f"btc-updown-5m-{current}" and (heartbeat_age is None or heartbeat_age > 35):
        alerts.append("LIVE_HEARTBEAT_STALE")
    elif latest.get("slug") != f"btc-updown-5m-{current}" and now-current > 30:
        alerts.append("CURRENT_MARKET_NOT_OPEN_CHECK_STARTUP")
    if latest.get("signal_failure") or latest.get("reason"):
        alerts.append("LIVE_REPORTED_FAILURE_OR_HALT")
    operational = control.get("review_scope") == "operational"
    history = {}
    if not operational:
        for key, strategy in (("rolling_12h", "M4"), ("rolling_12h_m6", "M6")):
            try:
                history[key] = rolling_coverage(project, state, now, strategy=strategy)
            except (OSError, ValueError, KeyError, TypeError, ArithmeticError, sqlite3.Error) as error:
                history[key] = {"strategy": strategy, "status": "READ_ERROR", "error_type": type(error).__name__}
        history["price_band_observation"] = state.get("price_band_observation")
    pool_path = root / "taker-pool.sqlite3"
    shared = {}
    if pool_path.exists():
        try:
            with closing(sqlite3.connect(pool_path.resolve().as_uri()+"?mode=ro", uri=True, timeout=2)) as db:
                db.execute("PRAGMA query_only=ON")
                decisions = [{"epoch": epoch, "release_at": release_at, "released_at": released_at,
                              **json.loads(payload)}
                             for epoch, release_at, released_at, payload in db.execute(
                    "SELECT epoch,release_at,released_at,payload FROM market_openings WHERE epoch>=? ORDER BY epoch",
                    (min(epochs, default=current)-300,))]
            m6_epochs = epochs | set(state.get("m6_pending_market_epochs", []))
            have_m6 = {int(p.stem.rsplit("-", 1)[-1]) for p in (root / "m6-ledgers").glob("btc-updown-5m-*.sqlite3")}
            expected_m6 = {item["epoch"] for item in decisions if M6_LEDGER in item["settings"]}
            m6_markets = []
            for epoch in sorted(m6_epochs & (have_m6 | expected_m6)):
                try:
                    m6_markets.append(inspect_market(project, epoch, now, strategy="M6"))
                except (ValueError, KeyError, TypeError, ArithmeticError, OSError, sqlite3.Error) as error:
                    m6_markets.append({"epoch": epoch, "record_status": "READ_ERROR",
                                       "error_type": type(error).__name__, "alerts": ["M6_MARKET_AUDIT_READ_ERROR"]})
            m6_latest_path = root / "m6-latest.json"
            m6_latest = read_json(m6_latest_path, {})
            m6_heartbeat_age = now-m6_latest_path.stat().st_mtime if m6_latest_path.exists() else None
            current_opening = next((item for item in decisions if item["epoch"] == current), {})
            if M6_LEDGER in current_opening.get("reserved", {}):
                if m6_latest.get("slug") == f"btc-updown-5m-{current}":
                    if m6_heartbeat_age is None or m6_heartbeat_age > 35:
                        alerts.append("M6_HEARTBEAT_STALE")
                    if m6_latest.get("signal_failure") or m6_latest.get("reason"):
                        alerts.append("M6_REPORTED_FAILURE_OR_HALT")
                elif now-current > 30:
                    alerts.append("M6_CURRENT_MARKET_HEARTBEAT_MISSING")
            shared = {"openings": decisions, "m6_markets": m6_markets,
                      "m6_latest": m6_latest, "m6_heartbeat_age_sec": m6_heartbeat_age}
        except (ValueError, KeyError, TypeError, ArithmeticError, OSError, sqlite3.Error) as error:
            shared = {"status": "READ_ERROR", "error_type": type(error).__name__}
    return {"observed_at": now, "bucket": int((now-20)//300), "previous_market_epoch": current-300,
            "review_scope": "operational" if operational else "full",
            **history,
            "markets": markets, "live_service": service, "live_latest": latest,
            "heartbeat_age_sec": heartbeat_age, "startup": startup, "balance": balance,
            **({"shared_taker_pool": shared} if shared else {}),
            "balance_observation_age_sec": now-balance["observed_at"] if "observed_at" in balance else None,
            "stop_markers": sorted(p.name for p in (project / "runtime/trading-control").glob("*stop*.json")),
            "alerts": alerts, "observation_mode": "LOCAL_READ_ONLY_NO_TRADING_API"}


def wake(project, state_dir, control, report, runner=subprocess.run):
    with (state_dir / "agent-state.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = read_json(state_dir / "agent-state.json", {})
        previously_requested_epoch = state.get("requested_through_epoch", 0)
        state["requested_report"] = report["report_path"]
        state["requested_through_epoch"] = report["previous_market_epoch"]
        if report["bucket"] in state.get("notified_buckets", []):
            return {"status": "ALREADY_QUEUED"}
        # An outstanding review may serve a repeat of the same market. At
        # market cadence, each newly closed market needs its own review request.
        if (not state.get("resume_unconfirmed")
                and state.get("last_queued_at", 0) > state.get("reviewed_at", 0)
                and time.time()-state["last_queued_at"] < 590
                and (control.get("interval_seconds", 3600) > 300
                     or previously_requested_epoch >= report["previous_market_epoch"])):
            atomic_json(state_dir / "agent-state.json", state)
            return {"status": "COALESCED_WITH_PENDING_REVIEW"}
        prompt_name = ("m4_taker_watch_operational_prompt.txt" if control.get("review_scope") == "operational"
                       else "m4_taker_watch_prompt.txt")
        prompt = (project / "ops" / prompt_name).read_text()
        prompt += f"\n本轮记录：{report['report_path']}\n最新索引：{state_dir / 'latest.json'}\n"
        result = runner([str(Path.home() / ".local/bin/codex"), "queue", "--thread", control["thread"],
                         "--message", prompt, "--resume"], cwd=project, capture_output=True, text=True, timeout=45)
        if result.returncode:
            return {"status": "WAKE_FAILED", "returncode": result.returncode}
        warning = "warning" in (result.stdout+result.stderr).lower()
        state.update(last_queued_at=time.time(), resume_unconfirmed=warning)
        state["notified_buckets"] = (state.get("notified_buckets", [])+[report["bucket"]])[-288:]
        atomic_json(state_dir / "agent-state.json", state)
        return {"status": "QUEUED_RESUME_UNCONFIRMED" if warning else "QUEUED_AND_RESUME_REQUESTED"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", type=Path, default=PROJECT)
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--no-wake", action="store_true")
    args = parser.parse_args()
    project = args.project_dir.resolve()
    state_dir = args.state_dir or project / "research_outputs/m4_taker_watch"
    state_dir.mkdir(parents=True, exist_ok=True)
    with (state_dir / "observer.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('{"status":"ALREADY_RUNNING"}')
            return
        control = read_json(state_dir / "control.json", {})
        if not control.get("enabled"):
            print('{"status":"DISABLED"}')
            return
        report = collect(project, state_dir, control)
        stamp = datetime.fromtimestamp(report["observed_at"], ZoneInfo("Asia/Shanghai")).strftime("%Y%m%dT%H%M%S")
        output = state_dir / "reports" / (stamp+".json")
        report["report_path"] = str(output)
        atomic_json(output, report)
        atomic_json(state_dir / "latest.json", report)
        with (state_dir / "agent-state.lock").open("a+") as state_lock:
            fcntl.flock(state_lock, fcntl.LOCK_EX)
            state = read_json(state_dir / "agent-state.json", {})
            state["pending_market_epochs"] = pending_epochs(state, report["markets"])
            if "m6_markets" in report.get("shared_taker_pool", {}):
                state["m6_pending_market_epochs"] = pending_epochs(
                    {"pending_market_epochs": state.get("m6_pending_market_epochs", [])},
                    report["shared_taker_pool"]["m6_markets"])
            atomic_json(state_dir / "agent-state.json", state)
        if not args.no_wake and read_json(state_dir / "control.json").get("enabled"):
            try:
                report["delivery"] = wake(project, state_dir, control, report)
            except (OSError, subprocess.SubprocessError) as error:
                report["delivery"] = {"status": "WAKE_FAILED", "error_type": type(error).__name__}
        else:
            report["delivery"] = {"status": "NO_WAKE"}
        atomic_json(output, report)
        atomic_json(state_dir / "latest.json", report)
        print(json.dumps({"report": str(output), "alerts": report["alerts"], "delivery": report["delivery"]}))


if __name__ == "__main__":
    main()

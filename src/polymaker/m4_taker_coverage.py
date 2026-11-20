"""Rolling owned-signal coverage. Local SQLite/journals only; no trading clients."""
from __future__ import annotations

import json
import sqlite3
import time
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from polymaker.m4_live_price_band import BEST_ASK_BAND_ABANDONED, SIGNED_LIMIT_BAND_ABANDONED
from polymaker.m4_types import TAKER_LEDGER_IDS

FUNDER = "0x2d73e1f6c90662d30e13fed534bf10bc5bd99d5d"
REVISION = "m4-taker-budget-v1"
NORMAL_FILTERS = {
    "M4_PRICE_OUTSIDE_BAND", "M4_DIRECTION_OPPORTUNITY_LIMIT", "SESSION_INSTRUCTION_LIMIT",
    "M4_TAKER_MINIMUM_EXCEEDS_ALL_IN_BUDGET", "M4_TAKER_ANCHOR_PLUS_TICK_INVALID",
    "M4_POST_PRICE_OUTSIDE_BAND",
    "M21_FORMAL_AFTER_T240",
    "M421_FORMAL_AFTER_T240",
}
FINAL_TASKS = {"CUTOFF", "DUST", "COMPLETE", "ABORTED", "BUDGET_EXHAUSTED"}
TERMINAL_ATTEMPTS = {"TERMINAL", "UNSENT"}
MARKERS = tuple(x.encode() for x in (
    '"sm_m4_signal"', '"sm_m6_signal"', '"sm_m21_signal"', '"sm_m421_signal"', '"sm_prepared"', '"sm_m4_candidate_skipped"',
    '"sm_m4_live_candidate_skipped"', '"sm_m4_live_post_error"',
    '"sm_m4_owned_order_resolved"', '"sm_m4_execution_wait"',
))


def _read_ledger(path, strategy):
    if path.is_symlink():
        raise ValueError("SYMLINK_LEDGER")
    with closing(sqlite3.connect(path.as_uri()+"?mode=ro", uri=True, timeout=2)) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        meta = {r["key"]: json.loads(r["value"]) for r in db.execute("SELECT * FROM metadata")}
        identity, settings = meta.get("identity", {}), meta.get("settings", {})
        if (identity.get("mode") != "LIVE" or identity.get("strategy") != strategy
                or identity.get("funder", "").lower() != FUNDER
                or settings.get("execution_revision") != REVISION):
            return None
        return {"meta": meta, **{table: [dict(r) for r in db.execute("SELECT * FROM "+table)]
                for table in ("opportunities", "taker_attempts", "taker_trade_evidence")}}


def _zero_audits(project, state):
    """Use reviewed exact-order evidence; never reinterpret UNKNOWN from an empty GET."""
    out = {}
    allowed = (project/"research_outputs/m4_taker_watch/reviews").resolve()
    for oid, entry in state.get("owned_order_investigations", {}).items():
        if entry.get("status") != "AUDIT_COMPLETE_ZERO_EXECUTION":
            continue
        path = Path(entry.get("result", ""))
        if not path.is_absolute() or path.is_symlink() or not path.resolve().is_relative_to(allowed):
            continue
        try:
            value = json.loads(path.read_text())
            verified = Path(value["offline_verification"])
            if verified.is_symlink() or not verified.resolve().is_relative_to(allowed):
                continue
            proof = json.loads(verified.read_text())
            if (value.get("order_id") == proof.get("actual_order_id") == oid
                    and value.get("execution_outcome") == "PROVEN_NO_HTTP_ORDER_SENT"
                    and value.get("actual_quantity") == "0"
                    and value.get("actual_all_in_usdc") == "0"
                    and proof.get("status") == "PASS"
                    and proof.get("real_trace_matches_proven_pre_http_TLS_failure") is True):
                out[oid] = str(path)
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return out


def _confirmed(attempt, trade_rows, market, token):
    proof = json.loads(attempt.get("proof") or "{}")
    trades = proof.get("trades", [])
    ids = {r["trade_id"] for r in trade_rows
           if r["order_id"] == attempt["order_id"] and r["status"] == "CONFIRMED"}
    confirmed = [t for t in trades if t.get("status") == "CONFIRMED"]
    if (attempt["state"] != "TERMINAL" or proof.get("kind") != "OWNED_TERMINAL_ORDER_AND_TRADES"
            or not confirmed or any(t.get("trade_id") not in ids for t in confirmed)
            or any(t.get("status") not in {"CONFIRMED", "FAILED"} or t.get("maker_order_id") != attempt["order_id"]
                   or t.get("market_id", "").lower() != market.lower()
                   or t.get("token_id") != token or t.get("our_side") != "BUY" for t in trades)):
        raise ValueError("MISSING_OWNED_CONFIRMED_FILL_PROOF")
    quantity = sum((Decimal(str(t["size"])) for t in confirmed), Decimal(0))
    cost = sum((Decimal(t["principal_usdc"])+Decimal(t["fee_usdc"]) for t in confirmed), Decimal(0))
    if (abs(quantity-Decimal(attempt["filled"])) > Decimal(".000001")
            or cost != Decimal(attempt["spent"])):
        raise ValueError("CONFIRMED_ECONOMICS_CONFLICT")
    return Decimal(attempt["filled"]), cost


def _pool_openings(root, first, last):
    path = root/"taker-pool.sqlite3"
    if not path.exists():
        return {}
    if path.is_symlink():
        raise ValueError("SYMLINK_POOL")
    with closing(sqlite3.connect(path.as_uri()+"?mode=ro", uri=True, timeout=2)) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        return {epoch: json.loads(payload) for epoch, payload in db.execute(
            "SELECT epoch,payload FROM market_openings WHERE funder=? AND epoch<=? AND "
            "(epoch>=? OR epoch=(SELECT MAX(epoch) FROM market_openings WHERE funder=? AND epoch<?)) ORDER BY epoch",
            (FUNDER, last, first, FUNDER, first))}


def rolling_coverage(project, state, now=None, window_seconds=43200, *, strategy="M4"):
    """Signals born in [cutoff-window_seconds, cutoff); outcomes supported by the cutoff."""
    project = Path(project).resolve()
    end = time.time() if now is None else now
    start = end-window_seconds
    root = project/"research_outputs/m4_live"
    identity = TAKER_LEDGER_IDS[strategy]
    secondary = strategy != "M4"
    lane = strategy.lower()
    journal_dir = root/(f"{lane}-journal" if secondary else "journal")
    signal_event = f"sm_{lane}_signal"
    first, last = int(start//300)*300, int(end//300)*300
    if end == last:
        last -= 300
    openings = _pool_openings(root, first, last) if secondary else {}
    expected = any(identity in o["settings"] for e, o in openings.items() if e < first)
    pool_skips, out_of_scope = [], []
    ledgers, tasks, attempts, sources, gaps, errors, excluded = {}, {}, defaultdict(list), [], [], [], []
    for epoch in range(first, last+1, 300):
        path = root/(f"{lane}-ledgers" if secondary else "ledgers")/f"btc-updown-5m-{epoch}.sqlite3"
        opening = openings.get(epoch)
        if opening:
            expected = identity in opening["settings"]
            if expected and opening["status"] == "SKIPPED":
                pool_skips.append({"epoch": epoch, "reason": opening["reason"]})
        if not path.exists():
            if secondary and not expected:
                out_of_scope.append(epoch)
            else:
                reason = "CURRENT_PREPARING" if epoch+300 > end else "NO_SESSION"
                if opening and opening["status"] == "SKIPPED":
                    reason = "SKIPPED_WITHOUT_SIGNAL_OBSERVATION"
                elif secondary and not opening and epoch+300 <= end:
                    reason = "MISSING_POOL_OPENING"
                gaps.append({"epoch": epoch, "reason": reason})
            continue
        try:
            data = _read_ledger(path, identity)
            if data is None:
                excluded.append(epoch)
                continue
            ledgers[epoch] = data
            sources.append({"ledger": str(path), "epoch": epoch, "read_mode": "mode=ro,query_only,BEGIN"})
            for task in data["opportunities"]:
                if task["id"] in tasks:
                    raise ValueError("DUPLICATE_SIGNAL_TASK")
                tasks[task["id"]] = (epoch, task)
            for attempt in data["taker_attempts"]:
                attempts[attempt["task_id"]].append(attempt)
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
            errors.append({"source": str(path), "reason": type(exc).__name__})

    identified, candidates, skips, prepared, resolved = {}, {}, {}, {}, {}
    posts, unsigned, waits, journal_sources = {}, defaultdict(Counter), defaultdict(Counter), []
    # Include every executor session, so restarts do not erase earlier signals or repeat the denominator.
    journals = list(journal_dir.glob(f"rdp_sm_{FUNDER}_m4_live_*.jsonl"))
    if secondary and any(a["started"] for owned in attempts.values() for a in owned):
        # The shared network emits its transport evidence through the M4 writer.
        # Join only exact signed-body hashes from the M6 preparations below.
        journals.extend((root/"journal").glob(f"rdp_sm_{FUNDER}_m4_live_*.jsonl"))
    for path in sorted(journals):
        if path.is_symlink() or path.stat().st_mtime < start-300:
            continue
        size = path.stat().st_size
        journal_sources.append({"path": str(path), "bytes": size})
        with path.open("rb") as stream:
            for line in stream.read(size).splitlines(keepends=True):
                if not line.endswith(b"\n"):
                    continue
                if not any(marker in line for marker in MARKERS) and not (
                        b'"sm_network_request"' in line and b'"POST"' in line):
                    continue
                try:
                    record = json.loads(line)
                    wall, kind, payload = record["ts"], record["kind"], record["data"]["payload"]
                    if not start-300 <= wall < end:
                        continue
                    if path.parent != journal_dir and kind != "sm_network_request":
                        continue
                    if kind == signal_event and payload.get("event") == "OPPORTUNITY_IDENTIFIED":
                        sid = payload["opportunity"]["episode_id"]
                        if start <= wall:
                            old = identified.get(sid)
                            if old is None or wall < old["wall"]:
                                identified[sid] = {"wall": wall, **payload["opportunity"]}
                    elif kind == signal_event and payload.get("event") == "CANDIDATE" and start <= wall:
                        candidate = payload["evidence"]["payload"] if secondary else payload
                        candidates[candidate["id"]] = {"wall": wall, **candidate,
                                                       "root": candidate.get("root", candidate.get("root_id"))}
                    elif kind in {"sm_m4_candidate_skipped", "sm_m4_live_candidate_skipped"}:
                        sid = payload.get("opportunity", {}).get("episode_id") or payload.get("opportunity_id")
                        skips[sid] = payload["reason"]
                    elif kind == "sm_prepared":
                        oid, body = payload["order_id"], payload["body_hash"]
                        if oid in prepared and prepared[oid] != body:
                            raise ValueError("PREPARED_BODY_CONFLICT")
                        prepared[oid] = body
                    elif kind == "sm_network_request" and payload.get("method") == "POST":
                        key = payload.get("request_id") or (str(path), record.get("writer_sequence"), wall)
                        posts[key] = {"wall": wall, **payload}
                    elif kind == "sm_m4_owned_order_resolved":
                        resolved[payload["order_id"]] = {"wall": wall, **payload}
                    elif kind == "sm_m4_live_post_error" and not payload.get("order_id"):
                        unsigned[payload.get("opportunity_id")][payload.get("reason", "UNKNOWN")] += 1
                    elif kind == "sm_m4_execution_wait":
                        waits[payload.get("opportunity_id")][payload.get("reason", "UNKNOWN")] += 1
                except (ValueError, KeyError, TypeError) as exc:
                    errors.append({"source": str(path), "reason": type(exc).__name__})

    zero_audits = _zero_audits(project, state)
    rows = []
    for sid, signal in sorted(identified.items(), key=lambda item: (item[1]["wall"], item[0])):
        # Only signals whose token/market belong to this executor's owned ledgers enter the metric.
        epoch, task = tasks.get(sid, (int(signal["wall"]//300)*300, None))
        data = ledgers.get(epoch)
        if data is None:
            errors.append({"signal": sid, "reason": "SIGNAL_WITHOUT_OWNED_LEDGER"})
            continue
        own = sorted(attempts[sid], key=lambda a: a["seq"])
        http = [post for post in posts.values()
                if post.get("operation_id") in {"post:"+prepared[a["order_id"]] for a in own if a["order_id"] in prepared}]
        audited = [a["order_id"] for a in own if a["order_id"] in zero_audits]
        proven_pre_http = {"post:"+prepared[oid] for oid in audited if oid in prepared}
        sent = [post for post in http if post.get("operation_id") not in proven_pre_http]
        raw_unresolved = [a["order_id"] for a in own if a["started"] and a["state"] not in TERMINAL_ATTEMPTS]
        unresolved = [oid for oid in raw_unresolved if oid not in zero_audits]
        quantity = cost = Decimal(0)
        problems = []
        for a in own:
            if Decimal(a["filled"]) <= 0:
                continue
            try:
                if a["order_id"] not in resolved:
                    raise ValueError("CONFIRMATION_NOT_OBSERVED_BY_CUTOFF")
                q, c = _confirmed(a, data["taker_trade_evidence"],
                                  data["meta"]["identity"]["market_id"], signal["token_id"])
                quantity += q
                cost += c
            except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
                problems.append(str(exc))
        if any(a["started"] and a["state"] != "UNSENT" and a["order_id"] not in prepared for a in own):
            problems.append("STARTED_ORDER_WITHOUT_PREPARED_BODY_EVIDENCE")
        expected_hashes = {"post:"+prepared[a["order_id"]] for a in own
                           if a["started"] and a["state"] != "UNSENT" and a["order_id"] in prepared}
        if expected_hashes-set(post.get("operation_id") for post in http):
            problems.append("STARTED_ORDER_WITHOUT_HTTP_POST_EVIDENCE")
        reason = skips.get(sid) or (task or {}).get("reason")
        normal = reason in NORMAL_FILTERS and (task is None or task["state"] == "SKIPPED")
        if secondary and task:
            normal |= (task["state"] == "CUTOFF" and reason in {
                "M4_POST_PRICE_OUTSIDE_BAND", f"{strategy}_NO_LEGAL_FAK_QUANTITY"}
                and not own and not data["meta"].get("post_price_band_eligible:"+sid))
        if task and task["state"] == "SKIPPED" and reason and reason.startswith(("TAKER_POOL_", "TAKER_STRATEGY_")):
            normal = True
        admitted = task is not None and not normal
        if normal and any(a["started"] for a in own):
            problems.append("FILTERED_SIGNAL_HAS_POST_INTENT")
        if problems:
            status, reason = "NEEDS_REVIEW", ";".join(problems)
        elif quantity > 0:
            status, reason = "CONFIRMED", "OWNED_CONFIRMED"
        elif normal:
            status = "NORMAL_FILTER"
        elif not task:
            status, reason = "NEEDS_REVIEW", "IDENTIFIED_WITHOUT_TASK"
        elif task["state"] == "SKIPPED":
            status, reason = "NEEDS_REVIEW", reason or "UNCLASSIFIED_SKIP"
        elif audited and not unresolved:
            status, reason = "FAILED_ZERO_FILL", "TLS_BEFORE_HTTP_SEND"
        elif unresolved or task["state"] not in FINAL_TASKS:
            status = "PENDING"
        else:
            status = "FAILED_ZERO_FILL"
            proof_kinds = {json.loads(a.get("proof") or "{}").get("kind") for a in own}
            if reason in {BEST_ASK_BAND_ABANDONED, SIGNED_LIMIT_BAND_ABANDONED}:
                # Prior zero matches do not explain why entry was finally stopped.
                pass
            elif own and proof_kinds == {"EXACT_FAK_KILLED"}:
                reason = "FAK_NO_MATCH_UNTIL_CUTOFF"
            elif any("invalid tick size" in text for text in unsigned[sid]):
                reason = "SDK_TICK_SIGNING_REJECTION"
            elif unsigned[sid]:
                reason = "LOCAL_PREPARATION_ERROR"
            elif task["state"] == "DUST":
                reason = "NO_LEGAL_QUANTITY_DUST"
            else:
                reason = reason or "ZERO_FILL_NEEDS_CAUSE_REVIEW"
        unsent = sum(not a["started"] or a["state"] == "UNSENT" for a in own)
        settings = data["meta"]["settings"]
        budget = Decimal(str(settings.get("taker_strategy", {}).get(
            "signal_budget_usdc", settings.get("order_budget_usdc", "1.50" if strategy == "M6" else 5))))
        rows.append({"id": sid, "epoch": epoch, "signal_wall": signal["wall"],
                     "outcome": signal["outcome"], "anchor": str(signal["anchor_bid_price"]),
                     "status": status, "reason": reason, "admitted": admitted,
                     "durable_POST_intents": sum(a["started"] for a in own),
                     "HTTP_POST_invocations": len(http), "real_POST": len(sent),
                     "additional_POST": max(0, len(sent)-1), "proven_pre_HTTP_attempts": len(audited),
                     "UNSENT_preparations": unsent, "unsigned_failures": sum(unsigned[sid].values()),
                     "unsigned_error_reasons": dict(unsigned[sid]), "wait_reasons": dict(waits[sid]),
                     "confirmed_quantity": str(quantity), "all_in_usdc": str(cost),
                     "remaining_budget_usdc": str(max(Decimal(0), budget-cost)) if admitted else None,
                     "available_remainder_usdc": str(max(Decimal(0), budget-cost))
                     if admitted and not unresolved and Decimal(task.get("reserved", "0")) == 0 else None,
                     "raw_ledger_state": (task or {}).get("state"),
                     "raw_reserved_usdc": (task or {}).get("reserved"),
                     "raw_unresolved_order_ids": raw_unresolved, "unresolved_order_ids": unresolved,
                     "audit_evidence": [zero_audits[oid] for oid in audited],
                     "order_ids": [a["order_id"] for a in own]})

    orphan_tasks = [{"id": sid, "epoch": epoch} for sid, (epoch, _) in tasks.items()
                    if start <= epoch and epoch+300 <= end and sid not in identified]
    identified_roots = ({s.get("root_id", s["episode_id"]) for s in identified.values()}
                        | {task.get("root", sid) for sid, (_, task) in tasks.items()})
    candidate_only = {}
    for candidate in sorted(candidates.values(), key=lambda c: c["wall"]):
        if candidate["root"] not in identified_roots:
            candidate_only.setdefault(candidate["root"], candidate)
    observation_gaps = []
    for epoch, data in ledgers.items():
        if secondary:
            session_id = data["meta"].get("session_id")
            journal = journal_dir/f"rdp_sm_{FUNDER}_{session_id}.jsonl"
            if not session_id or not journal.is_file() or journal.is_symlink():
                observation_gaps.append({"epoch": epoch, "reason": f"{strategy}_OWNED_SIGNAL_JOURNAL_UNAVAILABLE"})
        path = root/"startup-admissions"/f"btc-updown-5m-{epoch}.json"
        if path.exists():
            try:
                admission = json.loads(path.read_text())
                first, latest = admission["first_preparation"], admission["latest_preparation"]
                if first["prepared_at"]-epoch > data["meta"]["settings"].get("start_sec", 15):
                    observation_gaps.append({"epoch": epoch, "reason": "LATE_FRESH_ENTRY", "until": first["prepared_at"]})
                if latest.get("recovery"):
                    observation_gaps.append({"epoch": epoch, "reason": "RESTART_OBSERVATION_GAP", "recovery_at": latest["prepared_at"]})
            except (OSError, ValueError, KeyError, TypeError) as exc:
                errors.append({"source": str(path), "reason": type(exc).__name__})
    counts = Counter(row["status"] for row in rows)
    admitted = sum(row["admitted"] for row in rows)
    confirmed = counts["CONFIRMED"]
    totals = {key: sum(row[key] for row in rows) for key in
              ("durable_POST_intents", "HTTP_POST_invocations", "real_POST", "additional_POST", "proven_pre_HTTP_attempts", "UNSENT_preparations", "unsigned_failures")}
    missing_signal_journal = any(g["reason"] == f"{strategy}_OWNED_SIGNAL_JOURNAL_UNAVAILABLE"
                                 for g in observation_gaps)
    return {"status": "REVIEWABLE" if not errors and not orphan_tasks and not counts["NEEDS_REVIEW"]
            and not missing_signal_journal else "PARTIAL_REVIEW_REQUIRED",
            "strategy": strategy,
            "window_start": start, "window_end": end, "window_seconds": window_seconds,
            "window_start_beijing": datetime.fromtimestamp(start, ZoneInfo("Asia/Shanghai")).isoformat(),
            "window_end_beijing": datetime.fromtimestamp(end, ZoneInfo("Asia/Shanghai")).isoformat(),
            "read_completed_at": time.time(), "candidate_events": len(candidates),
            "candidate_roots": len({c["root"] for c in candidates.values()}),
            "candidate_roots_without_identified": list(candidate_only.values()),
            "identified_signals": len(rows), "admitted_signals": admitted,
            "posted_signals": sum(row["real_POST"] > 0 for row in rows),
            "confirmed_signals": confirmed, "failed_zero_fill_signals": counts["FAILED_ZERO_FILL"],
            "normal_filtered_signals": counts["NORMAL_FILTER"], "pending_signals": counts["PENDING"],
            "needs_review_signals": counts["NEEDS_REVIEW"],
            "confirmed_per_admitted_rate": str(Decimal(confirmed)/admitted) if admitted else None,
            "failure_reasons": dict(Counter(row["reason"] for row in rows if row["status"] == "FAILED_ZERO_FILL")),
            "normal_filter_reasons": dict(Counter(row["reason"] for row in rows if row["status"] == "NORMAL_FILTER")),
            "raw_unresolved_orders": sum(len(row["raw_unresolved_order_ids"]) for row in rows),
            "unresolved_orders_after_reviewed_zero_proofs": sum(len(row["unresolved_order_ids"]) for row in rows),
            "confirmed_quantity": str(sum((Decimal(row["confirmed_quantity"]) for row in rows), Decimal(0))),
            "confirmed_all_in_usdc": str(sum((Decimal(row["all_in_usdc"]) for row in rows), Decimal(0))),
            **totals, "signals": rows, "ledger_sources": sources, "journal_sources": journal_sources,
            "missing_markets": gaps, "excluded_non_executor_markets": excluded,
            "pool_skipped_markets": pool_skips, "outside_strategy_membership_epochs": out_of_scope,
            "observation_gaps": observation_gaps, "orphan_tasks": orphan_tasks, "read_errors": errors,
            "definitions": {"signal": "Unique OPPORTUNITY_IDENTIFIED episode_id born in [start,end), across restarts; observed executor signals only.",
                            "success": "Positive final exact-owned CONFIRMED fill, including a valid partial fill.",
                            "failure": "Admitted signal ended with zero fill, or reviewed single-request pre-HTTP zero proof; does not itself imply a code bug.",
                            "rate": "confirmed_signals/admitted_signals; normal initial filters excluded; pending remains in denominator.",
                            "post": "Owned signed-body HTTP invocations excluding reviewed proven pre-HTTP failures; native intents andUNSENT are separate.",
                            "remaining_budget": "Planned unspent cash; not available for a new order while UNKNOWN/ACK/SETTLING or another reservation remains.",
                            "UNKNOWN": "Raw ledger unresolved counts remain visible even when separate audited evidence proves zero execution."},
            "production_ledger_writes": False, "trading_API_calls": 0}

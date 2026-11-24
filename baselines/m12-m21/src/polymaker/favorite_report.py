"""Bounded FavoriteFirst run artifacts built after disarm from existing truth."""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

SCHEMA_VERSION = 1
ANOMALY_EVENTS = {
    "order_place_aborted_stale_connectivity": "STALE_CONNECTIVITY",
    "place_outcome_unknown": "PLACEMENT_UNKNOWN",
    "position_divergence": "POSITION_DIVERGENCE",
    "market_ws_blind": "MARKET_WS_BLIND",
    "market_blind": "MARKET_WS_BLIND",
    "market_ws_trading_resumed": "MARKET_WS_RECOVERED",
    "user_ws_dropped": "USER_WS_DISCONNECT",
    "user_ws_reconnected_forcing_reconcile": "USER_WS_RECONNECT",
    "quarantine": "QUARANTINE",
    "trade_quarantined": "QUARANTINE",
    "position_forced": "POSITION_CORRECTION",
    "reconcile_error": "RECONCILIATION_ERROR",
    "heartbeat_down_halting": "HALT",
}


def gross_paired_pnl(first_qty: float, first_vwap: float | None,
                     pair_qty: float, pair_vwap: float | None) -> float | None:
    """Gross locked PnL from accepted strategy fills; unknown if unpaired."""
    paired = min(first_qty, pair_qty)
    if paired == 0:
        return 0.0
    if first_vwap is None or pair_vwap is None:
        return None
    return paired * (1 - first_vwap - pair_vwap)


def leg_economics(fills: list[dict[str, Any]]) -> tuple[float, float | None]:
    """Economic quantity and VWAP from the already deduplicated accepted fills."""
    qty = sum(fill["fill_qty"] for fill in fills)
    vwap = sum(fill["fill_qty"] * fill["fill_price"] for fill in fills) / qty if qty else None
    return qty, vwap


def run_id(start: float, market: str, execution: float) -> str:
    stamp = datetime.fromtimestamp(execution, UTC).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{stamp}_{market}"


def attach_raw_log(path: Path) -> logging.FileHandler:
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(path)
    handler.setFormatter(structlog.stdlib.ProcessorFormatter(processors=[
        structlog.stdlib.ProcessorFormatter.remove_processors_meta,
        structlog.processors.JSONRenderer(),
    ]))
    logging.getLogger().addHandler(handler)
    return handler


def detach_raw_log(handler: logging.FileHandler) -> None:
    logging.getLogger().removeHandler(handler)
    handler.close()


def _utc(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, UTC).isoformat() if ts and ts > 0 else None


def _event(kind: str, ts: float | None, start: float, first_ts: float | None = None,
           **fields: Any) -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "event": kind, "timestamp_utc": _utc(ts),
            "market_elapsed_s": round(ts - start, 3) if ts and ts > 0 else None,
            "seconds_since_first_fill": round(ts - first_ts, 3)
            if ts and first_ts and ts >= first_ts else None, **fields}


def _read_raw_anomalies(path: Path, start: float, first_ts: float | None) -> list[dict[str, Any]]:
    """Read only one market's separate log; raw remains the diagnostic record."""
    if not path.exists():
        return []
    found = []
    for line in path.open():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        message = str(row.get("event", ""))
        kind = ANOMALY_EVENTS.get(message)
        if kind is None and row.get("level") in ("warning", "error", "critical"):
            kind = message.upper() or "WARNING"
        if kind is None:
            continue
        try:
            ts = datetime.fromisoformat(str(row["timestamp"]).replace("Z", "+00:00")).timestamp()
        except (KeyError, ValueError, TypeError):
            ts = None
        found.append(_event(kind, ts, start, first_ts, source=message,
                            detail={k: v for k, v in row.items() if k in
                                    ("reason", "order_id", "token", "cid", "consecutive",
                                     "internal", "onchain")}))
    return found


def build_events(report: dict[str, Any], raw_path: Path) -> list[dict[str, Any]]:
    start = report["start"]
    first_ts = report.get("first_fill_time")
    events = [_event("RUN_START", report.get("selected_at"), start),
              _event("MARKET_COMMITTED", report.get("selected_at"), start,
                     market=report["market"], condition_id=report["condition_id"])]
    if report.get("preflight_pass_at") is not None:
        events.append(_event("PREFLIGHT_PASS", report["preflight_pass_at"], start))
    previous_state = previous_quote = previous_pnl = previous_peak = None
    previous_trigger = None
    for row in report.get("decisions", []):
        data, ts = row["data"], row["ts"]
        state, quote = data.get("strategy_state"), data.get("desired_price")
        pnl = data.get("total_lockable_pnl")
        kind = None
        if state != previous_state:
            kind = {"OBSERVE": "OBSERVE_START", "ENTRY_CONFIRM": "ENTRY_CONFIRM",
                    "ENTRY_QUOTING": "FIRST_QUOTE", "FIRST_LEG_RISK_WINDOW": "RISK_WINDOW_START",
                    "RUNNER": "RUNNER_START", "PAIR_STOP": "RISK_WINDOW_DECISION",
                    "FORCED_PAIR": "FORCED_PAIR", "COMPLETE": "PAIR_COMPLETE",
                    "NO_TRADE": "MARKET_COMPLETE", "HALTED": "HALT",
                    "UNPAIRABLE_RESIDUAL": "UNPAIRABLE_RESIDUAL"}.get(state, "STATE_CHANGE")
        elif quote is not None and quote != previous_quote:
            kind = "FIRST_REQUOTE" if state == "ENTRY_QUOTING" else "PAIR_REQUOTE"
        elif (state == "FIRST_LEG_RISK_WINDOW" and pnl is not None and
              (previous_pnl is None or abs(pnl - previous_pnl) >= 0.05)):
            kind = "RISK_SAMPLE"
        if kind:
            events.append(_event(kind, ts, start, first_ts, state=state,
                                 favorite_side=data.get("favorite_side"),
                                 favorite_probability=data.get("favorite_probability"),
                                 desired_price=quote,
                                 active_order_price=data.get("active_order_price"),
                                 pair_quote_mode=data.get("pair_quote_mode"),
                                 total_lockable_pnl=pnl,
                                 peak_total_lockable_pnl=data.get("peak_total_lockable_pnl"),
                                 trailing_floor=data.get("trailing_floor"),
                                 pair_trigger=data.get("pair_trigger"),
                                 risk_window_elapsed=data.get("risk_window_elapsed"),
                                 risk_window_remaining=data.get("risk_window_remaining")))
        trigger = data.get("pair_trigger")
        if trigger and trigger != previous_trigger:
            events.append(_event(trigger if trigger in
                                 {"EMERGENCY_STOP", "TRAILING_STOP", "TIME_FORCED"}
                                 else "PAIR_TRIGGER", ts, start, first_ts,
                                 pair_trigger=trigger, total_lockable_pnl=pnl))
        previous_trigger = trigger
        peak = data.get("peak_total_lockable_pnl")
        if (state == "RUNNER" and peak is not None and
                (previous_peak is None or peak - previous_peak >= 0.05)):
            events.append(_event("PEAK_PNL_UPDATE", ts, start, first_ts,
                                 peak_total_lockable_pnl=peak,
                                 total_lockable_pnl=pnl,
                                 trailing_floor=data.get("trailing_floor")))
            previous_peak = peak
        previous_state, previous_quote = state, quote
        if pnl is not None:
            previous_pnl = pnl
    for leg, orders in (("FIRST", report.get("first_orders", [])),
                        ("PAIR", report.get("pair_orders", []))):
        for order in orders:
            events.append(_event("FIRST_ORDER" if leg == "FIRST" else "PAIR_ORDER",
                                 order["timestamp"], start, first_ts, leg=leg,
                                 order_id=order.get("order_id"), side="BUY",
                                 price=order["price"], qty=order["qty"]))
    for leg, fills in (("FIRST", report.get("first_fills", [])),
                       ("PAIR", report.get("pair_fills", []))):
        cumulative = 0.0
        for fill in sorted(fills, key=lambda f: f.get("timestamp") or 0):
            cumulative += fill["fill_qty"]
            events.append(_event("FIRST_FILL" if leg == "FIRST" else "PAIR_FILL",
                                 fill.get("timestamp"), start, first_ts, leg=leg,
                                 order_id=fill.get("order_id"), trade_id=fill.get("trade_id"),
                                 token=fill.get("token"), outcome=fill.get("outcome"),
                                 side=fill.get("side"), fill_qty=fill["fill_qty"],
                                 fill_price=fill["fill_price"],
                                 cumulative_leg_qty=cumulative))
    first_qty, _ = leg_economics(report.get("first_fills", []))
    memory_qty = report.get("first_leg_final_qty")
    if memory_qty is not None and abs(memory_qty - first_qty) > 1e-8:
        events.append(_event("ACCOUNTING_STATE_MISMATCH", report.get("finalized_at"), start,
                             first_ts, memory_qty=memory_qty, authoritative_qty=first_qty,
                             fill_truth_complete=report.get("fill_truth_complete")))
    pair_qty, _ = leg_economics(report.get("pair_fills", []))
    if pair_qty > first_qty + 1e-8:
        events.append(_event("ACCOUNTING_OVERPAIR", report.get("finalized_at"), start,
                             first_ts, first_qty=first_qty, pair_qty=pair_qty))
    events += _read_raw_anomalies(raw_path, start, first_ts)
    events.append(_event("RECONCILIATION_START", report.get("finalization_started_at"),
                         start, first_ts))
    events.append(_event("RECONCILIATION_COMPLETE", report.get("finalized_at"), start, first_ts,
                         **{k: report.get(k) for k in
                            ("yes_qty", "no_qty", "fill_truth_complete", "position_reconciled")}))
    events.append(_event("FINAL_REPORT", report.get("finalized_at"), start, first_ts))
    events.append(_event("RUN_END", report.get("finalized_at"), start, first_ts))
    events.sort(key=lambda e: (e["market_elapsed_s"] is None,
                               e["market_elapsed_s"] or 0))
    return events


def _classify_anomalies(events: list[dict[str, Any]], report: dict[str, Any]) -> list[dict[str, Any]]:
    anomalies = []
    pair_fill = any(e["event"] == "PAIR_FILL" for e in events)
    for e in events:
        kind = e["event"]
        if kind not in (set(ANOMALY_EVENTS.values()) - {"MARKET_WS_RECOVERED", "USER_WS_RECONNECT"}) | {"UNPAIRABLE_RESIDUAL", "RECONCILIATION_ERROR", "ACCOUNTING_STATE_MISMATCH", "ACCOUNTING_OVERPAIR"}:
            continue
        if kind == "POSITION_DIVERGENCE":
            recovered = bool(report.get("position_reconciled") and report.get("fill_truth_complete"))
        elif kind == "POSITION_CORRECTION":
            recovered = bool(report.get("position_reconciled"))
        elif kind == "STALE_CONNECTIVITY":
            recovered = pair_fill and any(x["event"] == "PAIR_FILL" and
                                          (x["market_elapsed_s"] or 0) >=
                                          (e["market_elapsed_s"] or 0) for x in events)
        elif kind == "MARKET_WS_BLIND":
            recovered = any(x["event"] == "MARKET_WS_RECOVERED" and
                            (x["market_elapsed_s"] or 0) >= (e["market_elapsed_s"] or 0)
                            for x in events)
        elif kind == "USER_WS_DISCONNECT":
            recovered = any(x["event"] == "USER_WS_RECONNECT" and
                            (x["market_elapsed_s"] or 0) >= (e["market_elapsed_s"] or 0)
                            for x in events)
        else:
            recovered = False
        status = ("RECOVERED_BY_FILL_TRUTH" if kind == "ACCOUNTING_STATE_MISMATCH"
                  and report.get("fill_truth_complete") is True else
                  "RECOVERED" if recovered else "EXPECTED_SAFETY_ACTION"
                  if kind in {"QUARANTINE", "UNPAIRABLE_RESIDUAL"} else "UNRESOLVED")
        detail = dict(e.get("detail", {}))
        if kind == "ACCOUNTING_STATE_MISMATCH":
            detail.update(memory_qty=e["memory_qty"], authoritative_qty=e["authoritative_qty"])
        elif kind == "ACCOUNTING_OVERPAIR":
            detail.update(first_qty=e["first_qty"], pair_qty=e["pair_qty"])
        if kind == "STALE_CONNECTIVITY":
            previous_quotes = [x for x in events if x.get("desired_price") is not None and
                               x.get("pair_quote_mode") and x["market_elapsed_s"] is not None and
                               e["market_elapsed_s"] is not None and
                               x["market_elapsed_s"] <= e["market_elapsed_s"]]
            later_fills = [x for x in events if x["event"] == "PAIR_FILL" and
                           x["market_elapsed_s"] is not None and
                           e["market_elapsed_s"] is not None and
                           x["market_elapsed_s"] >= e["market_elapsed_s"]]
            detail["blocked_desired_price"] = previous_quotes[-1]["desired_price"] if previous_quotes else None
            detail["eventual_fill_price"] = later_fills[0]["fill_price"] if later_fills else None
        anomalies.append({"event": kind, "market_elapsed_s": e["market_elapsed_s"],
                          "status": status, "detail": detail})
    return anomalies


def build_summary(report: dict[str, Any], events: list[dict[str, Any]],
                  result: str, artifact_dir: Path) -> dict[str, Any]:
    first_qty, first_vwap = leg_economics(report.get("first_fills", []))
    pair_qty, pair_vwap = leg_economics(report.get("pair_fills", []))
    paired = min(first_qty, pair_qty)
    fill_truth_complete = report.get("fill_truth_complete")
    gross = (None if fill_truth_complete is False else
             gross_paired_pnl(first_qty, first_vwap, pair_qty, pair_vwap))
    residual = abs(first_qty - pair_qty)
    first_side = next((fill.get("outcome") for fill in report.get("first_fills", [])
                       if fill.get("outcome") in ("YES", "NO")), None)
    pair_side = next((fill.get("outcome") for fill in report.get("pair_fills", [])
                      if fill.get("outcome") in ("YES", "NO")), None)
    residual_side = first_side if first_qty > pair_qty else pair_side if pair_qty > first_qty else None
    outcome = ("UNSETTLED_RESIDUAL" if residual else "NO_TRADE" if not first_qty and not pair_qty else
               "PROFIT" if gross is not None and gross > 1e-8 else
               "LOSS" if gross is not None and gross < -1e-8 else "BREAKEVEN" if gross is not None else "UNKNOWN")
    anomalies = _classify_anomalies(events, report)
    memory_qty = report.get("first_leg_final_qty")
    if (memory_qty is not None and abs(memory_qty - first_qty) > 1e-8 and
            not any(a["event"] == "ACCOUNTING_STATE_MISMATCH" for a in anomalies)):
        anomalies.append({"event": "ACCOUNTING_STATE_MISMATCH", "market_elapsed_s": None,
                          "status": "RECOVERED_BY_FILL_TRUTH" if fill_truth_complete is True else "UNRESOLVED",
                          "detail": {"memory_qty": memory_qty, "authoritative_qty": first_qty}})
    if pair_qty > first_qty + 1e-8 and not any(a["event"] == "ACCOUNTING_OVERPAIR" for a in anomalies):
        anomalies.append({"event": "ACCOUNTING_OVERPAIR", "market_elapsed_s": None,
                          "status": "UNRESOLVED", "detail": {"first_qty": first_qty, "pair_qty": pair_qty}})
    if (fill_truth_complete is False or pair_qty > first_qty + 1e-8 or
            any(a["event"] == "POSITION_DIVERGENCE" and a["status"] == "UNRESOLVED"
                for a in anomalies)):
        result = "INCOMPLETE_RECONCILIATION"
    trigger = report.get("pair_trigger")
    trigger_event = next((e for e in events if e.get("pair_trigger") == trigger and trigger), None)
    last_fill = max((e["market_elapsed_s"] for e in events if e["event"] == "PAIR_FILL" and
                     e["market_elapsed_s"] is not None), default=None)
    trigger_pnl = trigger_event.get("total_lockable_pnl") if trigger_event else None
    orders = report.get("first_orders", []) + report.get("pair_orders", [])
    summary = {"schema_version": SCHEMA_VERSION, "run_id": report["run_id"],
               "market": report["market"], "condition_id": report["condition_id"],
               "start": _utc(report["start"]), "end": _utc(report.get("finalized_at")),
               "selected_tick": report.get("selected_tick"),
               "min_order_size": report.get("min_order_size"),
               "effective_config": report["effective_config"],
               "engineering_result": result, "strategy_outcome": outcome,
               "strategy_state": report.get("strategy_state"),
               "entry": {"favorite_side": report.get("favorite_at_entry"),
                         "favorite_probability": report.get("favorite_probability_at_entry"),
                         "first_quote_elapsed_s": _elapsed(report.get("first_quote_time"), report["start"]),
                         "first_quote_price": report.get("first_quote_price"),
                         "first_fill_elapsed_s": _elapsed(report.get("first_fill_time"), report["start"]),
                         "first_fill_vwap": first_vwap,
                         "requote_count": report.get("first_quote_reprices")},
               "first_leg": {"qty": first_qty, "vwap": first_vwap,
                             "fills": report.get("first_fills", []),
                             "orders": report.get("first_orders", [])},
               "risk": {"decision_elapsed_s": _elapsed(report.get("risk_decision_time"), report["start"]),
                        "decision_pnl": report.get("risk_decision_total_lockable_pnl"),
                        "decision": report.get("decision"),
                        "peak_total_lockable_pnl": report.get("peak_total_lockable_pnl"),
                        "trailing_floor": report.get("trailing_floor")},
               "pair": {"qty": pair_qty, "vwap": pair_vwap,
                        "trigger": trigger, "quote_mode": next((e.get("pair_quote_mode") for e in reversed(events)
                                                                   if e.get("pair_quote_mode")), None),
                        "orders": report.get("pair_orders", []), "fills": report.get("pair_fills", []),
                        "trigger_pnl": trigger_pnl,
                        "trigger_to_fill_s": round(last_fill - trigger_event["market_elapsed_s"], 3)
                        if last_fill is not None and trigger_event and trigger_event["market_elapsed_s"] is not None else None,
                        "emergency_exit_delta": gross - trigger_pnl
                        if trigger == "EMERGENCY_STOP" and gross is not None and trigger_pnl is not None else None,
                        "peak_capture_ratio": gross / report["peak_total_lockable_pnl"]
                        if report.get("decision") == "RUNNER" and gross is not None and
                           report.get("peak_total_lockable_pnl") and report["peak_total_lockable_pnl"] > 0 else None},
               "economics": {"first_leg_cost": first_qty * first_vwap if first_vwap is not None else None,
                             "pair_leg_cost": pair_qty * pair_vwap if pair_vwap is not None else None,
                             "paired_qty": paired, "gross_paired_locked_pnl": gross,
                             "residual_qty": residual,
                             "residual_side": residual_side,
                             "status": "INCOMPLETE_FILL_TRUTH" if fill_truth_complete is False
                             else "OVERPAIRED" if pair_qty > first_qty + 1e-8 else "COMPLETE",
                             "residual_settlement_pnl": report.get("residual_or_settlement_pnl"),
                             "maker_rebate": report.get("maker_rebate"),
                             "final_net_pnl": report.get("total_settled_pnl")},
               "safety": {"sell_orders": sum(o.get("side") == "SELL" for o in orders),
                          "taker_orders": 0 if all(o.get("side", "BUY") == "BUY" for o in orders) else None,
                          "max_yes_strategy_qty": first_qty if first_side == "YES" else pair_qty if first_side == "NO" else 0,
                          "max_no_strategy_qty": first_qty if first_side == "NO" else pair_qty if first_side == "YES" else 0,
                          "t270_violation": any(o.get("timestamp", 0) >= report["start"] + 270 for o in orders),
                          "external_position_mismatch": (None if report.get("position_reconciled") is None
                                                         else not report["position_reconciled"]),
                          "final_open_orders": report.get("owned_open_orders")},
               "anomalies": anomalies,
               "reconciliation": {"yes_qty": report.get("yes_qty"), "no_qty": report.get("no_qty"),
                                  "paired_qty": paired, "residual_qty": residual,
                                  "residual_side": residual_side,
                                  "owned_open_orders": report.get("owned_open_orders"),
                                  "fill_truth_complete": report.get("fill_truth_complete"),
                                  "positions_reconciled": report.get("position_reconciled"),
                                  "runtime_halt": result == "HALTED_DURING_MARKET"},
               "artifact_paths": {name: str(artifact_dir / name) for name in
                                  ("raw.log", "events.jsonl", "run.json", "chat_report.md")}}
    return summary


def _elapsed(ts: float | None, start: float) -> float | None:
    return round(ts - start, 3) if ts and ts > 0 else None


def _fmt(value: Any) -> str:
    return "unknown" if value is None else str(value)


def _money(value: float | None) -> str:
    return "unknown" if value is None else f"${value:+.2f}"


def render_chat(summary: dict[str, Any], events: list[dict[str, Any]]) -> str:
    s = summary
    entry, risk, pair, econ = (s[k] for k in ("entry", "risk", "pair", "economics"))
    rec = s["reconciliation"]
    lines = ["# FavoriteFirst Run Report", "", "## Run",
             f"run_id: {s['run_id']}", f"market: {s['market']} ({s['condition_id'][:12]})",
             f"start: {_fmt(s['start'])} | end: {_fmt(s['end'])}",
             f"tick: {_fmt(s['selected_tick'])} | min order size: {_fmt(s['min_order_size'])}",
             f"result: {s['engineering_result']} | state: {_fmt(s['strategy_state'])} | outcome: {s['strategy_outcome']}",
             "", "## Effective Config"]
    config = s["effective_config"]
    lines += [f"{k}: {v}" for k, v in config.items()]
    lines += ["", "## Entry",
              f"Favorite: {_fmt(entry['favorite_side'])} @ {_fmt(entry['favorite_probability'])}",
              f"First quote: T+{_fmt(entry['first_quote_elapsed_s'])} @ {_fmt(entry['first_quote_price'])}; reprices: {_fmt(entry['requote_count'])}",
              f"First fill: T+{_fmt(entry['first_fill_elapsed_s'])}; qty: {s['first_leg']['qty']}; VWAP: {_fmt(entry['first_fill_vwap'])}"]
    for fill in s["first_leg"]["fills"]:
        lines.append(f"Fill: {fill['fill_qty']} @ {fill['fill_price']} ({_fmt(fill.get('trade_id'))})")
    lines += ["", "## First-Leg Risk Window",
              f"Decision: {_fmt(risk['decision'])} at T+{_fmt(risk['decision_elapsed_s'])}; PnL: {_money(risk['decision_pnl'])}"]
    samples = [e for e in events if e["event"] in
               ("RISK_WINDOW_START", "RISK_SAMPLE", "RISK_WINDOW_DECISION", "EMERGENCY_STOP")]
    for e in samples[:8]:
        lines.append(f"T+{_fmt(e['market_elapsed_s'])} TotalLockablePnL {_money(e.get('total_lockable_pnl'))} {e['event']}")
    lines += ["", "## Exit / Pair",
              f"Mode: {_fmt(pair['quote_mode'])}; trigger: {_fmt(pair['trigger'])}",
              f"Accepted pair qty: {pair['qty']}; VWAP: {_fmt(pair['vwap'])}"]
    for order in pair["orders"][:12]:
        lines.append(f"T+{_fmt(_elapsed(order['timestamp'], _start_epoch(s)))} BUY {order['qty']} @ {order['price']} ({_fmt(order.get('order_id'))})")
    for fill in pair["fills"]:
        lines.append(f"Pair fill: {fill['fill_qty']} @ {fill['fill_price']} ({_fmt(fill.get('trade_id'))})")
    lines += [f"Trigger to final fill: {_fmt(pair['trigger_to_fill_s'])}s",
              f"Emergency trigger PnL: {_money(pair['trigger_pnl'])}; exit delta: {_money(pair['emergency_exit_delta'])}",
              f"Runner peak PnL: {_money(risk['peak_total_lockable_pnl'])}; capture ratio: {_fmt(pair['peak_capture_ratio'])}",
              "", "## Economics",
              f"First-leg cost: {_money(econ['first_leg_cost'])}; pair-leg cost: {_money(econ['pair_leg_cost'])}",
              f"Gross paired locked PnL: {_money(econ['gross_paired_locked_pnl'])}; paired qty: {econ['paired_qty']}",
              f"Residual: {econ['residual_qty']} {_fmt(econ['residual_side'])}",
              f"Residual/settlement PnL: {_money(econ['residual_settlement_pnl'])}; maker rebate: {_money(econ['maker_rebate'])}; final net PnL: {_money(econ['final_net_pnl'])}",
              "", "## Safety"]
    lines += [f"{k}: {_fmt(v)}" for k, v in s["safety"].items()]
    lines += ["", "## Anomalies"]
    if not s["anomalies"]:
        lines.append("None observed.")
    visible_anomalies = sorted(s["anomalies"], key=lambda a: (a["status"] != "UNRESOLVED",
                                                              a["market_elapsed_s"] or 0))
    for a in visible_anomalies[:12]:
        lines.append(f"T+{_fmt(a['market_elapsed_s'])} {a['event']} — {a['status']} {a['detail']}")
    if len(visible_anomalies) > 12:
        lines.append(f"{len(visible_anomalies) - 12} more anomalies: see run.json")
    lines += ["", "## Key Timeline"]
    key = {"ENTRY_CONFIRM", "FIRST_QUOTE", "FIRST_REQUOTE", "FIRST_ORDER", "FIRST_FILL",
           "RISK_WINDOW_START", "RISK_WINDOW_DECISION", "RUNNER_START", "FORCED_PAIR",
           "PAIR_ORDER", "PAIR_REQUOTE", "PAIR_FILL", "PAIR_COMPLETE", "STALE_CONNECTIVITY",
           "POSITION_DIVERGENCE", "MARKET_COMPLETE", "UNPAIRABLE_RESIDUAL", "HALT"}
    key |= {"ACCOUNTING_STATE_MISMATCH", "ACCOUNTING_OVERPAIR"}
    for e in (e for e in events if e["event"] in key):
        if len(lines) > 125:
            lines.append("Additional events: see events.jsonl")
            break
        detail = next((f"{e[k]}" for k in ("fill_price", "price", "desired_price") if e.get(k) is not None), "")
        lines.append(f"T+{_fmt(e['market_elapsed_s'])} {e['event']} {detail}")
    lines += ["", "## Final Reconciliation"]
    lines += [f"{k}: {_fmt(v)}" for k, v in rec.items()]
    lines += ["", "## Verdict", f"Engineering: {s['engineering_result']}",
              f"Strategy outcome: {s['strategy_outcome']}", f"Exit trigger: {_fmt(pair['trigger'])}",
              f"Gross locked PnL: {_money(econ['gross_paired_locked_pnl'])}",
              f"Residual: {econ['residual_qty']}", "", "## Artifact References"]
    lines += [f"{k}: {v}" for k, v in s["artifact_paths"].items() if k != "chat_report.md"]
    return "\n".join(lines) + "\n"


def _start_epoch(summary: dict[str, Any]) -> float:
    return datetime.fromisoformat(summary["start"]).timestamp()


def write_artifacts(report: dict[str, Any], result: str, directory: Path) -> dict[str, Any]:
    directory.mkdir(parents=True, exist_ok=True)
    events = build_events(report, directory / "raw.log")
    with (directory / "events.jsonl").open("w") as stream:
        for event in events:
            stream.write(json.dumps(event, default=str) + "\n")
    summary = build_summary(report, events, result, directory)
    (directory / "run.json").write_text(json.dumps(summary, indent=2, default=str) + "\n")
    try:
        (directory / "chat_report.md").write_text(render_chat(summary, events))
    except Exception:  # noqa: BLE001 - disarmed trading cleanup must survive a renderer failure
        logging.getLogger(__name__).exception("favorite_chat_report_failed")
    return summary


def aggregate(base: Path, last: int) -> str:
    rows = []
    for path in base.glob("*/run.json"):
        try:
            row = json.loads(path.read_text())
        except (ValueError, OSError):
            continue
        if row.get("schema_version") == SCHEMA_VERSION and row.get("engineering_result") in {
            "COMPLETED", "HALTED_DURING_MARKET", "UNPAIRABLE_RESIDUAL",
            "INCOMPLETE_RECONCILIATION",
        }:
            rows.append(row)
    rows = sorted(rows, key=lambda r: r.get("run_id", ""), reverse=True)[:last]
    traded = [r for r in rows if r["first_leg"]["qty"] > 0]
    emergencies = [r for r in rows if r["pair"]["trigger"] == "EMERGENCY_STOP"]
    runners = [r for r in rows if r["risk"]["decision"] == "RUNNER"]
    known = [r["economics"]["gross_paired_locked_pnl"] for r in rows
             if r["economics"]["gross_paired_locked_pnl"] is not None]
    rebates = [r["economics"]["maker_rebate"] for r in rows
               if r["economics"]["maker_rebate"] is not None]
    final_pnl = [r["economics"]["final_net_pnl"] for r in rows
                 if r["economics"]["final_net_pnl"] is not None]
    def avg(values: list[float | None]) -> str:
        valid = [v for v in values if v is not None]
        return f"{sum(valid) / len(valid):.3f} (n={len(valid)})" if valid else "unknown"
    lines = [f"FavoriteFirst — Last {len(rows)} Markets", "",
             f"Markets with trade: {len(traded)}; No Trade: {sum(r['strategy_outcome'] == 'NO_TRADE' for r in rows)}",
             "Outcomes: " + ", ".join(f"{name} {sum(r['strategy_outcome'] == name for r in rows)}"
                                      for name in ("PROFIT", "BREAKEVEN", "LOSS", "UNSETTLED_RESIDUAL")),
             "Risk: " + ", ".join(f"{name} {sum(r['pair']['trigger'] == name for r in rows)}"
                                   for name in ("EMERGENCY_STOP", "PROBATION_FAILED", "TRAILING_STOP", "TIME_FORCED")),
             f"Gross locked PnL (known {len(known)}/{len(rows)}): {_money(sum(known)) if known else 'unknown'}",
             f"Known rebates ({len(rebates)}/{len(rows)}): {_money(sum(rebates)) if rebates else 'unknown'}; known final PnL ({len(final_pnl)}/{len(rows)}): {_money(sum(final_pnl)) if final_pnl else 'unknown'}",
             f"Average gross PnL: {avg(known)}",
             f"Average favorite probability: {avg([r['entry']['favorite_probability'] for r in traded])}",
             f"Average first-fill T+: {avg([r['entry']['first_fill_elapsed_s'] for r in traded])}",
             f"Emergency: {len(emergencies)}; trigger PnL: {avg([r['pair']['trigger_pnl'] for r in emergencies])}; final PnL: {avg([r['economics']['gross_paired_locked_pnl'] for r in emergencies])}",
             f"Emergency trigger→fill seconds: {avg([r['pair']['trigger_to_fill_s'] for r in emergencies])}; exit delta: {avg([r['pair']['emergency_exit_delta'] for r in emergencies])}",
             f"Runner: {len(runners)}; peak PnL: {avg([r['risk']['peak_total_lockable_pnl'] for r in runners])}; final PnL: {avg([r['economics']['gross_paired_locked_pnl'] for r in runners])}; peak capture: {avg([r['pair']['peak_capture_ratio'] for r in runners])}",
             f"Residual markets: {sum(r['economics']['residual_qty'] > 0 for r in rows)}; incomplete reconciliation: {sum(not r['reconciliation']['positions_reconciled'] or not r['reconciliation']['fill_truth_complete'] for r in rows)}; halts: {sum(r['reconciliation']['runtime_halt'] for r in rows)}"]
    counts: dict[str, int] = {}
    for row in rows:
        for anomaly in row["anomalies"]:
            counts[anomaly["event"]] = counts.get(anomaly["event"], 0) + 1
    lines.append("Anomalies: " + (", ".join(f"{k} {v}" for k, v in sorted(counts.items())) or "none"))
    return "\n".join(lines) + "\n"

"""Project native M1 journals without equating order acceptance with a fill."""

from polymaker.dashboard.model import Record, number

M1_NATIVE_VERSION = 1
M1_SIGNAL_EVENTS = {"RF_CONFIRMED", "EX_CONFIRMED", "OPPORTUNITY_IDENTIFIED"}


def summarize_native(s: Record, events: list[Record]) -> Record:
    progress = s.get("m1_progress") or {}
    heartbeat = next((e for e in reversed(events) if e["kind"] == "sm_m1_heartbeat"), {})
    final_event = next((e for e in reversed(events) if e["kind"] == "sm_run_result"), {})
    final = final_event.get("payload", {})
    metric = final.get("m1_submissions") or progress.get("signals") or {}
    metric_ts = final_event.get("ts", 0) if final.get("m1_submissions") else heartbeat.get("ts", 0)
    policy = s.get("header", {}).get("m1_execution_policy") or {}
    if not isinstance(policy, dict):
        policy = {}  # Early native journals used a descriptive string here.
    untracked = metric.get("fill_tracking", final.get("fill_tracking", policy.get("fill_tracking"))) == "NOT_TRACKED"
    signals, components, posts = {}, {"RF": {}, "EX": {}}, {}
    orders = {o["order_id"]: o for o in s["orders"]}
    token_sides = {token: side for side, token in s.get("token_ids", {}).items()}
    balance, balance_at = None, None

    def opportunity(row, ts, state=None, reason=None):
        identity = row.get("episode_id") or row.get("id")
        if not identity:
            return None
        token = row.get("token_id") or row.get("token")
        side = row.get("outcome")
        if token and side in {"YES", "NO"}:
            token_sides[str(token)] = side
        signal = signals.setdefault(identity, {"id": identity, "ts": ts, "price": None,
            "outcome": side or "—", "state": "IDENTIFIED", "reason": None, "state_at": ts})
        if signal["price"] is None:
            signal["price"] = row.get("anchor_bid_price", row.get("price"))
        if state is not None and ts >= signal["state_at"]:
            signal.update(state=state, reason=reason, state_at=ts)
        return signal

    for e in events:
        kind, p, ts = e["kind"], e["payload"], e["ts"]
        if kind == "sm_m1_signal":
            event = p.get("event")
            if event in {"RF_CONFIRMED", "EX_CONFIRMED"}:
                key = event[:2]
                identity = p.get(key.lower()+"_id") or (ts, e.get("offset"))
                components[key][identity] = {"ts": ts, "token": p.get("token_id")}
            elif event == "OPPORTUNITY_IDENTIFIED":
                opportunity(p.get("opportunity") or {}, ts)
        elif kind == "sm_m1_live_signal":
            opportunity(p, ts, p.get("state", "OBSERVED"), p.get("reason"))
        elif kind == "sm_m1_candidate_skipped":
            opportunity(p.get("opportunity") or {}, ts, "SKIPPED", p.get("reason"))
        elif kind in {"sm_m1_native_post", "sm_m1_live_post"}:
            quote = p.get("quote") or {}
            identity = p.get("opportunity_id")
            signal = opportunity({**quote, "id": identity}, ts,
                p.get("outcome", "POSTED"), p.get("reason"))
            oid = p.get("order_id")
            if quote.get("token_id") and quote.get("outcome") in {"YES", "NO"}:
                token_sides[quote["token_id"]] = quote["outcome"]
            if kind == "sm_m1_native_post":
                posts[(identity, p.get("post_number"), oid)] = quote.get("outcome")
                if oid:
                    order = orders.setdefault(oid, {"order_id": oid, "role": "M1", "quote": quote,
                        "status": "UNKNOWN", "submitted_at": ts, "confirmed_quantity": "0", "confirmed_cost": "0"})
                    order["post_started"] = True
            if signal and kind == "sm_m1_live_post" and oid in orders:
                orders[oid]["status"] = p.get("outcome", orders[oid]["status"])
        elif kind == "sm_m1_account_snapshot" and p.get("source") == "CLOB_BALANCE_ALLOWANCE_ONLY":
            value = number(p.get("official_balance_usd"))
            if value is not None and value >= 0 and s.get("header", {}).get("execution_mode") == "LIVE":
                balance, balance_at = str(value), ts

    for row in metric.get("by_signal", []):
        opportunity(row, metric_ts, row.get("state", "UNKNOWN"), row.get("reason"))
    counts = progress.get("signal_counts") or {}
    latest = {}
    for kind, rows in components.items():
        if rows:
            row = max(rows.values(), key=lambda r: r["ts"])
            latest[kind] = {"ts": row["ts"], "outcome": token_sides.get(row["token"], "—")}
    sent = {side: max(s.get("post_counts", {}).get("M1", {}).get(side, 0),
        sum(row.get("posts", 0) for row in metric.get("by_signal", []) if row.get("outcome") == side),
        sum(outcome == side for outcome in posts.values())) for side in ("YES", "NO")}
    signal_rows = sorted(signals.values(), key=lambda row: (row["ts"], row["id"]))
    s.update(strategy="M1", post_type="REAL", virtual_submitted_orders=0,
        real_submitted_orders=sum(sent.values()), m1_native_version=M1_NATIVE_VERSION,
        m1_fill_tracking="NOT_TRACKED" if untracked else "CONFIRMED_FILLS",
        account_balance_usdc=balance, account_balance_at=balance_at,
        m1_components={kind: max(counts.get(kind+"_CONFIRMED", 0), len(rows)) for kind, rows in components.items()},
        m1_component_latest=latest, m1_signals=[{k: v for k, v in row.items() if k != "state_at"} for row in signal_rows],
        orders=list(orders.values()))
    s["post_types"] = {"VIRTUAL": {"YES": 0, "NO": 0}, "REAL": sent}
    s["signal_counts"]["M1"] = max(metric.get("signals", 0), len(signals))
    s["signals"]["M1"] = {row["outcome"]: {**row, "episode_id": row["id"], "anchor_bid_price": row["price"]}
        for row in signal_rows}
    if untracked:
        s["positions_verified"] = False
        s["residual_pnl"] = None
        s["other_pending"] = True
        if final.get("execution_status") == "SUBMISSION_WINDOW_COMPLETE":
            s["issues"] = [issue for issue in s["issues"] if issue != "TERMINAL_EXECUTION_UNRESOLVED"]
        s["issues"] = sorted(set([*s["issues"], "M1_FILL_UNVERIFIED"]))
        for order in s["orders"]:
            if not number(order.get("confirmed_quantity")):
                order["confirmed_quantity"] = order["confirmed_cost"] = None
    if untracked or s["pending_orders"] or s["pending_fills"] or metric.get("pending"):
        s["trading_pnl"] = None
    return s
